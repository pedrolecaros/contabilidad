from models import db, Cuenta
from engine.plan_cuentas_default import PLAN_CUENTAS_CHILE


def _migrar(app):
    """Agrega columnas nuevas a tablas existentes si no existen (SQLite no soporta IF NOT EXISTS)."""
    from sqlalchemy import text
    migraciones = [
        'ALTER TABLE documentos_sii ADD COLUMN conciliacion_id INTEGER REFERENCES conciliaciones(id)',
        'ALTER TABLE movimientos_banco ADD COLUMN conciliacion_id INTEGER REFERENCES conciliaciones(id)',
        "ALTER TABLE conciliaciones ADD COLUMN tipo VARCHAR(20) DEFAULT 'SII'",
        'ALTER TABLE conciliaciones ADD COLUMN respaldo_url VARCHAR(500)',
        'ALTER TABLE conciliaciones ADD COLUMN contraparte_id INTEGER REFERENCES contrapartes(id)',
        'ALTER TABLE empresas ADD COLUMN clave_sii VARCHAR(200)',
        'ALTER TABLE movimientos_banco ADD COLUMN respaldo_url VARCHAR(500)',
    ]
    with db.engine.connect() as con:
        for sql in migraciones:
            try:
                con.execute(text(sql))
                con.commit()
            except Exception:
                pass  # columna ya existe


def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _migrar(app)


def sembrar_plan_cuentas(empresa_id):
    """Crea el plan de cuentas PCGA Chile para una empresa nueva."""
    existente = Cuenta.query.filter_by(empresa_id=empresa_id).first()
    if existente:
        return

    for codigo, nombre, tipo, naturaleza, es_titulo, nivel in PLAN_CUENTAS_CHILE:
        cuenta = Cuenta(
            empresa_id=empresa_id,
            codigo=codigo,
            nombre=nombre,
            tipo=tipo,
            naturaleza=naturaleza,
            es_titulo=es_titulo,
            nivel=nivel,
        )
        db.session.add(cuenta)
    db.session.commit()
