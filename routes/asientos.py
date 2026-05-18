import json
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app
from datetime import date
from models import db, Empresa, Asiento, LineaAsiento, Cuenta, DocumentoSII, MovimientoBanco, Conciliacion, CuotaPrestamo
from engine.asientos import confirmar_asiento, anular_asiento
from engine.auditoria import registrar_auditoria

bp = Blueprint('asientos', __name__)


def _cuentas_json(cuentas):
    return json.dumps([{'id': c.id, 'codigo': c.codigo, 'nombre': c.nombre} for c in cuentas])


def _guardar_lineas(asiento_id, form):
    LineaAsiento.query.filter_by(asiento_id=asiento_id).delete()
    cuenta_ids = form.getlist('cuenta_id[]')
    debes      = form.getlist('debe[]')
    haberes    = form.getlist('haber[]')
    descs      = form.getlist('linea_desc[]')
    for orden, (cid, d, h, ld) in enumerate(zip(cuenta_ids, debes, haberes, descs)):
        if not cid:
            continue
        db.session.add(LineaAsiento(
            asiento_id=asiento_id,
            cuenta_id=int(cid),
            debe=float(d or 0),
            haber=float(h or 0),
            descripcion=ld.strip(),
            orden=orden,
        ))


ORIGENES_CONC    = {'LIBRO_COMPRAS', 'LIBRO_VENTAS', 'HONORARIOS', 'BANCO'}
ORIGENES_SII     = {'LIBRO_COMPRAS', 'LIBRO_VENTAS', 'HONORARIOS'}  # siempre requieren conciliación
TIPOS_CONC_LABEL = {
    'SII':    'Conciliado SII',
    'MANUAL': 'Manual',
    # legacy
    'SUELDO': 'Manual', 'RETIRO':   'Manual',
    'IMPUESTO':'Manual', 'F29':     'Manual',
    'BANCO':  'Manual', 'PRESTAMO': 'Manual',
    'INTERNO':'Manual', 'OTRO':     'Manual',
}


@bp.route('/empresa/<int:eid>/asientos')
def lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    page        = request.args.get('page', 1, type=int)
    origen      = request.args.get('origen', '')
    estado      = request.args.get('estado', '')
    descripcion = request.args.get('descripcion', '').strip()
    desde_str   = request.args.get('desde', '')
    hasta_str   = request.args.get('hasta', '')
    cuenta_id   = request.args.get('cuenta_id', type=int)

    q = Asiento.query.filter_by(empresa_id=eid)
    if origen:
        q = q.filter_by(origen=origen)
    if estado:
        q = q.filter_by(estado=estado)
    if descripcion:
        q = q.filter(Asiento.descripcion.ilike(f'%{descripcion}%'))
    if desde_str:
        try:
            q = q.filter(Asiento.fecha >= date.fromisoformat(desde_str))
        except ValueError:
            pass
    if hasta_str:
        try:
            q = q.filter(Asiento.fecha <= date.fromisoformat(hasta_str))
        except ValueError:
            pass
    if cuenta_id:
        sub = db.session.query(LineaAsiento.asiento_id).filter_by(cuenta_id=cuenta_id).subquery()
        q = q.filter(Asiento.id.in_(sub))

    asientos = q.order_by(Asiento.fecha.desc(), Asiento.numero.desc()).paginate(page=page, per_page=50)

    from collections import defaultdict
    ids = [a.id for a in asientos.items]

    lineas_raw = (LineaAsiento.query
                  .filter(LineaAsiento.asiento_id.in_(ids))
                  .order_by(LineaAsiento.asiento_id, LineaAsiento.orden)
                  .all()) if ids else []
    lineas_x_asiento = defaultdict(list)
    for l in lineas_raw:
        lineas_x_asiento[l.asiento_id].append(l)

    # Conciliación: asiento_id -> Conciliacion object
    conc_x_asiento = {}
    if ids:
        for d in DocumentoSII.query.filter(DocumentoSII.asiento_id.in_(ids),
                                           DocumentoSII.conciliacion_id != None).all():
            conc_x_asiento[d.asiento_id] = d.conciliacion_id
        for m in MovimientoBanco.query.filter(MovimientoBanco.asiento_id.in_(ids),
                                              MovimientoBanco.conciliacion_id != None).all():
            conc_x_asiento[m.asiento_id] = m.conciliacion_id
    # Fetch Conciliacion objects for tipo
    conc_objs = {}
    if conc_x_asiento:
        for c in Conciliacion.query.filter(Conciliacion.id.in_(set(conc_x_asiento.values()))).all():
            conc_objs[c.id] = c

    cuentas_filtro = (Cuenta.query.filter_by(empresa_id=eid, es_titulo=False, activa=True)
                      .order_by(Cuenta.codigo).all())
    return render_template('asientos/lista.html', empresa=empresa, asientos=asientos,
                           origen=origen, estado=estado,
                           descripcion=descripcion, desde_str=desde_str, hasta_str=hasta_str,
                           cuenta_id=cuenta_id, cuentas_filtro=cuentas_filtro,
                           lineas_x_asiento=lineas_x_asiento,
                           conc_x_asiento=conc_x_asiento,
                           conc_objs=conc_objs,
                           origenes_conc=ORIGENES_CONC,
                           origenes_sii=ORIGENES_SII,
                           tipos_conc_label=TIPOS_CONC_LABEL)


