from flask import Blueprint, render_template, request, flash, redirect, url_for
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import func
from models import db, Empresa, Cuenta, Asiento, LineaAsiento, ActivoFijo, DepreciacionRegistro

bp = Blueprint('activos', __name__)

CATEGORIA_NOMBRES = {
    'TERRENO': 'Terrenos',
    'CONSTRUCCION': 'Construcciones y Obras',
    'MAQUINARIA': 'Maquinarias y Equipos',
    'VEHICULO': 'Vehículos',
    'MUEBLE': 'Muebles y Enseres',
    'EQUIPO_COMP': 'Equipos Computacionales',
}

CATEGORIA_CUENTAS = {
    'TERRENO':      ('1.2.01', None),
    'CONSTRUCCION': ('1.2.02', '1.2.07'),
    'MAQUINARIA':   ('1.2.03', '1.2.08'),
    'VEHICULO':     ('1.2.04', '1.2.09'),
    'MUEBLE':       ('1.2.05', '1.2.10'),
    'EQUIPO_COMP':  ('1.2.06', '1.2.11'),
}

VIDA_UTIL_DEFAULT = {
    'TERRENO': 0,
    'CONSTRUCCION': 360,
    'MAQUINARIA': 120,
    'VEHICULO': 72,
    'MUEBLE': 120,
    'EQUIPO_COMP': 60,
}


def _get_cuenta(empresa_id, codigo):
    return Cuenta.query.filter_by(empresa_id=empresa_id, codigo=codigo, activa=True).first()


def _next_numero(empresa_id):
    ultimo = db.session.query(func.max(Asiento.numero)).filter_by(empresa_id=empresa_id).scalar()
    return (ultimo or 0) + 1


def _calcular_cuota(activo, mes_numero):
    """Cuota de depreciación para el mes N (1-based desde fecha_compra)."""
    if mes_numero > activo.vida_util_meses or activo.vida_util_meses == 0:
        return 0.0
    depreciable = activo.valor_compra - activo.valor_residual
    if activo.metodo == 'LINEAL':
        return round(depreciable / activo.vida_util_meses)
    # ACELERADO: tasa = 2/vida_util, aplicada sobre valor neto
    tasa = 2.0 / activo.vida_util_meses
    dep_acum = 0.0
    cuota = 0.0
    for m in range(1, mes_numero + 1):
        valor_neto = depreciable - dep_acum
        if valor_neto <= 0:
            cuota = 0.0
            break
        cuota = round(valor_neto * tasa)
        dep_acum += cuota
    return max(cuota, 0.0)


def _tabla_depreciacion(activo):
    """Generate list of {periodo, mes_num, cuota, dep_acum, valor_neto, registro}."""
    # Single query for all registros of this activo
    registros = {r.periodo: r for r in DepreciacionRegistro.query.filter_by(activo_fijo_id=activo.id).all()}
    rows = []
    dep_acum = 0.0
    for m in range(1, activo.vida_util_meses + 1):
        periodo_date = activo.fecha_compra + relativedelta(months=m - 1)
        periodo_str = periodo_date.strftime('%Y-%m')
        cuota = _calcular_cuota(activo, m)
        dep_acum += cuota
        valor_neto = activo.valor_compra - dep_acum
        rows.append({
            'mes_num': m,
            'periodo': periodo_str,
            'cuota': cuota,
            'dep_acum': dep_acum,
            'valor_neto': max(valor_neto, activo.valor_residual),
            'registro': registros.get(periodo_str),
        })
    return rows


@bp.route('/empresa/<int:eid>/activos')
def lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    activos = (ActivoFijo.query
               .filter_by(empresa_id=eid)
               .order_by(ActivoFijo.fecha_compra.desc())
               .all())
    cuentas_activo = (Cuenta.query
                      .filter(Cuenta.empresa_id == eid, Cuenta.es_titulo == False,
                              Cuenta.activa == True,
                              Cuenta.codigo.like('1.2.%'))
                      .order_by(Cuenta.codigo).all())
    cuentas_gasto_dep = (Cuenta.query
                         .filter(Cuenta.empresa_id == eid, Cuenta.es_titulo == False,
                                 Cuenta.activa == True,
                                 Cuenta.codigo.like('5.2.1%'))
                         .order_by(Cuenta.codigo).all())
    hoy = date.today()
    return render_template('activos/lista.html', empresa=empresa, activos=activos,
                           categorias=CATEGORIA_NOMBRES,
                           cuentas_activo=cuentas_activo,
                           cuentas_gasto_dep=cuentas_gasto_dep,
                           vida_util_default=VIDA_UTIL_DEFAULT,
                           hoy_mes=hoy.strftime('%Y-%m'))


