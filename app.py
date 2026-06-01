import os
import threading
from flask import Flask
from config import Config
from database import init_db

# Cargar .env si existe (sin dependencias externas)
_env_file = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())
from routes import main, empresas, asientos, cuentas, importar, pendientes, reportes, validacion, conciliacion, contrapartes, remuneraciones, prestamos, dashboard, buscar, tributario


def _auto_fetch_uf(app):
    from services.uf import fetch_today_if_missing
    fetch_today_if_missing(app)


def create_app(config_override=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_override:
        app.config.from_object(config_override)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    init_db(app)

    app.register_blueprint(main.bp)
    app.register_blueprint(empresas.bp)
    app.register_blueprint(asientos.bp)
    app.register_blueprint(cuentas.bp)
    app.register_blueprint(importar.bp)
    app.register_blueprint(pendientes.bp)
    app.register_blueprint(reportes.bp)
    app.register_blueprint(validacion.bp)
    app.register_blueprint(conciliacion.bp)
    app.register_blueprint(contrapartes.bp)
    app.register_blueprint(remuneraciones.bp)
    app.register_blueprint(prestamos.bp)

    app.register_blueprint(dashboard.bp)
    app.register_blueprint(buscar.bp)
    app.register_blueprint(tributario.bp)

    # Filtros Jinja2 para formato chileno
    @app.template_filter('clp')
    def fmt_clp(value):
        if value is None:
            return '0'
        try:
            return '{:,.0f}'.format(float(value)).replace(',', '.')
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter('uf_fmt')
    def fmt_uf(value):
        """Formatea un valor UF con 2 decimales al estilo chileno: 40.610,69"""
        if value is None:
            return '—'
        try:
            v = float(value)
            entero, dec = f'{v:,.2f}'.split('.')
            return entero.replace(',', '.') + ',' + dec
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter('adjunto_url')
    def fmt_adjunto_url(storage_key):
        from storage import attachment_url
        return attachment_url(storage_key)

    @app.template_filter('adjunto_es_pdf')
    def fmt_adjunto_es_pdf(storage_key):
        from storage import is_pdf
        return is_pdf(storage_key)

    @app.template_filter('fecha_cl')
    def fmt_fecha(value):
        if not value:
            return ''
        try:
            return value.strftime('%d/%m/%Y')
        except Exception:
            return str(value)

    @app.template_filter('fromjson')
    def fmt_fromjson(value):
        import json
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}

    from storage import attachment_url as _attachment_url, attachment_label as _attachment_label, is_image as _is_image
    app.jinja_env.globals['attachment_url'] = _attachment_url
    app.jinja_env.globals['attachment_label'] = _attachment_label
    app.jinja_env.globals['is_image'] = _is_image

    # Auto-fetch UF for the current year if today's value is missing
    t = threading.Thread(target=_auto_fetch_uf, args=(app,), daemon=True)
    t.start()

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
