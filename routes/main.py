import os
import shutil
import sqlite3
import tempfile
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, send_file, current_app, request, flash, redirect, url_for
from models import db, Empresa, Asiento, ArchivoImportado, DocumentoSII, MovimientoBanco, Liquidacion, Prestamo
from sqlalchemy import func

bp = Blueprint('main', __name__)


@bp.route('/')
def index():
    empresas = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()
    archivadas = Empresa.query.filter_by(activa=False).order_by(Empresa.razon_social).all()
    return render_template('index.html', empresas=empresas, archivadas=archivadas)


@bp.route('/consolidado')
def consolidado():
    empresas = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()
    ids = [e.id for e in empresas]

    hoy = date.today()
    hasta_mes = request.args.get('hasta', hoy.strftime('%Y-%m'))
    desde_mes_default = (hoy.replace(day=1) - relativedelta(months=11)).strftime('%Y-%m')
    desde_mes = request.args.get('desde', desde_mes_default)

    # Build months list desde_mes..hasta_mes inclusive
    desde_d = date(int(desde_mes[:4]), int(desde_mes[5:]), 1)
    hasta_d = date(int(hasta_mes[:4]), int(hasta_mes[5:]), 1)
    meses = []
    cur = desde_d
    while cur <= hasta_d:
        meses.append(cur.strftime('%Y-%m'))
        cur += relativedelta(months=1)
    if not meses:
        meses = [hoy.strftime('%Y-%m')]
    fecha_desde = desde_d

    # Asientos by (eid, mes, estado)
    rows_asi = (db.session.query(
            Asiento.empresa_id,
            func.strftime('%Y-%m', Asiento.fecha).label('mes'),
            Asiento.estado,
            func.count(Asiento.id).label('n'),
        )
        .filter(Asiento.empresa_id.in_(ids), Asiento.fecha >= fecha_desde,
                Asiento.estado != 'ANULADO')
        .group_by(Asiento.empresa_id, 'mes', Asiento.estado).all())
    asi = {(r.empresa_id, r.mes, r.estado): r.n for r in rows_asi}

    # Archivos importados by (eid, periodo, tipo)
    rows_arch = (db.session.query(
            ArchivoImportado.empresa_id,
            ArchivoImportado.periodo,
            ArchivoImportado.tipo,
            func.count(ArchivoImportado.id).label('n_arch'),
            func.sum(ArchivoImportado.ndocs).label('n_docs'),
        )
        .filter(ArchivoImportado.empresa_id.in_(ids),
                ArchivoImportado.periodo >= desde_mes,
                ArchivoImportado.periodo <= hasta_mes)
        .group_by(ArchivoImportado.empresa_id, ArchivoImportado.periodo, ArchivoImportado.tipo)
        .all())
    archivos_data = {(r.empresa_id, r.periodo, r.tipo): int(r.n_docs or 0) for r in rows_arch}
    archivos_loaded = {(r.empresa_id, r.periodo, r.tipo) for r in rows_arch}

    # Docs SII pendientes by (eid, mes, tipo_libro)
    rows_doc = (db.session.query(
            DocumentoSII.empresa_id,
            func.strftime('%Y-%m', DocumentoSII.fecha).label('mes'),
            DocumentoSII.tipo_libro,
            func.count(DocumentoSII.id).label('n'),
        )
        .filter(DocumentoSII.empresa_id.in_(ids), DocumentoSII.procesado == False,
                DocumentoSII.fecha >= fecha_desde)
        .group_by(DocumentoSII.empresa_id, 'mes', DocumentoSII.tipo_libro).all())
    docs_pend = {(r.empresa_id, r.mes, r.tipo_libro): r.n for r in rows_doc}

    # Movimientos bancarios pendientes (unprocessed AND not conciliated) by (eid, mes)
    # Must match the same filter pendientes.index uses so the count agrees with what you see there
    rows_mov = (db.session.query(
            MovimientoBanco.empresa_id,
            func.strftime('%Y-%m', MovimientoBanco.fecha).label('mes'),
            func.count(MovimientoBanco.id).label('n'),
        )
        .filter(MovimientoBanco.empresa_id.in_(ids),
                MovimientoBanco.procesado == False,
                MovimientoBanco.conciliacion_id == None,
                MovimientoBanco.fecha >= fecha_desde)
        .group_by(MovimientoBanco.empresa_id, 'mes').all())
    movs_pend = {(r.empresa_id, r.mes): r.n for r in rows_mov}

    # All bank movements (any state) by (eid, mes): total count + loaded detection
    rows_movs_all = (db.session.query(
            MovimientoBanco.empresa_id,
            func.strftime('%Y-%m', MovimientoBanco.fecha).label('mes'),
            func.count(MovimientoBanco.id).label('n'),
        )
        .filter(MovimientoBanco.empresa_id.in_(ids), MovimientoBanco.fecha >= fecha_desde)
        .group_by(MovimientoBanco.empresa_id, 'mes').all())
    movs_any   = {(r.empresa_id, r.mes) for r in rows_movs_all}
    movs_total = {(r.empresa_id, r.mes): r.n for r in rows_movs_all}

    # Liquidaciones by (eid, periodo)
    rows_liq = (db.session.query(
            Liquidacion.empresa_id,
            Liquidacion.periodo,
            func.count(Liquidacion.id).label('n'),
        )
        .filter(Liquidacion.empresa_id.in_(ids),
                Liquidacion.periodo >= desde_mes,
                Liquidacion.periodo <= hasta_mes)
        .group_by(Liquidacion.empresa_id, Liquidacion.periodo).all())
    liqs_data = {(r.empresa_id, r.periodo): r.n for r in rows_liq}

    # Asientos MANUAL sin respaldo_url por empresa (total en el rango)
    rows_sinresp = (db.session.query(
            Asiento.empresa_id,
            func.count(Asiento.id).label('n'),
        )
        .filter(Asiento.empresa_id.in_(ids), Asiento.origen == 'MANUAL',
                Asiento.estado != 'ANULADO', Asiento.respaldo_url == None,
                Asiento.fecha >= fecha_desde)
        .group_by(Asiento.empresa_id).all())
    sin_respaldo = {r.empresa_id: r.n for r in rows_sinresp}

    # Build celda_data[eid][mes]
    TIPOS = ['COMPRAS', 'VENTAS', 'HONORARIOS', 'BANCO']
    celda_data = {}
    for e in empresas:
        celda_data[e.id] = {}
        for mes in meses:
            libros = {}
            for tipo in TIPOS:
                if tipo == 'BANCO':
                    # Consider loaded if ArchivoImportado has it OR any MovimientoBanco exists
                    loaded  = (e.id, mes, tipo) in archivos_loaded or (e.id, mes) in movs_any
                    n_docs  = archivos_data.get((e.id, mes, tipo), 0) or movs_total.get((e.id, mes), 0)
                    pending = movs_pend.get((e.id, mes), 0)
                else:
                    loaded  = (e.id, mes, tipo) in archivos_loaded
                    n_docs  = archivos_data.get((e.id, mes, tipo), 0)
                    pending = docs_pend.get((e.id, mes, tipo), 0)
                libros[tipo] = {'loaded': loaded, 'n_docs': n_docs, 'pending': pending}

            conf = asi.get((e.id, mes, 'CONFIRMADO'), 0)
            borr = asi.get((e.id, mes, 'BORRADOR'), 0)
            liqs = liqs_data.get((e.id, mes), 0)
            total_pend = sum(l['pending'] for l in libros.values())
            any_loaded = any(l['loaded'] for l in libros.values())

            if conf == 0 and borr == 0 and not any_loaded and liqs == 0:
                estado = 'vacio'
            elif conf > 0 and borr == 0 and total_pend == 0:
                estado = 'ok'
            elif conf > 0 or any_loaded:
                estado = 'parcial'
            else:
                estado = 'borrador'

            celda_data[e.id][mes] = {
                'estado': estado,
                'libros': libros,
                'asientos': {'conf': conf, 'borr': borr},
                'liqs': liqs,
            }

    # DB info for backup section
    uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_path = uri.replace('sqlite:///', '')
    if not os.path.isabs(db_path):
        db_path = os.path.join(current_app.root_path, db_path)
    try:
        st = os.stat(db_path)
        db_size_mb = round(st.st_size / 1024 / 1024, 2)
        from datetime import datetime as dt
        db_modified = dt.fromtimestamp(st.st_mtime).strftime('%d/%m/%Y %H:%M')
    except Exception:
        db_size_mb = None
        db_modified = None

    # Inter-company loans: all loans between active companies
    interempresa = (Prestamo.query
        .filter(Prestamo.empresa_id.in_(ids),
                Prestamo.empresa_relacionada_id.in_(ids),
                Prestamo.activo == True)
        .order_by(Prestamo.empresa_id, Prestamo.tipo)
        .all())

    return render_template('consolidado.html',
        empresas=empresas, meses=meses,
        desde_mes=desde_mes, hasta_mes=hasta_mes,
        celda_data=celda_data,
        sin_respaldo=sin_respaldo,
        db_size_mb=db_size_mb, db_modified=db_modified,
        interempresa=interempresa)