@bp.route('/empresa/<int:eid>/asientos/confirmar-lote', methods=['POST'])
def confirmar_lote(eid):
    ids = request.form.getlist('ids', type=int)
    ok, errores = 0, []
    for aid in ids:
        asiento = Asiento.query.get(aid)
        if not asiento or asiento.empresa_id != eid:
            continue
        try:
            confirmar_asiento(asiento)
            ok += 1
        except ValueError as e:
            errores.append(f"N°{asiento.numero}: {e}")
    if ok:
        db.session.commit()
        flash(f'{ok} asiento(s) confirmado(s)', 'success')
    for e in errores:
        flash(e, 'warning')
    return redirect(url_for('asientos.lista', eid=eid,
                            origen=request.form.get('origen', ''),
                            estado=request.form.get('estado', ''),
                            page=request.form.get('page', 1)))


@bp.route('/empresa/<int:eid>/asientos/nuevo', methods=['GET', 'POST'])
def nuevo(eid):
    empresa = Empresa.query.get_or_404(eid)
    cuentas = Cuenta.query.filter_by(empresa_id=eid, es_titulo=False, activa=True).order_by(Cuenta.codigo).all()

    if request.method == 'POST':
        fecha_str   = request.form['fecha']
        descripcion = request.form['descripcion'].strip()
        try:
            fecha = date.fromisoformat(fecha_str)
        except ValueError:
            flash('Fecha inválida', 'danger')
            return render_template('asientos/form.html', empresa=empresa, cuentas=cuentas,
                                   cuentas_json=_cuentas_json(cuentas), asiento=None, lineas_json='[]')

        ultimo = Asiento.query.filter_by(empresa_id=eid).order_by(Asiento.numero.desc()).first()
        numero = (ultimo.numero or 0) + 1 if ultimo else 1

        accion = request.form.get('accion', 'confirmar')
        from storage import save_attachment
        respaldo_file = request.files.get('respaldo_file')
        if respaldo_file and respaldo_file.filename:
            try:
                respaldo_url = save_attachment(respaldo_file, respaldo_file.filename, current_app.config['UPLOAD_FOLDER'])
            except ValueError as e:
                flash(str(e), 'warning')
                respaldo_url = None
        else:
            respaldo_url = request.form.get('respaldo_url', '').strip() or None
        asiento = Asiento(empresa_id=eid, fecha=fecha, numero=numero,
                          descripcion=descripcion, respaldo_url=respaldo_url,
                          origen='MANUAL', estado='BORRADOR')
        db.session.add(asiento)
        db.session.flush()
        _guardar_lineas(asiento.id, request.form)
        if accion == 'borrador':
            registrar_auditoria(asiento, 'CREAR', f'Asiento N°{numero} guardado como borrador')
            db.session.commit()
            flash(f'Asiento N°{numero} guardado como borrador', 'info')
        elif asiento.cuadrado:
            asiento.estado = 'CONFIRMADO'
            registrar_auditoria(asiento, 'CREAR', f'Asiento N°{numero} creado y confirmado')
            db.session.commit()
            flash(f'Asiento N°{numero} creado y confirmado', 'success')
        else:
            registrar_auditoria(asiento, 'CREAR', f'Asiento N°{numero} guardado en borrador (no cuadra)')
            db.session.commit()
            flash(f'Asiento N°{numero} guardado en borrador — no cuadra (Debe {asiento.total_debe:,.0f} ≠ Haber {asiento.total_haber:,.0f})', 'warning')
        return redirect(url_for('asientos.detalle', eid=eid, aid=asiento.id))

    ultimo_asiento = Asiento.query.filter_by(empresa_id=eid, estado='CONFIRMADO').order_by(Asiento.fecha.desc()).first()
    fecha_default = ultimo_asiento.fecha.isoformat() if ultimo_asiento else date.today().isoformat()
    return render_template('asientos/form.html', empresa=empresa, cuentas=cuentas,
                           cuentas_json=_cuentas_json(cuentas), asiento=None, lineas_json='[]',
                           fecha_default=fecha_default)


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/editar', methods=['GET', 'POST'])
def editar(eid, aid):
    empresa = Empresa.query.get_or_404(eid)
    asiento = Asiento.query.get_or_404(aid)
    cuentas = Cuenta.query.filter_by(empresa_id=eid, es_titulo=False, activa=True).order_by(Cuenta.codigo).all()

    if asiento.estado == 'ANULADO':
        flash('No se puede editar un asiento anulado.', 'danger')
        return redirect(url_for('asientos.detalle', eid=eid, aid=aid))

    if request.method == 'POST':
        try:
            asiento.fecha       = date.fromisoformat(request.form['fecha'])
        except ValueError:
            flash('Fecha inválida', 'danger')
            return redirect(url_for('asientos.editar', eid=eid, aid=aid))
        accion = request.form.get('accion', 'confirmar')
        asiento.descripcion = request.form['descripcion'].strip()
        from storage import save_attachment
        respaldo_file = request.files.get('respaldo_file')
        if respaldo_file and respaldo_file.filename:
            try:
                asiento.respaldo_url = save_attachment(respaldo_file, respaldo_file.filename, current_app.config['UPLOAD_FOLDER'])
            except ValueError as e:
                flash(str(e), 'warning')
        elif request.form.get('respaldo_url', '').strip():
            asiento.respaldo_url = request.form.get('respaldo_url').strip()
        # else: keep existing respaldo_url unchanged
        asiento.estado = 'BORRADOR'
        _guardar_lineas(asiento.id, request.form)
        if accion == 'borrador':
            registrar_auditoria(asiento, 'EDITAR', f'Asiento N°{asiento.numero} editado y guardado como borrador')
            db.session.commit()
            flash(f'Asiento N°{asiento.numero} guardado como borrador', 'info')
        elif asiento.cuadrado:
            asiento.estado = 'CONFIRMADO'
            registrar_auditoria(asiento, 'EDITAR', f'Asiento N°{asiento.numero} editado y confirmado')
            db.session.commit()
            flash(f'Asiento N°{asiento.numero} actualizado y confirmado', 'success')
        else:
            registrar_auditoria(asiento, 'EDITAR', f'Asiento N°{asiento.numero} editado (no cuadra)')
            db.session.commit()
            flash(f'Asiento N°{asiento.numero} guardado en borrador — no cuadra (Debe {asiento.total_debe:,.0f} ≠ Haber {asiento.total_haber:,.0f})', 'warning')
        return redirect(url_for('asientos.detalle', eid=eid, aid=aid))

    lineas_json = json.dumps([{
        'cuenta_id': l.cuenta_id,
        'debe':      l.debe,
        'haber':     l.haber,
        'descripcion': l.descripcion or '',
    } for l in asiento.lineas])

    return render_template('asientos/form.html', empresa=empresa, cuentas=cuentas,
                           cuentas_json=_cuentas_json(cuentas),
                           asiento=asiento, lineas_json=lineas_json)


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/eliminar', methods=['POST'])
def eliminar(eid, aid):
    from models import AsientoAudit, VacacionEmpleado, DepreciacionRegistro, Conciliacion
    asiento = Asiento.query.get_or_404(aid)
    if asiento.estado == 'CONFIRMADO':
        flash('No se puede eliminar un asiento confirmado. Primero anúlalo.', 'danger')
        return redirect(url_for('asientos.detalle', eid=eid, aid=aid))

    # Recolectar conciliaciones asociadas antes de desligar
    conc_ids = set()
    for m in MovimientoBanco.query.filter_by(asiento_id=aid).all():
        if m.conciliacion_id:
            conc_ids.add(m.conciliacion_id)
        m.procesado = False
        m.asiento_id = None
        m.conciliacion_id = None
    for d in DocumentoSII.query.filter_by(asiento_id=aid).all():
        if d.conciliacion_id:
            conc_ids.add(d.conciliacion_id)
        d.procesado = False
        d.asiento_id = None
        d.conciliacion_id = None

    for cuota in CuotaPrestamo.query.filter_by(asiento_id=aid).all():
        if cuota.movimiento_banco_id:
            mov = MovimientoBanco.query.get(cuota.movimiento_banco_id)
            if mov:
                mov.procesado = False
                mov.asiento_id = None
                mov.conciliacion_id = None
        cuota.asiento_id = None
        cuota.movimiento_banco_id = None
        cuota.pagada = False
        cuota.fecha_pago = None
        cuota.uf_valor_pago = None
        cuota.cuota_total_pesos = None

    VacacionEmpleado.query.filter_by(asiento_id=aid).update({'asiento_id': None})
    DepreciacionRegistro.query.filter_by(asiento_id=aid).update({'asiento_id': None})

    # Borrar registros con FK NOT NULL
    AsientoAudit.query.filter_by(asiento_id=aid).delete()
    LineaAsiento.query.filter_by(asiento_id=aid).delete()
    db.session.delete(asiento)

    # Eliminar conciliaciones que quedaron sin movimientos ni documentos
    for cid in conc_ids:
        tiene_movs = MovimientoBanco.query.filter_by(conciliacion_id=cid).first()
        tiene_docs = DocumentoSII.query.filter_by(conciliacion_id=cid).first()
        if not tiene_movs and not tiene_docs:
            conc = Conciliacion.query.get(cid)
            if conc:
                db.session.delete(conc)

    db.session.commit()
    flash('Borrador eliminado', 'success')
    return redirect(url_for('asientos.lista', eid=eid))


