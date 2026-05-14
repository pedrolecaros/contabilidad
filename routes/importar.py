import concurrent.futures
import hashlib
import io
import time
import threading
import uuid
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app
from sqlalchemy import func
from models import db, Empresa, ArchivoImportado, DocumentoSII, MovimientoBanco
from importers import libro_compras, libro_ventas, libro_honorarios, cartola

bp = Blueprint('importar', __name__)

# ── Background job store ──────────────────────────────────────────────────────
_JOBS: dict = {}
_JOBS_LOCK  = threading.Lock()

def _job_set(job_id, **kw):
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)

def _run_sii_job(app, job_id, eid, rut, clave_sii, tipo, periodo):
    """Ejecuta la descarga SII en un thread de background."""
    from werkzeug.datastructures import FileStorage

    def up(pct, msg):
        _job_set(job_id, pct=pct, message=msg)

    try:
        up(5,  'Iniciando navegador…')
        from importers.sii_scraper import descargar, SIILoginError, SIIDownloadError
        up(10, 'Conectando al portal SII…')
        contenido = descargar(rut, clave_sii, periodo, tipo)

        up(75, 'Verificando duplicados…')
        sha    = hashlib.sha256(contenido).hexdigest()
        ext    = 'csv' if contenido[:3] != b'PK\x03' else 'xlsx'
        nombre = f'sii_{tipo}_{periodo}.{ext}'

        with app.app_context():
            existente = ArchivoImportado.query.filter_by(empresa_id=eid, sha256=sha, tipo=tipo.upper()).first()
            if existente:
                _job_set(job_id, status='done', pct=100, message='Ya importado',
                         result={'ok': True, 'tipo': tipo, 'importados': 0, 'errores': [],
                                 'nombre': nombre,
                                 'aviso': f'El período {periodo} ya estaba importado ({existente.fecha_importacion.strftime("%d/%m/%Y")}).'})
                return

            up(80, 'Procesando registros…')
            fs = FileStorage(stream=io.BytesIO(contenido), filename=nombre,
                             content_type='application/octet-stream')
            if tipo == 'compras':
                resultado = libro_compras.importar(fs, eid)
            elif tipo == 'ventas':
                resultado = libro_ventas.importar(fs, eid)
            elif tipo == 'honorarios':
                resultado = libro_honorarios.importar(fs, eid)
            else:
                raise ValueError(f'Tipo desconocido: {tipo}')

            up(95, 'Guardando…')
            periodo_det = _periodo_docs(eid, tipo.upper(), datetime.now())
            db.session.add(ArchivoImportado(
                empresa_id=eid, tipo=tipo.upper(), nombre_archivo=nombre,
                sha256=sha, ndocs=resultado.get('importados', 0),
                periodo=periodo_det or periodo,
            ))
            db.session.commit()

            _job_set(job_id, status='done', pct=100, message='Completado',
                     result={'ok': True, 'tipo': tipo,
                             'importados': resultado.get('importados', 0),
                             'errores': resultado.get('errores', []),
                             'nombre': nombre})

    except Exception as e:
        _job_set(job_id, status='error', pct=100, message=str(e),
                 result={'ok': False, 'tipo': tipo, 'error': str(e)})

ALLOWED = {'csv', 'xls', 'xlsx'}