@bp.route('/empresa/<int:eid>/activos/nuevo', methods=['POST'])
def nuevo(eid):
    empresa = Empresa.query.get_or_404(eid)
    nombre = request.form.get('nombre', '').strip()
    categoria = request.form.get('categoria', '')
    metodo = request.form.get('metodo', 'LINEAL')
    descripcion = request.form.get('descripcion', '').strip()
    try:
        valor_compra = float(request.form['valor_compra'])
        valor_residual = float(request.form.get('valor_residual', 0) or 0)
        vida_util_meses = int(request.form['vida_util_meses'])
        fecha_compra = date.fromisoformat(request.form['fecha_compra'])
    except (ValueError, KeyError) as exc:
        flash(f'Datos inválidos: {exc}', 'danger')
        return redirect(url_for('activos.lista', eid=eid))
    if not nombre or not categoria or vida_util_meses < 1 or valor_compra <= 0:
        flash('Faltan datos obligatorios o valores inválidos.', 'danger')
        return redirect(url_for('activos.lista', eid=eid))

    cod_activo, cod_dep = CATEGORIA_CUENTAS.get(categoria, (None, None))
    cuenta_activo = _get_cuenta(eid, cod_activo) if cod_activo else None
    cuenta_dep = _get_cuenta(eid, cod_dep) if cod_dep else None

    af = ActivoFijo(
        empresa_id=eid,
        nombre=nombre,
        descripcion=descripcion,
        categoria=categoria,
        valor_compra=valor_compra,
        valor_residual=valor_residual,
        vida_util_meses=vida_util_meses,
        fecha_compra=fecha_compra,
        metodo=metodo,
        cuenta_activo_id=cuenta_activo.id if cuenta_activo else None,
        cuenta_dep_id=cuenta_dep.id if cuenta_dep else None,
    )
    db.session.add(af)
    db.session.commit()
    flash(f'Activo "{nombre}" registrado.', 'success')
    return redirect(url_for('activos.lista', eid=eid))


@bp.route('/empresa/<int:eid>/activos/<int:aid>')
def detalle(eid, aid):
    empresa = Empresa.query.get_or_404(eid)
    activo = ActivoFijo.query.filter_by(id=aid, empresa_id=eid).first_or_404()
    tabla = _tabla_depreciacion(activo)
    hoy = date.today()
    periodo_actual = hoy.strftime('%Y-%m')

    # Summary card calculations
    total_depreciado = sum(r.monto for r in activo.depreciaciones)
    valor_neto = activo.valor_compra - total_depreciado
    pct_depreciado = (total_depreciado / activo.valor_compra * 100) if activo.valor_compra > 0 else 0
    diff_hoy = relativedelta(hoy, activo.fecha_compra)
    meses_transcurridos = diff_hoy.years * 12 + diff_hoy.months
    meses_restantes = max(0, activo.vida_util_meses - meses_transcurridos)

    return render_template('activos/detalle.html', empresa=empresa, activo=activo,
                           tabla=tabla, categorias=CATEGORIA_NOMBRES,
                           periodo_actual=periodo_actual,
                           total_depreciado=total_depreciado,
                           valor_neto=valor_neto,
                           pct_depreciado=pct_depreciado,
                           meses_restantes=meses_restantes)