@bp.route('/consolidado/financiero')
def consolidado_financiero():
    from models import LineaAsiento, Cuenta

    empresas = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()
    ids = [e.id for e in empresas]

    hoy = date.today()
    desde_str = request.args.get('desde', f'{hoy.year}-01-01')
    hasta_str = request.args.get('hasta', f'{hoy.year}-12-31')
    try:
        desde_d = date.fromisoformat(desde_str)
    except Exception:
        desde_d = date(hoy.year, 1, 1)
    try:
        hasta_d = date.fromisoformat(hasta_str)
    except Exception:
        hasta_d = date(hoy.year, 12, 31)

    # All leaf accounts for these companies
    cuentas_q = Cuenta.query.filter(
        Cuenta.empresa_id.in_(ids), Cuenta.es_titulo == False, Cuenta.activa == True
    ).all()
    cuenta_info = {c.id: c for c in cuentas_q}

    # Aggregate lineas per (empresa_id, cuenta_id) for confirmed asientos in period
    rows = (db.session.query(
            Asiento.empresa_id,
            LineaAsiento.cuenta_id,
            func.sum(LineaAsiento.debe).label('td'),
            func.sum(LineaAsiento.haber).label('th'),
        )
        .join(LineaAsiento, LineaAsiento.asiento_id == Asiento.id)
        .filter(
            Asiento.empresa_id.in_(ids),
            Asiento.estado == 'CONFIRMADO',
            Asiento.fecha >= desde_d,
            Asiento.fecha <= hasta_d,
        )
        .group_by(Asiento.empresa_id, LineaAsiento.cuenta_id)
        .all())

    # saldo per empresa per cuenta
    empresa_saldos = {e.id: {} for e in empresas}
    for r in rows:
        c = cuenta_info.get(r.cuenta_id)
        if not c:
            continue
        debe = float(r.td or 0)
        haber = float(r.th or 0)
        saldo = (debe - haber) if c.naturaleza == 'DEUDORA' else (haber - debe)
        empresa_saldos[r.empresa_id][r.cuenta_id] = saldo

    TIPOS = ['ACTIVO', 'PASIVO', 'PATRIMONIO', 'INGRESO', 'GASTO']

    # Per empresa totals
    empresa_totales = {}
    for e in empresas:
        tots = {t: 0.0 for t in TIPOS}
        for cid, saldo in empresa_saldos[e.id].items():
            c = cuenta_info.get(cid)
            if c and c.tipo in tots:
                tots[c.tipo] += saldo
        tots['resultado'] = tots['INGRESO'] - tots['GASTO']
        empresa_totales[e.id] = tots

    # Group breakdown for P&L by first 2 code segments (e.g. "5.2")
    grupos = {}
    for e in empresas:
        peso = (e.participacion_ecox or 0) / 100.0
        for cid, saldo in empresa_saldos[e.id].items():
            c = cuenta_info.get(cid)
            if not c or c.tipo not in ('INGRESO', 'GASTO'):
                continue
            parts = c.codigo.split('.')
            prefix = '.'.join(parts[:2]) if len(parts) >= 2 else c.codigo
            key = (c.tipo, prefix)
            if key not in grupos:
                grupos[key] = {'tipo': c.tipo, 'prefix': prefix, 'nombre': prefix, 'total': 0.0, 'ecox': 0.0}
            grupos[key]['total'] += saldo
            grupos[key]['ecox'] += saldo * peso

    # Try to find group names from titulo accounts
    titulos = Cuenta.query.filter(
        Cuenta.empresa_id.in_(ids), Cuenta.es_titulo == True
    ).order_by(Cuenta.empresa_id, Cuenta.codigo).all()
    titulo_nombres = {}
    for t in titulos:
        parts = t.codigo.split('.')
        prefix = '.'.join(parts[:2]) if len(parts) >= 2 else t.codigo
        if prefix not in titulo_nombres:
            titulo_nombres[prefix] = t.nombre
    for key, g in grupos.items():
        if g['nombre'] == g['prefix'] and g['prefix'] in titulo_nombres:
            g['nombre'] = titulo_nombres[g['prefix']]

    grupos_ingresos = sorted([g for g in grupos.values() if g['tipo'] == 'INGRESO'], key=lambda g: g['prefix'])
    grupos_gastos   = sorted([g for g in grupos.values() if g['tipo'] == 'GASTO'],   key=lambda g: g['prefix'])

    # Total view (simple sum)
    total = {t: sum(empresa_totales[e.id][t] for e in empresas) for t in TIPOS}
    total['resultado'] = total['INGRESO'] - total['GASTO']

    # Ecox view (weighted sum)
    ecox = {t: 0.0 for t in TIPOS}
    for e in empresas:
        peso = (e.participacion_ecox or 0) / 100.0
        for t in TIPOS:
            ecox[t] += empresa_totales[e.id][t] * peso
    ecox['resultado'] = ecox['INGRESO'] - ecox['GASTO']

    # Empresas with ecox participation
    empresas_ecox = [e for e in empresas if e.participacion_ecox]

    return render_template('consolidado_financiero.html',
        empresas=empresas,
        empresas_ecox=empresas_ecox,
        desde_d=desde_d, hasta_d=hasta_d,
        empresa_totales=empresa_totales,
        total=total,
        ecox=ecox,
        grupos_ingresos=grupos_ingresos,
        grupos_gastos=grupos_gastos,
    )


