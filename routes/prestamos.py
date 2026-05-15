from datetime import date
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from models import db, Empresa, Prestamo, CuotaPrestamo, ValorUF, Asiento, LineaAsiento, Contraparte, MovimientoBanco, Cuenta
from engine.asientos import generar_asiento_cuota_prestamo, generar_asiento_cuota_custom

bp = Blueprint('prestamos', __name__)


# ── PMT helper ────────────────────────────────────────────────────────────────

def _pmt(capital, tasa, n):
    """Excel-style PMT: periodic payment for fixed-schedule loan."""
    if tasa == 0 or n == 0:
        return capital / n if n else 0
    return capital * tasa / (1 - (1 + tasa) ** (-n))


def _resolver_empresa_relacionada(eid, emp_rel_id_str, acreedor_rut):
    """Returns empresa_relacionada_id: explicit form value, or auto-detected from RUT."""
    emp_rel_id = None
    if emp_rel_id_str:
        try:
            emp_rel_id = int(emp_rel_id_str)
        except (ValueError, TypeError):
            pass
    if not emp_rel_id and acreedor_rut:
        emp = Empresa.query.filter_by(rut=acreedor_rut).first()
        if emp and emp.id != eid:
            emp_rel_id = emp.id
    return emp_rel_id


def _periodicidad_delta(periodicidad):
    if periodicidad == 'TRIMESTRAL':
        return relativedelta(months=3)
    elif periodicidad == 'ANUAL':
        return relativedelta(years=1)
    else:  # MENSUAL default
        return relativedelta(months=1)


def _auto_crear_contraparte(eid, rut, nombre, tipo_prestamo):
    """Create Contraparte from loan acreedor/deudor if one doesn't exist yet."""
    if not rut or not nombre:
        return
    existe = Contraparte.query.filter_by(empresa_id=eid, rut=rut).first()
    if not existe:
        tipo_cp = 'PROVEEDOR' if tipo_prestamo == 'PAGAR' else 'CLIENTE'
        cp = Contraparte(empresa_id=eid, rut=rut, razon_social=nombre, tipo=tipo_cp)
        db.session.add(cp)


def _generar_cuotas(prestamo):
    """Regenerate amortization table. Skips for LIBRE loans."""
    if prestamo.periodicidad == 'LIBRE' or not prestamo.n_cuotas:
        return

    # Delete existing cuotas
    CuotaPrestamo.query.filter_by(prestamo_id=prestamo.id).delete()

    n = prestamo.n_cuotas
    tasa_anual = prestamo.tasa_interes_anual or 0.0

    # Determine periodic rate
    if prestamo.periodicidad == 'TRIMESTRAL':
        tasa_periodo = tasa_anual / 4
    elif prestamo.periodicidad == 'ANUAL':
        tasa_periodo = tasa_anual
    else:  # MENSUAL
        tasa_periodo = tasa_anual / 12

    pmt = _pmt(prestamo.monto_original, tasa_periodo, n)
    delta = _periodicidad_delta(prestamo.periodicidad)

    saldo = float(prestamo.monto_original)  # keep exact float to avoid rounding drift
    fecha = prestamo.fecha_inicio + delta

    for i in range(1, n + 1):
        interes_raw = saldo * tasa_periodo
        # Last cuota: pay exact remaining saldo (no rounding drift)
        capital_raw = saldo if i == n else (pmt - interes_raw)
        interes = round(interes_raw, 0)
        capital = round(capital_raw, 0)
        cuota_total = capital + interes
        saldo = saldo - capital_raw  # subtract exact, not rounded
        if saldo < 0:
            saldo = 0.0

        cuota = CuotaPrestamo(
            prestamo_id=prestamo.id,
            numero_cuota=i,
            fecha_vencimiento=fecha,
            capital=capital,
            interes=interes,
            cuota_total=cuota_total,
            saldo_insoluto=max(round(saldo, 0), 0),
        )
        db.session.add(cuota)
        fecha = fecha + delta


