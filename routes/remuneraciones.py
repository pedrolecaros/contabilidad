from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from models import db, Empresa, Empleado, Liquidacion, VariablesMensuales, ValorUF
from engine import remuneraciones as motor

bp = Blueprint('remuneraciones', __name__)

AFP_OPCIONES = ['Capital', 'Cuprum', 'Habitat', 'Modelo', 'PlanVital', 'ProVida', 'Uno']


@bp.route('/empresa/<int:eid>/remuneraciones')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    empleados_activos = Empleado.query.filter_by(empresa_id=eid, activo=True).order_by(Empleado.nombre).all()
    empleados_inactivos = Empleado.query.filter_by(empresa_id=eid, activo=False).order_by(Empleado.nombre).all()
    return render_template('remuneraciones/index.html', empresa=empresa,
                           empleados=empleados_activos, empleados_inactivos=empleados_inactivos)


@bp.route('/empresa/<int:eid>/remuneraciones/nuevo', methods=['GET', 'POST'])
def nuevo(eid):
    empresa = Empresa.query.get_or_404(eid)
    if request.method == 'POST':
        emp = Empleado(empresa_id=eid)
        _poblar(emp, request.form)
        db.session.add(emp)
        db.session.commit()
        flash('Empleado creado correctamente.', 'success')
        return redirect(url_for('remuneraciones.index', eid=eid))
    return render_template('remuneraciones/form.html', empresa=empresa,
                           emp=None, afp_opciones=AFP_OPCIONES)


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/editar', methods=['GET', 'POST'])
def editar(eid, emp_id):
    empresa = Empresa.query.get_or_404(eid)
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    if request.method == 'POST':
        _poblar(emp, request.form)
        db.session.commit()
        flash('Empleado actualizado.', 'success')
        return redirect(url_for('remuneraciones.index', eid=eid))
    return render_template('remuneraciones/form.html', empresa=empresa,
                           emp=emp, afp_opciones=AFP_OPCIONES)


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/eliminar', methods=['POST'])
def eliminar(eid, emp_id):
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    emp.activo = False
    db.session.commit()
    flash(f'{emp.nombre} marcado como inactivo. Puedes reactivarlo desde la lista.', 'warning')
    return redirect(url_for('remuneraciones.index', eid=eid))


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/reactivar', methods=['POST'])
def reactivar(eid, emp_id):
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    emp.activo = True
    db.session.commit()
    flash(f'{emp.nombre} reactivado.', 'success')
    return redirect(url_for('remuneraciones.index', eid=eid))


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/liquidar', methods=['GET', 'POST'])
def liquidar(eid, emp_id):
    from datetime import date
    empresa = Empresa.query.get_or_404(eid)
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()

    if request.method == 'POST':
        periodo = request.form.get('periodo', '').strip()
        horas_extra = float(request.form.get('horas_extra', 0) or 0)
        otros = float(request.form.get('otros', 0) or 0)
        dias_trabajados = max(1, min(30, int(request.form.get('dias_trabajados', 30) or 30)))
        accion = request.form.get('accion', 'calcular')

        if not periodo:
            flash('El período es obligatorio.', 'danger')
            hoy = date.today()
            periodo_default = f'{hoy.year}-{hoy.month:02d}'
            vars_mes = VariablesMensuales.query.filter_by(periodo=periodo_default).first()
            return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                                   vars_mes=vars_mes, periodo_default=periodo_default,
                                   dias_trabajados=30)

        vars = VariablesMensuales.query.filter_by(periodo=periodo).first()
        if not vars:
            flash(f'No hay variables para {periodo}. Carga en Remuneraciones → Variables.', 'warning')
            hoy = date.today()
            periodo_default = f'{hoy.year}-{hoy.month:02d}'
            vars_mes = VariablesMensuales.query.filter_by(periodo=periodo_default).first()
            return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                                   vars_mes=vars_mes, periodo_default=periodo_default,
                                   dias_trabajados=dias_trabajados)

        # Prorate if not full month
        from types import SimpleNamespace
        factor = dias_trabajados / 30.0
        if factor != 1.0:
            emp_calc = SimpleNamespace(
                sueldo_base=round(emp.sueldo_base * factor),
                bono_colacion=round((getattr(emp, 'bono_colacion', 0) or 0) * factor),
                bono_movilizacion=round((getattr(emp, 'bono_movilizacion', 0) or 0) * factor),
                otros_haberes=getattr(emp, 'otros_haberes', 0) or 0,
                afp=emp.afp, tasa_afp_comision=emp.tasa_afp_comision,
                tipo_salud=emp.tipo_salud, isapre=emp.isapre,
                monto_isapre=getattr(emp, 'monto_isapre', 0) or 0,
                monto_isapre_uf=getattr(emp, 'monto_isapre_uf', 0) or 0,
                tasa_mutual=getattr(emp, 'tasa_mutual', 0.0093) or 0.0093,
                tipo_sueldo=getattr(emp, 'tipo_sueldo', 'BRUTO'),
            )
        else:
            emp_calc = emp

        resultado = motor.calcular(
            emp_calc,
            utm=vars.utm,
            uf=vars.uf,
            tope_gratificacion=vars.tope_gratificacion,
            tope_imponible=vars.tope_imponible,
            horas_extra=horas_extra,
            otros=otros,
            tasa_sis=vars.tasa_sis or None,
        )

        if accion == 'calcular':
            return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                                   vars_mes=vars, periodo_default=periodo,
                                   preview=resultado, dias_trabajados=dias_trabajados,
                                   form_data={'periodo': periodo, 'horas_extra': horas_extra,
                                              'otros': otros, 'dias_trabajados': dias_trabajados})

        # accion == 'borrador' o 'emitir' → guardar
        existe = Liquidacion.query.filter_by(empleado_id=emp_id, periodo=periodo).first()
        if existe:
            flash(f'Ya existe una liquidación para {periodo}. Edítela o elimínela primero.', 'warning')
            return redirect(url_for('remuneraciones.detalle', eid=eid, liq_id=existe.id))

        estado = 'EMITIDA' if accion == 'emitir' else 'BORRADOR'
        liq = Liquidacion(empresa_id=eid, empleado_id=emp_id, periodo=periodo, estado=estado)
        for campo, valor in resultado.items():
            if hasattr(liq, campo):
                setattr(liq, campo, valor)
        db.session.add(liq)
        db.session.commit()
        if accion == 'emitir':
            # Go directly to the printable PDF view
            return redirect(url_for('remuneraciones.imprimir', eid=eid, liq_id=liq.id))
        flash(f'Borrador {periodo} guardado.', 'success')
        return redirect(url_for('remuneraciones.detalle', eid=eid, liq_id=liq.id))

    # GET
    hoy = date.today()
    periodo_default = f'{hoy.year}-{hoy.month:02d}'
    vars_mes = VariablesMensuales.query.filter_by(periodo=periodo_default).first()
    return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                           vars_mes=vars_mes, periodo_default=periodo_default,
                           dias_trabajados=30)