@bp.route('/empresa/<int:eid>/activos/<int:aid>/depreciar', methods=['POST'])
def depreciar(eid, aid):
    empresa = Empresa.query.get_or_404(eid)
    activo = ActivoFijo.query.filter_by(id=aid, empresa_id=eid).first_or_404()
    periodo = request.form['periodo']  # YYYY-MM

    existing = DepreciacionRegistro.query.filter_by(
        activo_fijo_id=aid, periodo=periodo
    ).first()
    if existing:
        flash(f'Ya existe un registro de depreciación para {periodo}.', 'warning')
        return redirect(url_for('activos.detalle', eid=eid, aid=aid))

    periodo_date = date.fromisoformat(periodo + '-01')
    diff = relativedelta(periodo_date, activo.fecha_compra)
    mes_num = diff.years * 12 + diff.months + 1  # 1-based

    if mes_num < 1 or mes_num > activo.vida_util_meses:
        flash('El período está fuera de la vida útil del activo.', 'danger')
        return redirect(url_for('activos.detalle', eid=eid, aid=aid))

    cuota = _calcular_cuota(activo, mes_num)
    if cuota <= 0:
        flash('La cuota de depreciación calculada es cero.', 'warning')
        return redirect(url_for('activos.detalle', eid=eid, aid=aid))

    if not activo.cuenta_dep_id:
        flash('El activo no tiene cuenta de depreciación acumulada asignada.', 'danger')
        return redirect(url_for('activos.detalle', eid=eid, aid=aid))

    cuenta_gasto_dep = _get_cuenta(eid, '5.2.14')
    if not cuenta_gasto_dep:
        flash('No se encontró la cuenta 5.2.14 (Depreciación del Ejercicio).', 'danger')
        return redirect(url_for('activos.detalle', eid=eid, aid=aid))

    import calendar
    fecha_asiento = periodo_date.replace(
        day=calendar.monthrange(periodo_date.year, periodo_date.month)[1]
    )

    num = _next_numero(eid)
    asiento = Asiento(
        empresa_id=eid,
        fecha=fecha_asiento,
        numero=num,
        descripcion=f'Depreciación {periodo} – {activo.nombre}',
        origen='MANUAL',
        estado='CONFIRMADO',
    )
    db.session.add(asiento)
    db.session.flush()

    db.session.add(LineaAsiento(
        asiento_id=asiento.id,
        cuenta_id=cuenta_gasto_dep.id,
        debe=cuota,
        haber=0.0,
        descripcion=f'Dep. {activo.nombre}',
        orden=1,
    ))
    db.session.add(LineaAsiento(
        asiento_id=asiento.id,
        cuenta_id=activo.cuenta_dep_id,
        debe=0.0,
        haber=cuota,
        descripcion=f'Dep. Acum. {activo.nombre}',
        orden=2,
    ))

    reg = DepreciacionRegistro(
        activo_fijo_id=aid,
        periodo=periodo,
        monto=cuota,
        asiento_id=asiento.id,
    )
    db.session.add(reg)
    db.session.commit()
    flash(f'Depreciación de {periodo} registrada: ${cuota:,.0f}. Asiento #{num} confirmado.', 'success')
    return redirect(url_for('activos.detalle', eid=eid, aid=aid))


