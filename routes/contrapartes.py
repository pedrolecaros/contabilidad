import calendar
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, Contraparte, DocumentoSII, Conciliacion, MovimientoBanco, Cuenta, LineaAsiento, Asiento
from sqlalchemy import func
from engine.saldos import saldo_por_contraparte

bp = Blueprint('contrapartes', __name__)


def _contactos_de_empresa_ids(eid):
    """IDs de contrapartes con actividad (líneas o docs SII) en una empresa."""
    ids_lineas = {r[0] for r in db.session.query(LineaAsiento.contraparte_id)
                  .join(Asiento)
                  .filter(Asiento.empresa_id == eid,
                          LineaAsiento.contraparte_id != None).distinct().all()}
    ruts_docs = {r[0] for r in db.session.query(DocumentoSII.rut_contraparte)
                 .filter(DocumentoSII.empresa_id == eid,
                         DocumentoSII.rut_contraparte != None).distinct().all()}
    ids_docs = {r[0] for r in db.session.query(Contraparte.id)
                .filter(Contraparte.rut.in_(ruts_docs)).all()} if ruts_docs else set()
    return ids_lineas | ids_docs


@bp.route('/contactos')
def contactos():
    """Vista de contactos. Si entra desde una empresa, filtra a los usados ahí.
    En el consolidado (sin `from`), muestra todos."""
    buscar = request.args.get('q', '').strip()
    from_eid = request.args.get('from', type=int)
    empresa = Empresa.query.get(from_eid) if from_eid else None

    q = Contraparte.query
    if empresa:
        ids = _contactos_de_empresa_ids(empresa.id)
        q = q.filter(Contraparte.id.in_(ids)) if ids else q.filter(False)
    if buscar:
        like = f'%{buscar}%'
        q = q.filter(db.or_(Contraparte.rut.ilike(like),
                            Contraparte.razon_social.ilike(like)))
    contactos = q.order_by(Contraparte.razon_social).all()

    # Conteo de movimientos por contraparte (limitado a la empresa si aplica)
    from sqlalchemy import func as _func
    mc_q = db.session.query(LineaAsiento.contraparte_id, _func.count(LineaAsiento.id))
    if empresa:
        mc_q = mc_q.join(Asiento).filter(Asiento.empresa_id == empresa.id)
    cnt = dict(mc_q.filter(LineaAsiento.contraparte_id != None)
               .group_by(LineaAsiento.contraparte_id).all())

    return render_template('contrapartes/contactos.html',
                           contactos=contactos, buscar=buscar,
                           empresa=empresa, from_empresa=empresa,
                           mov_count=cnt)


@bp.route('/contactos/<int:cid>/editar', methods=['GET', 'POST'])
def contacto_editar(cid):
    cp = Contraparte.query.get_or_404(cid)
    from_eid = request.args.get('from', type=int) or request.form.get('from', type=int)
    from_empresa = Empresa.query.get(from_eid) if from_eid else None
    if request.method == 'POST':
        cp.rut = request.form.get('rut', '').strip()
        if cp.rut and not _validar_rut_dv(cp.rut):
            flash(f'Advertencia: RUT {cp.rut} tiene dígito verificador inválido', 'warning')
        cp.razon_social = request.form.get('razon_social', '').strip()
        cp.tipo = request.form.get('tipo', cp.tipo or 'OTRO')
        cp.email = request.form.get('email', '').strip() or None
        cp.telefono = request.form.get('telefono', '').strip() or None
        cp.notas = request.form.get('notas', '').strip() or None
        cp.activo = bool(request.form.get('activo'))
        db.session.commit()
        flash('Contacto actualizado', 'success')
        return redirect(url_for('contrapartes.contactos',
                                **({'from': from_eid} if from_eid else {})))
    return render_template('contrapartes/contacto_form.html',
                           cp=cp, from_empresa=from_empresa, empresa=from_empresa)