@bp.route('/empresa/<int:eid>/remuneraciones/liquidacion/<int:liq_id>')
def detalle(eid, liq_id):
    empresa = Empresa.query.get_or_404(eid)
    liq = Liquidacion.query.filter_by(id=liq_id, empresa_id=eid).first_or_404()
    return render_template('remuneraciones/detalle.html', empresa=empresa, liq=liq)


@bp.route('/empresa/<int:eid>/remuneraciones/liquidacion/<int:liq_id>/imprimir')
def imprimir(eid, liq_id):
    empresa = Empresa.query.get_or_404(eid)
    liq = Liquidacion.query.filter_by(id=liq_id, empresa_id=eid).first_or_404()
    return render_template('remuneraciones/imprimir.html', empresa=empresa, liq=liq)


@bp.route('/empresa/<int:eid>/remuneraciones/liquidacion/<int:liq_id>/emitir', methods=['POST'])
def emitir_liq(eid, liq_id):
    liq = Liquidacion.query.filter_by(id=liq_id, empresa_id=eid).first_or_404()
    liq.estado = 'EMITIDA'
    db.session.commit()
    flash('Liquidación emitida.', 'success')
    return redirect(url_for('remuneraciones.detalle', eid=eid, liq_id=liq_id))


@bp.route('/empresa/<int:eid>/remuneraciones/liquidacion/<int:liq_id>/eliminar', methods=['POST'])
def eliminar_liq(eid, liq_id):
    liq = Liquidacion.query.filter_by(id=liq_id, empresa_id=eid).first_or_404()
    emp_id = liq.empleado_id
    db.session.delete(liq)
    db.session.commit()
    flash('Liquidación eliminada.', 'success')
    return redirect(url_for('remuneraciones.historial', eid=eid, emp_id=emp_id))


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/historial')
def historial(eid, emp_id):
    empresa = Empresa.query.get_or_404(eid)
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    liqs = (Liquidacion.query
            .filter_by(empleado_id=emp_id)
            .order_by(Liquidacion.periodo.desc())
            .all())
    return render_template('remuneraciones/historial.html', empresa=empresa, emp=emp, liqs=liqs)


