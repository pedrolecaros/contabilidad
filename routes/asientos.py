import json
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app
from datetime import date
from models import db, Empresa, Asiento, LineaAsiento, Cuenta, DocumentoSII, MovimientoBanco, Conciliacion, Contraparte
from engine.asientos import confirmar_asiento, anular_asiento
from engine.auditoria import registrar_auditoria

bp = Blueprint('asientos', __name__)


def _cuentas_json(cuentas):
    return json.dumps([{'id': c.id, 'codigo': c.codigo, 'nombre': c.nombre} for c in cuentas])


def _contrapartes_json(eid):
    """Todas las contrapartes activas, marcando cuáles ya tienen actividad
    en la empresa (campo `en_empresa`). El frontend las agrupa visualmente."""
    ids_lineas = {r[0] for r in db.session.query(LineaAsiento.contraparte_id)
                  .join(Asiento)
                  .filter(Asiento.empresa_id == eid,
                          LineaAsiento.contraparte_id != None).distinct().all()}
    ruts_docs = {r[0] for r in db.session.query(DocumentoSII.rut_contraparte)
                 .filter(DocumentoSII.empresa_id == eid,
                         DocumentoSII.rut_contraparte != None).distinct().all()}
    ids_docs = ({r[0] for r in db.session.query(Contraparte.id)
                 .filter(Contraparte.rut.in_(ruts_docs)).all()}
                if ruts_docs else set())
    en_empresa_ids = ids_lineas | ids_docs

    cp = (Contraparte.query
          .filter(Contraparte.activo == True)
          .order_by(Contraparte.razon_social).all())
    return json.dumps([
        {'id': c.id, 'nombre': c.razon_social, 'rut': c.rut,
         'en_empresa': c.id in en_empresa_ids}
        for c in cp
    ])


def _prestamo_dict(p):
    return {
        'id': p.id,
        'nombre': p.nombre,
        'tipo': p.tipo,
        'moneda': p.moneda,
        'acreedor_deudor': p.acreedor_deudor or '',
        'saldo_actual': p.saldo_actual(),
    }


from engine.plan_cuentas_default import PRESTAMOS_PAGAR_CODIGOS, PRESTAMOS_COBRAR_CODIGOS
_PRESTAMO_CODIGOS = (
    {c: 'PAGAR' for c in PRESTAMOS_PAGAR_CODIGOS}
    | {c: 'COBRAR' for c in PRESTAMOS_COBRAR_CODIGOS}
)


def _save_prestamo_link(asiento, prestamo_id_str):
    """Link asiento to a prestamo (without marking any cuota as paid)."""
    from models import Prestamo as _Prestamo
    if prestamo_id_str:
        try:
            pid = int(prestamo_id_str)
            p = _Prestamo.query.get(pid)
            if p and p.empresa_id == asiento.empresa_id:
                asiento.prestamo_id = pid
                return
        except (ValueError, AttributeError):
            pass
    asiento.prestamo_id = None


