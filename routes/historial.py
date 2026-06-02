from datetime import datetime
from flask import Blueprint, render_template, request
from models import db, Empresa, Historial

bp = Blueprint('historial', __name__)


@bp.route('/empresa/<int:eid>/historial')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    tipo = request.args.get('tipo', '')
    accion = request.args.get('accion', '')
    q = Historial.query.filter_by(empresa_id=eid)
    if tipo:
        q = q.filter_by(tipo_objeto=tipo)
    if accion:
        q = q.filter_by(accion=accion)
    page = request.args.get('page', 1, type=int)
    eventos = q.order_by(Historial.fecha.desc()).paginate(page=page, per_page=80)

    tipos = sorted({r[0] for r in db.session.query(Historial.tipo_objeto)
                    .filter_by(empresa_id=eid).distinct().all()})
    acciones = sorted({r[0] for r in db.session.query(Historial.accion)
                       .filter_by(empresa_id=eid).distinct().all()})

    return render_template('historial/index.html', empresa=empresa,
                           eventos=eventos, tipo=tipo, accion=accion,
                           tipos=tipos, acciones=acciones)