@bp.route('/empresa/<int:eid>/remuneraciones/provision-vacaciones', methods=['GET', 'POST'])
def provision_vacaciones(eid):
    from datetime import date, timedelta
    import calendar
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    # Default: last month
    if hoy.month == 1:
        default_periodo = f'{hoy.year - 1}-12'
    else:
        default_periodo = f'{hoy.year}-{hoy.month - 1:02d}'
    periodo = request.args.get('periodo', default_periodo)

    # Parse period
    try:
        anio_p, mes_p = int(periodo[:4]), int(periodo[5:7])
    except (ValueError, IndexError):
        anio_p, mes_p = hoy.year, max(1, hoy.month - 1)
        periodo = f'{anio_p}-{mes_p:02d}'

    ultimo_dia_periodo = date(anio_p, mes_p, calendar.monthrange(anio_p, mes_p)[1])

    # Get active employees with at least one EMITIDA liquidacion
    empleados_activos = (Empleado.query
        .filter_by(empresa_id=eid, activo=True)
        .order_by(Empleado.nombre)
        .all())

    filas = []
    total_provision = 0.0

    for emp in empleados_activos:
        # Get last EMITIDA liquidacion for base salary reference
        ultima_liq = (Liquidacion.query
            .filter_by(empresa_id=eid, empleado_id=emp.id, estado='EMITIDA')
            .order_by(Liquidacion.periodo.desc())
            .first())

        if not ultima_liq:
            continue  # Skip if no emitida liquidacion

        # Base: renta imponible from last liquidacion
        renta_base = ultima_liq.renta_imponible or emp.sueldo_base or 0

        # Monthly accrual = renta_base / 12
        provision_mensual = round(renta_base / 12)

        # Count months active (from fecha_ingreso or first liquidacion)
        if emp.fecha_ingreso:
            meses_activos = (hoy.year - emp.fecha_ingreso.year) * 12 + (hoy.month - emp.fecha_ingreso.month)
        else:
            # Count EMITIDA liquidaciones
            meses_activos = Liquidacion.query.filter_by(
                empresa_id=eid, empleado_id=emp.id, estado='EMITIDA').count()

        meses_activos = max(0, meses_activos)
        dias_acumulados = round(meses_activos * 1.25, 1)
        total_acumulado = round(provision_mensual * meses_activos)

        filas.append({
            'emp': emp,
            'renta_base': renta_base,
            'provision_mensual': provision_mensual,
            'meses_activos': meses_activos,
            'dias_acumulados': dias_acumulados,
            'total_acumulado': total_acumulado,
            'ultimo_periodo': ultima_liq.periodo,
        })
        total_provision += provision_mensual

    # Look for vacation accounts
    from models import Cuenta, Asiento, LineaAsiento
    from sqlalchemy import or_

    cuenta_gasto = (Cuenta.query
        .filter_by(empresa_id=eid, activa=True, es_titulo=False)
        .filter(Cuenta.tipo == 'GASTO')
        .filter(or_(
            Cuenta.nombre.ilike('%vacacion%'),
            Cuenta.codigo.like('5.1.10%'),
        ))
        .first())

    cuenta_pasivo = (Cuenta.query
        .filter_by(empresa_id=eid, activa=True, es_titulo=False)
        .filter(Cuenta.tipo == 'PASIVO')
        .filter(or_(
            Cuenta.nombre.ilike('%vacacion%'),
            Cuenta.codigo.like('2.1.05%'),
        ))
        .first())

    asiento_generado = None
    warning_cuentas = not cuenta_gasto or not cuenta_pasivo

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'generar_asiento':
            cuenta_gasto_id = request.form.get('cuenta_gasto_id', type=int)
            cuenta_pasivo_id = request.form.get('cuenta_pasivo_id', type=int)

            if not cuenta_gasto_id or not cuenta_pasivo_id:
                flash('Selecciona las cuentas contables para generar el asiento.', 'danger')
            elif total_provision <= 0:
                flash('No hay provisión que registrar.', 'warning')
            else:
                # Generate journal entry
                from models import Asiento as AsientoModel, LineaAsiento as LineaModel
                total_prov_round = round(total_provision)

                # Next asiento number
                ultimo = (AsientoModel.query
                    .filter_by(empresa_id=eid)
                    .order_by(AsientoModel.numero.desc())
                    .first())
                siguiente_num = (ultimo.numero + 1) if ultimo and ultimo.numero else 1

                asiento = AsientoModel(
                    empresa_id=eid,
                    fecha=ultimo_dia_periodo,
                    numero=siguiente_num,
                    descripcion=f'Provisión vacaciones {periodo}',
                    origen='MANUAL',
                    estado='CONFIRMADO',
                )
                db.session.add(asiento)
                db.session.flush()

                linea_debe = LineaModel(
                    asiento_id=asiento.id,
                    cuenta_id=cuenta_gasto_id,
                    debe=total_prov_round,
                    haber=0.0,
                    descripcion=f'Gasto provisión vacaciones {periodo}',
                    orden=1,
                )
                linea_haber = LineaModel(
                    asiento_id=asiento.id,
                    cuenta_id=cuenta_pasivo_id,
                    debe=0.0,
                    haber=total_prov_round,
                    descripcion=f'Pasivo provisión vacaciones {periodo}',
                    orden=2,
                )
                db.session.add(linea_debe)
                db.session.add(linea_haber)
                db.session.commit()

                asiento_generado = asiento
                flash(f'Asiento N°{siguiente_num} generado por $ {total_prov_round:,.0f} (provisión vacaciones {periodo}).', 'success')

    # All accounts for manual selection
    cuentas_gasto = (Cuenta.query
        .filter_by(empresa_id=eid, activa=True, es_titulo=False, tipo='GASTO')
        .order_by(Cuenta.codigo).all())
    cuentas_pasivo = (Cuenta.query
        .filter_by(empresa_id=eid, activa=True, es_titulo=False, tipo='PASIVO')
        .order_by(Cuenta.codigo).all())

    return render_template('remuneraciones/provision_vacaciones.html',
        empresa=empresa, periodo=periodo, filas=filas,
        total_provision=total_provision,
        cuenta_gasto=cuenta_gasto, cuenta_pasivo=cuenta_pasivo,
        cuentas_gasto=cuentas_gasto, cuentas_pasivo=cuentas_pasivo,
        warning_cuentas=warning_cuentas,
        asiento_generado=asiento_generado,
        ultimo_dia_periodo=ultimo_dia_periodo)


