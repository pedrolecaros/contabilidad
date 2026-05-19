from datetime import date
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from models import db, Empresa, Prestamo, CuotaPrestamo, PagoCuota, ValorUF, Asiento, LineaAsiento, Contraparte, MovimientoBanco, Cuenta
from engine.asientos import generar_asiento_cuota_prestamo, generar_asiento_cuota_custom, generar_asiento_abono_capital

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

    # UF de hoy para convertir cuotas UF → pesos
    uf_row = ValorUF.query.filter(ValorUF.fecha <= hoy).order_by(ValorUF.fecha.desc()).first()
    uf_hoy = uf_row.valor if uf_row else None

    def _a_pesos(prestamo, monto_uf_o_pesos):
        if prestamo.moneda == 'UF' and uf_hoy:
            return monto_uf_o_pesos * uf_hoy
        return monto_uf_o_pesos

    total_pagar = 0.0
    total_cobrar = 0.0
    proximas = []
    vencidas = []

    for p in prestamos:
        if not p.activo:
            continue
        cuotas_pend = [c for c in p.cuotas if not c.pagada]
        monto_pend = sum(_a_pesos(p, c.cuota_total) for c in cuotas_pend)
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

    # Saldo pendiente por prestamo en pesos (for table display)
    saldos = {}
    for p in prestamos:
        cuotas_pend = [c for c in p.cuotas if not c.pagada]
        saldos[p.id] = sum(_a_pesos(p, c.cuota_total) for c in cuotas_pend)

    # Proxima cuota por prestamo
    proxima_cuota = {}
    for p in prestamos:
        cuotas_pend = [c for c in p.cuotas if not c.pagada]
        if cuotas_pend:
            proxima_cuota[p.id] = min(cuotas_pend, key=lambda c: c.fecha_vencimiento)

    # Month-by-month cash flow projection separada por tipo
    from collections import defaultdict
    def _new_row(): return {'capital': 0.0, 'interes': 0.0, 'total': 0.0}
    proy_pagar   = defaultdict(_new_row)
    proy_cobrar  = defaultdict(_new_row)
    proy_total   = defaultdict(_new_row)
    for p in prestamos:
        if not p.activo:
            continue
        bucket = proy_pagar if p.tipo == 'PAGAR' else proy_cobrar
        for c in p.cuotas:
            if c.pagada:
                continue
            mes = c.fecha_vencimiento.strftime('%Y-%m')
            cap = _a_pesos(p, c.capital or 0)
            ints = _a_pesos(p, c.interes or 0)
            tot = _a_pesos(p, c.cuota_total or 0)
            bucket[mes]['capital'] += cap
            bucket[mes]['interes'] += ints
            bucket[mes]['total']   += tot
            proy_total[mes]['capital'] += cap
            proy_total[mes]['interes'] += ints
            proy_total[mes]['total']   += tot

    # Unión de todas las fechas
    all_meses = sorted(set(list(proy_pagar) + list(proy_cobrar)))
    proyeccion_list = [(m, proy_pagar.get(m, _new_row()),
                           proy_cobrar.get(m, _new_row()),
                           proy_total.get(m, _new_row())) for m in all_meses]

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
                           uf_hoy=uf_hoy,
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
            subtipo=request.form.get('subtipo', 'BANCARIO'),
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
    uf_hoy = None
    if prestamo.moneda == 'UF':
        fechas = set(c.fecha_vencimiento for c in prestamo.cuotas)
        fechas.update(c.fecha_pago for c in prestamo.cuotas if c.fecha_pago)
        fechas.add(hoy)
        if fechas:
            uf_rows = ValorUF.query.filter(ValorUF.fecha.in_(fechas)).all()
            uf_vals = {r.fecha: r.valor for r in uf_rows}
        # Try to find nearest UF if today's is missing
        if hoy not in uf_vals:
            row = ValorUF.query.filter(ValorUF.fecha <= hoy).order_by(ValorUF.fecha.desc()).first()
            if row:
                uf_vals[hoy] = row.valor
        uf_hoy = uf_vals.get(hoy)

    # Separate regular vs extraordinary cuotas
    cuotas_regulares      = [c for c in prestamo.cuotas if (c.tipo or 'REGULAR') == 'REGULAR']
    cuotas_extraordinarias= [c for c in prestamo.cuotas if (c.tipo or 'REGULAR') == 'EXTRAORDINARIO']

    # Totals — split paid vs pending (regular cuotas only for amortization stats)
    cuotas_pagadas   = [c for c in cuotas_regulares if c.pagada]
    cuotas_pendientes = [c for c in cuotas_regulares if not c.pagada]

    total_capital     = sum(c.capital     for c in prestamo.cuotas)
    total_interes     = sum(c.interes     for c in prestamo.cuotas)
    total_cuotas      = sum(c.cuota_total for c in prestamo.cuotas)

    capital_pagado    = sum(c.capital     for c in cuotas_pagadas)
    interes_pagado    = sum(c.interes     for c in cuotas_pagadas)
    total_pagado      = sum(c.cuota_total for c in cuotas_pagadas)

    capital_pendiente = sum(c.capital     for c in cuotas_pendientes)
    interes_pendiente = sum(c.interes     for c in cuotas_pendientes)

    # CLP equivalents for UF loans
    total_cuotas_clp       = None
    capital_pendiente_clp  = None
    capital_pagado_clp     = None
    interes_pagado_clp     = None
    total_pagado_clp       = None
    if prestamo.moneda == 'UF' and uf_hoy:
        def _uf_clp(cuota, campo):
            uf = cuota.uf_valor_pago or uf_hoy
            return round(getattr(cuota, campo, 0) * uf)
        total_cuotas_clp      = sum(_uf_clp(c, 'cuota_total') for c in prestamo.cuotas)
        capital_pendiente_clp = round(capital_pendiente * uf_hoy)
        interes_pendiente_clp = round(interes_pendiente * uf_hoy)
        capital_pagado_clp    = sum(_uf_clp(c, 'capital') for c in cuotas_pagadas)
        interes_pagado_clp    = sum(_uf_clp(c, 'interes') for c in cuotas_pagadas)
        total_pagado_clp      = sum(_uf_clp(c, 'cuota_total') for c in cuotas_pagadas)
    else:
        interes_pendiente_clp = None

    import json
    cuentas_activas = Cuenta.query.filter_by(empresa_id=eid, activa=True, es_titulo=False).order_by(Cuenta.codigo).all()
    cuentas_json = json.dumps([{'id': c.id, 'codigo': c.codigo, 'nombre': c.nombre} for c in cuentas_activas])

    # Asientos linked to this prestamo (via Asiento.prestamo_id) not yet assigned to a cuota
    from models import Asiento as _Asiento, LineaAsiento as _Linea, PagoCuota as _PagoCuota
    cuota_asiento_ids = {c.asiento_id for c in prestamo.cuotas if c.asiento_id}
    # Also include asientos linked via PagoCuota
    for c in prestamo.cuotas:
        for p in c.pagos:
            if p.asiento_id:
                cuota_asiento_ids.add(p.asiento_id)
    asientos_vinculados = (_Asiento.query
                           .filter_by(prestamo_id=prestamo.id)
                           .filter(_Asiento.estado != 'ANULADO')
                           .order_by(_Asiento.fecha.desc())
                           .all())

    _CODIGOS_PAGAR  = {'2.1.10', '2.1.11', '2.1.12'}
    _CODIGOS_COBRAR = {'1.1.11', '1.1.12', '1.1.13'}
    _codigos_loan = _CODIGOS_PAGAR if prestamo.tipo == 'PAGAR' else _CODIGOS_COBRAR

    def _tipo_movimiento(asiento):
        """'PAGO' si reduce la deuda, 'AUMENTO' si la incrementa."""
        debe = haber = 0
        for l in asiento.lineas:
            if l.cuenta and l.cuenta.codigo in _codigos_loan:
                debe  += l.debe  or 0
                haber += l.haber or 0
        if prestamo.tipo == 'PAGAR':
            return 'PAGO' if debe >= haber else 'AUMENTO'
        else:
            return 'PAGO' if haber >= debe else 'AUMENTO'

    asientos_sin_cuota  = []
    asientos_aumento    = []
    for a in asientos_vinculados:
        if a.id in cuota_asiento_ids:
            continue
        if _tipo_movimiento(a) == 'AUMENTO':
            asientos_aumento.append(a)
        else:
            asientos_sin_cuota.append(a)

    # Build chronological payment history (real payments)
    pagos_historia = []
    for c in prestamo.cuotas:
        for p in c.pagos:
            pagos_historia.append({
                'fecha': p.fecha,
                'monto': p.monto,
                'cuota_num': c.numero_cuota,
                'cuota_tipo': c.tipo or 'REGULAR',
                'sin_efecto': p.sin_efecto_contable,
                'asiento_id': p.asiento_id,
                'asiento_num': p.asiento.numero if p.asiento else None,
                'notas': p.notas or '',
            })
        # Backward compat: cuotas paid before PagoCuota existed
        if c.pagada and not c.pagos and c.asiento_id:
            pagos_historia.append({
                'fecha': c.fecha_pago or c.fecha_vencimiento,
                'monto': float(c.cuota_total_pesos or c.cuota_total or 0),
                'cuota_num': c.numero_cuota,
                'cuota_tipo': c.tipo or 'REGULAR',
                'sin_efecto': False,
                'asiento_id': c.asiento_id,
                'asiento_num': c.asiento.numero if c.asiento else None,
                'notas': c.notas or '',
            })
    pagos_historia.sort(key=lambda x: (x['fecha'] or date.min))

    return render_template('prestamos/detalle.html',
                           empresa=empresa,
                           prestamo=prestamo,
                           hoy=hoy,
                           uf_vals=uf_vals,
                           uf_hoy=uf_hoy,
                           cuotas_regulares=cuotas_regulares,
                           cuotas_extraordinarias=cuotas_extraordinarias,
                           total_capital=total_capital,
                           total_interes=total_interes,
                           total_cuotas=total_cuotas,
                           capital_pendiente=capital_pendiente,
                           interes_pendiente=interes_pendiente,
                           capital_pagado=capital_pagado,
                           interes_pagado=interes_pagado,
                           total_pagado=total_pagado,
                           total_cuotas_clp=total_cuotas_clp,
                           capital_pendiente_clp=capital_pendiente_clp,
                           interes_pendiente_clp=interes_pendiente_clp,
                           capital_pagado_clp=capital_pagado_clp,
                           interes_pagado_clp=interes_pagado_clp,
                           total_pagado_clp=total_pagado_clp,
                           cuentas_json=cuentas_json,
                           asientos_sin_cuota=asientos_sin_cuota,
                           asientos_aumento=asientos_aumento,
                           pagos_historia=pagos_historia)


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
        prestamo.subtipo = request.form.get('subtipo', 'BANCARIO')
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

    if cuota.pagada or cuota.pagada_parcialmente:
        # Desmarcar — eliminar todos los PagoCuota y asientos auto-generados
        for pago in list(cuota.pagos):
            if pago.asiento_id and not pago.sin_efecto_contable:
                asiento_pago = Asiento.query.get(pago.asiento_id)
                if asiento_pago and asiento_pago.origen == 'PRESTAMO':
                    LineaAsiento.query.filter_by(asiento_id=asiento_pago.id).delete()
                    db.session.delete(asiento_pago)
        PagoCuota.query.filter_by(cuota_id=cuota.id).delete()
        # Backward compat: also clear legacy asiento_id
        if cuota.movimiento_banco_id:
            mov = MovimientoBanco.query.get(cuota.movimiento_banco_id)
            if mov:
                mov.procesado = False
                mov.asiento_id = None
            cuota.movimiento_banco_id = None
        if cuota.asiento_id:
            asiento = Asiento.query.get(cuota.asiento_id)
            cuota.asiento_id = None
            if asiento and asiento.origen == 'PRESTAMO':
                LineaAsiento.query.filter_by(asiento_id=asiento.id).delete()
                db.session.delete(asiento)
        cuota.pagada = False
        cuota.fecha_pago = None
        cuota.uf_valor_pago = None
        cuota.cuota_total_pesos = None
    else:
        fecha_pago_str = request.form.get('fecha_pago', '')
        try:
            fecha_pago = date.fromisoformat(fecha_pago_str)
        except (ValueError, TypeError):
            fecha_pago = date.today()
        cuota.fecha_pago = fecha_pago

        # Parse monto_real (supports partial payments)
        monto_real_str = request.form.get('monto_real', '').strip()
        try:
            monto_real = float(monto_real_str) if monto_real_str else None
        except ValueError:
            monto_real = None

        sin_efecto = request.form.get('sin_efecto_contable') == '1'

        # Lookup UF value if UF loan — nearest prior date if exact not found
        if prestamo.moneda == 'UF':
            uf_row = (ValorUF.query
                      .filter(ValorUF.fecha <= fecha_pago)
                      .order_by(ValorUF.fecha.desc()).first())
            if uf_row:
                cuota.uf_valor_pago = uf_row.valor
                cuota.cuota_total_pesos = round((cuota.cuota_total or 0) * uf_row.valor)
                if monto_real is None:
                    monto_real = cuota.cuota_total_pesos
            else:
                flash('No se encontró valor UF para la fecha de pago — asiento en pesos puede ser incorrecto.', 'warning')
        elif monto_real is None:
            monto_real = cuota.cuota_total

        # Determine if this is a full payment
        cuota_total_ref = cuota.cuota_total_pesos or cuota.cuota_total or 0
        es_pago_completo = monto_real is None or round(monto_real) >= round(cuota_total_ref)

        # Generate accounting entry unless sin_efecto_contable
        import json as _json
        asiento = None
        if not sin_efecto:
            lineas_json_str = request.form.get('lineas_json', '').strip()
            if lineas_json_str:
                try:
                    lineas_data = _json.loads(lineas_json_str)
                    asiento = generar_asiento_cuota_custom(cuota, lineas_data)
                    cuota.asiento_id = asiento.id
                except Exception as exc:
                    flash(f'Error en asiento editado: {exc}', 'danger')
                    try:
                        asiento = generar_asiento_cuota_prestamo(cuota, monto_real)
                        cuota.asiento_id = asiento.id if es_pago_completo else cuota.asiento_id
                    except ValueError:
                        pass
            else:
                try:
                    asiento = generar_asiento_cuota_prestamo(cuota, monto_real)
                    cuota.asiento_id = asiento.id if es_pago_completo else cuota.asiento_id
                except ValueError:
                    pass

        # Create PagoCuota record
        pago = PagoCuota(
            cuota_id=cuota.id,
            monto=round(monto_real or cuota_total_ref),
            fecha=fecha_pago,
            asiento_id=asiento.id if asiento else None,
            sin_efecto_contable=sin_efecto,
            notas='Sin efecto contable — período anterior' if sin_efecto else None,
        )
        db.session.add(pago)
        db.session.flush()

        if es_pago_completo:
            cuota.pagada = True
        else:
            cuota.pagada = False
            flash(f'Pago parcial registrado: ${round(monto_real):,.0f} de ${cuota_total_ref:,.0f}. '
                  f'Quedan ${cuota_total_ref - round(monto_real):,.0f} pendientes.', 'info')

        # Link bank movement if provided
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
        uf_row = (ValorUF.query
                  .filter(ValorUF.fecha <= fecha_pago)
                  .order_by(ValorUF.fecha.desc()).first())
        if uf_row:
            uf_valor = uf_row.valor
            cuota_total_pesos = round((capital + interes) * uf_row.valor)
        else:
            flash('No se encontró valor UF para la fecha de pago — asiento en pesos puede ser incorrecto.', 'warning')

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