@bp.route('/contactos/<int:cid>')
def contacto_detalle(cid):
    """Detalle global de un contacto: asientos y documentos donde aparece (en todas las empresas)."""
    cp = Contraparte.query.get_or_404(cid)
    from_eid = request.args.get('from', type=int)
    from_empresa = Empresa.query.get(from_eid) if from_eid else None
    empresa = from_empresa  # para que base.html renderice sidebar

    # Movimientos (líneas de asiento). Si entra desde una empresa, filtrar a esa.
    movs_q = (LineaAsiento.query
              .join(Asiento)
              .filter(LineaAsiento.contraparte_id == cp.id,
                      Asiento.estado == 'CONFIRMADO'))
    if from_empresa:
        movs_q = movs_q.filter(Asiento.empresa_id == from_empresa.id)
    movs = movs_q.order_by(Asiento.fecha.desc(), Asiento.numero).all()

    # Agrupar por empresa para ver presencia
    from collections import defaultdict
    por_empresa = defaultdict(lambda: {'empresa': None, 'lineas': [], 'debe': 0.0, 'haber': 0.0})
    for l in movs:
        eid = l.asiento.empresa_id
        por_empresa[eid]['empresa'] = l.asiento.empresa
        por_empresa[eid]['lineas'].append(l)
        por_empresa[eid]['debe']  += l.debe or 0
        por_empresa[eid]['haber'] += l.haber or 0
    grupos = sorted(por_empresa.values(),
                    key=lambda g: g['empresa'].razon_social if g['empresa'] else '')

    # Documentos SII relacionados (por RUT). Filtrar a la empresa si entra desde una.
    docs = []
    if cp.rut:
        docs_q = DocumentoSII.query.filter_by(rut_contraparte=cp.rut)
        if from_empresa:
            docs_q = docs_q.filter(DocumentoSII.empresa_id == from_empresa.id)
        docs = docs_q.order_by(DocumentoSII.fecha.desc()).limit(50).all()

    return render_template('contrapartes/contacto_detalle.html',
                           cp=cp, grupos=grupos, total_movs=len(movs),
                           docs=docs, from_empresa=from_empresa, empresa=empresa)


@bp.route('/contactos/nuevo', methods=['GET', 'POST'])
def contacto_nuevo():
    from_eid = request.args.get('from', type=int) or request.form.get('from', type=int)
    from_empresa = Empresa.query.get(from_eid) if from_eid else None
    if request.method == 'POST':
        razon = request.form.get('razon_social', '').strip()
        if not razon:
            flash('Razón social / nombre es obligatorio', 'danger')
        else:
            cp = Contraparte(
                rut=request.form.get('rut', '').strip(),
                razon_social=razon,
                tipo=request.form.get('tipo', 'OTRO'),
                email=request.form.get('email', '').strip() or None,
                telefono=request.form.get('telefono', '').strip() or None,
                notas=request.form.get('notas', '').strip() or None,
                activo=bool(request.form.get('activo')),
            )
            db.session.add(cp)
            db.session.commit()
            flash(f'Contacto "{razon}" creado', 'success')
            return redirect(url_for('contrapartes.contactos',
                                    **({'from': from_eid} if from_eid else {})))
    return render_template('contrapartes/contacto_form.html', cp=None,
                           from_empresa=from_empresa, empresa=from_empresa)

TIPO_LIBRO_MAP = {
    'PROVEEDOR':   ['COMPRAS'],
    'CLIENTE':     ['VENTAS'],
    'HONORARIOS':  ['HONORARIOS'],
    'AMBOS':       ['COMPRAS', 'VENTAS'],
    'RELACIONADA': [],  # solo movimientos contables, sin documentos SII
}


from utils.rut import validar_rut_dv as _validar_rut_dv


def _periodo(mes_str):
    desde = date.fromisoformat(mes_str + '-01')
    ultimo = calendar.monthrange(desde.year, desde.month)[1]
    return desde, desde.replace(day=ultimo)