def _ext_ok(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED


def _sha256(file_storage):
    file_storage.stream.seek(0)
    h = hashlib.sha256(file_storage.stream.read()).hexdigest()
    file_storage.stream.seek(0)
    return h


def _periodo_docs(empresa_id, tipo, after_dt):
    """Derives YYYY-MM from most recent docs imported after after_dt."""
    if tipo == 'BANCO':
        mov = (MovimientoBanco.query
               .filter_by(empresa_id=empresa_id)
               .order_by(MovimientoBanco.id.desc())
               .first())
        if mov and mov.fecha:
            return mov.fecha.strftime('%Y-%m')
    else:
        map_tipo = {'COMPRAS': 'COMPRAS', 'VENTAS': 'VENTAS', 'HONORARIOS': 'HONORARIOS'}
        tipo_libro = map_tipo.get(tipo, tipo)
        doc = (DocumentoSII.query
               .filter_by(empresa_id=empresa_id, tipo_libro=tipo_libro)
               .order_by(DocumentoSII.id.desc())
               .first())
        if doc and doc.fecha:
            return doc.fecha.strftime('%Y-%m')
    return None


MESES_ES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
            'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


def _build_grilla(archivos, ano_actual):
    from collections import defaultdict

    def period_key(a):
        p = a.periodo or a.fecha_importacion.strftime('%Y-%m')
        return p if int(p[:4]) == ano_actual else p[:4]

    raw = defaultdict(lambda: defaultdict(list))
    for a in archivos:
        raw[period_key(a)][a.tipo].append(a)

    def sort_key(k):
        return (int(k[:4]), int(k[5:7])) if len(k) == 7 else (int(k), 0)

    grilla = []
    for k in sorted(raw.keys(), key=sort_key, reverse=True):
        row = {'key': k, 'label': (f"{MESES_ES[int(k[5:7])]} {k[:4]}" if len(k) == 7 else k)}
        for tipo in ('BANCO', 'COMPRAS', 'VENTAS', 'HONORARIOS'):
            lst = raw[k].get(tipo, [])
            row[tipo] = {'ndocs': sum(a.ndocs or 0 for a in lst), 'n': len(lst)} if lst else None
        grilla.append(row)
    return grilla


@bp.route('/empresa/<int:eid>/importar')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    archivos = (ArchivoImportado.query
                .filter_by(empresa_id=eid)
                .order_by(ArchivoImportado.fecha_importacion.desc())
                .all())
    from datetime import date
    hoy = date.today()
    if hoy.month == 1:
        mes_anterior = date(hoy.year - 1, 12, 1)
    else:
        mes_anterior = date(hoy.year, hoy.month - 1, 1)

    # Contar pendientes por archivo_origen (para mostrar cuántos se pueden revertir)
    doc_pend = dict(
        db.session.query(DocumentoSII.archivo_origen, func.count(DocumentoSII.id))
        .filter_by(empresa_id=eid, procesado=False)
        .group_by(DocumentoSII.archivo_origen).all()
    )
    mov_pend = dict(
        db.session.query(MovimientoBanco.archivo_origen, func.count(MovimientoBanco.id))
        .filter_by(empresa_id=eid, procesado=False)
        .group_by(MovimientoBanco.archivo_origen).all()
    )
    def _pendientes(a):
        if a.tipo == 'BANCO':
            return mov_pend.get(a.nombre_archivo, 0)
        return doc_pend.get(a.nombre_archivo, 0)

    return render_template('importar/index.html', empresa=empresa, archivos=archivos,
                           pendientes_fn=_pendientes,
                           hoy=hoy.isoformat(),
                           mes_anterior=mes_anterior.strftime('%Y-%m'))


@bp.route('/empresa/<int:eid>/importar/<int:aid>/revertir', methods=['POST'])
def revertir(eid, aid):
    """Elimina los documentos/movimientos pendientes (no procesados) de un archivo importado."""
    archivo = ArchivoImportado.query.filter_by(id=aid, empresa_id=eid).first_or_404()

    if archivo.tipo == 'BANCO':
        pendientes = MovimientoBanco.query.filter_by(
            empresa_id=eid, archivo_origen=archivo.nombre_archivo, procesado=False).all()
        procesados = MovimientoBanco.query.filter_by(
            empresa_id=eid, archivo_origen=archivo.nombre_archivo, procesado=True).count()
    else:
        tipo_libro = archivo.tipo  # COMPRAS, VENTAS, HONORARIOS
        pendientes = DocumentoSII.query.filter_by(
            empresa_id=eid, archivo_origen=archivo.nombre_archivo,
            tipo_libro=tipo_libro, procesado=False).all()
        procesados = DocumentoSII.query.filter_by(
            empresa_id=eid, archivo_origen=archivo.nombre_archivo,
            tipo_libro=tipo_libro, procesado=True).count()

    n_eliminados = len(pendientes)
    for doc in pendientes:
        db.session.delete(doc)

    db.session.delete(archivo)
    db.session.commit()

    if procesados:
        flash(
            f'Se eliminaron {n_eliminados} registro(s) pendiente(s) de "{archivo.nombre_archivo}". '
            f'{procesados} registro(s) ya contabilizados no fueron eliminados.',
            'warning'
        )
    else:
        flash(
            f'Importación revertida: {n_eliminados} registro(s) eliminado(s) de "{archivo.nombre_archivo}".',
            'success'
        )
    return redirect(url_for('importar.index', eid=eid))


@bp.route('/empresa/<int:eid>/importar/<tipo>', methods=['POST'])
def subir(eid, tipo):
    empresa = Empresa.query.get_or_404(eid)

    if 'archivo' not in request.files or request.files['archivo'].filename == '':
        flash('Seleccione un archivo', 'danger')
        return redirect(url_for('importar.index', eid=eid))

    archivo = request.files['archivo']
    if not _ext_ok(archivo.filename):
        flash('Formato no válido. Use CSV, XLS o XLSX', 'danger')
        return redirect(url_for('importar.index', eid=eid))

    sha = _sha256(archivo)

    # Duplicate check
    existente = ArchivoImportado.query.filter_by(empresa_id=eid, sha256=sha).first()
    if existente:
        flash(
            f'Este archivo ya fue importado el '
            f'{existente.fecha_importacion.strftime("%d/%m/%Y %H:%M")} '
            f'({existente.ndocs} registros). No se volvió a importar.',
            'warning'
        )
        return redirect(url_for('importar.index', eid=eid))

    try:
        tipo_upper = tipo.upper() if tipo != 'banco' else 'BANCO'
        if tipo == 'compras':
            resultado = libro_compras.importar(archivo, eid)
            tipo_upper = 'COMPRAS'
        elif tipo == 'ventas':
            resultado = libro_ventas.importar(archivo, eid)
            tipo_upper = 'VENTAS'
        elif tipo == 'honorarios':
            resultado = libro_honorarios.importar(archivo, eid)
            tipo_upper = 'HONORARIOS'
        elif tipo == 'banco':
            banco = request.form.get('banco', '').strip()
            cuenta_bancaria = request.form.get('cuenta_bancaria', '').strip()
            resultado = cartola.importar(archivo, eid, banco, cuenta_bancaria)
            tipo_upper = 'BANCO'
        else:
            flash('Tipo de importación desconocido', 'danger')
            return redirect(url_for('importar.index', eid=eid))
    except Exception as e:
        flash(f'Error al procesar archivo: {e}', 'danger')
        return redirect(url_for('importar.index', eid=eid))

    # Register the imported file
    periodo = _periodo_docs(eid, tipo_upper, datetime.now())
    registro = ArchivoImportado(
        empresa_id=eid,
        tipo=tipo_upper,
        nombre_archivo=archivo.filename,
        sha256=sha,
        ndocs=resultado.get('importados', 0),
        periodo=periodo,
        banco=request.form.get('banco', '').strip() if tipo == 'banco' else None,
        cuenta_bancaria=request.form.get('cuenta_bancaria', '').strip() if tipo == 'banco' else None,
    )
    db.session.add(registro)
    db.session.commit()

    return jsonify({
        'ok': True,
        'tipo': tipo,
        'importados': resultado.get('importados', 0),
        'errores': resultado.get('errores', []),
        'nombre': archivo.filename,
    })


def _run_sii_batch_job(app, job_id, eid, rut, clave_sii, tipos, periodo):
    """Descarga compras+ventas+honorarios en una sola sesión Playwright."""
    from werkzeug.datastructures import FileStorage

    def up(pct, msg, current_tipo=None):
        kw = dict(pct=pct, message=msg)
        if current_tipo is not None:
            kw['current_tipo'] = current_tipo
        _job_set(job_id, **kw)

    try:
        from importers.sii_scraper import descargar_lote, SIILoginError, SIIDownloadError

        # Pre-chequeo: filtrar tipos ya importados
        with app.app_context():
            tipos_a_bajar, ya_importados = [], {}
            for tipo in tipos:
                ya = (ArchivoImportado.query
                      .filter_by(empresa_id=eid, tipo=tipo.upper())
                      .filter(ArchivoImportado.periodo == periodo).first())
                if ya:
                    ya_importados[tipo] = ya
                else:
                    tipos_a_bajar.append(tipo)

        if not tipos_a_bajar:
            results = {t: {'ok': True, 'tipo': t, 'importados': 0, 'errores': [],
                           'aviso': f'Ya importado ({ya.fecha_importacion.strftime("%d/%m/%Y")}).',
                           'nombre': ''}
                       for t, ya in ya_importados.items()}
            _job_set(job_id, status='done', pct=100, message='Ya importados', results=results)
            return

        up(5, 'Iniciando navegador…')
        contenidos = descargar_lote(
            rut, clave_sii, periodo, tipos_a_bajar,
            progress_cb=lambda pct, msg: up(pct, msg),
        )

        results = {}
        for tipo, ya in ya_importados.items():
            results[tipo] = {'ok': True, 'tipo': tipo, 'importados': 0, 'errores': [], 'nombre': '',
                             'aviso': f'Ya importado ({ya.fecha_importacion.strftime("%d/%m/%Y")}).'}

        up(85, 'Procesando registros…')
        with app.app_context():
            for i, tipo in enumerate(tipos_a_bajar):
                up(85 + int((i / len(tipos_a_bajar)) * 10), f'Guardando {tipo}…', tipo)
                contenido = contenidos.get(tipo)

                if isinstance(contenido, Exception):
                    results[tipo] = {'ok': False, 'tipo': tipo, 'error': str(contenido)}
                    continue

                sha = hashlib.sha256(contenido).hexdigest()
                if tipo == 'honorarios':
                    ext = 'xls'
                elif contenido[:3] == b'PK\x03':
                    ext = 'xlsx'
                else:
                    ext = 'csv'
                nombre = f'sii_{tipo}_{periodo}.{ext}'

                existente = ArchivoImportado.query.filter_by(empresa_id=eid, sha256=sha, tipo=tipo.upper()).first()
                if existente:
                    results[tipo] = {'ok': True, 'tipo': tipo, 'importados': 0, 'errores': [], 'nombre': nombre,
                                     'aviso': f'Ya importado ({existente.fecha_importacion.strftime("%d/%m/%Y")}).'}
                    continue

                try:
                    fs = FileStorage(stream=io.BytesIO(contenido), filename=nombre,
                                     content_type='application/octet-stream')
                    if tipo == 'compras':
                        resultado = libro_compras.importar(fs, eid)
                    elif tipo == 'ventas':
                        resultado = libro_ventas.importar(fs, eid)
                    elif tipo == 'honorarios':
                        resultado = libro_honorarios.importar(fs, eid)
                    else:
                        raise ValueError(f'Tipo desconocido: {tipo}')

                    db.session.add(ArchivoImportado(
                        empresa_id=eid, tipo=tipo.upper(), nombre_archivo=nombre,
                        sha256=sha, ndocs=resultado.get('importados', 0),
                        periodo=periodo,
                    ))
                    db.session.commit()
                    results[tipo] = {'ok': True, 'tipo': tipo,
                                     'importados': resultado.get('importados', 0),
                                     'errores': resultado.get('errores', []),
                                     'nombre': nombre}
                except Exception as e:
                    results[tipo] = {'ok': False, 'tipo': tipo, 'error': str(e)}

        _job_set(job_id, status='done', pct=100, message='Completado', results=results)

    except Exception as e:
        _job_set(job_id, status='error', pct=100, message=str(e),
                 results={t: {'ok': False, 'tipo': t, 'error': str(e)} for t in tipos})


@bp.route('/empresa/<int:eid>/importar/sii-start-batch', methods=['POST'])
def sii_start_batch(eid):
    """Inicia descarga de todos los libros SII en una sola sesión Playwright."""
    empresa = Empresa.query.get_or_404(eid)
    tipos_str = request.form.get('tipos', 'compras,ventas,honorarios')
    periodo   = request.form.get('periodo', '')

    if not empresa.clave_sii:
        return jsonify({'ok': False, 'error': 'Sin clave SII — configúrala en la ficha de la empresa.'})
    if not periodo:
        return jsonify({'ok': False, 'error': 'Falta el período'})

    tipos = [t.strip() for t in tipos_str.split(',') if t.strip()]

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _JOBS[job_id] = {'status': 'running', 'pct': 0, 'message': 'En cola…',
                         'tipos': tipos, 'periodo': periodo, 'results': None}

    app = current_app._get_current_object()
    t = threading.Thread(
        target=_run_sii_batch_job,
        args=(app, job_id, eid, empresa.rut, empresa.clave_sii, tipos, periodo),
        daemon=True,
    )
    t.start()
    return jsonify({'ok': True, 'job_id': job_id, 'tipos': tipos, 'periodo': periodo})


@bp.route('/empresa/<int:eid>/importar/sii-start', methods=['POST'])
def sii_start(eid):
    """Inicia descarga SII en background, devuelve job_id inmediatamente."""
    empresa = Empresa.query.get_or_404(eid)
    tipo    = request.form.get('tipo', '')
    periodo = request.form.get('periodo', '')

    if not empresa.clave_sii:
        return jsonify({'ok': False, 'error': 'Sin clave SII — configúrala en la ficha de la empresa.'})
    if not tipo or not periodo:
        return jsonify({'ok': False, 'error': 'Faltan tipo o período'})

    # Chequeo rápido antes de lanzar Playwright: ¿ya existe ese período?
    ya_importado = (ArchivoImportado.query
                    .filter_by(empresa_id=eid, tipo=tipo.upper())
                    .filter(ArchivoImportado.periodo == periodo)
                    .first())
    if ya_importado:
        return jsonify({'ok': True, 'job_id': None, 'tipo': tipo, 'periodo': periodo,
                        'done': True,
                        'result': {'ok': True, 'tipo': tipo, 'importados': 0, 'errores': [],
                                   'nombre': '',
                                   'aviso': f'El período {periodo} ya estaba importado ({ya_importado.fecha_importacion.strftime("%d/%m/%Y")}).'}})

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _JOBS[job_id] = {'status': 'running', 'pct': 0, 'message': 'En cola…',
                         'tipo': tipo, 'periodo': periodo, 'result': None}

    app = current_app._get_current_object()
    t = threading.Thread(
        target=_run_sii_job,
        args=(app, job_id, eid, empresa.rut, empresa.clave_sii, tipo, periodo),
        daemon=True,
    )
    t.start()
    return jsonify({'ok': True, 'job_id': job_id, 'tipo': tipo, 'periodo': periodo})


@bp.route('/empresa/<int:eid>/importar/sii-status/<job_id>')
def sii_status(eid, job_id):
    """Devuelve estado actual de un job de descarga SII."""
    with _JOBS_LOCK:
        job = dict(_JOBS.get(job_id, {'status': 'unknown'}))
    # Limpiar jobs terminados para no acumular memoria
    if job.get('status') in ('done', 'error'):
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)
    return jsonify(job)


