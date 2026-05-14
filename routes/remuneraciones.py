from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from models import db, Empresa, Empleado, Liquidacion, VariablesMensuales
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
        accion = request.form.get('accion', 'calcular')

        if not periodo:
            flash('El período es obligatorio.', 'danger')
            hoy = date.today()
            periodo_default = f'{hoy.year}-{hoy.month:02d}'
            vars_mes = VariablesMensuales.query.filter_by(periodo=periodo_default).first()
            return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                                   vars_mes=vars_mes, periodo_default=periodo_default)

        vars = VariablesMensuales.query.filter_by(periodo=periodo).first()
        if not vars:
            flash(f'No hay variables para {periodo}. Carga en Remuneraciones → Variables.', 'warning')
            hoy = date.today()
            periodo_default = f'{hoy.year}-{hoy.month:02d}'
            vars_mes = VariablesMensuales.query.filter_by(periodo=periodo_default).first()
            return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                                   vars_mes=vars_mes, periodo_default=periodo_default)

        resultado = motor.calcular(
            emp,
            utm=vars.utm,
            uf=vars.uf,
            tope_gratificacion=vars.tope_gratificacion,
            tope_imponible=vars.tope_imponible,
            horas_extra=horas_extra,
            otros=otros,
        )

        if accion == 'calcular':
            # Mostrar preview sin guardar
            return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                                   vars_mes=vars, periodo_default=periodo,
                                   preview=resultado,
                                   form_data={'periodo': periodo, 'horas_extra': horas_extra, 'otros': otros})

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
        msg = f'Liquidación {periodo} emitida.' if estado == 'EMITIDA' else f'Borrador {periodo} guardado.'
        flash(msg, 'success')
        return redirect(url_for('remuneraciones.detalle', eid=eid, liq_id=liq.id))

    # GET
    hoy = date.today()
    periodo_default = f'{hoy.year}-{hoy.month:02d}'
    vars_mes = VariablesMensuales.query.filter_by(periodo=periodo_default).first()
    return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp,
                           vars_mes=vars_mes, periodo_default=periodo_default)


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


# ── Variables Mensuales ──────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/remuneraciones/variables')
def variables(eid):
    empresa = Empresa.query.get_or_404(eid)
    vars_list = VariablesMensuales.query.order_by(VariablesMensuales.periodo.desc()).all()
    return render_template('remuneraciones/variables.html', empresa=empresa, vars_list=vars_list)


@bp.route('/empresa/<int:eid>/remuneraciones/variables/guardar', methods=['POST'])
def variables_guardar(eid):
    empresa = Empresa.query.get_or_404(eid)
    periodo = request.form.get('periodo', '').strip()
    if not periodo:
        flash('Período obligatorio', 'danger')
        return redirect(url_for('remuneraciones.variables', eid=eid))

    v = VariablesMensuales.query.filter_by(periodo=periodo).first()
    if not v:
        v = VariablesMensuales(periodo=periodo)
        db.session.add(v)

    def _f(name): return float(request.form.get(name, 0) or 0)
    v.uf = _f('uf')
    v.utm = _f('utm')
    v.tope_imponible = _f('tope_imponible')
    v.tope_gratificacion = _f('tope_gratificacion')
    v.imm = _f('imm')
    from datetime import datetime
    v.fecha_actualizacion = datetime.now()
    db.session.commit()
    flash(f'Variables {periodo} guardadas.', 'success')
    return redirect(url_for('remuneraciones.variables', eid=eid))


@bp.route('/empresa/<int:eid>/remuneraciones/variables/eliminar/<periodo>', methods=['POST'])
def variables_eliminar(eid, periodo):
    v = VariablesMensuales.query.filter_by(periodo=periodo).first_or_404()
    db.session.delete(v)
    db.session.commit()
    flash(f'Variables {periodo} eliminadas.', 'success')
    return redirect(url_for('remuneraciones.variables', eid=eid))


@bp.route('/empresa/<int:eid>/remuneraciones/variables/get/<periodo>')
def variables_get(eid, periodo):
    """Devuelve las variables de un período como JSON (para AJAX en liquidar)."""
    v = VariablesMensuales.query.filter_by(periodo=periodo).first()
    if not v:
        return jsonify({'ok': False})
    return jsonify({
        'ok': True,
        'periodo': v.periodo,
        'uf': v.uf,
        'utm': v.utm,
        'tope_imponible': v.tope_imponible,
        'tope_gratificacion': v.tope_gratificacion,
        'imm': v.imm,
    })


@bp.route('/empresa/<int:eid>/remuneraciones/variables/fetch-indicadores')
def variables_fetch_previred(eid):
    """Obtiene UF y UTM del mes desde mindicador.cl (API JSON gratuita)."""
    try:
        import requests
        import calendar
        from datetime import date

        periodo = request.args.get('periodo', '')
        if not periodo:
            hoy = date.today()
            periodo = hoy.strftime('%Y-%m')

        anio, mes = int(periodo[:4]), int(periodo[5:7])
        ultimo_dia = calendar.monthrange(anio, mes)[1]

        headers = {'User-Agent': 'Mozilla/5.0'}
        base = 'https://mindicador.cl/api'

        # UF: valor del último día del mes
        r_uf = requests.get(f'{base}/uf/{ultimo_dia:02d}-{mes:02d}-{anio}',
                            timeout=10, headers=headers)
        uf_data = r_uf.json()
        uf = uf_data.get('serie', [{}])[0].get('valor') if r_uf.status_code == 200 else None

        # UTM: valor del mes (usar día 01)
        r_utm = requests.get(f'{base}/utm/01-{mes:02d}-{anio}',
                             timeout=10, headers=headers)
        utm_data = r_utm.json()
        utm = utm_data.get('serie', [{}])[0].get('valor') if r_utm.status_code == 200 else None

        result = {
            'ok': True,
            'periodo': periodo,
            'uf': uf,
            'utm': utm,
            'imm': None,
        }
        # Topes derivados
        if uf:
            result['tope_imponible'] = round(uf * 90)   # 90 UF tope imponible
        if result.get('imm'):
            result['tope_gratificacion'] = round(result['imm'] * 4.75 / 12)

        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


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
    emp.activo = form.get('activo') == 'on'
    fi = form.get('fecha_ingreso', '').strip()
    if fi:
        from datetime import date
        try:
            emp.fecha_ingreso = date.fromisoformat(fi)
        except ValueError:
            pass