@bp.route('/empresa/<int:eid>/contrapartes')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    tipo_filtro = request.args.get('tipo', '')
    buscar = request.args.get('q', '').strip()
    vista = request.args.get('vista', 'apar')   # 'apar' | 'lista'

    hoy = date.today()
    desde_str = request.args.get('desde', date(hoy.year, 1, 1).isoformat())
    hasta_str  = request.args.get('hasta', hoy.isoformat())
    try:
        desde = date.fromisoformat(desde_str)
        hasta = date.fromisoformat(hasta_str)
    except ValueError:
        desde, hasta = date(hoy.year, 1, 1), hoy

    # ── Saldo contable por contraparte ──
    nom_prov, saldo_cta_prov, proveedores = saldo_por_contraparte(eid, '2.1.01')
    nom_cli,  saldo_cta_cli,  clientes    = saldo_por_contraparte(eid, '1.1.03')
    nom_hon,  saldo_cta_hon,  honorarios  = saldo_por_contraparte(eid, '2.1.04')

    # ── Contrapartes list (globales: no filtran por empresa) ──
    q = Contraparte.query
    if tipo_filtro:
        q = q.filter_by(tipo=tipo_filtro)
    if buscar:
        like = f'%{buscar}%'
        q = q.filter(
            db.or_(Contraparte.rut.ilike(like),
                   Contraparte.razon_social.ilike(like))
        )
    contrapartes = q.order_by(Contraparte.razon_social).all()

    # Saldos pendientes por RUT
    rows_saldo = (db.session.query(
                      DocumentoSII.rut_contraparte,
                      func.count(DocumentoSII.id).label('ndocs'),
                      func.sum(DocumentoSII.total).label('total'),
                  )
                  .filter(DocumentoSII.empresa_id == eid,
                          DocumentoSII.conciliacion_id == None)
                  .group_by(DocumentoSII.rut_contraparte)
                  .all())
    saldos = {r.rut_contraparte: {'ndocs': r.ndocs, 'total': r.total or 0}
              for r in rows_saldo}

    # Saldos contables de cuentas control
    def _saldo_cta(codigo):
        c = Cuenta.query.filter_by(empresa_id=eid, codigo=codigo).first()
        return (c.nombre, round(c.saldo())) if c else (None, None)


    return render_template('contrapartes/index.html',
                           empresa=empresa, contrapartes=contrapartes,
                           tipo_filtro=tipo_filtro, buscar=buscar, vista=vista,
                           desde=desde, hasta=hasta,
                           proveedores=proveedores, honorarios=honorarios, clientes=clientes,
                           saldos=saldos,
                           saldo_cta_prov=saldo_cta_prov, nom_prov=nom_prov,
                           saldo_cta_cli=saldo_cta_cli,   nom_cli=nom_cli,
                           saldo_cta_hon=saldo_cta_hon,   nom_hon=nom_hon)


@bp.route('/empresa/<int:eid>/contrapartes/nueva', methods=['GET', 'POST'])
def nueva(eid):
    empresa = Empresa.query.get_or_404(eid)
    if request.method == 'POST':
        rut = request.form.get('rut', '').strip()
        razon_social = request.form.get('razon_social', '').strip()
        tipo = request.form.get('tipo', 'PROVEEDOR')
        if not rut or not razon_social:
            flash('RUT y razón social son obligatorios', 'danger')
        else:
            if not _validar_rut_dv(rut):
                flash(f'Advertencia: RUT {rut} tiene dígito verificador inválido', 'warning')
            c = Contraparte(
                rut=rut,
                razon_social=razon_social,
                tipo=tipo,
                email=request.form.get('email', '').strip() or None,
                telefono=request.form.get('telefono', '').strip() or None,
                notas=request.form.get('notas', '').strip() or None,
                activo=bool(request.form.get('activo')),
            )
            db.session.add(c)
            db.session.commit()
            flash(f'Contraparte "{razon_social}" creada', 'success')
            return redirect(url_for('contrapartes.index', eid=eid))

    return render_template('contrapartes/form.html',
                           empresa=empresa, cp=None,
                           titulo='Nueva contraparte')


@bp.route('/empresa/<int:eid>/contrapartes/<int:cid>/editar', methods=['GET', 'POST'])
def editar(eid, cid):
    empresa = Empresa.query.get_or_404(eid)
    cp = Contraparte.query.get_or_404(cid)
    # Contrapartes globales: cualquier empresa puede editarlas

    if request.method == 'POST':
        cp.rut = request.form.get('rut', '').strip()
        if not _validar_rut_dv(cp.rut):
            flash(f'Advertencia: RUT {cp.rut} tiene dígito verificador inválido', 'warning')
        cp.razon_social = request.form.get('razon_social', '').strip()
        cp.tipo = request.form.get('tipo', 'PROVEEDOR')
        cp.email = request.form.get('email', '').strip() or None
        cp.telefono = request.form.get('telefono', '').strip() or None
        cp.notas = request.form.get('notas', '').strip() or None
        cp.activo = bool(request.form.get('activo'))
        db.session.commit()
        flash('Contraparte actualizada', 'success')
        vista = request.form.get('vista', request.args.get('vista', ''))
        return redirect(url_for('contrapartes.index', eid=eid, vista=vista) if vista else url_for('contrapartes.detalle', eid=eid, cid=cid))

    return render_template('contrapartes/form.html',
                           empresa=empresa, cp=cp,
                           titulo='Editar contraparte')


