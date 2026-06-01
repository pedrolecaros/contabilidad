import os
import uuid
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.pdf', '.webp'}


def save_attachment(file_obj, original_filename, upload_folder, subfolder=None):
    """Save uploaded file. Returns a storage key like 'local:path/to/file.pdf'.

    If subfolder is given (e.g. 'empresa_123/gastos/2026-05'), the file is saved
    inside that subfolder under upload_folder. Otherwise falls back to flat layout.
    """
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f'Tipo de archivo no permitido: {ext}')
    filename = f"rec_{uuid.uuid4().hex[:12]}{ext}"
    if subfolder:
        dest_dir = os.path.join(upload_folder, subfolder)
        rel_path = f"{subfolder}/{filename}"
    else:
        dest_dir = upload_folder
        rel_path = filename
    os.makedirs(dest_dir, exist_ok=True)
    file_obj.save(os.path.join(dest_dir, filename))
    return f"local:{rel_path}"


def save_bytes(data: bytes, filename: str, upload_folder: str, subfolder: str) -> str:
    """Save raw bytes (e.g. a generated PDF) to an organized subfolder."""
    dest_dir = os.path.join(upload_folder, subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, filename)
    with open(path, 'wb') as f:
        f.write(data)
    return f"local:{subfolder}/{filename}"


def attachment_url(storage_key):
    """Convert a storage key to a browser-accessible URL."""
    if not storage_key:
        return None
    if storage_key.startswith('local:'):
        from flask import url_for
        rel = storage_key[6:]
        return url_for('main.servir_adjunto', filepath=rel)
    return storage_key


def attachment_label(storage_key):
    """Human-readable label for display."""
    if not storage_key:
        return None
    if storage_key.startswith('local:'):
        return os.path.basename(storage_key[6:])
    return storage_key


def is_image(storage_key):
    if not storage_key:
        return False
    ext = os.path.splitext(storage_key)[1].lower()
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def is_pdf(storage_key):
    if not storage_key:
        return False
    return os.path.splitext(storage_key)[1].lower() == '.pdf'