# ── Lista ─────────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos')
def lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    prestamos = (Prestamo.query
                 .filter_by(empresa_id=eid)
                 .order_by(Prestamo.activo.desc(), Prestamo.fecha_inicio.desc())
                 .all())

    hoy = date.today()
    en_30_dias = hoy + relativedelta(days=30)

    total_pagar = 0.0
    total_cobrar = 0.0
    proximas = []
    vencidas = []

    for p in prestamos:
        if not p.activo:
            continue
        cuotas_pend = [c for c in p.cuotas if not c.pagada]
        monto_pend = sum(c.cuota_total for c in cuotas_pend)
        if p.tipo == 'PAGAR':
            total_pagar += monto_pend
        else:
            total_cobrar += monto_pend

        for c in cuotas_pend:
            if c.fecha_vencimiento < hoy:
                vencidas.append((p, c))
            elif c.fecha_vencimiento <= en_30_dias:
                proximas.append((p, c))

    proximas.sort(key=lambda x: x[1].fecha_vencimiento)
    vencidas.sort(key=lambda x: x[1].fecha_vencimiento)

    # Saldo pendiente por prestamo (for table display)
    saldos = {}
    for p in prestamos:
        cuotas_pend = [c for c in p.cuotas if not c.pagada]
        saldos[p.id] = sum(c.cuota_total for c in cuotas_pend)

    # Proxima cuota por prestamo
    proxima_cuota = {}
    for p in prestamos:
        cuotas_pend = [c for c in p.cuotas if not c.pagada]
        if cuotas_pend:
            proxima_cuota[p.id] = min(cuotas_pend, key=lambda c: c.fecha_vencimiento)

    # Month-by-month cash flow projection (pending cuotas, next 24 months)
    from collections import defaultdict
    proyeccion = defaultdict(lambda: {'capital': 0.0, 'interes': 0.0, 'total': 0.0})
    for p in prestamos:
        if not p.activo:
            continue
        for c in p.cuotas:
            if c.pagada:
                continue
            mes = c.fecha_vencimiento.strftime('%Y-%m')
            proyeccion[mes]['capital'] += c.capital or 0
            proyeccion[mes]['interes'] += c.interes or 0
            proyeccion[mes]['total'] += c.cuota_total or 0
    proyeccion_list = sorted(proyeccion.items())

    return render_template('prestamos/lista.html',
                           empresa=empresa,
                           prestamos=prestamos,
                           total_pagar=total_pagar,
                           total_cobrar=total_cobrar,
                           proximas=proximas,
                           vencidas=vencidas,
                           saldos=saldos,
                           proxima_cuota=proxima_cuota,
                           proyeccion=proyeccion_list,
                           hoy=hoy)


# ── Nuevo ─────────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/nuevo', methods=['GET', 'POST'])
def nuevo(eid):
    empresa = Empresa.query.get_or_404(eid)
    empresas_rel = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        tipo = request.form.get('tipo', 'PAGAR')
        moneda = request.form.get('moneda', 'PESOS')
        periodicidad = request.form.get('periodicidad', 'MENSUAL')

        try:
            monto_original = float(request.form.get('monto_original', 0))
        except (ValueError, TypeError):
            monto_original = 0.0

        try:
            tasa = float(request.form.get('tasa_interes_anual', 0))
        except (ValueError, TypeError):
            tasa = 0.0

        try:
            fecha_inicio = date.fromisoformat(request.form.get('fecha_inicio', ''))
        except (ValueError, TypeError):
            flash('Fecha de inicio inválida', 'danger')
            return render_template('prestamos/form.html',
                                   empresa=empresa, prestamo=None,
                                   empresas_rel=empresas_rel, titulo='Nuevo Préstamo')

        n_cuotas = None
        if periodicidad != 'LIBRE':
            try:
                n_cuotas = int(request.form.get('n_cuotas', 0))
                if n_cuotas <= 0:
                    n_cuotas = None
            except (ValueError, TypeError):
                n_cuotas = None

        acreedor_rut = request.form.get('acreedor_rut', '').strip() or None
        emp_rel_id = _resolver_empresa_relacionada(
            eid, request.form.get('empresa_relacionada_id'), acreedor_rut)

        if not nombre:
            flash('El nombre es obligatorio', 'danger')
            contrapartes = Contraparte.query.filter_by(empresa_id=eid).order_by(Contraparte.razon_social).all()
            return render_template('prestamos/form.html',
                                   empresa=empresa, prestamo=None,
                                   empresas_rel=empresas_rel, contrapartes=contrapartes,
                                   titulo='Nuevo Préstamo')

        prestamo = Prestamo(
            empresa_id=eid,
            nombre=nombre,
            tipo=tipo,
            moneda=moneda,
            monto_original=monto_original,
            tasa_interes_anual=tasa / 100.0,
            fecha_inicio=fecha_inicio,
            n_cuotas=n_cuotas,
            periodicidad=periodicidad,
            acreedor_deudor=request.form.get('acreedor_deudor', '').strip() or None,
            acreedor_rut=acreedor_rut,
            empresa_relacionada_id=emp_rel_id,
            activo=True,
            notas=request.form.get('notas', '').strip() or None,
        )
        db.session.add(prestamo)
        db.session.flush()
        _generar_cuotas(prestamo)
        _auto_crear_contraparte(eid, acreedor_rut,
                                request.form.get('acreedor_deudor', '').strip(),
                                tipo)
        db.session.commit()
        flash(f'Préstamo "{nombre}" creado', 'success')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=prestamo.id))

    contrapartes = Contraparte.query.filter_by(empresa_id=eid).order_by(Contraparte.razon_social).all()
    return render_template('prestamos/form.html',
                           empresa=empresa, prestamo=None,
                           empresas_rel=empresas_rel, contrapartes=contrapartes,
                           titulo='Nuevo Préstamo')