@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/asignar-cuota-asiento', methods=['POST'])
def asignar_cuota_asiento(eid, pid):
    """Assign an asiento (linked via prestamo_id) to a specific cuota and mark it paid."""
    from models import Asiento as _Asiento
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        return jsonify({'ok': False, 'error': 'No autorizado'}), 403
    cuota_id  = request.form.get('cuota_id', type=int)
    asiento_id = request.form.get('asiento_id', type=int)
    if not cuota_id or not asiento_id:
        return jsonify({'ok': False, 'error': 'Datos incompletos'}), 400
    cuota = CuotaPrestamo.query.get(cuota_id)
    asiento = _Asiento.query.get(asiento_id)
    if not cuota or cuota.prestamo_id != pid:
        return jsonify({'ok': False, 'error': 'Cuota no encontrada'}), 404
    if not asiento or asiento.empresa_id != eid:
        return jsonify({'ok': False, 'error': 'Asiento no encontrado'}), 404
    if cuota.pagada:
        return jsonify({'ok': False, 'error': 'Esta cuota ya está pagada'}), 400

    monto_asiento = max(asiento.total_debe or 0, asiento.total_haber or 0)
    monto_cuota_ref = float(cuota.cuota_total_pesos or cuota.cuota_total or 0)

    # Add PagoCuota record for this asiento
    pago = PagoCuota(
        cuota_id=cuota.id,
        monto=round(monto_asiento),
        fecha=asiento.fecha,
        asiento_id=asiento.id,
        sin_efecto_contable=False,
    )
    db.session.add(pago)

    # Compute total paid including all prior pagos
    monto_total_pagado = sum(p.monto for p in cuota.pagos) + round(monto_asiento)
    es_completo = monto_total_pagado >= monto_cuota_ref * 0.99  # 1% tolerance

    cuota.fecha_pago = asiento.fecha
    if es_completo:
        cuota.pagada = True
        cuota.asiento_id = asiento.id
    # If partial, leave pagada=False and don't set asiento_id

    db.session.commit()

    warning = None
    if monto_cuota_ref > 0 and abs(monto_asiento - monto_cuota_ref) / monto_cuota_ref > 0.01:
        if monto_asiento < monto_cuota_ref:
            warning = (f'Pago parcial: el asiento es por ${monto_asiento:,.0f} '
                       f'pero la cuota es ${monto_cuota_ref:,.0f} '
                       f'(faltan ${monto_cuota_ref - monto_asiento:,.0f}).')
        else:
            warning = (f'El asiento es por ${monto_asiento:,.0f} '
                       f'pero la cuota esperaba ${monto_cuota_ref:,.0f} '
                       f'(diferencia ${abs(monto_asiento - monto_cuota_ref):,.0f}). '
                       f'Verificá si el monto es correcto.')

    return jsonify({'ok': True, 'warning': warning, 'completo': es_completo})


