import calendar
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, Contraparte, DocumentoSII, Conciliacion, MovimientoBanco, Cuenta
from sqlalchemy import func

bp = Blueprint('contrapartes', __name__)

TIPO_LIBRO_MAP = {
    'PROVEEDOR':  ['COMPRAS'],
    'CLIENTE':    ['VENTAS'],
    'HONORARIOS': ['HONORARIOS'],
    'AMBOS':      ['COMPRAS', 'VENTAS'],
}


def _validar_rut_dv(rut: str) -> bool:
    """Valida el dígito verificador de un RUT chileno usando módulo 11."""
    try:
        rut_clean = rut.strip().upper().replace('.', '').replace(' ', '')
        if not rut_clean:
            return True
        if '-' in rut_clean:
            body, dv = rut_clean.rsplit('-', 1)
        else:
            body, dv = rut_clean[:-1], rut_clean[-1]
        body = body.lstrip('0') or '0'
        if not body.isdigit():
            return False
        digits = [int(c) for c in body]
        factors = [2, 3, 4, 5, 6, 7]
        total = 0
        for i, d in enumerate(reversed(digits)):
            total += d * factors[i % 6]
        remainder = 11 - (total % 11)
        if remainder == 11:
            expected = '0'
        elif remainder == 10:
            expected = 'K'
        else:
            expected = str(remainder)
        return dv == expected
    except Exception:
        return True


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

    # ── AP/AR summary: only outstanding (not yet conciliated) documents ──
    def _resumen_sii(tipo_libro):
        return (db.session.query(
            DocumentoSII.rut_contraparte,
            func.max(DocumentoSII.razon_social_contraparte).label('razon_social'),
            func.count(DocumentoSII.id).label('ndocs'),
            func.sum(DocumentoSII.monto_neto).label('total_neto'),
            func.sum(DocumentoSII.iva).label('total_iva'),
            func.sum(DocumentoSII.total).label('total_bruto'),
        )
        .filter(
            DocumentoSII.empresa_id == eid,
            DocumentoSII.tipo_libro == tipo_libro,
            DocumentoSII.conciliacion_id == None,
        )
        .group_by(DocumentoSII.rut_contraparte)
        .order_by(func.sum(DocumentoSII.total).desc())
        .all())

    proveedores = _resumen_sii('COMPRAS')
    honorarios  = _resumen_sii('HONORARIOS')
    clientes    = _resumen_sii('VENTAS')

    # ── Contrapartes list ──
    q = Contraparte.query.filter_by(empresa_id=eid)
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

    nom_prov, saldo_cta_prov = _saldo_cta('2.1.01')
    nom_cli,  saldo_cta_cli  = _saldo_cta('1.1.03')
    nom_hon,  saldo_cta_hon  = _saldo_cta('2.1.04')

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
                empresa_id=eid,
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
    if cp.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('contrapartes.index', eid=eid))

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
    if cp.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('contrapartes.index', eid=eid))
    db.session.delete(cp)
    db.session.commit()
    flash('Contraparte eliminada', 'warning')
    vista = request.form.get('vista', request.args.get('vista', ''))
    return redirect(url_for('contrapartes.index', eid=eid, vista=vista) if vista else url_for('contrapartes.index', eid=eid))


@bp.route('/empresa/<int:eid>/contrapartes/<int:cid>')
def detalle(eid, cid):
    empresa = Empresa.query.get_or_404(eid)
    cp = Contraparte.query.get_or_404(cid)
    if cp.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('contrapartes.index', eid=eid))

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
                           rows_hist=rows_hist)


@bp.route('/empresa/<int:eid>/contrapartes/importar', methods=['POST'])
def importar_desde_docs(eid):
    """Crea contrapartes para todos los RUTs únicos en DocumentoSII que aún no existen."""
    empresa = Empresa.query.get_or_404(eid)
    existentes = {c.rut for c in Contraparte.query.filter_by(empresa_id=eid).all()}

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
            empresa_id=eid,
            rut=rut,
            razon_social=info['razon'][:200],
            tipo=tipo,
            activo=True,
        ))
        creados += 1

    db.session.commit()
    flash(f'{creados} contraparte(s) importadas desde documentos SII', 'success')
    return redirect(url_for('contrapartes.index', eid=eid))
