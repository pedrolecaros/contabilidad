import os
import uuid
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.pdf', '.webp'}


def save_attachment(file_obj, original_filename, upload_folder):
    """Save uploaded file to local filesystem. Returns a storage key like 'local:rec_abc123.pdf'."""
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f'Tipo de archivo no permitido: {ext}')
    filename = f"rec_{uuid.uuid4().hex[:12]}{ext}"
    os.makedirs(upload_folder, exist_ok=True)
    file_obj.save(os.path.join(upload_folder, filename))
    return f"local:{filename}"


def attachment_url(storage_key):
    """Convert a storage key to a browser-accessible URL. Works for both local: keys and plain URLs."""
    if not storage_key:
        return None
    if storage_key.startswith('local:'):
        from flask import url_for
        filename = storage_key[6:]
        return url_for('main.servir_adjunto', filename=filename)
    # Legacy plain URL (http/https)
    return storage_key


def attachment_label(storage_key):
    """Human-readable label for display."""
    if not storage_key:
        return None
    if storage_key.startswith('local:'):
        return storage_key[6:]  # just the filename
    return storage_key  # the URL itself


def is_image(storage_key):
    if not storage_key:
        return False
    ext = os.path.splitext(storage_key)[1].lower()
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