# Mantener compatibilidad con el endpoint antiguo (por si hay caché de la pág anterior)
@bp.route('/empresa/<int:eid>/importar/sii-auto', methods=['POST'])
def sii_auto(eid):
    return sii_start(eid)


# ── Descarga masiva desde consolidado ────────────────────────────────────────

def _run_sii_bulk_job(app, job_id, empresas_data, periodo):
    """Descarga compras+ventas+honorarios de todas las empresas, una a una."""
    from werkzeug.datastructures import FileStorage
    from importers.sii_scraper import descargar_lote

    n = len(empresas_data)

    def set_state(i, msg, pct=None):
        _job_set(job_id,
                 pct=pct if pct is not None else int(i / n * 100),
                 empresa_idx=i + 1,
                 empresa_nombre=empresas_data[i][1],
                 message=msg)

    all_results = {}

    for i, (eid, nombre, rut, clave_sii) in enumerate(empresas_data):
        set_state(i, f'{nombre} — conectando…')
        tipos = ['compras', 'ventas', 'honorarios']

        try:
            with app.app_context():
                tipos_a_bajar, ya_importados = [], {}
                for tipo in tipos:
                    ya = (ArchivoImportado.query
                          .filter_by(empresa_id=eid, tipo=tipo.upper())
                          .filter(ArchivoImportado.periodo == periodo).first())
                    if ya:
                        ya_importados[tipo] = ya
                    else:
                        tipos_a_bajar.append(tipo)

            tipo_results = {
                t: {'ok': True, 'importados': 0,
                    'aviso': f'Ya importado ({ya.fecha_importacion.strftime("%d/%m/%Y")})'}
                for t, ya in ya_importados.items()
            }

            if tipos_a_bajar:
                def _cb(pct_inner, msg, _i=i, _n=n, _nom=nombre):
                    outer = int(_i / _n * 100 + pct_inner / _n)
                    _job_set(job_id, pct=outer, empresa_idx=_i + 1,
                             empresa_nombre=_nom, message=f'{_nom} — {msg}')

                ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                fut = ex.submit(descargar_lote, rut, clave_sii, periodo,
                                tipos_a_bajar, _cb)
                try:
                    contenidos = fut.result(timeout=240)  # 4 min máx por empresa
                except concurrent.futures.TimeoutError:
                    raise Exception('Tiempo de espera agotado (4 min) — sin respuesta del SII')
                finally:
                    ex.shutdown(wait=False)

                with app.app_context():
                    for tipo in tipos_a_bajar:
                        contenido = contenidos.get(tipo)
                        if isinstance(contenido, Exception):
                            tipo_results[tipo] = {'ok': False, 'error': str(contenido)}
                            continue

                        sha = hashlib.sha256(contenido).hexdigest()
                        if tipo == 'honorarios':
                            ext = 'xls'
                        elif contenido[:3] == b'PK\x03':
                            ext = 'xlsx'
                        else:
                            ext = 'csv'
                        nombre_arch = f'sii_{tipo}_{periodo}.{ext}'

                        existente = ArchivoImportado.query.filter_by(
                            empresa_id=eid, sha256=sha, tipo=tipo.upper()).first()
                        if existente:
                            tipo_results[tipo] = {
                                'ok': True, 'importados': 0,
                                'aviso': f'Ya importado ({existente.fecha_importacion.strftime("%d/%m/%Y")})',
                            }
                            continue

                        try:
                            fs = FileStorage(stream=io.BytesIO(contenido),
                                             filename=nombre_arch,
                                             content_type='application/octet-stream')
                            if tipo == 'compras':
                                resultado = libro_compras.importar(fs, eid)
                            elif tipo == 'ventas':
                                resultado = libro_ventas.importar(fs, eid)
                            else:
                                resultado = libro_honorarios.importar(fs, eid)

                            db.session.add(ArchivoImportado(
                                empresa_id=eid, tipo=tipo.upper(),
                                nombre_archivo=nombre_arch, sha256=sha,
                                ndocs=resultado.get('importados', 0),
                                periodo=periodo,
                            ))
                            db.session.commit()
                            tipo_results[tipo] = {
                                'ok': True,
                                'importados': resultado.get('importados', 0),
                                'errores': resultado.get('errores', []),
                            }
                        except Exception as e:
                            tipo_results[tipo] = {'ok': False, 'error': str(e)}

            all_results[eid] = {'nombre': nombre, 'tipos': tipo_results}

        except Exception as e:
            all_results[eid] = {'nombre': nombre, 'error': str(e)}

        if i < n - 1:
            set_state(i, f'{nombre} lista. Pausa breve antes de la siguiente…',
                      pct=int((i + 1) / n * 100))
            time.sleep(4)

    _job_set(job_id, status='done', pct=100,
             message='Descarga completada', results=all_results)


