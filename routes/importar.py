import concurrent.futures
import hashlib
import io
import time
import threading
import uuid
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app
from collections import Counter
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
        from importers.sii_scraper import descargar, SIILoginError, SIIDownloadError, SIIEmptyPeriodError
        up(10, 'Conectando al portal SII…')
        try:
            contenido = descargar(rut, clave_sii, periodo, tipo)
        except SIIEmptyPeriodError as e:
            _job_set(job_id, status='done', pct=100, message='Sin movimientos',
                     result={'ok': True, 'tipo': tipo, 'importados': 0, 'errores': [],
                             'nombre': '', 'aviso': str(e)})
            return

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

            try:
                from storage import save_import_backup
                empresa_obj = Empresa.query.get(eid)
                save_import_backup(contenido, nombre, app.config['UPLOAD_FOLDER'],
                                   empresa_obj.rut if empresa_obj else '',
                                   tipo.upper(), periodo_det or periodo, sha[:8])
            except Exception:
                pass

            _job_set(job_id, status='done', pct=100, message='Completado',
                     result={'ok': True, 'tipo': tipo,
                             'importados': resultado.get('importados', 0),
                             'errores': resultado.get('errores', []),
                             'nombre': nombre})

    except Exception as e:
        _job_set(job_id, status='error', pct=100, message=str(e),
                 result={'ok': False, 'tipo': tipo, 'error': str(e)})

ALLOWED = {'csv', 'xls', 'xlsx', 'pdf'}