def _get_db_path():
    uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_path = uri.replace('sqlite:///', '')
    if not os.path.isabs(db_path):
        db_path = os.path.join(current_app.root_path, db_path)
    return db_path


@bp.route('/adjunto/<filename>')
def servir_adjunto(filename):
    from flask import abort
    # Prevent path traversal
    if '/' in filename or '\\' in filename or '..' in filename:
        abort(404)
    folder = current_app.config['UPLOAD_FOLDER']
    path = os.path.join(folder, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


@bp.route('/backup')
def backup():
    db_path = _get_db_path()
    nombre = f'contabilidad_backup_{date.today().isoformat()}.db'
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    con = sqlite3.connect(db_path)
    con.execute(f"VACUUM INTO '{tmp.name}'")
    con.close()
    return send_file(tmp.name, as_attachment=True, download_name=nombre)


@bp.route('/db', methods=['GET'])
def db_manager():
    db_path = _get_db_path()
    try:
        st = os.stat(db_path)
        db_size_mb = round(st.st_size / 1024 / 1024, 2)
        db_modified = datetime.fromtimestamp(st.st_mtime).strftime('%d/%m/%Y %H:%M')
    except Exception:
        db_size_mb = None
        db_modified = None

    # Lista de respaldos automáticos guardados en la carpeta backups/
    backups_dir = os.path.join(current_app.root_path, 'backups')
    respaldos = []
    if os.path.isdir(backups_dir):
        for f in sorted(os.listdir(backups_dir), reverse=True):
            if f.endswith('.db'):
                fp = os.path.join(backups_dir, f)
                st2 = os.stat(fp)
                respaldos.append({
                    'nombre': f,
                    'size_mb': round(st2.st_size / 1024 / 1024, 2),
                    'fecha': datetime.fromtimestamp(st2.st_mtime).strftime('%d/%m/%Y %H:%M'),
                })

    return render_template('db.html',
        db_size_mb=db_size_mb, db_modified=db_modified,
        respaldos=respaldos)


@bp.route('/db/restaurar', methods=['POST'])
def db_restaurar():
    archivo = request.files.get('db_file')
    if not archivo or not archivo.filename:
        flash('Debes seleccionar un archivo .db para restaurar.', 'warning')
        return redirect(url_for('main.db_manager'))

    # Guardar el archivo subido en un temporal
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    archivo.save(tmp.name)
    tmp.close()

    # Validar que es un SQLite válido
    try:
        con = sqlite3.connect(tmp.name)
        con.execute('SELECT name FROM sqlite_master LIMIT 1')
        con.close()
    except Exception:
        os.unlink(tmp.name)
        flash('El archivo no es una base de datos SQLite válida.', 'danger')
        return redirect(url_for('main.db_manager'))

    db_path = _get_db_path()

    # Crear respaldo automático de la BD actual
    backups_dir = os.path.join(current_app.root_path, 'backups')
    os.makedirs(backups_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_nombre = f'contabilidad_antes_restauracion_{ts}.db'
    backup_path = os.path.join(backups_dir, backup_nombre)
    try:
        con = sqlite3.connect(db_path)
        con.execute(f"VACUUM INTO '{backup_path}'")
        con.close()
    except Exception as e:
        os.unlink(tmp.name)
        flash(f'No se pudo crear el respaldo automático: {e}', 'danger')
        return redirect(url_for('main.db_manager'))

    # Cerrar todas las conexiones de SQLAlchemy y reemplazar la BD
    db.engine.dispose()
    shutil.copy2(tmp.name, db_path)
    os.unlink(tmp.name)

    flash(
        f'Base de datos restaurada exitosamente. Respaldo automático guardado como "{backup_nombre}".',
        'success'
    )
    return redirect(url_for('main.index'))


@bp.route('/db/descargar-respaldo/<nombre>')
def db_descargar_respaldo(nombre):
    # Solo permitir nombres de archivo sin rutas
    if '/' in nombre or '\\' in nombre or '..' in nombre:
        flash('Nombre de archivo inválido.', 'danger')
        return redirect(url_for('main.db_manager'))
    backups_dir = os.path.join(current_app.root_path, 'backups')
    path = os.path.join(backups_dir, nombre)
    if not os.path.isfile(path):
        flash('Respaldo no encontrado.', 'danger')
        return redirect(url_for('main.db_manager'))
    return send_file(path, as_attachment=True, download_name=nombre)
