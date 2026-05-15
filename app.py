import os
from flask import Flask
from config import Config
from database import init_db
from routes import main, empresas, asientos, cuentas, importar, pendientes, reportes, validacion, conciliacion, contrapartes, remuneraciones, prestamos


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

    # Filtros Jinja2 para formato chileno
    @app.template_filter('clp')
    def fmt_clp(value):
        if value is None:
            return '0'
        try:
            return '{:,.0f}'.format(float(value)).replace(',', '.')
        except (ValueError, TypeError):
            return str(value)

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

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