# ── Detalle ───────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>')
def detalle(eid, pid):
    empresa = Empresa.query.get_or_404(eid)
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))

    hoy = date.today()

    # Load UF values for UF loans (dict {date: valor})
    uf_vals = {}
    if prestamo.moneda == 'UF':
        fechas = [c.fecha_vencimiento for c in prestamo.cuotas]
        if fechas:
            uf_rows = ValorUF.query.filter(
                ValorUF.fecha.in_(fechas)
            ).all()
            uf_vals = {r.fecha: r.valor for r in uf_rows}

    # Totals
    total_capital = sum(c.capital for c in prestamo.cuotas)
    total_interes = sum(c.interes for c in prestamo.cuotas)
    total_cuotas = sum(c.cuota_total for c in prestamo.cuotas)
    capital_pendiente = sum(c.capital for c in prestamo.cuotas if not c.pagada)

    import json
    cuentas_activas = Cuenta.query.filter_by(empresa_id=eid, activa=True, es_titulo=False).order_by(Cuenta.codigo).all()
    cuentas_json = json.dumps([{'id': c.id, 'codigo': c.codigo, 'nombre': c.nombre} for c in cuentas_activas])

    return render_template('prestamos/detalle.html',
                           empresa=empresa,
                           prestamo=prestamo,
                           hoy=hoy,
                           uf_vals=uf_vals,
                           total_capital=total_capital,
                           total_interes=total_interes,
                           total_cuotas=total_cuotas,
                           capital_pendiente=capital_pendiente,
                           cuentas_json=cuentas_json)


# ── Editar ────────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/editar', methods=['GET', 'POST'])
def editar(eid, pid):
    empresa = Empresa.query.get_or_404(eid)
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))

    empresas_rel = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()

    if request.method == 'POST':
        prestamo.nombre = request.form.get('nombre', '').strip()
        prestamo.tipo = request.form.get('tipo', 'PAGAR')
        prestamo.moneda = request.form.get('moneda', 'PESOS')
        prestamo.periodicidad = request.form.get('periodicidad', 'MENSUAL')
        prestamo.activo = bool(request.form.get('activo'))
        prestamo.acreedor_deudor = request.form.get('acreedor_deudor', '').strip() or None
        prestamo.acreedor_rut = request.form.get('acreedor_rut', '').strip() or None
        prestamo.notas = request.form.get('notas', '').strip() or None

        try:
            prestamo.monto_original = float(request.form.get('monto_original', 0))
        except (ValueError, TypeError):
            pass

        try:
            tasa = float(request.form.get('tasa_interes_anual', 0))
            prestamo.tasa_interes_anual = tasa / 100.0
        except (ValueError, TypeError):
            pass

        try:
            prestamo.fecha_inicio = date.fromisoformat(request.form.get('fecha_inicio', ''))
        except (ValueError, TypeError):
            flash('Fecha de inicio inválida', 'danger')
            return render_template('prestamos/form.html',
                                   empresa=empresa, prestamo=prestamo,
                                   empresas_rel=empresas_rel, titulo='Editar Préstamo')

        if prestamo.periodicidad != 'LIBRE':
            try:
                n = int(request.form.get('n_cuotas', 0))
                prestamo.n_cuotas = n if n > 0 else None
            except (ValueError, TypeError):
                prestamo.n_cuotas = None
        else:
            prestamo.n_cuotas = None

        prestamo.empresa_relacionada_id = _resolver_empresa_relacionada(
            eid, request.form.get('empresa_relacionada_id'), prestamo.acreedor_rut)

        if prestamo.periodicidad != 'LIBRE' and prestamo.n_cuotas:
            _generar_cuotas(prestamo)
        _auto_crear_contraparte(eid, prestamo.acreedor_rut,
                                prestamo.acreedor_deudor, prestamo.tipo)
        db.session.commit()
        flash('Préstamo actualizado', 'success')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))

    contrapartes = Contraparte.query.filter_by(empresa_id=eid).order_by(Contraparte.razon_social).all()
    return render_template('prestamos/form.html',
                           empresa=empresa, prestamo=prestamo,
                           empresas_rel=empresas_rel, contrapartes=contrapartes,
                           titulo='Editar Préstamo')