@bp.route('/empresa/<int:eid>/remuneraciones/informe-renta')
def informe_renta(eid):
    from datetime import date
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    anio = request.args.get('anio', hoy.year, type=int)

    # Query all EMITIDA liquidaciones for this year grouped by employee
    liqs = (Liquidacion.query
            .join(Empleado, Liquidacion.empleado_id == Empleado.id)
            .filter(
                Liquidacion.empresa_id == eid,
                Liquidacion.estado == 'EMITIDA',
                Liquidacion.periodo.like(f'{anio}-%'),
            )
            .order_by(Empleado.nombre, Liquidacion.periodo)
            .all())

    # Group by employee
    from collections import defaultdict
    grupos = {}  # emp_id -> dict
    detalles = defaultdict(list)  # emp_id -> [liq, ...]

    for liq in liqs:
        eid_emp = liq.empleado_id
        detalles[eid_emp].append(liq)
        if eid_emp not in grupos:
            grupos[eid_emp] = {
                'empleado': liq.empleado,
                'renta_acumulada': 0.0,
                'impuesto_acumulado': 0.0,
                'meses': 0,
            }
        grupos[eid_emp]['renta_acumulada'] += liq.renta_imponible or 0
        grupos[eid_emp]['impuesto_acumulado'] += liq.impuesto_renta or 0
        grupos[eid_emp]['meses'] += 1

    filas = []
    for eid_emp, g in grupos.items():
        meses = g['meses']
        impuesto_acum = g['impuesto_acumulado']
        proyeccion = round(impuesto_acum * 12 / meses) if meses > 0 else 0
        filas.append({
            'empleado': g['empleado'],
            'renta_acumulada': g['renta_acumulada'],
            'impuesto_acumulado': impuesto_acum,
            'meses': meses,
            'proyeccion_anual': proyeccion,
            'detalle': detalles[eid_emp],
        })

    # Sort by renta desc
    filas.sort(key=lambda x: x['renta_acumulada'], reverse=True)

    totales = {
        'renta_acumulada': sum(f['renta_acumulada'] for f in filas),
        'impuesto_acumulado': sum(f['impuesto_acumulado'] for f in filas),
        'proyeccion_anual': sum(f['proyeccion_anual'] for f in filas),
    }

    anios_disponibles = list(range(hoy.year - 2, hoy.year + 3))

    return render_template('remuneraciones/informe_renta.html',
        empresa=empresa, anio=anio, anios=anios_disponibles,
        filas=filas, totales=totales)


@bp.route('/empresa/<int:eid>/remuneraciones/buscar-liquidacion')
def buscar_liquidacion(eid):
    """Devuelve liquidaciones cuyo líquido coincide con el monto bancario (±1 peso)."""
    monto  = request.args.get('monto', 0, type=float)
    periodo = request.args.get('periodo', '').strip()
    q = (Liquidacion.query
         .join(Empleado, Liquidacion.empleado_id == Empleado.id)
         .filter(Liquidacion.empresa_id == eid)
         .filter(db.func.abs(Liquidacion.liquido - monto) < 1))
    if periodo:
        q = q.filter(Liquidacion.periodo == periodo)
    liqs = q.order_by(Liquidacion.periodo.desc()).limit(10).all()
    return jsonify([{
        'id':      l.id,
        'nombre':  l.empleado.nombre,
        'periodo': l.periodo,
        'liquido': l.liquido,
        'url':     url_for('remuneraciones.detalle', eid=eid, liq_id=l.id),
    } for l in liqs])


@bp.route('/empresa/<int:eid>/remuneraciones/libro')
def libro(eid):
    from datetime import date
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    periodo = request.args.get('periodo', f'{hoy.year}-{hoy.month:02d}')
    liqs = (Liquidacion.query
            .join(Empleado, Liquidacion.empleado_id == Empleado.id)
            .filter(Liquidacion.empresa_id == eid, Liquidacion.periodo == periodo)
            .order_by(Empleado.nombre)
            .all())
    periodos = [p[0] for p in (db.session.query(Liquidacion.periodo)
                .filter(Liquidacion.empresa_id == eid)
                .distinct().order_by(Liquidacion.periodo.desc()).all())]
    return render_template('remuneraciones/libro.html',
        empresa=empresa, periodo=periodo, liqs=liqs, periodos=periodos)


