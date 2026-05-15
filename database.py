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
        'ALTER TABLE asientos ADD COLUMN respaldo_url VARCHAR(500)',
        'ALTER TABLE empresas ADD COLUMN participacion_ecox REAL',
        "ALTER TABLE empresas ADD COLUMN tipo_participacion VARCHAR(10)",
        "ALTER TABLE liquidaciones ADD COLUMN estado VARCHAR(15) DEFAULT 'BORRADOR'",
        "CREATE TABLE IF NOT EXISTS variables_mensuales (id INTEGER PRIMARY KEY, periodo VARCHAR(7) UNIQUE, uf REAL, utm REAL, tope_imponible REAL, tope_gratificacion REAL, imm REAL, fecha_actualizacion DATETIME)",
        "ALTER TABLE empleados ADD COLUMN tipo_sueldo VARCHAR(10) DEFAULT 'BRUTO'",
        "ALTER TABLE empleados ADD COLUMN monto_isapre_uf REAL DEFAULT 0.0",
        "ALTER TABLE variables_mensuales ADD COLUMN tasa_sis REAL",
        "ALTER TABLE variables_mensuales ADD COLUMN tasas_afp_json TEXT",
        "CREATE TABLE IF NOT EXISTS valores_uf (id INTEGER PRIMARY KEY, fecha DATE UNIQUE NOT NULL, valor REAL NOT NULL)",
        """CREATE TABLE IF NOT EXISTS prestamos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    empresa_relacionada_id INTEGER REFERENCES empresas(id),
    nombre VARCHAR(200) NOT NULL,
    tipo VARCHAR(10) NOT NULL,
    moneda VARCHAR(5) DEFAULT 'PESOS',
    monto_original REAL NOT NULL,
    tasa_interes_anual REAL DEFAULT 0.0,
    fecha_inicio DATE NOT NULL,
    n_cuotas INTEGER,
    periodicidad VARCHAR(10) DEFAULT 'MENSUAL',
    acreedor_deudor VARCHAR(200),
    activo INTEGER DEFAULT 1,
    notas TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)""",
        """CREATE TABLE IF NOT EXISTS cuotas_prestamo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prestamo_id INTEGER NOT NULL REFERENCES prestamos(id),
    numero_cuota INTEGER NOT NULL,
    fecha_vencimiento DATE NOT NULL,
    capital REAL DEFAULT 0,
    interes REAL DEFAULT 0,
    cuota_total REAL DEFAULT 0,
    saldo_insoluto REAL DEFAULT 0,
    pagada INTEGER DEFAULT 0,
    fecha_pago DATE,
    movimiento_banco_id INTEGER REFERENCES movimientos_banco(id),
    notas VARCHAR(300)
)""",
        "ALTER TABLE empresas ADD COLUMN contribuyente_iva INTEGER DEFAULT 1",
        "ALTER TABLE empresas ADD COLUMN tasa_ppm REAL DEFAULT 1.0",
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