# ── Eliminar ──────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/eliminar', methods=['POST'])
def eliminar(eid, pid):
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))
    nombre = prestamo.nombre
    db.session.delete(prestamo)
    db.session.commit()
    flash(f'Préstamo "{nombre}" eliminado', 'warning')
    return redirect(url_for('prestamos.lista', eid=eid))


# ── Preview asiento cuota (sin guardar) ──────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/cuota/<int:cid>/preview', methods=['POST'])
def cuota_preview(eid, pid, cid):
    """Devuelve JSON con el borrador del asiento para la cuota, sin guardar."""
    cuota = CuotaPrestamo.query.get_or_404(cid)
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid or cuota.prestamo_id != pid:
        return jsonify(error='No autorizado'), 403

    fecha_pago_str = request.form.get('fecha_pago', '')
    try:
        fecha_pago = date.fromisoformat(fecha_pago_str)
    except (ValueError, TypeError):
        fecha_pago = date.today()

    uf_valor = None
    if prestamo.moneda == 'UF':
        uf_row = ValorUF.query.filter_by(fecha=fecha_pago).first()
        uf_valor = uf_row.valor if uf_row else None

    # Calculate amounts
    if prestamo.moneda == 'UF' and uf_valor:
        capital_pesos = round((cuota.capital or 0) * uf_valor)
        interes_pesos = round((cuota.interes or 0) * uf_valor)
        total_pesos = capital_pesos + interes_pesos
    else:
        capital_pesos = round(cuota.capital or 0)
        interes_pesos = round(cuota.interes or 0)
        total_pesos = round(cuota.cuota_total or 0)

    nombre = prestamo.acreedor_deudor or prestamo.nombre
    desc = f"Cuota {cuota.numero_cuota} préstamo {nombre}"

    from models import Cuenta
    def _buscar(codigo):
        c = Cuenta.query.filter_by(empresa_id=eid, codigo=codigo, activa=True).first()
        return {'id': c.id if c else None, 'codigo': codigo,
                'nombre': c.nombre if c else f'({codigo} no configurada)', 'ok': bool(c)}

    lineas = []
    cuentas_ok = True

    if prestamo.tipo == 'PAGAR':
        c_pasivo = _buscar('2.2.01')
        c_gasto = _buscar('5.2.12')
        c_banco = _buscar('1.1.02')
        if not all(x['ok'] for x in [c_pasivo, c_gasto, c_banco]):
            cuentas_ok = False
        if capital_pesos:
            lineas.append({'cuenta_id': c_pasivo['id'], 'cuenta': c_pasivo['codigo'],
                           'nombre': c_pasivo['nombre'],
                           'descripcion': f'Capital {nombre}', 'debe': capital_pesos, 'haber': 0})
        if interes_pesos:
            lineas.append({'cuenta_id': c_gasto['id'], 'cuenta': c_gasto['codigo'],
                           'nombre': c_gasto['nombre'],
                           'descripcion': f'Interés {nombre}', 'debe': interes_pesos, 'haber': 0})
        lineas.append({'cuenta_id': c_banco['id'], 'cuenta': c_banco['codigo'],
                       'nombre': c_banco['nombre'], 'es_banco': True,
                       'descripcion': desc, 'debe': 0, 'haber': total_pesos})
    else:
        c_banco = _buscar('1.1.02')
        c_activo = _buscar('1.3.01')
        c_ingreso = _buscar('4.2.01')
        if not all(x['ok'] for x in [c_banco, c_activo, c_ingreso]):
            cuentas_ok = False
        lineas.append({'cuenta_id': c_banco['id'], 'cuenta': c_banco['codigo'],
                       'nombre': c_banco['nombre'], 'es_banco': True,
                       'descripcion': desc, 'debe': total_pesos, 'haber': 0})
        if capital_pesos:
            lineas.append({'cuenta_id': c_activo['id'], 'cuenta': c_activo['codigo'],
                           'nombre': c_activo['nombre'],
                           'descripcion': f'Capital {nombre}', 'debe': 0, 'haber': capital_pesos})
        if interes_pesos:
            lineas.append({'cuenta_id': c_ingreso['id'], 'cuenta': c_ingreso['codigo'],
                           'nombre': c_ingreso['nombre'],
                           'descripcion': f'Interés {nombre}', 'debe': 0, 'haber': interes_pesos})

    # Movimientos bancarios pendientes que podrían corresponder al pago
    if prestamo.tipo == 'PAGAR':
        movs_q = (MovimientoBanco.query
                  .filter_by(empresa_id=eid, procesado=False)
                  .filter(MovimientoBanco.cargo > 0)
                  .order_by(MovimientoBanco.fecha.desc())
                  .limit(30).all())
    else:
        movs_q = (MovimientoBanco.query
                  .filter_by(empresa_id=eid, procesado=False)
                  .filter(MovimientoBanco.abono > 0)
                  .order_by(MovimientoBanco.fecha.desc())
                  .limit(30).all())
    movimientos_banco = [{
        'id': m.id,
        'fecha': m.fecha.strftime('%d/%m/%Y') if m.fecha else '',
        'descripcion': (m.descripcion or '')[:60],
        'monto': float(m.cargo if (m.cargo or 0) > 0 else (m.abono or 0)),
        'banco': m.banco or '',
    } for m in movs_q]

    return jsonify(
        descripcion=desc,
        fecha=fecha_pago.isoformat(),
        moneda=prestamo.moneda,
        uf_valor=uf_valor,
        capital_pesos=capital_pesos,
        interes_pesos=interes_pesos,
        total_pesos=total_pesos,
        lineas=lineas,
        cuentas_ok=cuentas_ok,
        movimientos_banco=movimientos_banco,
    )