@bp.route('/empresa/<int:eid>/remuneraciones/libro/imprimir')
def libro_imprimir(eid):
    from datetime import date
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    periodo = request.args.get('periodo', f'{hoy.year}-{hoy.month:02d}')
    liqs = (Liquidacion.query
            .join(Empleado, Liquidacion.empleado_id == Empleado.id)
            .filter(Liquidacion.empresa_id == eid, Liquidacion.periodo == periodo)
            .order_by(Empleado.nombre)
            .all())
    return render_template('remuneraciones/libro_imprimir.html',
        empresa=empresa, periodo=periodo, liqs=liqs, hoy=hoy)


@bp.route('/empresa/<int:eid>/remuneraciones/libro/excel')
def libro_excel(eid):
    from datetime import date
    from io import BytesIO
    from flask import send_file
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    periodo = request.args.get('periodo', f'{hoy.year}-{hoy.month:02d}')
    liqs = (Liquidacion.query
            .join(Empleado, Liquidacion.empleado_id == Empleado.id)
            .filter(Liquidacion.empresa_id == eid, Liquidacion.periodo == periodo)
            .order_by(Empleado.nombre)
            .all())

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'Libro {periodo}'

    # Styles
    hdr_fill  = PatternFill('solid', fgColor='1F2937')
    hdr_font  = Font(color='FFFFFF', bold=True, size=9)
    tot_fill  = PatternFill('solid', fgColor='374151')
    tot_font  = Font(color='FFFFFF', bold=True, size=9)
    sub_fill  = PatternFill('solid', fgColor='E5E7EB')
    sub_font  = Font(bold=True, size=9)
    thin      = Side(style='thin', color='D1D5DB')
    border    = Border(left=thin, right=thin, top=thin, bottom=thin)
    num_fmt   = '#,##0'
    center    = Alignment(horizontal='center', vertical='center', wrap_text=True)
    right     = Alignment(horizontal='right', vertical='center')
    left_al   = Alignment(horizontal='left', vertical='center')

    # Title rows
    ws.merge_cells('A1:W1')
    ws['A1'] = f'LIBRO DE REMUNERACIONES – {empresa.razon_social} – RUT {empresa.rut}'
    ws['A1'].font = Font(bold=True, size=12)
    ws.merge_cells('A2:W2')
    ws['A2'] = f'Período: {periodo}'
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[1].height = 18
    ws.row_dimensions[2].height = 14

    # Column headers (row 3 = group labels, row 4 = individual labels)
    groups = [
        ('Identificación', 5),
        ('Haberes Imponibles', 4),
        ('No Imponibles', 2),
        ('Totales', 2),
        ('Descuentos Legales', 4),
        ('Total Desc. / Líquido', 2),
        ('Aportes Empleador', 4),
    ]
    col = 1
    for label, span in groups:
        start = get_column_letter(col)
        end   = get_column_letter(col + span - 1)
        if span > 1:
            ws.merge_cells(f'{start}3:{end}3')
        cell = ws[f'{start}3']
        cell.value = label
        cell.font  = sub_font
        cell.fill  = sub_fill
        cell.alignment = center
        cell.border = border
        col += span

    # Individual column headers (row 4)
    cols = [
        ('N°',         8,  center),
        ('RUT',        14, center),
        ('Nombre',     28, left_al),
        ('AFP',        10, center),
        ('Salud',      10, center),
        # Imponibles
        ('Sueldo\nBase', 14, right),
        ('HH.EE.',     11, right),
        ('Gratif.',    11, right),
        ('Otros\nImp.',11, right),
        # No imponibles
        ('Colación',   11, right),
        ('Moviliz.',   11, right),
        # Totales
        ('Total\nHaberes', 14, right),
        ('Renta\nImp.', 14, right),
        # Descuentos
        ('AFP $',      12, right),
        ('Salud $',    12, right),
        ('Ces.\nTrab.',11, right),
        ('Imp.\n2aCat',11, right),
        # Neto
        ('Total\nDesc.',12, right),
        ('Líquido',    14, right),
        # Empleador
        ('SIS',        11, right),
        ('Ces.\nEmp.', 11, right),
        ('Mutual',     11, right),
        ('Costo\nEmp.',14, right),
    ]
    for i, (label, width, align) in enumerate(cols, 1):
        c = ws.cell(row=4, column=i, value=label)
        c.font = hdr_font; c.fill = hdr_fill
        c.alignment = align; c.border = border
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[3].height = 22
    ws.row_dimensions[4].height = 30

    # Data rows
    for idx, liq in enumerate(liqs, 1):
        row = 4 + idx
        emp = liq.empleado
        salud_txt = emp.tipo_salud + (f' – {emp.isapre}' if emp.isapre else '')
        values = [
            idx, emp.rut, emp.nombre, emp.afp, salud_txt,
            liq.sueldo_base, liq.horas_extra, liq.gratificacion, liq.otros_haberes,
            liq.bono_colacion, liq.bono_movilizacion,
            liq.total_haberes, liq.renta_imponible,
            liq.afp, liq.salud, liq.cesantia_trab, liq.impuesto_renta,
            liq.total_descuentos, liq.liquido,
            liq.sis, liq.cesantia_emp, liq.mutual, liq.costo_empresa,
        ]
        fill_row = PatternFill('solid', fgColor='F9FAFB') if idx % 2 == 0 else None
        for ci, val in enumerate(values, 1):
            c = ws.cell(row=row, column=ci, value=val)
            c.font = Font(size=9)
            c.border = border
            if ci >= 6:
                c.number_format = num_fmt
                c.alignment = right
            else:
                c.alignment = left_al if ci == 3 else center
            if fill_row:
                c.fill = fill_row
        ws.row_dimensions[row].height = 14

    # Totals row
    if liqs:
        tot_row = 4 + len(liqs) + 1
        ws.cell(row=tot_row, column=1, value='TOTAL').font = tot_font
        ws.cell(row=tot_row, column=1).fill = tot_fill
        ws.cell(row=tot_row, column=1).alignment = center
        ws.cell(row=tot_row, column=1).border = border
        for ci in range(2, 6):
            c = ws.cell(row=tot_row, column=ci, value='')
            c.fill = tot_fill; c.border = border
        for ci in range(6, 24):
            col_letter = get_column_letter(ci)
            data_start  = 5
            data_end    = 4 + len(liqs)
            formula     = f'=SUM({col_letter}{data_start}:{col_letter}{data_end})'
            c = ws.cell(row=tot_row, column=ci, value=formula)
            c.font = tot_font; c.fill = tot_fill
            c.number_format = num_fmt; c.alignment = right; c.border = border
        ws.row_dimensions[tot_row].height = 16

    ws.freeze_panes = 'F5'

    buf = BytesIO()
    wb.save(buf); buf.seek(0)
    fname = f'libro_remuneraciones_{empresa.rut}_{periodo}.xlsx'
    return send_file(buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=fname)