@bp.route('/empresa/<int:eid>/activos/depreciar-todos', methods=['POST'])
def depreciar_todos(eid):
    """Genera asientos de depreciación de todos los activos activos para un período."""
    Empresa.query.get_or_404(eid)
    periodo = request.form.get('periodo', '')
    if not periodo:
        flash('Debe indicar el período.', 'danger')
        return redirect(url_for('activos.lista', eid=eid))

    cuenta_gasto_dep = _get_cuenta(eid, '5.2.14')
    if not cuenta_gasto_dep:
        flash('No se encontró la cuenta 5.2.14 (Depreciación del Ejercicio).', 'danger')
        return redirect(url_for('activos.lista', eid=eid))

    periodo_date = date.fromisoformat(periodo + '-01')
    import calendar
    fecha_asiento = periodo_date.replace(
        day=calendar.monthrange(periodo_date.year, periodo_date.month)[1]
    )

    activos_todos = ActivoFijo.query.filter_by(empresa_id=eid, activo=True).all()
    registrados = 0
    omitidos = []

    for activo in activos_todos:
        existing = DepreciacionRegistro.query.filter_by(
            activo_fijo_id=activo.id, periodo=periodo
        ).first()
        if existing:
            omitidos.append(f'{activo.nombre} (ya registrado)')
            continue

        diff = relativedelta(periodo_date, activo.fecha_compra)
        mes_num = diff.years * 12 + diff.months + 1
        if mes_num < 1 or mes_num > activo.vida_util_meses:
            omitidos.append(f'{activo.nombre} (fuera de vida útil)')
            continue

        cuota = _calcular_cuota(activo, mes_num)
        if cuota <= 0:
            omitidos.append(f'{activo.nombre} (cuota cero)')
            continue

        if not activo.cuenta_dep_id:
            omitidos.append(f'{activo.nombre} (sin cuenta depreciación)')
            continue

        num = _next_numero(eid)
        asiento = Asiento(
            empresa_id=eid,
            fecha=fecha_asiento,
            numero=num,
            descripcion=f'Depreciación {periodo} – {activo.nombre}',
            origen='MANUAL',
            estado='CONFIRMADO',
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            asiento_id=asiento.id, cuenta_id=cuenta_gasto_dep.id,
            debe=cuota, haber=0.0, descripcion=f'Dep. {activo.nombre}', orden=1,
        ))
        db.session.add(LineaAsiento(
            asiento_id=asiento.id, cuenta_id=activo.cuenta_dep_id,
            debe=0.0, haber=cuota, descripcion=f'Dep. Acum. {activo.nombre}', orden=2,
        ))

        db.session.add(DepreciacionRegistro(
            activo_fijo_id=activo.id, periodo=periodo,
            monto=cuota, asiento_id=asiento.id,
        ))
        registrados += 1

    db.session.commit()

    if registrados:
        flash(f'Depreciación {periodo}: {registrados} activo(s) procesado(s).', 'success')
    if omitidos:
        flash(f'Omitidos: {"; ".join(omitidos)}.', 'info')
    if not registrados and not omitidos:
        flash('No hay activos activos para depreciar en este período.', 'warning')

    return redirect(url_for('activos.lista', eid=eid))


@bp.route('/empresa/<int:eid>/activos/depreciar-preview')
def depreciar_preview(eid):
    from flask import jsonify
    Empresa.query.get_or_404(eid)
    periodo = request.args.get('periodo', '')
    if not periodo:
        return jsonify(activos=[], total=0)
    try:
        periodo_date = date.fromisoformat(periodo + '-01')
    except ValueError:
        return jsonify(activos=[], total=0)

    activos_todos = ActivoFijo.query.filter_by(empresa_id=eid, activo=True).all()
    preview = []
    for activo in activos_todos:
        if DepreciacionRegistro.query.filter_by(activo_fijo_id=activo.id, periodo=periodo).first():
            continue
        diff = relativedelta(periodo_date, activo.fecha_compra)
        mes_num = diff.years * 12 + diff.months + 1
        if mes_num < 1 or mes_num > activo.vida_util_meses:
            continue
        if not activo.cuenta_dep_id:
            continue
        cuota = _calcular_cuota(activo, mes_num)
        if cuota > 0:
            preview.append({'nombre': activo.nombre, 'monto': round(cuota)})

    return jsonify(activos=preview, total=sum(a['monto'] for a in preview))


@bp.route('/empresa/<int:eid>/activos/<int:aid>/editar', methods=['POST'])
def editar(eid, aid):
    activo = ActivoFijo.query.filter_by(id=aid, empresa_id=eid).first_or_404()
    activo.nombre = request.form.get('nombre', activo.nombre).strip()
    activo.descripcion = request.form.get('descripcion', '').strip() or None
    try:
        activo.valor_compra = float(request.form['valor_compra'])
        activo.valor_residual = float(request.form.get('valor_residual', 0) or 0)
        activo.vida_util_meses = int(request.form['vida_util_meses'])
    except (ValueError, KeyError):
        flash('Datos inválidos.', 'danger')
        return redirect(url_for('activos.lista', eid=eid))
    db.session.commit()
    flash(f'Activo "{activo.nombre}" actualizado.', 'success')
    return redirect(url_for('activos.detalle', eid=eid, aid=aid))


@bp.route('/empresa/<int:eid>/activos/<int:aid>/desactivar', methods=['POST'])
def desactivar(eid, aid):
    activo = ActivoFijo.query.filter_by(id=aid, empresa_id=eid).first_or_404()
    activo.activo = False
    db.session.commit()
    flash(f'Activo "{activo.nombre}" desactivado.', 'info')
    return redirect(url_for('activos.lista', eid=eid))