def _ext_ok(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED


_RE_RUT = None
def _detectar_rut_en_archivo(blob: bytes, filename: str) -> str | None:
    """Busca un RUT chileno (X[X].XXX.XXX-X o XXXXXXXX-X) en el contenido del archivo.
    Devuelve el primer match o None. Para XLS/XLSX/CSV escanea texto plano del blob.
    """
    import re
    global _RE_RUT
    if _RE_RUT is None:
        _RE_RUT = re.compile(rb'\b(\d{1,2}\.?\d{3}\.?\d{3}-[\dkK])\b')
    # Para XLSX (zip) buscamos en strings de shared strings y de hojas.
    # Heurística simple: extrae bytes ASCII/Latin1 y aplica regex.
    try:
        text = blob.decode('latin-1', errors='ignore')
        # XLSX viene como zip — el regex sobre bytes funciona igual sobre el texto descomprimido
        # cuando el RUT está en strings sin comprimir. Para zip robusto, parseamos:
        if filename.lower().endswith('.xlsx'):
            import zipfile
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                    inner = b''
                    for name in zf.namelist():
                        if name.startswith('xl/'):
                            inner += zf.read(name)
                    m = _RE_RUT.search(inner)
                    if m:
                        return m.group(1).decode('latin-1')
            except zipfile.BadZipFile:
                pass
        # XLS / CSV / TXT
        m = _RE_RUT.search(blob[:200_000])  # solo primeros 200KB
        if m:
            return m.group(1).decode('latin-1')
    except Exception:
        return None
    return None


def _sha256(file_storage):
    file_storage.stream.seek(0)
    h = hashlib.sha256(file_storage.stream.read()).hexdigest()
    file_storage.stream.seek(0)
    return h


def _periodo_docs(empresa_id, tipo, after_dt):
    """Derives YYYY-MM from most recent docs imported after after_dt."""
    if tipo in ('BANCO', 'TARJETA'):
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


def _detectar_coherencia_periodo(eid, tipo_upper, max_doc_id_antes, max_mov_id_antes,
                                  nombre_archivo=None):
    """
    Analiza los documentos/movimientos recién importados y devuelve advertencia
    si hay mezcla de períodos o el período no coincide con el último archivo.

    Si se pasa `nombre_archivo`, filtra por `archivo_origen == nombre_archivo`
    (más confiable que id > max porque tolera importaciones en paralelo).
    Si no, cae al método legacy basado en id.

    Retorna (periodo_predominante, aviso_o_None).
    """
    if tipo_upper in ('COMPRAS', 'VENTAS', 'HONORARIOS'):
        q = DocumentoSII.query.filter(DocumentoSII.empresa_id == eid,
                                       DocumentoSII.tipo_libro == tipo_upper)
        if nombre_archivo:
            q = q.filter(DocumentoSII.archivo_origen == nombre_archivo)
        else:
            q = q.filter(DocumentoSII.id > max_doc_id_antes)
        fechas = [d.fecha for d in q.all() if d.fecha]
    else:
        q = MovimientoBanco.query.filter(MovimientoBanco.empresa_id == eid)
        if nombre_archivo:
            q = q.filter(MovimientoBanco.archivo_origen == nombre_archivo)
        else:
            q = q.filter(MovimientoBanco.id > max_mov_id_antes)
        fechas = [m.fecha for m in q.all() if m.fecha]

    if not fechas:
        return None, None

    conteo = Counter(f.strftime('%Y-%m') for f in fechas)
    periodo_pred = conteo.most_common(1)[0][0]
    periodos = sorted(conteo.keys())

    avisos = []

    # Mezcla de períodos
    if len(periodos) > 1:
        detalle = ', '.join(
            f'{p} ({conteo[p]} docs, {conteo[p]/len(fechas)*100:.0f}%)'
            for p in periodos
        )
        avisos.append(
            f'El archivo contiene documentos de <strong>múltiples períodos</strong>: {detalle}. '
            f'Período predominante registrado: <strong>{periodo_pred}</strong>.'
        )

    # Comparar con el último archivo importado del mismo tipo
    ultimo = (ArchivoImportado.query
              .filter_by(empresa_id=eid, tipo=tipo_upper)
              .filter(ArchivoImportado.periodo.isnot(None))
              .order_by(ArchivoImportado.id.desc())
              .first())
    if ultimo and ultimo.periodo and ultimo.periodo == periodo_pred and not avisos:
        avisos.append(
            f'El período <strong>{periodo_pred}</strong> de este archivo '
            f'coincide con una importación anterior '
            f'({ultimo.fecha_importacion.strftime("%d/%m/%Y")}). '
            'Verifique que no sea un duplicado.'
        )

    return periodo_pred, (' '.join(avisos) if avisos else None)


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
        try:
            return (int(k[:4]), int(k[5:7])) if len(k) == 7 else (int(k), 0)
        except (ValueError, TypeError):
            return (0, 0)

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
    doc_cont = dict(
        db.session.query(DocumentoSII.archivo_origen, func.count(DocumentoSII.id))
        .filter_by(empresa_id=eid, procesado=True)
        .group_by(DocumentoSII.archivo_origen).all()
    )
    mov_cont = dict(
        db.session.query(MovimientoBanco.archivo_origen, func.count(MovimientoBanco.id))
        .filter_by(empresa_id=eid, procesado=True)
        .group_by(MovimientoBanco.archivo_origen).all()
    )
    def _pendientes(a):
        if a.tipo in ('BANCO', 'TARJETA'):
            return mov_pend.get(a.nombre_archivo, 0)
        return doc_pend.get(a.nombre_archivo, 0)

    def _contabilizados(a):
        if a.tipo in ('BANCO', 'TARJETA'):
            return mov_cont.get(a.nombre_archivo, 0)
        return doc_cont.get(a.nombre_archivo, 0)

    return render_template('importar/index.html', empresa=empresa, archivos=archivos,
                           pendientes_fn=_pendientes,
                           contabilizados_fn=_contabilizados,
                           hoy=hoy.isoformat(),
                           mes_anterior=mes_anterior.strftime('%Y-%m'))


@bp.route('/empresa/<int:eid>/importar/<int:aid>/detalle')
def detalle_archivo(eid, aid):
    from flask import jsonify
    archivo = ArchivoImportado.query.filter_by(id=aid, empresa_id=eid).first_or_404()
    filas = []
    if archivo.tipo in ('BANCO', 'TARJETA'):
        movs = (MovimientoBanco.query
                .filter_by(empresa_id=eid, archivo_origen=archivo.nombre_archivo)
                .order_by(MovimientoBanco.fecha).all())
        for m in movs:
            filas.append({
                'fecha': m.fecha.strftime('%d/%m/%Y') if m.fecha else '',
                'descripcion': (m.descripcion or '')[:80],
                'cargo': m.cargo or 0,
                'abono': m.abono or 0,
                'estado': 'Contabilizado' if m.procesado else 'Pendiente',
            })
    else:
        docs = (DocumentoSII.query
                .filter_by(empresa_id=eid, archivo_origen=archivo.nombre_archivo,
                           tipo_libro=archivo.tipo)
                .order_by(DocumentoSII.fecha).all())
        for d in docs:
            filas.append({
                'fecha': d.fecha.strftime('%d/%m/%Y') if d.fecha else '',
                'tipo_dte': d.tipo_dte or '',
                'folio': d.folio or '',
                'razon_social': (d.razon_social_contraparte or '')[:40],
                'rut': d.rut_contraparte or '',
                'neto': d.monto_neto or 0,
                'iva': d.iva or 0,
                'total': d.total or 0,
                'estado': 'Contabilizado' if d.procesado else 'Pendiente',
            })
    return jsonify({'tipo': archivo.tipo, 'filas': filas,
                    'nombre': archivo.nombre_archivo, 'periodo': archivo.periodo or ''})


@bp.route('/empresa/<int:eid>/importar/<int:aid>/revertir', methods=['POST'])
def revertir(eid, aid):
    """Elimina los documentos/movimientos pendientes (no procesados) de un archivo importado."""
    archivo = ArchivoImportado.query.filter_by(id=aid, empresa_id=eid).first_or_404()

    if archivo.tipo in ('BANCO', 'TARJETA'):
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
    # "Otros" acepta cualquier extensión (PDF, DOCX, JPG, etc.) — solo se guarda como respaldo.
    # Los demás tipos requieren CSV/XLS/XLSX/PDF.
    if tipo != 'otros' and not _ext_ok(archivo.filename):
        flash('Formato no válido. Use CSV, XLS o XLSX', 'danger')
        return redirect(url_for('importar.index', eid=eid))

    sha = _sha256(archivo)

    # Snapshot bytes para backup en disco
    archivo.stream.seek(0)
    _backup_bytes = archivo.stream.read()
    archivo.stream.seek(0)

    # Validar que el RUT presente en el archivo (si lo hay) coincide con el de la empresa.
    # Aplica especialmente a cartolas (banco + tarjeta) que vienen con RUT en el header.
    if tipo in ('banco', 'tarjeta') and _backup_bytes:
        rut_archivo = _detectar_rut_en_archivo(_backup_bytes, archivo.filename)
        if rut_archivo:
            from importers.sii_f29 import _normalizar_rut as _norm_rut
            if _norm_rut(rut_archivo) != _norm_rut(empresa.rut):
                flash(
                    f'El archivo contiene RUT {rut_archivo}, que NO coincide con '
                    f'{empresa.razon_social} ({empresa.rut}) — no se importó.',
                    'danger'
                )
                return redirect(url_for('importar.index', eid=eid))

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

    # Snapshot de IDs actuales para detectar registros recién importados
    max_doc_id = db.session.query(func.max(DocumentoSII.id)).filter_by(empresa_id=eid).scalar() or 0
    max_mov_id = db.session.query(func.max(MovimientoBanco.id)).filter_by(empresa_id=eid).scalar() or 0

    try:
        tipo_upper = tipo.upper() if tipo != 'banco' else 'BANCO'
        if tipo == 'otros':
            # Documento libre: solo guardar backup, no procesar.
            # Responde JSON porque el form-import en el UI espera JSON (fetch + res.json()).
            from storage import save_import_backup
            periodo_libre = (request.form.get('periodo') or '').strip() or None
            categoria = (request.form.get('categoria') or 'OTROS').strip()
            sub_rel = save_import_backup(_backup_bytes, archivo.filename,
                                          current_app.config['UPLOAD_FOLDER'],
                                          empresa.rut, categoria, periodo_libre)
            registro = ArchivoImportado(
                empresa_id=eid, tipo=categoria.upper(),
                nombre_archivo=archivo.filename, sha256=sha,
                periodo=periodo_libre or '',
                fecha_importacion=datetime.now(), ndocs=0,
            )
            db.session.add(registro)
            db.session.commit()
            return jsonify({
                'ok': True, 'tipo': 'OTROS', 'nombre': archivo.filename,
                'importados': 0, 'errores': [],
                'aviso': f'Documento guardado en backup ({categoria}{", " + periodo_libre if periodo_libre else " — GLOBAL"})',
            })
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
        elif tipo == 'tarjeta':
            from importers import tarjeta_credito as tc_mod
            banco = request.form.get('banco', '').strip() or 'Banco de Chile (TC)'
            cuenta_bancaria = request.form.get('cuenta_bancaria', '').strip()
            resultado = tc_mod.importar(archivo, eid, banco, cuenta_bancaria)
            tipo_upper = 'TARJETA'
        else:
            flash('Tipo de importación desconocido', 'danger')
            return redirect(url_for('importar.index', eid=eid))
    except Exception as e:
        flash(f'Error al procesar archivo: {e}', 'danger')
        return redirect(url_for('importar.index', eid=eid))

    # Detectar período predominante y verificar coherencia
    # Filtramos por archivo_origen para no contaminar con archivos subidos
    # en paralelo desde el drag&drop multi-file.
    periodo_det, aviso_periodo = _detectar_coherencia_periodo(
        eid, tipo_upper, max_doc_id, max_mov_id,
        nombre_archivo=archivo.filename)

    # Register the imported file
    periodo = periodo_det or _periodo_docs(eid, tipo_upper, datetime.now())
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

    # Guardar copia en backups_importacion (no bloqueante si falla)
    try:
        from storage import save_import_backup
        save_import_backup(_backup_bytes, archivo.filename,
                           current_app.config['UPLOAD_FOLDER'],
                           empresa.rut, tipo_upper, periodo, sha[:8])
    except Exception as e:
        current_app.logger.warning(f'No se pudo guardar backup de importación: {e}')

    response = {
        'ok': True,
        'tipo': tipo,
        'importados': resultado.get('importados', 0),
        'errores': resultado.get('errores', []),
        'nombre': archivo.filename,
    }
    if aviso_periodo:
        response['aviso'] = aviso_periodo
    return jsonify(response)


def _run_sii_batch_job(app, job_id, eid, rut, clave_sii, tipos, periodo):
    """Descarga compras+ventas+honorarios en una sola sesión Playwright."""
    from werkzeug.datastructures import FileStorage

    def up(pct, msg, current_tipo=None):
        kw = dict(pct=pct, message=msg)
        if current_tipo is not None:
            kw['current_tipo'] = current_tipo
        _job_set(job_id, **kw)

    try:
        from importers.sii_scraper import descargar_lote, SIILoginError, SIIDownloadError, SIIEmptyPeriodError

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

                if isinstance(contenido, SIIEmptyPeriodError):
                    results[tipo] = {'ok': True, 'tipo': tipo, 'importados': 0, 'errores': [],
                                     'nombre': '', 'aviso': str(contenido)}
                    ya = ArchivoImportado.query.filter_by(
                        empresa_id=eid, tipo=tipo.upper(), periodo=periodo).first()
                    if not ya:
                        db.session.add(ArchivoImportado(
                            empresa_id=eid, tipo=tipo.upper(),
                            nombre_archivo=f'sii_{tipo}_{periodo}_vacio',
                            sha256=f'empty_{tipo}_{eid}_{periodo}',
                            ndocs=0, periodo=periodo,
                        ))
                        db.session.commit()
                    continue

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
                    try:
                        from storage import save_import_backup
                        empresa_obj = Empresa.query.get(eid)
                        save_import_backup(contenido, nombre, app.config['UPLOAD_FOLDER'],
                                           empresa_obj.rut if empresa_obj else '',
                                           tipo.upper(), periodo, sha[:8])
                    except Exception:
                        pass
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
    from importers.sii_scraper import descargar_lote, SIIEmptyPeriodError

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
                        if isinstance(contenido, SIIEmptyPeriodError):
                            tipo_results[tipo] = {'ok': True, 'importados': 0,
                                                   'aviso': str(contenido)}
                            # Register empty period so grid shows the white badge
                            ya = ArchivoImportado.query.filter_by(
                                empresa_id=eid, tipo=tipo.upper(), periodo=periodo).first()
                            if not ya:
                                db.session.add(ArchivoImportado(
                                    empresa_id=eid, tipo=tipo.upper(),
                                    nombre_archivo=f'sii_{tipo}_{periodo}_vacio',
                                    sha256=f'empty_{tipo}_{eid}_{periodo}',
                                    ndocs=0, periodo=periodo,
                                ))
                                db.session.commit()
                            continue
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
                            try:
                                from storage import save_import_backup
                                empresa_obj = Empresa.query.get(eid)
                                save_import_backup(contenido, nombre_arch, app.config['UPLOAD_FOLDER'],
                                                   empresa_obj.rut if empresa_obj else '',
                                                   tipo.upper(), periodo, sha[:8])
                            except Exception:
                                pass
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

    # eids opcionales: filtrar solo las empresas seleccionadas
    eids_raw = request.form.get('eids', '')
    eids_sel = [int(x) for x in eids_raw.split(',') if x.strip().isdigit()] if eids_raw else []

    q = (Empresa.query
         .filter(Empresa.activa == True,
                 Empresa.clave_sii.isnot(None),
                 Empresa.clave_sii != ''))
    if eids_sel:
        q = q.filter(Empresa.id.in_(eids_sel))
    empresas = q.order_by(Empresa.razon_social).all()

    if not empresas:
        return jsonify({'ok': False, 'error': 'No hay empresas seleccionadas con clave SII configurada'})

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