# ── Variables Mensuales (globales – sin empresa) ─────────────────────────────

@bp.route('/remuneraciones/variables')
def variables():
    vars_list = VariablesMensuales.query.order_by(VariablesMensuales.periodo.desc()).all()
    return render_template('remuneraciones/variables.html', vars_list=vars_list)


# Ruta legacy con eid → redirige a la global
@bp.route('/empresa/<int:eid>/remuneraciones/variables')
def variables_legacy(eid):
    return redirect(url_for('remuneraciones.variables'))


@bp.route('/remuneraciones/variables/guardar', methods=['POST'])
def variables_guardar():
    periodo = request.form.get('periodo', '').strip()
    if not periodo:
        flash('Período obligatorio', 'danger')
        return redirect(url_for('remuneraciones.variables'))

    v = VariablesMensuales.query.filter_by(periodo=periodo).first()
    if not v:
        v = VariablesMensuales(periodo=periodo)
        db.session.add(v)

    import json as _json
    def _f(name): return float(request.form.get(name, 0) or 0) or None
    v.uf = _f('uf')
    v.utm = _f('utm')
    v.tope_imponible = _f('tope_imponible')
    v.tope_gratificacion = _f('tope_gratificacion')
    v.imm = _f('imm')
    raw_sis = request.form.get('tasa_sis', '').strip()
    v.tasa_sis = float(raw_sis) / 100 if raw_sis else None

    # AFP commissions (submitted as afp_<Name> in %)
    AFP_NAMES = ['Capital', 'Cuprum', 'Habitat', 'Modelo', 'PlanVital', 'ProVida', 'Uno']
    tasas = {}
    for nombre in AFP_NAMES:
        raw = request.form.get(f'afp_{nombre}', '').strip()
        if raw:
            try:
                tasas[nombre] = round(float(raw.replace(',', '.')), 2)
            except ValueError:
                pass
    v.tasas_afp_json = _json.dumps(tasas) if tasas else None

    from datetime import datetime
    v.fecha_actualizacion = datetime.now()
    db.session.commit()
    flash(f'Variables {periodo} guardadas.', 'success')
    return redirect(url_for('remuneraciones.variables'))


@bp.route('/remuneraciones/variables/eliminar/<periodo>', methods=['POST'])
def variables_eliminar(periodo):
    v = VariablesMensuales.query.filter_by(periodo=periodo).first_or_404()
    db.session.delete(v)
    db.session.commit()
    flash(f'Variables {periodo} eliminadas.', 'success')
    return redirect(url_for('remuneraciones.variables'))


@bp.route('/empresa/<int:eid>/remuneraciones/variables/get/<periodo>')
def variables_get(eid, periodo):
    """Devuelve las variables de un período como JSON (para AJAX en liquidar)."""
    v = VariablesMensuales.query.filter_by(periodo=periodo).first()
    if not v:
        return jsonify({'ok': False})
    import json as _json
    tasas_afp = {}
    if v.tasas_afp_json:
        try:
            tasas_afp = _json.loads(v.tasas_afp_json)
        except Exception:
            pass
    return jsonify({
        'ok': True,
        'periodo': v.periodo,
        'uf': v.uf,
        'utm': v.utm,
        'tope_imponible': v.tope_imponible,
        'tope_gratificacion': v.tope_gratificacion,
        'imm': v.imm,
        'tasa_sis': round(v.tasa_sis * 100, 4) if v.tasa_sis else None,
        'tasas_afp': tasas_afp,
    })