@bp.route('/consolidado/sii-bajar-todos', methods=['POST'])
def sii_bulk_start():
    periodo = request.form.get('periodo', '')
    if not periodo:
        return jsonify({'ok': False, 'error': 'Falta el período'})

    empresas = (Empresa.query
                .filter(Empresa.activa == True,
                        Empresa.clave_sii.isnot(None),
                        Empresa.clave_sii != '')
                .order_by(Empresa.razon_social).all())
    if not empresas:
        return jsonify({'ok': False, 'error': 'No hay empresas con clave SII configurada'})

    empresas_data = [
        (e.id, e.nombre_fantasia or e.razon_social, e.rut, e.clave_sii)
        for e in empresas
    ]
    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            'status': 'running', 'pct': 0, 'message': 'Iniciando…',
            'empresa_idx': 0, 'empresa_total': len(empresas_data),
            'empresa_nombre': '', 'results': None,
        }

    app = current_app._get_current_object()
    threading.Thread(
        target=_run_sii_bulk_job,
        args=(app, job_id, empresas_data, periodo),
        daemon=True,
    ).start()
    return jsonify({'ok': True, 'job_id': job_id,
                    'n_empresas': len(empresas_data), 'periodo': periodo})


@bp.route('/consolidado/sii-bulk-status/<job_id>')
def sii_bulk_status(job_id):
    with _JOBS_LOCK:
        job = dict(_JOBS.get(job_id, {'status': 'unknown'}))
    if job.get('status') in ('done', 'error'):
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)
    return jsonify(job)
