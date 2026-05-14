import calendar
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, Contraparte, DocumentoSII
from sqlalchemy import func

bp = Blueprint('contrapartes', __name__)

TIPO_LIBRO_MAP = {
    'PROVEEDOR':  ['COMPRAS'],
    'CLIENTE':    ['VENTAS'],
    'HONORARIOS': ['HONORARIOS'],
    'AMBOS':      ['COMPRAS', 'VENTAS'],
}


def _periodo(mes_str):
    desde = date.fromisoformat(mes_str + '-01')
    ultimo = calendar.monthrange(desde.year, desde.month)[1]
    return desde, desde.replace(day=ultimo)


@bp.route('/empresa/<int:eid>/contrapartes')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    tipo_filtro = request.args.get('tipo', '')
    buscar = request.args.get('q', '').strip()

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

    return render_template('contrapartes/index.html',
                           empresa=empresa, contrapartes=contrapartes,
                           tipo_filtro=tipo_filtro, buscar=buscar)


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
        cp.razon_social = request.form.get('razon_social', '').strip()
        cp.tipo = request.form.get('tipo', 'PROVEEDOR')
        cp.email = request.form.get('email', '').strip() or None
        cp.telefono = request.form.get('telefono', '').strip() or None
        cp.notas = request.form.get('notas', '').strip() or None
        cp.activo = bool(request.form.get('activo'))
        db.session.commit()
        flash('Contraparte actualizada', 'success')
        return redirect(url_for('contrapartes.detalle', eid=eid, cid=cid))

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
    return redirect(url_for('contrapartes.index', eid=eid))


@bp.route('/empresa/<int:eid>/contrapartes/<int:cid>')
def detalle(eid, cid):
    empresa = Empresa.query.get_or_404(eid)
    cp = Contraparte.query.get_or_404(cid)
    if cp.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('contrapartes.index', eid=eid))

    hoy = date.today()
    mes = request.args.get('mes', hoy.strftime('%Y-%m'))
    try:
        desde, hasta = _periodo(mes)
    except ValueError:
        mes = hoy.strftime('%Y-%m')
        desde, hasta = _periodo(mes)

    libros = TIPO_LIBRO_MAP.get(cp.tipo, ['COMPRAS', 'VENTAS', 'HONORARIOS'])
    docs = (DocumentoSII.query
            .filter_by(empresa_id=eid, rut_contraparte=cp.rut)
            .filter(DocumentoSII.tipo_libro.in_(libros))
            .filter(DocumentoSII.fecha >= desde, DocumentoSII.fecha <= hasta)
            .order_by(DocumentoSII.fecha)
            .all())

    # Totales del período
    total_neto = sum(d.monto_neto or 0 for d in docs)
    total_iva  = sum(d.iva or 0 for d in docs)
    total_bruto = sum(d.total or 0 for d in docs)

    # Historial anual: sumas por mes
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
                           docs=docs, mes=mes, desde=desde, hasta=hasta,
                           total_neto=total_neto, total_iva=total_iva,
                           total_bruto=total_bruto, rows_hist=rows_hist)


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