@bp.route('/remuneraciones/variables/fetch-indicadores')
def variables_fetch_previred():
    """Obtiene todos los indicadores previsionales desde previred.com."""
    try:
        from datetime import date
        periodo = request.args.get('periodo', '')
        if not periodo:
            hoy = date.today()
            periodo = hoy.strftime('%Y-%m')
        result = _scrape_previred(periodo)
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


def _scrape_previred(periodo: str) -> dict:
    """Scrape previred.com/indicadores-previsionales/ and return all indicator data."""
    import requests as req
    from bs4 import BeautifulSoup
    import re

    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
    r = req.get('https://www.previred.com/indicadores-previsionales/',
                timeout=15, headers=headers)
    if r.status_code != 200:
        return {'ok': False, 'error': f'HTTP {r.status_code}'}

    soup = BeautifulSoup(r.text, 'html.parser')
    lines = [l.strip() for l in soup.get_text().split('\n') if l.strip()]

    def clp(s):
        return float(s.replace('$', '').replace('\xa0', '').replace('.', '').replace(',', '.').strip())

    def pct(s):
        return float(s.replace('%', '').replace(',', '.').strip())

    AFP_NAMES = ['Capital', 'Cuprum', 'Habitat', 'PlanVital', 'ProVida', 'Modelo', 'Uno']
    result = {'ok': True, 'periodo': periodo, 'tasas_afp': {}}

    anio, mes = int(periodo[:4]), int(periodo[5:7])

    for i, l in enumerate(lines):
        # UF: take the first "Al DD de MONTH del YYYY:" match (most current on the page)
        if not result.get('uf'):
            uf_match = re.match(r'Al \d+ de \w+ del \d{4}:', l)
            if uf_match and i + 1 < len(lines):
                try:
                    result['uf'] = clp(lines[i + 1])
                except Exception:
                    pass

        # AFP commissions: line is AFP name, next is "11,44%" (10% mandatory + commission)
        if l in AFP_NAMES and i + 1 < len(lines):
            try:
                total_pct = pct(lines[i + 1])
                result['tasas_afp'][l] = round(total_pct - 10.0, 2)
            except Exception:
                pass

        # UTM: look for "VALOR" → "UTM" → "UTA" → period → value
        if l == 'UTM' and i + 2 < len(lines):
            try:
                result['utm'] = clp(lines[i + 2])
            except Exception:
                pass

        # IMM: "Trab. Dependientes e Independientes:" → amount
        if 'Dependientes e Independientes' in l and i + 1 < len(lines):
            try:
                result['imm'] = clp(lines[i + 1])
            except Exception:
                pass

        # SIS: "Tasa SIS" → "1,62%"
        if l == 'Tasa SIS' and i + 1 < len(lines):
            try:
                result['tasa_sis'] = round(pct(lines[i + 1]) / 100, 6)
            except Exception:
                pass

    # Derived topes
    if result.get('uf'):
        result['tope_imponible'] = round(result['uf'] * 90)
    if result.get('imm'):
        result['tope_gratificacion'] = round(result['imm'] * 4.75 / 12)

    return result


# ── Tabla UF diaria ──────────────────────────────────────────────────────────

@bp.route('/remuneraciones/uf-tabla')
def uf_tabla():
    from datetime import date
    hoy = date.today()
    anio = request.args.get('anio', hoy.year, type=int)
    mes  = request.args.get('mes',  hoy.month, type=int)
    from datetime import date as dt
    desde = dt(anio, mes, 1)
    import calendar
    ultimo = calendar.monthrange(anio, mes)[1]
    hasta = dt(anio, mes, ultimo)
    filas = (ValorUF.query
             .filter(ValorUF.fecha >= desde, ValorUF.fecha <= hasta)
             .order_by(ValorUF.fecha)
             .all())
    return render_template('remuneraciones/uf_tabla.html',
                           filas=filas, anio=anio, mes=mes, hoy=hoy)