def _parse_monto(v):
    """Parse Cleave.js formatted number: '1.000.000' or '1.000,50' → float."""
    s = (v or '0').replace('.', '').replace(',', '.')
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _guardar_lineas(asiento_id, form):
    LineaAsiento.query.filter_by(asiento_id=asiento_id).delete()
    cuenta_ids      = form.getlist('cuenta_id[]')
    debes           = form.getlist('debe[]')
    haberes         = form.getlist('haber[]')
    descs           = form.getlist('linea_desc[]')
    contraparte_ids = form.getlist('contraparte_id[]')
    for orden, (cid, d, h, ld) in enumerate(zip(cuenta_ids, debes, haberes, descs)):
        if not cid:
            continue
        cpid_raw = contraparte_ids[orden] if orden < len(contraparte_ids) else ''
        db.session.add(LineaAsiento(
            asiento_id=asiento_id,
            cuenta_id=int(cid),
            contraparte_id=int(cpid_raw) if cpid_raw else None,
            debe=_parse_monto(d),
            haber=_parse_monto(h),
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

ORIGEN_LABEL = {
    'MANUAL':        'Manual',
    'APERTURA':      'Apertura',
    'BANCO':         'Banco',
    'LIBRO_COMPRAS': 'Compras',
    'LIBRO_VENTAS':  'Ventas',
    'HONORARIOS':    'Honorarios',
    'PRESTAMO':      'Préstamo',
    'REMUNERACION':  'Remuneración',
    'DEPRECIACION':  'Depreciación',
    'VACACIONES':    'Vacaciones',
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
    mes_str     = request.args.get('mes', '').strip()  # 'YYYY-MM'
    cuenta_id   = request.args.get('cuenta_id', type=int)

    # Mes vigente: si se pasa `mes=YYYY-MM`, sobreescribe desde/hasta al primer/último día.
    if mes_str and len(mes_str) == 7:
        try:
            anio, mes = int(mes_str[:4]), int(mes_str[5:7])
            primer = date(anio, mes, 1)
            import calendar as _cal
            ultimo = date(anio, mes, _cal.monthrange(anio, mes)[1])
            desde_str = primer.isoformat()
            hasta_str = ultimo.isoformat()
        except ValueError:
            pass

    q = Asiento.query.filter_by(empresa_id=eid)
    if origen:
        q = q.filter_by(origen=origen)
    if estado and estado != 'TODOS':
        q = q.filter_by(estado=estado)
    elif estado != 'TODOS':
        # Default: hide anulados unless user explicitly chose TODOS
        q = q.filter(Asiento.estado != 'ANULADO')
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
                           mes_str=mes_str,
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
        _save_prestamo_link(asiento, request.form.get('prestamo_vinculado', ''))
        if asiento.prestamo_id:
            asiento.prestamo_sentido = request.form.get('prestamo_sentido', '-')
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
    from models import Prestamo as _Prestamo
    prestamos_eid = _Prestamo.query.filter_by(empresa_id=eid, activo=True).order_by(_Prestamo.nombre).all()
    prestamos_json = json.dumps([_prestamo_dict(p) for p in prestamos_eid])
    return render_template('asientos/form.html', empresa=empresa, cuentas=cuentas,
                           cuentas_json=_cuentas_json(cuentas), asiento=None, lineas_json='[]',
                           fecha_default=fecha_default, prestamos_json=prestamos_json,
                           contrapartes_json=_contrapartes_json(eid))


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
        _save_prestamo_link(asiento, request.form.get('prestamo_vinculado', ''))
        if asiento.prestamo_id:
            asiento.prestamo_sentido = request.form.get('prestamo_sentido', '-')
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

        back = request.form.get('next_url', '').strip()
        if back:
            return redirect(url_for('asientos.detalle', eid=eid, aid=aid, back=back))
        return redirect(url_for('asientos.detalle', eid=eid, aid=aid))

    lineas_json = json.dumps([{
        'cuenta_id':     l.cuenta_id,
        'debe':          l.debe,
        'haber':         l.haber,
        'descripcion':   l.descripcion or '',
        'contraparte_id': l.contraparte_id or '',
    } for l in asiento.lineas])

    # Load prestamos for the selector
    from models import Prestamo as _Prestamo
    prestamos_eid = _Prestamo.query.filter_by(empresa_id=eid, activo=True).order_by(_Prestamo.nombre).all()
    prestamos_json = json.dumps([_prestamo_dict(p) for p in prestamos_eid])

    back_url = (request.args.get('next', '').strip()
                or request.referrer
                or url_for('asientos.lista', eid=eid))
    return render_template('asientos/form.html', empresa=empresa, cuentas=cuentas,
                           cuentas_json=_cuentas_json(cuentas),
                           asiento=asiento, lineas_json=lineas_json,
                           prestamos_json=prestamos_json,
                           contrapartes_json=_contrapartes_json(eid),
                           back_url=back_url)


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/eliminar', methods=['POST'])
def eliminar(eid, aid):
    from models import AsientoAudit, Conciliacion
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

    # Borrar registros con FK NOT NULL
    from routes.papelera import enviar_papelera, _ser_asiento
    enviar_papelera(
        'ASIENTO', asiento.id, asiento.empresa_id,
        f'Asiento #{asiento.numero} – {asiento.descripcion or ""}',
        _ser_asiento(asiento)
    )
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
    back_url = request.args.get('back', '').strip()
    return render_template('asientos/detalle.html', empresa=empresa, asiento=asiento,
                           prev_id=prev_a.id if prev_a else None,
                           next_id=next_a.id if next_a else None,
                           conc=conc, conc_mes=conc_mes,
                           audits=audits,
                           back_url=back_url,
                           origenes_conc=ORIGENES_CONC,
                           origenes_sii=ORIGENES_SII,
                           tipos_conc_label=TIPOS_CONC_LABEL)


@bp.route('/empresa/<int:eid>/asientos/apertura', methods=['GET', 'POST'])
def apertura(eid):
    empresa = Empresa.query.get_or_404(eid)
    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, es_titulo=False, activa=True)
               .order_by(Cuenta.codigo)
               .all())

    ORDEN_TIPO = {'ACTIVO': 0, 'PASIVO': 1, 'PATRIMONIO': 2, 'INGRESO': 3, 'GASTO': 4}
    cuentas_sorted = sorted(cuentas, key=lambda c: (ORDEN_TIPO.get(c.tipo, 9), c.codigo))

    if request.method == 'POST':
        fecha_str  = request.form.get('fecha', '').strip()
        descripcion = request.form.get('descripcion', 'Asiento de apertura').strip()
        accion     = request.form.get('accion', 'confirmar')

        try:
            fecha = date.fromisoformat(fecha_str)
        except ValueError:
            flash('Fecha inválida', 'danger')
            return redirect(url_for('asientos.apertura', eid=eid))

        lineas = []
        for c in cuentas:
            debe_str  = request.form.get(f'debe_{c.id}',  '').strip().replace('.', '').replace(',', '.')
            haber_str = request.form.get(f'haber_{c.id}', '').strip().replace('.', '').replace(',', '.')
            desc      = request.form.get(f'desc_{c.id}',  '').strip()
            debe  = float(debe_str)  if debe_str  else 0.0
            haber = float(haber_str) if haber_str else 0.0
            if debe > 0 or haber > 0:
                lineas.append({'cuenta_id': c.id, 'debe': debe, 'haber': haber, 'descripcion': desc})

        if not lineas:
            flash('Ingresa al menos un saldo para crear el asiento.', 'warning')
            return redirect(url_for('asientos.apertura', eid=eid))

        total_debe  = sum(l['debe']  for l in lineas)
        total_haber = sum(l['haber'] for l in lineas)

        if accion == 'confirmar' and abs(total_debe - total_haber) > 0.5:
            flash(f'El asiento no cuadra — Debe {total_debe:,.0f} ≠ Haber {total_haber:,.0f}. '
                  f'Diferencia: {abs(total_debe - total_haber):,.0f}. Guardado como borrador.', 'warning')
            accion = 'borrador'

        numero = (db.session.query(db.func.max(Asiento.numero))
                  .filter_by(empresa_id=eid).scalar() or 0) + 1

        asiento = Asiento(
            empresa_id  = eid,
            numero      = numero,
            fecha       = fecha,
            descripcion = descripcion,
            origen      = 'APERTURA',
            estado      = 'CONFIRMADO' if accion == 'confirmar' else 'BORRADOR',
        )
        db.session.add(asiento)
        db.session.flush()

        for l in lineas:
            db.session.add(LineaAsiento(
                asiento_id  = asiento.id,
                cuenta_id   = l['cuenta_id'],
                debe        = l['debe'],
                haber       = l['haber'],
                descripcion = '',
            ))

        from engine.auditoria import registrar_auditoria
        registrar_auditoria(asiento, 'CREAR', f'Asiento de apertura N°{numero}')
        db.session.commit()
        flash(f'Asiento de apertura N°{numero} creado.', 'success')
        return redirect(url_for('asientos.detalle', eid=eid, aid=asiento.id))

    # GET — check if one already exists
    existente = (Asiento.query
                 .filter_by(empresa_id=eid, origen='APERTURA')
                 .order_by(Asiento.fecha.desc())
                 .first())

    return render_template('asientos/apertura.html',
                           empresa=empresa,
                           cuentas=cuentas_sorted,
                           existente=existente)


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/confirmar', methods=['POST'])
def confirmar(eid, aid):
    asiento = Asiento.query.get_or_404(aid)
    try:
        confirmar_asiento(asiento)
        registrar_auditoria(asiento, 'CONFIRMAR')
        from services.historial import log_asiento
        log_asiento('CONFIRMAR', asiento, revertible=True)
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
    from services.historial import log_asiento
    log_asiento('ANULAR', asiento, revertible=True)
    db.session.commit()
    flash(f'Asiento N°{asiento.numero} anulado', 'warning')
    # Volver a la lista preservando filtros (next_url viene del form)
    back = request.form.get('next_url', '').strip()
    if back and back.startswith('/'):
        return redirect(back)
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
    back = request.form.get('next_url', '').strip()
    if back and back.startswith('/'):
        return redirect(back)
    return redirect(url_for('asientos.detalle', eid=eid, aid=aid))


@bp.route('/empresa/<int:eid>/asientos/<int:aid>/respaldo', methods=['POST'])
def subir_respaldo(eid, aid):
    asiento = Asiento.query.get_or_404(aid)
    respaldo_file = request.files.get('respaldo_file')
    respaldo_url_form = request.form.get('respaldo_url', '').strip()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if respaldo_file and respaldo_file.filename:
        from storage import save_attachment
        upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
        try:
            asiento.respaldo_url = save_attachment(respaldo_file, respaldo_file.filename, upload_folder)
        except ValueError as e:
            if is_ajax:
                return jsonify({'ok': False, 'error': str(e)}), 400
            flash(str(e), 'warning')
            return redirect(url_for('asientos.detalle', eid=eid, aid=aid))
    elif respaldo_url_form:
        asiento.respaldo_url = respaldo_url_form
    else:
        if is_ajax:
            return jsonify({'ok': False, 'error': 'Ingrese un archivo o URL de respaldo.'}), 400
        flash('Ingrese un archivo o URL de respaldo.', 'warning')
        return redirect(url_for('asientos.detalle', eid=eid, aid=aid))

    db.session.commit()
    if is_ajax:
        from storage import attachment_url as _att_url
        return jsonify({'ok': True, 'respaldo_url': asiento.respaldo_url,
                        'respaldo_href': _att_url(asiento.respaldo_url)})
    flash('Respaldo actualizado', 'success')
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