@bp.route('/empresa/<int:eid>/contrapartes/<int:cid>/eliminar', methods=['POST'])
def eliminar(eid, cid):
    cp = Contraparte.query.get_or_404(cid)
    db.session.delete(cp)
    db.session.commit()
    flash('Contraparte eliminada', 'warning')
    vista = request.form.get('vista', request.args.get('vista', ''))
    return redirect(url_for('contrapartes.index', eid=eid, vista=vista) if vista else url_for('contrapartes.index', eid=eid))


@bp.route('/empresa/<int:eid>/contrapartes/<int:cid>')
def detalle(eid, cid):
    empresa = Empresa.query.get_or_404(eid)
    cp = Contraparte.query.get_or_404(cid)
    # Contrapartes globales: cualquier empresa accede al detalle (saldos filtran por eid)

    hoy = date.today()
    desde_mes = request.args.get('desde', f'{hoy.year}-01')
    hasta_mes  = request.args.get('hasta', f'{hoy.year}-{hoy.month:02d}')
    try:
        desde, _ = _periodo(desde_mes)
        _, hasta  = _periodo(hasta_mes)
    except ValueError:
        desde_mes, hasta_mes = f'{hoy.year}-01', f'{hoy.year}-{hoy.month:02d}'
        desde, _ = _periodo(desde_mes)
        _, hasta  = _periodo(hasta_mes)

    libros = TIPO_LIBRO_MAP.get(cp.tipo, ['COMPRAS', 'VENTAS', 'HONORARIOS'])

    # Todos los docs históricos de esta contraparte
    todos_docs = (DocumentoSII.query
                  .filter_by(empresa_id=eid, rut_contraparte=cp.rut)
                  .filter(DocumentoSII.tipo_libro.in_(libros))
                  .order_by(DocumentoSII.fecha.desc())
                  .all())

    # Docs del período seleccionado
    docs = [d for d in todos_docs if desde <= (d.fecha or date.min) <= hasta]

    # Totales del período
    total_neto  = sum(d.monto_neto or 0 for d in docs)
    total_iva   = sum(d.iva or 0 for d in docs)
    total_bruto = sum(d.total or 0 for d in docs)

    # Resumen histórico
    total_historico = sum(d.total or 0 for d in todos_docs)
    ndocs_historico = len(todos_docs)

    # Saldo pendiente: docs sin conciliar (no tienen movimiento de pago asociado)
    docs_pendientes = [d for d in todos_docs if not d.conciliacion_id]
    saldo_pendiente = sum(d.total or 0 for d in docs_pendientes)

    # Conciliaciones: directas (contraparte_id) + indirectas (vía documentos)
    concs_directas = (Conciliacion.query
                      .filter_by(empresa_id=eid, contraparte_id=cp.id)
                      .all())
    conc_ids_directas = {c.id for c in concs_directas}
    conc_ids_via_docs = {d.conciliacion_id for d in todos_docs if d.conciliacion_id}
    ids_extra = conc_ids_via_docs - conc_ids_directas
    concs_extra = (Conciliacion.query.filter(Conciliacion.id.in_(ids_extra)).all()
                   if ids_extra else [])
    conciliaciones = sorted(concs_directas + concs_extra,
                            key=lambda c: c.fecha, reverse=True)

    # Total pagado / cobrado (suma de movimientos de banco en esas conciliaciones)
    total_movs = 0
    for c in conciliaciones:
        for m in c.movimientos:
            total_movs += (m.cargo or 0) + (m.abono or 0)

    # Historial mensual (para los chips de navegación)
    rows_hist = (db.session.query(
                     func.strftime('%Y-%m', DocumentoSII.fecha).label('mes'),
                     func.sum(DocumentoSII.total).label('total'),
                 )
                 .filter_by(empresa_id=eid, rut_contraparte=cp.rut)
                 .filter(DocumentoSII.tipo_libro.in_(libros))
                 .group_by('mes')
                 .order_by('mes')
                 .all())

    # ── Saldo contable (auxiliar) ────────────────────────────────────────────
    # Todos los movimientos de asientos confirmados donde esta contraparte aparece
    movs_aux = (LineaAsiento.query
                .join(Asiento)
                .filter(
                    LineaAsiento.contraparte_id == cp.id,
                    Asiento.empresa_id == eid,
                    Asiento.estado == 'CONFIRMADO',
                )
                .order_by(Asiento.fecha, Asiento.numero)
                .all())

    # Agrupar por cuenta y calcular saldo
    from collections import defaultdict
    aux_por_cuenta = defaultdict(lambda: {'cuenta': None, 'lineas': [], 'debe': 0.0, 'haber': 0.0})
    for l in movs_aux:
        k = l.cuenta_id
        aux_por_cuenta[k]['cuenta'] = l.cuenta
        aux_por_cuenta[k]['lineas'].append(l)
        aux_por_cuenta[k]['debe']  += l.debe
        aux_por_cuenta[k]['haber'] += l.haber

    # Calcular saldo neto por cuenta
    for g in aux_por_cuenta.values():
        c = g['cuenta']
        if c and c.naturaleza == 'DEUDORA':
            g['saldo'] = g['debe'] - g['haber']
        else:
            g['saldo'] = g['haber'] - g['debe']

    aux_cuentas = sorted(aux_por_cuenta.values(), key=lambda g: g['cuenta'].codigo)
    saldo_aux_total = sum(g['saldo'] for g in aux_cuentas)

    return render_template('contrapartes/detalle.html',
                           empresa=empresa, cp=cp,
                           docs=docs, desde_mes=desde_mes, hasta_mes=hasta_mes,
                           desde=desde, hasta=hasta,
                           total_neto=total_neto, total_iva=total_iva,
                           total_bruto=total_bruto,
                           total_historico=total_historico,
                           ndocs_historico=ndocs_historico,
                           docs_pendientes=docs_pendientes,
                           saldo_pendiente=saldo_pendiente,
                           conciliaciones=conciliaciones,
                           total_movs=total_movs,
                           rows_hist=rows_hist,
                           aux_cuentas=aux_cuentas,
                           saldo_aux_total=saldo_aux_total,
                           movs_aux=movs_aux)