@bp.route('/remuneraciones/uf-tabla/actualizar', methods=['POST'])
def uf_actualizar():
    """Descarga los valores de UF del mes desde mindicador.cl y los guarda."""
    from datetime import date
    import requests as req, calendar
    hoy = date.today()
    anio = request.form.get('anio', hoy.year, type=int)
    mes  = request.form.get('mes',  hoy.month, type=int)
    ultimo = calendar.monthrange(anio, mes)[1]
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = req.get(f'https://mindicador.cl/api/uf', timeout=15, headers=headers)
        if r.status_code != 200:
            flash(f'Error al consultar mindicador.cl: HTTP {r.status_code}', 'danger')
            return redirect(url_for('remuneraciones.uf_tabla', anio=anio, mes=mes))
        serie = r.json().get('serie', [])
        actualizados = 0
        for item in serie:
            raw_fecha = item.get('fecha', '')[:10]  # YYYY-MM-DD
            try:
                from datetime import date as dt
                fecha = dt.fromisoformat(raw_fecha)
                if fecha.year == anio and fecha.month == mes:
                    valor = float(item['valor'])
                    existing = ValorUF.query.filter_by(fecha=fecha).first()
                    if existing:
                        existing.valor = valor
                    else:
                        db.session.add(ValorUF(fecha=fecha, valor=valor))
                    actualizados += 1
            except Exception:
                pass
        db.session.commit()
        flash(f'{actualizados} valores UF actualizados para {mes:02d}/{anio}.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('remuneraciones.uf_tabla', anio=anio, mes=mes))


@bp.route('/remuneraciones/uf-tabla/guardar', methods=['POST'])
def uf_guardar():
    """Guarda o actualiza un valor UF manual."""
    from datetime import date as dt
    raw = request.form.get('fecha', '').strip()
    valor_s = request.form.get('valor', '').strip().replace(',', '.')
    try:
        fecha = dt.fromisoformat(raw)
        valor = float(valor_s)
    except (ValueError, TypeError):
        flash('Fecha o valor inválido.', 'danger')
        return redirect(url_for('remuneraciones.uf_tabla'))
    existing = ValorUF.query.filter_by(fecha=fecha).first()
    if existing:
        existing.valor = valor
    else:
        db.session.add(ValorUF(fecha=fecha, valor=valor))
    db.session.commit()
    flash(f'UF {fecha} = ${valor:,.2f} guardada.', 'success')
    return redirect(url_for('remuneraciones.uf_tabla', anio=fecha.year, mes=fecha.month))


@bp.route('/remuneraciones/uf/get')
def uf_get_valor():
    """Retorna el valor UF para una fecha (JSON). Usado por otras secciones."""
    from datetime import date as dt
    raw = request.args.get('fecha', '')
    try:
        fecha = dt.fromisoformat(raw)
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'fecha inválida'})
    # Busca la fecha exacta; si no existe, retorna la más reciente anterior
    uf = (ValorUF.query
          .filter(ValorUF.fecha <= fecha)
          .order_by(ValorUF.fecha.desc())
          .first())
    if uf:
        return jsonify({'ok': True, 'fecha': str(uf.fecha), 'valor': uf.valor})
    return jsonify({'ok': False, 'error': 'Sin datos UF para esa fecha'})


def _poblar(emp, form):
    emp.rut = form.get('rut', '').strip()
    emp.nombre = form.get('nombre', '').strip()
    emp.cargo = form.get('cargo', '').strip()
    emp.tipo_contrato = form.get('tipo_contrato', 'INDEFINIDO')
    raw_sueldo = (form.get('sueldo_base', '0') or '0').replace('.', '').replace(',', '.')
    emp.sueldo_base = float(raw_sueldo) if raw_sueldo else 0.0
    emp.tipo_sueldo = form.get('tipo_sueldo', 'BRUTO')
    emp.afp = form.get('afp', 'Habitat')
    raw_tasa = form.get('tasa_afp_comision', '').strip()
    emp.tasa_afp_comision = float(raw_tasa) / 100 if raw_tasa else motor.AFP_COMISIONES.get(emp.afp, 0.0127)
    emp.tipo_salud = form.get('tipo_salud', 'FONASA')
    emp.isapre = form.get('isapre', '').strip() or None
    raw_isapre_uf = form.get('monto_isapre_uf', '').strip()
    emp.monto_isapre_uf = float(raw_isapre_uf) if raw_isapre_uf else 0.0
    # Keep legacy monto_isapre in sync (set to 0 since we use UF now)
    emp.monto_isapre = 0.0
    raw_colacion = (form.get('bono_colacion', '0') or '0').replace('.', '').replace(',', '.')
    emp.bono_colacion = float(raw_colacion) if raw_colacion else 0.0
    raw_movil = (form.get('bono_movilizacion', '0') or '0').replace('.', '').replace(',', '.')
    emp.bono_movilizacion = float(raw_movil) if raw_movil else 0.0
    raw_otros = (form.get('otros_haberes', '0') or '0').replace('.', '').replace(',', '.')
    emp.otros_haberes = float(raw_otros) if raw_otros else 0.0
    raw_mutual = form.get('tasa_mutual', '').strip()
    emp.tasa_mutual = float(raw_mutual) / 100 if raw_mutual else 0.0093
    raw_apv = (form.get('apv_monto', '0') or '0').replace('.', '').replace(',', '.')
    emp.apv_monto = float(raw_apv) if raw_apv else 0.0
    emp.apv_tipo = form.get('apv_tipo', 'A') or 'A'
    emp.activo = form.get('activo') == 'on'
    fi = form.get('fecha_ingreso', '').strip()
    if fi:
        from datetime import date
        try:
            emp.fecha_ingreso = date.fromisoformat(fi)
        except ValueError:
            pass
