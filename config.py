import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ruta de la base de datos: usa variable de entorno DB_PATH si está definida,
# de lo contrario usa contabilidad.db en la carpeta del proyecto.
# Ejemplo para Google Drive en Windows:
#   DB_PATH=C:\Users\Pedro\Google Drive\contabilidad\contabilidad.db
_db_path = os.environ.get('DB_PATH') or os.path.join(BASE_DIR, 'contabilidad.db')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'contabilidad-chile-dev-key-2024')
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{_db_path}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024
