import os
import sqlite3
import tempfile
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, send_file, current_app, request, flash, redirect, url_for
from models import db, Empresa, Asiento, ArchivoImportado, DocumentoSII, MovimientoBanco, Liquidacion, Prestamo, LineaAsiento, Contraparte, Cuenta
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

    # F29 declaraciones by (eid, periodo)
    from models import DeclaracionF29, DeclaracionF22
    rows_f29 = (db.session.query(DeclaracionF29.empresa_id, DeclaracionF29.periodo)
                .filter(DeclaracionF29.empresa_id.in_(ids),
                        DeclaracionF29.periodo >= desde_mes,
                        DeclaracionF29.periodo <= hasta_mes)
                .all())
    f29_loaded = {(r.empresa_id, r.periodo) for r in rows_f29}

    # F22 declaraciones by (eid, anio) — se presentan en abril del AT
    anios_visibles = {int(m[:4]) for m in meses if m.endswith('-04')}
    rows_f22 = (db.session.query(DeclaracionF22.empresa_id, DeclaracionF22.anio)
                .filter(DeclaracionF22.empresa_id.in_(ids),
                        DeclaracionF22.anio.in_(anios_visibles) if anios_visibles else False)
                .all()) if anios_visibles else []
    f22_loaded = {(r.empresa_id, r.anio) for r in rows_f22}

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

            es_abril = mes.endswith('-04')
            anio_at = int(mes[:4]) if es_abril else None
            celda_data[e.id][mes] = {
                'estado': estado,
                'libros': libros,
                'asientos': {'conf': conf, 'borr': borr},
                'liqs': liqs,
                'f29': (e.id, mes) in f29_loaded,
                'f22_anio': anio_at,
                'f22': es_abril and (e.id, anio_at) in f22_loaded,
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

    # TC por empresa-mes: solo indica si se subió o no la cartola TARJETA.
    # Verde si hay archivo TARJETA importado para ese (empresa, periodo); gris si no.
    tc_status = {}  # (eid, 'YYYY-MM') -> 'ok' (cargada) | 'pend' (no cargada)
    for emp in empresas:
        if not getattr(emp, 'tc_activa', False):
            continue
        for m in meses:
            cargada = (emp.id, m, 'TARJETA') in archivos_loaded
            tc_status[(emp.id, m)] = 'ok' if cargada else 'pend'

    return render_template('consolidado.html',
        empresas=empresas, meses=meses,
        desde_mes=desde_mes, hasta_mes=hasta_mes,
        celda_data=celda_data,
        sin_respaldo=sin_respaldo,
        db_size_mb=db_size_mb, db_modified=db_modified,
        interempresa=interempresa,
        tc_status=tc_status)


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


@bp.route('/consolidado/interempresa')
def consolidado_interempresa():
    """Posiciones interempresa derivadas de los auxiliares (contraparte) de cada asiento.

    Cuando una línea de asiento de la empresa A tiene como contraparte a un contacto
    cuyo RUT coincide con el RUT de otra empresa B del sistema, se considera una
    posición interempresa. Se acumula DEBE/HABER y se compara con la posición espejo
    desde B hacia A para detectar descuadres.
    """
    empresas = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()
    rut_to_empresa = {e.rut: e for e in empresas if e.rut}

    # Líneas con contraparte cuyo RUT corresponde a otra empresa registrada.
    # Solo cuentas operativas de cobrar/pagar (excluye patrimonio, resultados,
    # caja/banco, IVA/PPM, activo fijo, e inversiones LP en empresas relacionadas
    # que son equity stakes, no posiciones interempresa).
    EXCLUIR = ('1.1.01', '1.1.02', '1.1.05', '1.1.06', '1.1.15')
    rows = (db.session.query(
                Asiento.empresa_id.label('empresa_id'),
                Contraparte.rut.label('rut_b'),
                func.sum(LineaAsiento.debe).label('debe'),
                func.sum(LineaAsiento.haber).label('haber'),
                func.count(LineaAsiento.id).label('n'),
            )
            .join(Asiento, LineaAsiento.asiento_id == Asiento.id)
            .join(Contraparte, LineaAsiento.contraparte_id == Contraparte.id)
            .join(Cuenta, LineaAsiento.cuenta_id == Cuenta.id)
            .filter(Asiento.estado == 'CONFIRMADO')
            .filter(Contraparte.rut.in_(list(rut_to_empresa.keys())))
            .filter(Cuenta.tipo.in_(['ACTIVO', 'PASIVO']))
            .filter(~Cuenta.codigo.like('1.2.%'))   # activo fijo
            .filter(~Cuenta.codigo.like('1.3.%'))   # inversiones LP (equity)
            .filter(~Cuenta.codigo.in_(EXCLUIR))
            .group_by(Asiento.empresa_id, Contraparte.rut)
            .all())

    # Map (a_id, b_id) → {debe, haber, saldo, n}.
    # saldo > 0 → A tiene cuenta por COBRAR neta a B (cargó más de lo abonado).
    # saldo < 0 → A tiene cuenta por PAGAR neta a B.
    legs = {}
    for r in rows:
        b = rut_to_empresa.get(r.rut_b)
        if not b or b.id == r.empresa_id:
            continue
        legs[(r.empresa_id, b.id)] = {
            'debe': float(r.debe or 0),
            'haber': float(r.haber or 0),
            'saldo': float((r.debe or 0) - (r.haber or 0)),
            'n': int(r.n or 0),
        }

    # Construir pares únicos {A, B}
    emp_by_id = {e.id: e for e in empresas}
    seen = set()
    pares = []
    for (a_id, b_id), leg in legs.items():
        key = tuple(sorted([a_id, b_id]))
        if key in seen:
            continue
        seen.add(key)
        ka_id, kb_id = key
        ka = emp_by_id.get(ka_id)
        kb = emp_by_id.get(kb_id)
        leg_ab = legs.get((ka_id, kb_id))
        leg_ba = legs.get((kb_id, ka_id))
        saldo_ab = leg_ab['saldo'] if leg_ab else 0.0
        saldo_ba = leg_ba['saldo'] if leg_ba else 0.0
        # Reciprocidad esperada: lo que A tiene como CxC con B debería igualar lo que B tiene como CxP con A.
        # Es decir: saldo_ab (A→B) + saldo_ba (B→A) ≈ 0 si las dos contabilidades son consistentes.
        # Si no hay leg espejo, descuadre = magnitud del leg presente.
        diff = saldo_ab + saldo_ba
        if leg_ab is None or leg_ba is None:
            status = 'missing'
        elif abs(diff) < 1.0:
            status = 'ok'
        else:
            status = 'diff'
        pares.append({
            'a': ka, 'b': kb,
            'leg_ab': leg_ab, 'leg_ba': leg_ba,
            'saldo_ab': saldo_ab, 'saldo_ba': saldo_ba,
            'diff': diff, 'status': status,
        })

    # Ordenar por |saldo| desc
    pares.sort(key=lambda p: -max(abs(p['saldo_ab']), abs(p['saldo_ba'])))

    # ── Sección 2: contactos relevantes (no-empresa) con presencia en ≥2 empresas ──
    rows_cp = (db.session.query(
                    Contraparte.id.label('cp_id'),
                    Contraparte.razon_social.label('nombre'),
                    Contraparte.rut.label('rut'),
                    Asiento.empresa_id.label('empresa_id'),
                    func.sum(LineaAsiento.debe).label('debe'),
                    func.sum(LineaAsiento.haber).label('haber'),
                    func.count(LineaAsiento.id).label('n'),
                )
                .join(LineaAsiento, LineaAsiento.contraparte_id == Contraparte.id)
                .join(Asiento, LineaAsiento.asiento_id == Asiento.id)
                .join(Cuenta, LineaAsiento.cuenta_id == Cuenta.id)
                .filter(Asiento.estado == 'CONFIRMADO')
                .filter(Cuenta.tipo.in_(['ACTIVO', 'PASIVO']))
                .group_by(Contraparte.id, Asiento.empresa_id)
                .all())

    rut_emp_set = set(rut_to_empresa.keys())
    contactos_acc = {}
    for r in rows_cp:
        if r.rut and r.rut in rut_emp_set:
            continue  # ya está en sección empresa-empresa
        c = contactos_acc.setdefault(r.cp_id, {
            'cp_id': r.cp_id, 'nombre': r.nombre, 'rut': r.rut,
            'por_empresa': {}, 'total_saldo': 0.0,
        })
        saldo = float((r.debe or 0) - (r.haber or 0))
        c['por_empresa'][r.empresa_id] = {
            'debe': float(r.debe or 0),
            'haber': float(r.haber or 0),
            'saldo': saldo,
            'n': int(r.n or 0),
        }
        c['total_saldo'] += saldo

    contactos_multi = [c for c in contactos_acc.values() if len(c['por_empresa']) >= 2]
    contactos_multi.sort(key=lambda c: -abs(c['total_saldo']))

    return render_template('consolidado_interempresa.html',
                           empresas=empresas, pares=pares,
                           contactos_multi=contactos_multi)


def _get_db_path():
    uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_path = uri.replace('sqlite:///', '')
    if not os.path.isabs(db_path):
        db_path = os.path.join(current_app.root_path, db_path)
    return db_path


@bp.route('/adjunto/<path:filepath>')
def servir_adjunto(filepath):
    from flask import abort
    import posixpath
    # Prevent path traversal
    if '..' in filepath:
        abort(404)
    folder = current_app.config['UPLOAD_FOLDER']
    # Resolve and verify the file is inside uploads/
    safe = os.path.realpath(os.path.join(folder, filepath))
    if not safe.startswith(os.path.realpath(folder)):
        abort(403)
    if not os.path.isfile(safe):
        abort(404)
    return send_file(safe)


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


@bp.route('/backup/documentos')
def backup_documentos():
    """Descarga un ZIP con los archivos de uploads/ filtrado por año."""
    import zipfile
    anio = request.args.get('anio', date.today().year, type=int)
    upload_folder = current_app.config['UPLOAD_FOLDER']
    if not os.path.isdir(upload_folder):
        from flask import abort
        abort(404)

    buf = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    buf.close()
    str_anio = str(anio)
    added = 0
    with zipfile.ZipFile(buf.name, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(upload_folder):
            # Solo incluir archivos cuya ruta contiene el año
            rel_root = os.path.relpath(root, upload_folder)
            if str_anio not in rel_root and str_anio not in root:
                continue
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.join(rel_root, fname)
                zf.write(fpath, arcname)
                added += 1

    if added == 0:
        from flask import flash, redirect
        flash(f'No hay documentos del año {anio}.', 'warning')
        return redirect(url_for('main.db_manager'))

    nombre_zip = f'documentos_{anio}_{date.today().isoformat()}.zip'
    return send_file(buf.name, as_attachment=True, download_name=nombre_zip)


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

    return render_template('db.html', db_size_mb=db_size_mb, db_modified=db_modified,
                           anio_actual=date.today().year)