# ── Toggle cuota pagada ───────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/cuota/<int:cid>/toggle', methods=['POST'])
def toggle_cuota(eid, pid, cid):
    cuota = CuotaPrestamo.query.get_or_404(cid)
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid or cuota.prestamo_id != pid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))

    if cuota.pagada:
        # Desmarcar — eliminar asiento generado automáticamente
        if cuota.movimiento_banco_id:
            mov = MovimientoBanco.query.get(cuota.movimiento_banco_id)
            if mov:
                mov.procesado = False
                mov.asiento_id = None
            cuota.movimiento_banco_id = None
        if cuota.asiento_id:
            asiento = Asiento.query.get(cuota.asiento_id)
            cuota.asiento_id = None
            if asiento:
                LineaAsiento.query.filter_by(asiento_id=asiento.id).delete()
                db.session.delete(asiento)
        cuota.pagada = False
        cuota.fecha_pago = None
        cuota.uf_valor_pago = None
        cuota.cuota_total_pesos = None
    else:
        cuota.pagada = True
        fecha_pago_str = request.form.get('fecha_pago', '')
        try:
            cuota.fecha_pago = date.fromisoformat(fecha_pago_str)
        except (ValueError, TypeError):
            cuota.fecha_pago = date.today()

        # Lookup UF value if UF loan
        if prestamo.moneda == 'UF':
            uf_row = ValorUF.query.filter_by(fecha=cuota.fecha_pago).first()
            if uf_row:
                cuota.uf_valor_pago = uf_row.valor
                cuota.cuota_total_pesos = round((cuota.cuota_total or 0) * uf_row.valor)

        # Generate accounting entry (custom lineas if user edited them)
        import json as _json
        asiento = None
        lineas_json_str = request.form.get('lineas_json', '').strip()
        if lineas_json_str:
            try:
                lineas_data = _json.loads(lineas_json_str)
                asiento = generar_asiento_cuota_custom(cuota, lineas_data)
                cuota.asiento_id = asiento.id
            except Exception as exc:
                flash(f'Error en asiento editado: {exc}', 'danger')
                try:
                    asiento = generar_asiento_cuota_prestamo(cuota)
                    cuota.asiento_id = asiento.id
                except ValueError:
                    pass
        else:
            try:
                asiento = generar_asiento_cuota_prestamo(cuota)
                cuota.asiento_id = asiento.id
            except ValueError:
                pass  # missing accounts — skip silently

        # Link bank movement if provided — only if it hasn't been processed elsewhere
        mov_id_str = request.form.get('movimiento_banco_id', '').strip()
        if mov_id_str:
            try:
                mov = MovimientoBanco.query.get(int(mov_id_str))
                if mov and mov.empresa_id == eid:
                    if mov.procesado:
                        flash(f'El movimiento bancario del {mov.fecha} ya está procesado '
                              f'en otro comprobante — no se vinculó.', 'warning')
                    else:
                        cuota.movimiento_banco_id = mov.id
                        mov.procesado = True
                        if asiento:
                            mov.asiento_id = asiento.id
            except (ValueError, TypeError):
                pass

    db.session.commit()
    return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))


