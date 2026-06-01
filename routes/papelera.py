import json
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, abort
from models import (db, Papelera, Empresa, Asiento, LineaAsiento,
                    Liquidacion, DocumentoSII, MovimientoBanco, AsientoAudit)

DIAS = 180

bp = Blueprint('papelera', __name__)


# ── Helpers de serialización ──────────────────────────────────────────────

def _ser_asiento(a):
    return {
        'fecha': str(a.fecha),
        'numero': a.numero,
        'descripcion': a.descripcion,
        'respaldo_url': a.respaldo_url,
        'origen': a.origen,
        'estado': a.estado,
        'lineas': [
            {'cuenta_id': l.cuenta_id, 'contraparte_id': l.contraparte_id,
             'debe': l.debe, 'haber': l.haber, 'descripcion': l.descripcion, 'orden': l.orden}
            for l in a.lineas
        ],
    }

def _ser_liquidacion(liq):
    cols = [c.name for c in liq.__table__.columns if c.name not in ('id',)]
    return {c: getattr(liq, c) for c in cols}

def _ser_documento_sii(doc):
    cols = [c.name for c in doc.__table__.columns if c.name not in ('id',)]
    d = {}
    for c in cols:
        v = getattr(doc, c)
        d[c] = str(v) if hasattr(v, 'strftime') else v
    return d

def _ser_movimiento_banco(mov):
    cols = [c.name for c in mov.__table__.columns if c.name not in ('id',)]
    d = {}
    for c in cols:
        v = getattr(mov, c)
        d[c] = str(v) if hasattr(v, 'strftime') else v
    return d


def enviar_papelera(tipo, objeto_id, empresa_id, descripcion, datos):
    """Crea registro en papelera. Llamar ANTES de db.session.delete."""
    ahora = datetime.now()
    p = Papelera(
        tipo=tipo,
        objeto_id=objeto_id,
        empresa_id=empresa_id,
        descripcion=descripcion,
        datos_json=json.dumps(datos, ensure_ascii=False, default=str),
        deleted_at=ahora,
        expires_at=ahora + timedelta(days=DIAS),
    )
    db.session.add(p)
    return p


# ── Routes ────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/papelera')
def index(eid):
    empresa = db.session.get(Empresa, eid) or abort(404)
    # Limpiar expirados
    Papelera.query.filter(
        Papelera.empresa_id == eid,
        Papelera.expires_at < datetime.now()
    ).delete()
    db.session.commit()

    items = (Papelera.query
             .filter_by(empresa_id=eid)
             .order_by(Papelera.deleted_at.desc())
             .all())
    return render_template('papelera/index.html', empresa=empresa, items=items, dias=DIAS,
                           now=datetime.now())


@bp.route('/empresa/<int:eid>/papelera/<int:pid>/restaurar', methods=['POST'])
def restaurar(eid, pid):
    empresa = db.session.get(Empresa, eid) or abort(404)
    p = db.session.get(Papelera, pid) or abort(404)
    if p.empresa_id != eid:
        abort(403)

    datos = json.loads(p.datos_json)
    try:
        if p.tipo == 'ASIENTO':
            from datetime import date
            a = Asiento(
                empresa_id=eid,
                fecha=date.fromisoformat(datos['fecha']),
                numero=datos.get('numero'),
                descripcion=datos.get('descripcion'),
                respaldo_url=datos.get('respaldo_url'),
                origen=datos.get('origen', 'MANUAL'),
                estado=datos.get('estado', 'BORRADOR'),
            )
            db.session.add(a)
            db.session.flush()
            for l in datos.get('lineas', []):
                db.session.add(LineaAsiento(
                    asiento_id=a.id,
                    cuenta_id=l['cuenta_id'],
                    contraparte_id=l.get('contraparte_id'),
                    debe=l['debe'],
                    haber=l['haber'],
                    descripcion=l.get('descripcion'),
                    orden=l.get('orden', 0),
                ))

        elif p.tipo == 'LIQUIDACION':
            from datetime import date
            liq = Liquidacion()
            for k, v in datos.items():
                if hasattr(liq, k):
                    if k in ('creado_en',) and v:
                        try:
                            v = datetime.fromisoformat(str(v))
                        except Exception:
                            pass
                    setattr(liq, k, v)
            db.session.add(liq)

        elif p.tipo == 'DOCUMENTO_SII':
            from datetime import date
            doc = DocumentoSII()
            for k, v in datos.items():
                if hasattr(doc, k):
                    if k == 'fecha' and v:
                        try:
                            v = date.fromisoformat(str(v))
                        except Exception:
                            pass
                    setattr(doc, k, v)
            db.session.add(doc)

        elif p.tipo == 'MOVIMIENTO_BANCO':
            from datetime import date
            mov = MovimientoBanco()
            for k, v in datos.items():
                if hasattr(mov, k):
                    if k == 'fecha' and v:
                        try:
                            v = date.fromisoformat(str(v))
                        except Exception:
                            pass
                    setattr(mov, k, v)
            db.session.add(mov)

        db.session.delete(p)
        db.session.commit()
        flash('Elemento restaurado correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al restaurar: {e}', 'danger')

    return redirect(url_for('papelera.index', eid=eid))


@bp.route('/empresa/<int:eid>/papelera/<int:pid>/eliminar', methods=['POST'])
def eliminar(eid, pid):
    p = db.session.get(Papelera, pid) or abort(404)
    if p.empresa_id != eid:
        abort(403)
    db.session.delete(p)
    db.session.commit()
    flash('Eliminado permanentemente.', 'warning')
    return redirect(url_for('papelera.index', eid=eid))


@bp.route('/empresa/<int:eid>/papelera/vaciar', methods=['POST'])
def vaciar(eid):
    Papelera.query.filter_by(empresa_id=eid).delete()
    db.session.commit()
    flash('Papelera vaciada.', 'warning')
    return redirect(url_for('papelera.index', eid=eid))
