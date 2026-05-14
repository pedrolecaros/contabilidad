import json
from flask import Blueprint, render_template, redirect, url_for, request, flash
from datetime import date
from models import db, Empresa, Asiento, LineaAsiento, Cuenta, DocumentoSII, MovimientoBanco, Conciliacion
from engine.asientos import confirmar_asiento, anular_asiento

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
    'SII':      'Conciliado',
    'SUELDO':   'Sueldo',
    'RETIRO':   'Retiro socio',
    'IMPUESTO': 'Impuesto',
    'BANCO':    'Gasto bancario',
    'PRESTAMO': 'Préstamo',
    'INTERNO':  'Transf. interna',
    'OTRO':     'Otro',
}


@bp.route('/empresa/<int:eid>/asientos')
def lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    page   = request.args.get('page', 1, type=int)
    origen = request.args.get('origen', '')
    estado = request.args.get('estado', '')

    q = Asiento.query.filter_by(empresa_id=eid)
    if origen:
        q = q.filter_by(origen=origen)
    if estado:
        q = q.filter_by(estado=estado)

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

    return render_template('asientos/lista.html', empresa=empresa, asientos=asientos,
                           origen=origen, estado=estado,
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

        asiento = Asiento(empresa_id=eid, fecha=fecha, numero=numero,
                          descripcion=descripcion, origen='MANUAL', estado='BORRADOR')
        db.session.add(asiento)
        db.session.flush()
        _guardar_lineas(asiento.id, request.form)
        if asiento.cuadrado:
            asiento.estado = 'CONFIRMADO'
            db.session.commit()
            flash(f'Asiento N°{numero} creado y confirmado', 'success')
        else:
            db.session.commit()
            flash(f'Asiento N°{numero} guardado en borrador — no cuadra (Debe {asiento.total_debe:,.0f} ≠ Haber {asiento.total_haber:,.0f}).', 'warning')
        return redirect(url_for('asientos.detalle', eid=eid, aid=asiento.id))

    return render_template('asientos/form.html', empresa=empresa, cuentas=cuentas,
                           cuentas_json=_cuentas_json(cuentas), asiento=None, lineas_json='[]')


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
        asiento.descripcion = request.form['descripcion'].strip()
        asiento.estado = 'BORRADOR'
        _guardar_lineas(asiento.id, request.form)
        # Confirmar automáticamente si cuadra
        if asiento.cuadrado:
            asiento.estado = 'CONFIRMADO'
            db.session.commit()
            flash(f'Asiento N°{asiento.numero} actualizado y confirmado.', 'success')
        else:
            db.session.commit()
            flash(f'Asiento N°{asiento.numero} guardado en borrador — no cuadra (Debe {asiento.total_debe:,.0f} ≠ Haber {asiento.total_haber:,.0f}).', 'warning')
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
    asiento = Asiento.query.get_or_404(aid)
    if asiento.estado == 'CONFIRMADO':
        flash('No se puede eliminar un asiento confirmado. Primero anúlalo.', 'danger')
        return redirect(url_for('asientos.detalle', eid=eid, aid=aid))
    # Desligar documentos asociados
    DocumentoSII.query.filter_by(asiento_id=aid).update({'procesado': False, 'asiento_id': None})
    MovimientoBanco.query.filter_by(asiento_id=aid).update({'procesado': False, 'asiento_id': None})
    db.session.delete(asiento)
    db.session.commit()
    flash('Asiento eliminado', 'success')
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

    return render_template('asientos/detalle.html', empresa=empresa, asiento=asiento,
                           prev_id=prev_a.id if prev_a else None,
                           next_id=next_a.id if next_a else None,
                           conc=conc, conc_mes=conc_mes,
                           origenes_conc=ORIGENES_CONC,
                           origenes_sii=ORIGENES_SII,
                           tipos_conc_label=TIPOS_CONC_LABEL)


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/confirmar', methods=['POST'])
def confirmar(eid, aid):
    asiento = Asiento.query.get_or_404(aid)
    try:
        confirmar_asiento(asiento)
        db.session.commit()
        flash(f'Asiento N°{asiento.numero} confirmado', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('asientos.detalle', eid=eid, aid=aid))


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/anular', methods=['POST'])
def anular(eid, aid):
    asiento = Asiento.query.get_or_404(aid)
    anular_asiento(asiento)
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