# ── Agregar pago libre ────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/pago', methods=['POST'])
def agregar_pago(eid, pid):
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))

    if prestamo.periodicidad != 'LIBRE':
        flash('Este préstamo no es de tipo libre', 'warning')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))

    try:
        fecha_pago = date.fromisoformat(request.form.get('fecha_pago', ''))
    except (ValueError, TypeError):
        flash('Fecha de pago inválida', 'danger')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))

    try:
        capital = float(request.form.get('capital', 0))
    except (ValueError, TypeError):
        capital = 0.0

    try:
        interes = float(request.form.get('interes', 0))
    except (ValueError, TypeError):
        interes = 0.0

    notas = request.form.get('notas', '').strip() or None

    # Determine next numero_cuota
    ultimo = (db.session.query(db.func.max(CuotaPrestamo.numero_cuota))
              .filter_by(prestamo_id=pid).scalar() or 0)

    uf_valor = None
    cuota_total_pesos = None
    if prestamo.moneda == 'UF':
        uf_row = ValorUF.query.filter_by(fecha=fecha_pago).first()
        if uf_row:
            uf_valor = uf_row.valor
            cuota_total_pesos = round((capital + interes) * uf_row.valor)

    cuota = CuotaPrestamo(
        prestamo_id=pid,
        numero_cuota=ultimo + 1,
        fecha_vencimiento=fecha_pago,
        capital=capital,
        interes=interes,
        cuota_total=capital + interes,
        saldo_insoluto=0,
        pagada=True,
        fecha_pago=fecha_pago,
        notas=notas,
        uf_valor_pago=uf_valor,
        cuota_total_pesos=cuota_total_pesos,
    )
    db.session.add(cuota)
    db.session.flush()

    asiento = None
    try:
        asiento = generar_asiento_cuota_prestamo(cuota)
        cuota.asiento_id = asiento.id
    except ValueError:
        pass

    mov_id_str = request.form.get('movimiento_banco_id', '').strip()
    if mov_id_str:
        try:
            mov = MovimientoBanco.query.get(int(mov_id_str))
            if mov and mov.empresa_id == eid:
                if mov.procesado:
                    flash(f'El movimiento bancario del {mov.fecha} ya está procesado '
                          f'en otro comprobante — no se vinculó.', 'warning')
                else:
                    cuota.movimiento_banco_id = mov.id
                    mov.procesado = True
                    if asiento:
                        mov.asiento_id = asiento.id
        except (ValueError, TypeError):
            pass

    db.session.commit()
    flash('Pago registrado', 'success')
    return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))



# ── API cuotas pendientes (para conciliación) ─────────────────────────────────

@bp.route('/empresa/<int:eid>/api/cuotas-pendientes')
def api_cuotas_pendientes(eid):
    """Devuelve cuotas no pagadas agrupadas por préstamo, para vincular en conciliación."""
    empresa = Empresa.query.get_or_404(eid)
    prestamos = Prestamo.query.filter_by(empresa_id=eid).order_by(Prestamo.nombre).all()

    resultado = []
    for p in prestamos:
        cuotas_pend = [c for c in p.cuotas if not c.pagada]
        if not cuotas_pend:
            continue
        cuotas_data = []
        for c in sorted(cuotas_pend, key=lambda x: x.fecha_vencimiento or date.today()):
            monto = float(c.cuota_total or 0)
            cuotas_data.append({
                'id': c.id,
                'numero': c.numero_cuota,
                'fecha_venc': c.fecha_vencimiento.isoformat() if c.fecha_vencimiento else '',
                'capital': float(c.capital or 0),
                'interes': float(c.interes or 0),
                'total': monto,
                'moneda': p.moneda,
                'desc': f"Cuota {c.numero_cuota} – vcto {c.fecha_vencimiento.strftime('%d/%m/%Y') if c.fecha_vencimiento else '?'}",
            })
        resultado.append({
            'id': p.id,
            'nombre': p.nombre,
            'tipo': p.tipo,
            'moneda': p.moneda,
            'cuotas': cuotas_data,
        })

    return jsonify(prestamos=resultado)
