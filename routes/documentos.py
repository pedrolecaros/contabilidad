"""Explorador de documentos: lista TODOS los archivos en backup por empresa
(libros SII, cartolas, F29, F22, fotos respaldo, otros). Permite descargar y eliminar."""
import os
from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   current_app, send_from_directory, abort)
from sqlalchemy import text
from models import db, Empresa, ArchivoImportado

bp = Blueprint('documentos', __name__)


def _rut_clean(rut: str) -> str:
    return (rut or '').replace('.', '').replace('-', '') or 'sin_rut'


def _scan_dir(root, rel='', max_depth=4):
    """Recorre el árbol y devuelve lista plana de archivos."""
    items = []
    full = os.path.join(root, rel) if rel else root
    if not os.path.isdir(full):
        return items
    try:
        entries = sorted(os.listdir(full))
    except (OSError, PermissionError):
        return items
    for name in entries:
        if name.startswith('.'):
            continue
        path = os.path.join(full, name)
        rel_path = os.path.join(rel, name) if rel else name
        if os.path.isdir(path):
            depth = rel_path.count(os.sep)
            if depth < max_depth:
                items.extend(_scan_dir(root, rel_path, max_depth))
        elif os.path.isfile(path):
            try:
                st = os.stat(path)
                parts = rel_path.split(os.sep)
                # parts puede ser [TIPO, periodo, filename] o [TIPO, filename] o solo [filename]
                tipo = parts[0] if len(parts) >= 2 else '(raíz)'
                periodo = parts[1] if len(parts) >= 3 else ''
                fname = parts[-1]
                items.append({
                    'rel_path': rel_path,
                    'nombre': fname,
                    'tipo': tipo,
                    'periodo': periodo if periodo not in ('sin_periodo', '') else '',
                    'tamano': st.st_size,
                    'mtime': st.st_mtime,
                })
            except OSError:
                pass
    return items


@bp.route('/empresa/<int:eid>/archivos')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    rut_clean = _rut_clean(empresa.rut)
    base = os.path.join(current_app.config['UPLOAD_FOLDER'],
                        'backups_importacion', rut_clean)
    archivos = _scan_dir(base, max_depth=5)

    # Filtros
    f_tipo = request.args.get('tipo', '').strip().upper()
    f_periodo = request.args.get('periodo', '').strip()
    f_search = request.args.get('q', '').strip().lower()

    if f_tipo:
        archivos = [a for a in archivos if a['tipo'].upper() == f_tipo]
    if f_periodo:
        archivos = [a for a in archivos if a['periodo'] == f_periodo]
    if f_search:
        archivos = [a for a in archivos if f_search in a['nombre'].lower()]

    # Ordenar por mtime desc
    archivos.sort(key=lambda x: x['mtime'], reverse=True)

    # Agregados: tipos y periodos únicos para los filtros
    all_archivos = _scan_dir(base, max_depth=5)
    tipos = sorted({a['tipo'] for a in all_archivos})
    periodos = sorted({a['periodo'] for a in all_archivos if a['periodo']}, reverse=True)

    total_bytes = sum(a['tamano'] for a in archivos)

    return render_template('documentos/index.html',
        empresa=empresa, archivos=archivos,
        tipos=tipos, periodos=periodos,
        f_tipo=f_tipo, f_periodo=f_periodo, f_search=f_search,
        total_bytes=total_bytes, total_count=len(all_archivos),
        rut_clean=rut_clean)


@bp.route('/empresa/<int:eid>/archivos/descargar')
def descargar(eid):
    empresa = Empresa.query.get_or_404(eid)
    rel = request.args.get('rel', '').strip()
    if not rel or '..' in rel.split('/'):
        abort(400)
    rut_clean = _rut_clean(empresa.rut)
    base = os.path.join(current_app.config['UPLOAD_FOLDER'],
                        'backups_importacion', rut_clean)
    full = os.path.join(base, rel)
    if not os.path.isfile(full):
        flash(f'Archivo no encontrado: {rel}', 'danger')
        return redirect(url_for('documentos.index', eid=eid))
    directory = os.path.dirname(full)
    fname = os.path.basename(full)
    return send_from_directory(directory, fname, as_attachment=True)


@bp.route('/empresa/<int:eid>/archivos/eliminar', methods=['POST'])
def eliminar(eid):
    empresa = Empresa.query.get_or_404(eid)
    rel = request.form.get('rel', '').strip()
    if not rel or '..' in rel.split('/'):
        flash('Ruta inválida', 'danger')
        return redirect(url_for('documentos.index', eid=eid))
    rut_clean = _rut_clean(empresa.rut)
    base = os.path.join(current_app.config['UPLOAD_FOLDER'],
                        'backups_importacion', rut_clean)
    full = os.path.join(base, rel)
    if not os.path.isfile(full):
        flash(f'Archivo no encontrado: {rel}', 'warning')
    else:
        try:
            os.remove(full)
            # Limpiar registro en archivos_importados si existe (matchea por sufijo)
            db.session.execute(text(
                "DELETE FROM archivos_importados WHERE empresa_id=:e AND respaldo_url LIKE :p"
            ), {'e': eid, 'p': f'%{rel}'})
            db.session.commit()
            flash(f'Archivo "{os.path.basename(rel)}" eliminado', 'info')
        except Exception as e:
            flash(f'Error al eliminar: {e}', 'danger')
    return redirect(url_for('documentos.index', eid=eid))