@bp.route('/empresa/<int:eid>/contrapartes/importar', methods=['POST'])
def importar_desde_docs(eid):
    """Crea contrapartes para todos los RUTs únicos en DocumentoSII que aún no existen."""
    empresa = Empresa.query.get_or_404(eid)
    existentes = {c.rut for c in Contraparte.query.all() if c.rut}

    rows = (db.session.query(
                DocumentoSII.rut_contraparte,
                func.max(DocumentoSII.razon_social_contraparte).label('razon'),
                DocumentoSII.tipo_libro,
            )
            .filter_by(empresa_id=eid)
            .filter(DocumentoSII.rut_contraparte != None)
            .group_by(DocumentoSII.rut_contraparte, DocumentoSII.tipo_libro)
            .all())

    # Agrupar por RUT: determinar tipo
    from collections import defaultdict
    por_rut = defaultdict(lambda: {'razon': '', 'libros': set()})
    for r in rows:
        por_rut[r.rut_contraparte]['razon'] = r.razon or r.rut_contraparte
        por_rut[r.rut_contraparte]['libros'].add(r.tipo_libro)

    creados = 0
    for rut, info in por_rut.items():
        if rut in existentes:
            continue
        libros = info['libros']
        if 'COMPRAS' in libros and 'VENTAS' in libros:
            tipo = 'AMBOS'
        elif 'VENTAS' in libros:
            tipo = 'CLIENTE'
        elif 'HONORARIOS' in libros:
            tipo = 'HONORARIOS'
        else:
            tipo = 'PROVEEDOR'
        db.session.add(Contraparte(
            rut=rut,
            razon_social=info['razon'][:200],
            tipo=tipo,
            activo=True,
        ))
        creados += 1

    db.session.commit()
    flash(f'{creados} contraparte(s) importadas desde documentos SII', 'success')
    return redirect(url_for('contrapartes.index', eid=eid))