@bp.route('/empresa/<int:eid>/asientos/<int:aid>')
def detalle(eid, aid):
    empresa = Empresa.query.get_or_404(eid)
    asiento = Asiento.query.get_or_404(aid)
    prev_a = (Asiento.query.filter_by(empresa_id=eid)
              .filter(Asiento.numero < asiento.numero)
              .order_by(Asiento.numero.desc()).first())
    next_a = (Asiento.query.filter_by(empresa_id=eid)
              .filter(Asiento.numero > asiento.numero)
              .order_by(Asiento.numero.asc()).first())

    # Conciliación
    conc = None
    if asiento.origen in ORIGENES_CONC:
        doc = DocumentoSII.query.filter_by(asiento_id=aid).first()
        if doc and doc.conciliacion_id:
            conc = Conciliacion.query.get(doc.conciliacion_id)
        if not conc:
            mov = MovimientoBanco.query.filter_by(asiento_id=aid).first()
            if mov and mov.conciliacion_id:
                conc = Conciliacion.query.get(mov.conciliacion_id)

    conc_mes = asiento.fecha.strftime('%Y-%m') if asiento.fecha else None

    audits = asiento.audits.order_by(None).order_by(db.text('creado_en ASC')).all()
    return render_template('asientos/detalle.html', empresa=empresa, asiento=asiento,
                           prev_id=prev_a.id if prev_a else None,
                           next_id=next_a.id if next_a else None,
                           conc=conc, conc_mes=conc_mes,
                           audits=audits,
                           origenes_conc=ORIGENES_CONC,
                           origenes_sii=ORIGENES_SII,
                           tipos_conc_label=TIPOS_CONC_LABEL)


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/confirmar', methods=['POST'])
def confirmar(eid, aid):
    asiento = Asiento.query.get_or_404(aid)
    try:
        confirmar_asiento(asiento)
        registrar_auditoria(asiento, 'CONFIRMAR')
        db.session.commit()
        flash(f'Asiento N°{asiento.numero} confirmado', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('asientos.detalle', eid=eid, aid=aid))


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/anular', methods=['POST'])
def anular(eid, aid):
    asiento = Asiento.query.get_or_404(aid)
    anular_asiento(asiento)
    registrar_auditoria(asiento, 'ANULAR')
    db.session.commit()
    flash(f'Asiento N°{asiento.numero} anulado', 'warning')
    return redirect(url_for('asientos.detalle', eid=eid, aid=aid))


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/recuperar', methods=['POST'])
def recuperar(eid, aid):
    asiento = Asiento.query.get_or_404(aid)
    if asiento.estado != 'ANULADO':
        flash('Solo se pueden recuperar asientos anulados.', 'warning')
        return redirect(url_for('asientos.detalle', eid=eid, aid=aid))
    asiento.estado = 'BORRADOR'
    db.session.commit()
    flash(f'Asiento N°{asiento.numero} recuperado como borrador', 'success')
    return redirect(url_for('asientos.detalle', eid=eid, aid=aid))


@bp.route('/empresa/<int:eid>/api/cuentas')
def api_cuentas(eid):
    """JSON: lista de cuentas con saldo actual para Tom Select."""
    Empresa.query.get_or_404(eid)
    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, es_titulo=False, activa=True)
               .order_by(Cuenta.codigo).all())

    # Saldo por cuenta (suma lineas confirmadas)
    from sqlalchemy import func
    saldos = dict(
        db.session.query(LineaAsiento.cuenta_id,
                         func.sum(LineaAsiento.debe) - func.sum(LineaAsiento.haber))
        .join(Asiento, Asiento.id == LineaAsiento.asiento_id)
        .filter(Asiento.empresa_id == eid, Asiento.estado == 'CONFIRMADO')
        .group_by(LineaAsiento.cuenta_id).all()
    )

    result = []
    for c in cuentas:
        saldo_raw = saldos.get(c.id, 0) or 0
        if c.naturaleza == 'ACREEDORA':
            saldo_raw = -saldo_raw
        result.append({
            'id': c.id,
            'codigo': c.codigo,
            'nombre': c.nombre,
            'tipo': c.tipo or '',
            'saldo': round(saldo_raw),
            'label': f'{c.codigo} — {c.nombre}',
        })
    return jsonify(result)
