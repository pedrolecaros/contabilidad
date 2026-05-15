from flask import Blueprint, request, jsonify, url_for
from models import db, Empresa, Asiento, Cuenta, Empleado, DocumentoSII, Contraparte
from sqlalchemy import or_

bp = Blueprint('buscar', __name__)


@bp.route('/empresa/<int:eid>/buscar')
def buscar(eid):
    q = request.args.get('q', '').strip()
    if not q or len(q) < 1:
        return jsonify(resultados=[])

    resultados = []
    q_like = f'%{q}%'

    # Asientos — por número o descripción
    asientos = (Asiento.query
                .filter_by(empresa_id=eid)
                .filter(or_(
                    Asiento.descripcion.ilike(q_like),
                    db.cast(Asiento.numero, db.String).like(q + '%'),
                ))
                .order_by(Asiento.numero.desc())
                .limit(8).all())
    for a in asientos:
        resultados.append({
            'tipo': 'asiento',
            'titulo': f'N°{a.numero} — {a.descripcion or ""}',
            'subtitulo': a.fecha.strftime('%d/%m/%Y') + f' · {a.estado}',
            'url': url_for('asientos.detalle', eid=eid, aid=a.id),
        })

    # Cuentas — por código o nombre
    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .filter(or_(
                   Cuenta.codigo.ilike(q_like),
                   Cuenta.nombre.ilike(q_like),
               ))
               .order_by(Cuenta.codigo)
               .limit(6).all())
    for c in cuentas:
        resultados.append({
            'tipo': 'cuenta',
            'titulo': f'{c.codigo} — {c.nombre}',
            'subtitulo': c.tipo or '',
            'url': url_for('reportes.mayor', eid=eid, cuenta_id=c.id),
        })

    # Empleados — por nombre o RUT
    empleados = (Empleado.query
                 .filter_by(empresa_id=eid, activo=True)
                 .filter(or_(
                     Empleado.nombre.ilike(q_like),
                     Empleado.rut.ilike(q_like),
                 ))
                 .limit(5).all())
    for e in empleados:
        resultados.append({
            'tipo': 'empleado',
            'titulo': e.nombre,
            'subtitulo': f'RUT {e.rut}',
            'url': url_for('remuneraciones.historial', eid=eid, emp_id=e.id),
        })

    # Contrapartes — por nombre o RUT
    contrapartes = (Contraparte.query
                    .filter_by(empresa_id=eid)
                    .filter(or_(
                        Contraparte.razon_social.ilike(q_like),
                        Contraparte.rut.ilike(q_like),
                    ))
                    .limit(5).all())
    for c in contrapartes:
        resultados.append({
            'tipo': 'contraparte',
            'titulo': c.razon_social,
            'subtitulo': f'RUT {c.rut}',
            'url': url_for('contrapartes.detalle', eid=eid, cid=c.id),
        })

    return jsonify(resultados=resultados)
