import os
from datetime import date
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, send_file, current_app
from models import db, Empresa, Asiento, ArchivoImportado, DocumentoSII, MovimientoBanco
from sqlalchemy import func

bp = Blueprint('main', __name__)


@bp.route('/')
def index():
    empresas = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()
    return render_template('index.html', empresas=empresas)


@bp.route('/consolidado')
def consolidado():
    empresas = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()
    ids = [e.id for e in empresas]

    # Últimos 12 meses como strings 'YYYY-MM'
    hoy = date.today()
    meses = [(hoy.replace(day=1) - relativedelta(months=i)).strftime('%Y-%m')
             for i in range(11, -1, -1)]
    fecha_desde = date(int(meses[0][:4]), int(meses[0][5:]), 1)

    # ── Asientos por empresa/mes/estado (una sola query) ────────────────────
    rows_asi = (db.session.query(
            Asiento.empresa_id,
            func.strftime('%Y-%m', Asiento.fecha).label('mes'),
            Asiento.estado,
            func.count(Asiento.id).label('n'),
        )
        .filter(Asiento.empresa_id.in_(ids),
                Asiento.fecha >= fecha_desde,
                Asiento.estado != 'ANULADO')
        .group_by(Asiento.empresa_id, 'mes', Asiento.estado)
        .all())
    # {(eid, mes, estado): count}
    asi = {(r.empresa_id, r.mes, r.estado): r.n for r in rows_asi}

    # ── Docs SII pendientes por empresa/mes ──────────────────────────────────
    rows_doc = (db.session.query(
            DocumentoSII.empresa_id,
            func.strftime('%Y-%m', DocumentoSII.fecha).label('mes'),
            func.count(DocumentoSII.id).label('n'),
        )
        .filter(DocumentoSII.empresa_id.in_(ids),
                DocumentoSII.procesado == False,
                DocumentoSII.fecha >= fecha_desde)
        .group_by(DocumentoSII.empresa_id, 'mes')
        .all())
    docs_pend = {(r.empresa_id, r.mes): r.n for r in rows_doc}

    # ── Movimientos bancarios pendientes por empresa/mes ────────────────────
    rows_mov = (db.session.query(
            MovimientoBanco.empresa_id,
            func.strftime('%Y-%m', MovimientoBanco.fecha).label('mes'),
            func.count(MovimientoBanco.id).label('n'),
        )
        .filter(MovimientoBanco.empresa_id.in_(ids),
                MovimientoBanco.procesado == False,
                MovimientoBanco.fecha >= fecha_desde)
        .group_by(MovimientoBanco.empresa_id, 'mes')
        .all())
    movs_pend = {(r.empresa_id, r.mes): r.n for r in rows_mov}

    # ── Construir grilla ─────────────────────────────────────────────────────
    # Estado por celda: 'ok', 'parcial', 'borrador', 'vacio'
    def _estado_celda(eid, mes):
        conf = asi.get((eid, mes, 'CONFIRMADO'), 0)
        borr = asi.get((eid, mes, 'BORRADOR'), 0)
        pend = docs_pend.get((eid, mes), 0) + movs_pend.get((eid, mes), 0)
        if conf == 0 and borr == 0 and pend == 0:
            return 'vacio'
        if conf > 0 and borr == 0 and pend == 0:
            return 'ok'
        if conf > 0:
            return 'parcial'
        return 'borrador'

    grilla = []
    for e in empresas:
        celdas = {mes: _estado_celda(e.id, mes) for mes in meses}
        grilla.append({'empresa': e, 'celdas': celdas})

    # ── Detalle para el acordeón ─────────────────────────────────────────────
    datos = []
    for e in empresas:
        archivos = (ArchivoImportado.query
                    .filter_by(empresa_id=e.id)
                    .order_by(ArchivoImportado.tipo, ArchivoImportado.periodo)
                    .all())
        datos.append({'empresa': e, 'archivos': archivos})

    return render_template('consolidado.html',
                           datos=datos, grilla=grilla, meses=meses)


@bp.route('/backup')
def backup():
    import sqlite3
    import tempfile
    from datetime import date
    uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_path = uri.replace('sqlite:///', '')
    if not os.path.isabs(db_path):
        db_path = os.path.join(current_app.root_path, db_path)
    nombre = f'contabilidad_backup_{date.today().isoformat()}.db'
    # VACUUM INTO crea una copia atómica y compacta sin bloquear la BD en uso
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    con = sqlite3.connect(db_path)
    con.execute(f"VACUUM INTO '{tmp.name}'")
    con.close()
    return send_file(tmp.name, as_attachment=True, download_name=nombre)
