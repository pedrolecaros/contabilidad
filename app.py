import os
import threading
from flask import Flask
from config import Config
from database import init_db
from routes import main, empresas, asientos, cuentas, importar, pendientes, reportes, validacion, conciliacion, contrapartes, remuneraciones, prestamos, f29, activos, dashboard, buscar


def _auto_fetch_uf(app):
    """Fetch full-year UF data on startup if today's value is missing."""
    from datetime import date
    import requests
    from models import db, ValorUF

    with app.app_context():
        hoy = date.today()
        try:
            tiene_hoy = ValorUF.query.filter_by(fecha=hoy).first()
            if tiene_hoy:
                return  # already up to date

            anio = hoy.year
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; contabilidad-app)'}
            r = requests.get(f'https://mindicador.cl/api/uf/{anio}', timeout=20, headers=headers)
            if r.status_code != 200:
                return

            actualizados = 0
            for item in r.json().get('serie', []):
                raw = item.get('fecha', '')[:10]
                try:
                    fecha = date.fromisoformat(raw)
                    if fecha.year != anio:
                        continue
                    valor = float(item['valor'])
                    existing = ValorUF.query.filter_by(fecha=fecha).first()
                    if existing:
                        existing.valor = valor
                    else:
                        db.session.add(ValorUF(fecha=fecha, valor=valor))
                    actualizados += 1
                except Exception:
                    pass
            if actualizados:
                db.session.commit()
        except Exception:
            pass  # network unavailable — silently skip


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
    app.register_blueprint(f29.bp)
    app.register_blueprint(activos.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(buscar.bp)

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

    # Auto-fetch UF for the current year if today's value is missing
    t = threading.Thread(target=_auto_fetch_uf, args=(app,), daemon=True)
    t.start()

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
