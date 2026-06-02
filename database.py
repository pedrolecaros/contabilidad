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
        # tipo_participacion: columna obsoleta — se mantiene en DBs existentes
        # por compatibilidad, pero ya no se usa en el modelo.
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
        "ALTER TABLE empresas ADD COLUMN contribuyente_iva INTEGER DEFAULT 1",
        "ALTER TABLE empresas ADD COLUMN tasa_ppm REAL DEFAULT 1.0",
        """CREATE TABLE IF NOT EXISTS asientos_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asiento_id INTEGER NOT NULL REFERENCES asientos(id),
    accion VARCHAR(20) NOT NULL,
    descripcion VARCHAR(500),
    creado_en DATETIME DEFAULT CURRENT_TIMESTAMP
)""",
        'ALTER TABLE prestamos ADD COLUMN acreedor_rut VARCHAR(20)',
        "ALTER TABLE empleados ADD COLUMN apv_monto REAL DEFAULT 0.0",
        "ALTER TABLE empleados ADD COLUMN apv_tipo VARCHAR(1) DEFAULT 'A'",
        "ALTER TABLE liquidaciones ADD COLUMN apv REAL DEFAULT 0.0",
        "ALTER TABLE empresas ADD COLUMN regimen VARCHAR(10) DEFAULT 'GENERAL'",
        'ALTER TABLE empresas ADD COLUMN logo_url VARCHAR(500)',
        # Integrity: one bank movement can only link to one asiento
        'CREATE UNIQUE INDEX IF NOT EXISTS uix_movimientos_banco_asiento ON movimientos_banco(asiento_id) WHERE asiento_id IS NOT NULL',
        "ALTER TABLE asientos ADD COLUMN prestamo_sentido VARCHAR(5) DEFAULT '-'",
        "ALTER TABLE empleados ADD COLUMN apellido_paterno VARCHAR(100)",
        "ALTER TABLE empleados ADD COLUMN apellido_materno VARCHAR(100)",
        "ALTER TABLE liquidaciones ADD COLUMN afp_emp REAL DEFAULT 0.0",
        "ALTER TABLE liquidaciones ADD COLUMN ev_emp REAL DEFAULT 0.0",
        "ALTER TABLE lineas_asiento ADD COLUMN contraparte_id INTEGER REFERENCES contrapartes(id)",
        """CREATE TABLE IF NOT EXISTS papelera (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    tipo VARCHAR(30) NOT NULL,
    objeto_id INTEGER NOT NULL,
    descripcion VARCHAR(500),
    datos_json TEXT NOT NULL,
    deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL
)""",
        """CREATE TABLE IF NOT EXISTS notas_contables (
    empresa_id INTEGER PRIMARY KEY REFERENCES empresas(id),
    contenido TEXT DEFAULT '',
    actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP
)""",
        """CREATE TABLE IF NOT EXISTS historial (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER REFERENCES empresas(id),
    fecha DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    accion VARCHAR(20) NOT NULL,
    tipo_objeto VARCHAR(30) NOT NULL,
    objeto_id INTEGER,
    descripcion VARCHAR(500),
    datos_json TEXT,
    revertible BOOLEAN DEFAULT 0
)""",
        "CREATE INDEX IF NOT EXISTS ix_historial_empresa_fecha ON historial(empresa_id, fecha DESC)",
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


def copiar_plan_cuentas(empresa_origen_id, empresa_destino_id):
    """Copia el plan de cuentas de empresa_origen a empresa_destino.
    Respeta la jerarquía de cuentas padre-hijo mapeando los ids antiguos a los nuevos."""
    existente = Cuenta.query.filter_by(empresa_id=empresa_destino_id).first()
    if existente:
        return

    cuentas_origen = (Cuenta.query
                      .filter_by(empresa_id=empresa_origen_id)
                      .order_by(Cuenta.nivel, Cuenta.codigo)
                      .all())

    # Mapa de id_antiguo -> nueva Cuenta (para resolver cuenta_padre_id)
    id_map = {}

    for c in cuentas_origen:
        nueva = Cuenta(
            empresa_id=empresa_destino_id,
            codigo=c.codigo,
            nombre=c.nombre,
            tipo=c.tipo,
            naturaleza=c.naturaleza,
            es_titulo=c.es_titulo,
            nivel=c.nivel,
        )
        if c.cuenta_padre_id and c.cuenta_padre_id in id_map:
            nueva.cuenta_padre_id = id_map[c.cuenta_padre_id].id
        db.session.add(nueva)
        db.session.flush()  # para obtener nueva.id antes del commit
        id_map[c.id] = nueva

    db.session.commit()