# ── Abono extraordinario a capital ───────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/abono-capital', methods=['POST'])
def abono_capital(eid, pid):
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))

    try:
        monto = float(request.form.get('monto', 0) or 0)
        fecha = date.fromisoformat(request.form.get('fecha', date.today().isoformat()))
    except (ValueError, TypeError):
        flash('Datos inválidos', 'danger')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))

    if monto <= 0:
        flash('El monto debe ser mayor a 0', 'warning')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))

    notas = request.form.get('notas', '').strip()
    sin_efecto = request.form.get('sin_efecto_contable') == '1'

    # Determine next cuota number for display
    max_num = db.session.query(db.func.max(CuotaPrestamo.numero_cuota)).filter_by(prestamo_id=pid).scalar() or 0

    cuota = CuotaPrestamo(
        prestamo_id=pid,
        numero_cuota=None,
        fecha_vencimiento=fecha,
        capital=monto,
        interes=0.0,
        cuota_total=monto,
        saldo_insoluto=0.0,
        pagada=True,
        fecha_pago=fecha,
        tipo='EXTRAORDINARIO',
        notas=notas or 'Abono extraordinario a capital',
    )
    db.session.add(cuota)
    db.session.flush()

    asiento = None
    if not sin_efecto:
        try:
            asiento = generar_asiento_abono_capital(prestamo, monto, fecha)
            cuota.asiento_id = asiento.id
        except ValueError as e:
            flash(f'No se pudo generar asiento: {e}', 'warning')

    pago = PagoCuota(
        cuota_id=cuota.id,
        monto=round(monto),
        fecha=fecha,
        asiento_id=asiento.id if asiento else None,
        sin_efecto_contable=sin_efecto,
        notas=notas,
    )
    db.session.add(pago)
    db.session.commit()

    if sin_efecto:
        flash(f'Abono a capital de ${monto:,.0f} registrado sin efecto contable.', 'success')
    else:
        flash(f'Abono a capital de ${monto:,.0f} registrado' + (f' — Asiento N°{asiento.numero}' if asiento else ''), 'success')
    return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))
