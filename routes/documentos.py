"""Documentos adjuntos por empresa — repositorio libre para Excels, PDFs, contratos,
respaldos, etc. Sin estructura contable; solo backup centralizado por sociedad."""
import os
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, send_from_directory, abort
from datetime import datetime
from sqlalchemy import text
from models import db, Empresa
from storage import save_attachment

bp = Blueprint('documentos', __name__)


CATEGORIAS = [
    'Crédito privado', 'Leasing', 'Contrato', 'Escritura',
    'Excel respaldo', 'PDF respaldo', 'Acta', 'Otro',
]


@bp.route('/empresa/<int:eid>/documentos', methods=['GET', 'POST'])
def index(eid):
    empresa = Empresa.query.get_or_404(eid)

    if request.method == 'POST':
        archivo = request.files.get('archivo')
        if not archivo or not archivo.filename:
            flash('Seleccioná un archivo', 'warning')
            return redirect(url_for('documentos.index', eid=eid))

        categoria = request.form.get('categoria', '').strip() or 'Otro'
        descripcion = request.form.get('descripcion', '').strip()

        # Snapshot bytes para conocer tamaño
        archivo.stream.seek(0, 2)
        tamano = archivo.stream.tell()
        archivo.stream.seek(0)

        try:
            archivo_url = save_attachment(archivo, archivo.filename,
                                          current_app.config['UPLOAD_FOLDER'],
                                          subfolder=f'documentos/{empresa.rut}')
        except ValueError as e:
            flash(f'Error al guardar archivo: {e}', 'danger')
            return redirect(url_for('documentos.index', eid=eid))

        db.session.execute(text("""
            INSERT INTO documentos_adjuntos (empresa_id, nombre, categoria, descripcion, archivo_url, tamano, fecha_subida)
            VALUES (:e, :n, :c, :d, :u, :t, :f)
        """), {
            'e': eid, 'n': archivo.filename, 'c': categoria, 'd': descripcion,
            'u': archivo_url, 't': tamano, 'f': datetime.now()
        })
        db.session.commit()
        flash(f'Documento "{archivo.filename}" guardado', 'success')
        return redirect(url_for('documentos.index', eid=eid))

    # GET — listar documentos
    docs = db.session.execute(text("""
        SELECT id, nombre, categoria, descripcion, archivo_url, tamano, fecha_subida
        FROM documentos_adjuntos
        WHERE empresa_id = :e
        ORDER BY fecha_subida DESC
    """), {'e': eid}).fetchall()

    return render_template('documentos/index.html',
                           empresa=empresa, docs=docs, categorias=CATEGORIAS)


@bp.route('/empresa/<int:eid>/documentos/<int:doc_id>/descargar')
def descargar(eid, doc_id):
    r = db.session.execute(text(
        "SELECT archivo_url, nombre FROM documentos_adjuntos WHERE id=:d AND empresa_id=:e"
    ), {'d': doc_id, 'e': eid}).fetchone()
    if not r:
        abort(404)
    archivo_url, nombre = r
    # archivo_url puede ser 'local:carpeta/archivo' o ruta directa
    if archivo_url.startswith('local:'):
        rel = archivo_url[6:]
    else:
        rel = archivo_url
    upload_folder = current_app.config['UPLOAD_FOLDER']
    full_path = os.path.join(upload_folder, rel)
    if not os.path.isfile(full_path):
        flash(f'Archivo no encontrado en disco: {rel}', 'danger')
        return redirect(url_for('documentos.index', eid=eid))
    directory = os.path.dirname(full_path)
    fname = os.path.basename(full_path)
    return send_from_directory(directory, fname, as_attachment=True, download_name=nombre)


@bp.route('/empresa/<int:eid>/documentos/<int:doc_id>/eliminar', methods=['POST'])
def eliminar(eid, doc_id):
    r = db.session.execute(text(
        "SELECT archivo_url, nombre FROM documentos_adjuntos WHERE id=:d AND empresa_id=:e"
    ), {'d': doc_id, 'e': eid}).fetchone()
    if not r:
        abort(404)
    archivo_url, nombre = r
    # Borrar archivo de disco
    rel = archivo_url[6:] if archivo_url.startswith('local:') else archivo_url
    full_path = os.path.join(current_app.config['UPLOAD_FOLDER'], rel)
    try:
        if os.path.isfile(full_path):
            os.remove(full_path)
    except Exception as e:
        flash(f'Aviso: archivo no se pudo borrar de disco ({e})', 'warning')
    # Borrar registro DB
    db.session.execute(text("DELETE FROM documentos_adjuntos WHERE id=:d"), {'d': doc_id})
    db.session.commit()
    flash(f'Documento "{nombre}" eliminado', 'info')
    return redirect(url_for('documentos.index', eid=eid))
