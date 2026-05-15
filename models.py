from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Empresa(db.Model):
    __tablename__ = 'empresas'
    id = db.Column(db.Integer, primary_key=True)
    rut = db.Column(db.String(12), unique=True, nullable=False)
    razon_social = db.Column(db.String(200), nullable=False)
    nombre_fantasia = db.Column(db.String(200))
    giro = db.Column(db.String(300))
    activa = db.Column(db.Boolean, default=True)

    clave_sii = db.Column(db.String(200))
    participacion_ecox = db.Column(db.Float)          # % participación Ecox (ej: 50.0)
    tipo_participacion  = db.Column(db.String(10))    # DIRECTA / INDIRECTA
    contribuyente_iva = db.Column(db.Boolean, default=True)  # False → IVA compras es gasto
    tasa_ppm = db.Column(db.Float, default=1.0)              # % PPM (ej: 1.0 = 1%)
    regimen = db.Column(db.String(10), default='GENERAL')    # PYME | GENERAL
    logo_url = db.Column(db.String(500))

    cuentas = db.relationship('Cuenta', backref='empresa', lazy='dynamic')
    asientos = db.relationship('Asiento', backref='empresa', lazy='dynamic')
    documentos_sii = db.relationship('DocumentoSII', backref='empresa', lazy='dynamic')
    movimientos_banco = db.relationship('MovimientoBanco', backref='empresa', lazy='dynamic')
    reglas = db.relationship('ReglaClasificacion', backref='empresa', lazy='dynamic')

    def __repr__(self):
        return f'<Empresa {self.rut} {self.razon_social}>'


class Cuenta(db.Model):
    __tablename__ = 'cuentas'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    codigo = db.Column(db.String(20), nullable=False)
    nombre = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)       # ACTIVO, PASIVO, PATRIMONIO, INGRESO, GASTO
    naturaleza = db.Column(db.String(10), nullable=False)  # DEUDORA, ACREEDORA
    nivel = db.Column(db.Integer, default=1)
    cuenta_padre_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=True)
    activa = db.Column(db.Boolean, default=True)
    es_titulo = db.Column(db.Boolean, default=False)

    hijos = db.relationship('Cuenta', backref=db.backref('padre', remote_side=[id]))
    lineas = db.relationship('LineaAsiento', backref='cuenta', lazy='dynamic')

    def saldo(self, desde=None, hasta=None):
        q = self.lineas.join(Asiento).filter(Asiento.estado == 'CONFIRMADO')
        if desde:
            q = q.filter(Asiento.fecha >= desde)
        if hasta:
            q = q.filter(Asiento.fecha <= hasta)
        lineas = q.all()
        debe = sum(l.debe for l in lineas)
        haber = sum(l.haber for l in lineas)
        return debe - haber if self.naturaleza == 'DEUDORA' else haber - debe

    def __repr__(self):
        return f'<Cuenta {self.codigo} {self.nombre}>'


class Asiento(db.Model):
    __tablename__ = 'asientos'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    fecha = db.Column(db.Date, nullable=False)
    numero = db.Column(db.Integer)
    descripcion = db.Column(db.String(500))
    respaldo_url = db.Column(db.String(500))
    origen = db.Column(db.String(20), default='MANUAL')
    # MANUAL, LIBRO_COMPRAS, LIBRO_VENTAS, HONORARIOS, BANCO
    estado = db.Column(db.String(15), default='BORRADOR')
    # BORRADOR, CONFIRMADO, ANULADO
    creado_en = db.Column(db.DateTime, default=datetime.now)

    lineas = db.relationship('LineaAsiento', backref='asiento', lazy='select',
                             cascade='all, delete-orphan',
                             order_by='LineaAsiento.orden')

    @property
    def total_debe(self):
        return sum(l.debe for l in self.lineas)

    @property
    def total_haber(self):
        return sum(l.haber for l in self.lineas)

    @property
    def cuadrado(self):
        return abs(self.total_debe - self.total_haber) < 1.0

    def __repr__(self):
        return f'<Asiento #{self.numero} {self.fecha}>'


class LineaAsiento(db.Model):
    __tablename__ = 'lineas_asiento'
    id = db.Column(db.Integer, primary_key=True)
    asiento_id = db.Column(db.Integer, db.ForeignKey('asientos.id'), nullable=False)
    cuenta_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=False)
    debe = db.Column(db.Float, default=0.0)
    haber = db.Column(db.Float, default=0.0)
    descripcion = db.Column(db.String(300))
    orden = db.Column(db.Integer, default=0)


class AsientoAudit(db.Model):
    __tablename__ = 'asientos_audit'
    id = db.Column(db.Integer, primary_key=True)
    asiento_id = db.Column(db.Integer, db.ForeignKey('asientos.id'), nullable=False)
    accion = db.Column(db.String(20), nullable=False)   # CREAR, EDITAR, CONFIRMAR, ANULAR
    descripcion = db.Column(db.String(500))
    creado_en = db.Column(db.DateTime, default=datetime.now)

    asiento = db.relationship('Asiento', backref=db.backref('audits', lazy='dynamic',
                                                             order_by='AsientoAudit.creado_en'))


class Contraparte(db.Model):
    __tablename__ = 'contrapartes'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    rut = db.Column(db.String(12), nullable=False)
    razon_social = db.Column(db.String(200), nullable=False)
    # PROVEEDOR, CLIENTE, AMBOS, HONORARIOS
    tipo = db.Column(db.String(20), nullable=False, default='PROVEEDOR')
    email = db.Column(db.String(200))
    telefono = db.Column(db.String(50))
    notas = db.Column(db.String(500))
    activo = db.Column(db.Boolean, default=True)

    empresa = db.relationship('Empresa', backref=db.backref('contrapartes', lazy='dynamic'))

    def __repr__(self):
        return f'<Contraparte {self.rut} {self.razon_social}>'


class Conciliacion(db.Model):
    __tablename__ = 'conciliaciones'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    fecha = db.Column(db.Date, nullable=False)
    descripcion = db.Column(db.String(300))
    # SII, SUELDO, RETIRO, IMPUESTO, BANCO, PRESTAMO, INTERNO, OTRO
    tipo = db.Column(db.String(20), default='SII')
    respaldo_url = db.Column(db.String(500))
    contraparte_id = db.Column(db.Integer, db.ForeignKey('contrapartes.id'), nullable=True)

    empresa = db.relationship('Empresa', backref=db.backref('conciliaciones', lazy='dynamic'))
    contraparte = db.relationship('Contraparte', backref=db.backref('conciliaciones', lazy='dynamic'))


class DocumentoSII(db.Model):
    __tablename__ = 'documentos_sii'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    tipo_libro = db.Column(db.String(15), nullable=False)  # COMPRAS, VENTAS, HONORARIOS
    tipo_dte = db.Column(db.String(10))                    # 33, 34, 39, 61, etc.
    folio = db.Column(db.String(20))
    fecha = db.Column(db.Date)
    rut_contraparte = db.Column(db.String(12))
    razon_social_contraparte = db.Column(db.String(200))
    monto_exento = db.Column(db.Float, default=0.0)
    monto_neto = db.Column(db.Float, default=0.0)
    iva = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)
    procesado = db.Column(db.Boolean, default=False)
    asiento_id = db.Column(db.Integer, db.ForeignKey('asientos.id'), nullable=True)
    archivo_origen = db.Column(db.String(300))
    conciliacion_id = db.Column(db.Integer, db.ForeignKey('conciliaciones.id'), nullable=True)

    asiento = db.relationship('Asiento', foreign_keys=[asiento_id])
    conciliacion = db.relationship('Conciliacion', backref=db.backref('documentos', lazy='select'))


class MovimientoBanco(db.Model):
    __tablename__ = 'movimientos_banco'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    banco = db.Column(db.String(100))
    cuenta_bancaria = db.Column(db.String(50))
    fecha = db.Column(db.Date)
    descripcion = db.Column(db.String(500))
    cargo = db.Column(db.Float, default=0.0)
    abono = db.Column(db.Float, default=0.0)
    saldo = db.Column(db.Float, nullable=True)
    procesado = db.Column(db.Boolean, default=False)
    asiento_id = db.Column(db.Integer, db.ForeignKey('asientos.id'), nullable=True)
    archivo_origen = db.Column(db.String(300))

    asiento = db.relationship('Asiento', foreign_keys=[asiento_id])
    conciliacion_id = db.Column(db.Integer, db.ForeignKey('conciliaciones.id'), nullable=True)
    conciliacion = db.relationship('Conciliacion', backref=db.backref('movimientos', lazy='select'))


class Empleado(db.Model):
    __tablename__ = 'empleados'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    rut = db.Column(db.String(12), nullable=False)
    nombre = db.Column(db.String(200), nullable=False)
    cargo = db.Column(db.String(200))
    fecha_ingreso = db.Column(db.Date)
    tipo_contrato = db.Column(db.String(20), default='INDEFINIDO')
    sueldo_base = db.Column(db.Float, default=0.0)
    # Previsión
    afp = db.Column(db.String(50), default='Habitat')
    tasa_afp_comision = db.Column(db.Float, default=0.0127)   # solo la comisión AFP (no los 10%)
    tipo_salud = db.Column(db.String(10), default='FONASA')   # FONASA | ISAPRE
    isapre = db.Column(db.String(100))
    monto_isapre = db.Column(db.Float, default=0.0)           # adicional sobre 7% si isapre (legacy pesos)
    tipo_sueldo = db.Column(db.String(10), default='BRUTO')   # BRUTO | LIQUIDO
    monto_isapre_uf = db.Column(db.Float, default=0.0)        # Plan isapre en UF (not pesos)
    # Haberes fijos mensuales
    bono_colacion = db.Column(db.Float, default=0.0)
    bono_movilizacion = db.Column(db.Float, default=0.0)
    otros_haberes = db.Column(db.Float, default=0.0)
    # Mutual de seguridad (tasa empleador)
    tasa_mutual = db.Column(db.Float, default=0.0093)
    apv_monto = db.Column(db.Float, default=0.0)   # APV mensual en pesos
    apv_tipo  = db.Column(db.String(1), default='A')  # 'A' o 'B'
    activo = db.Column(db.Boolean, default=True)

    empresa = db.relationship('Empresa', backref=db.backref('empleados', lazy='dynamic'))
    liquidaciones = db.relationship('Liquidacion', backref='empleado', lazy='dynamic',
                                    cascade='all, delete-orphan')


class Liquidacion(db.Model):
    __tablename__ = 'liquidaciones'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleados.id'), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)   # YYYY-MM
    # Haberes del mes
    sueldo_base = db.Column(db.Float, default=0.0)
    horas_extra = db.Column(db.Float, default=0.0)
    bono_colacion = db.Column(db.Float, default=0.0)
    bono_movilizacion = db.Column(db.Float, default=0.0)
    otros_haberes = db.Column(db.Float, default=0.0)
    gratificacion = db.Column(db.Float, default=0.0)
    # Totales calculados
    total_haberes = db.Column(db.Float, default=0.0)
    renta_imponible = db.Column(db.Float, default=0.0)
    afp = db.Column(db.Float, default=0.0)
    salud = db.Column(db.Float, default=0.0)
    cesantia_trab = db.Column(db.Float, default=0.0)
    impuesto_renta = db.Column(db.Float, default=0.0)
    total_descuentos = db.Column(db.Float, default=0.0)
    liquido = db.Column(db.Float, default=0.0)
    # Aportes empleador
    sis = db.Column(db.Float, default=0.0)
    cesantia_emp = db.Column(db.Float, default=0.0)
    mutual = db.Column(db.Float, default=0.0)
    costo_empresa = db.Column(db.Float, default=0.0)
    apv = db.Column(db.Float, default=0.0)
    # Referencia UTM usada
    utm = db.Column(db.Float, default=68306.0)
    estado = db.Column(db.String(15), default='BORRADOR')   # BORRADOR | EMITIDA
    creado_en = db.Column(db.DateTime, default=datetime.now)

    empresa = db.relationship('Empresa', backref=db.backref('liquidaciones', lazy='dynamic'))


class VariablesMensuales(db.Model):
    __tablename__ = 'variables_mensuales'
    id = db.Column(db.Integer, primary_key=True)
    periodo = db.Column(db.String(7), nullable=False, unique=True)  # YYYY-MM
    uf = db.Column(db.Float)               # UF del último día hábil del mes
    utm = db.Column(db.Float)              # UTM del mes
    tope_imponible = db.Column(db.Float)   # 90 UF en pesos
    tope_gratificacion = db.Column(db.Float)  # 4.75 * IMM / 12 en pesos
    imm = db.Column(db.Float)             # Ingreso Mínimo Mensual
    tasa_sis = db.Column(db.Float)        # SIS (empleador) en decimal, ej: 0.0162
    tasas_afp_json = db.Column(db.Text)   # JSON {"Capital": 1.44, "Habitat": 1.27, ...} en %
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.now)

    def get_tasas_afp(self):
        """Return AFP commissions as dict {name: decimal}, e.g. {'Habitat': 0.0127}."""
        import json
        if not self.tasas_afp_json:
            return {}
        try:
            raw = json.loads(self.tasas_afp_json)
            return {k: round(v / 100, 6) for k, v in raw.items()}
        except Exception:
            return {}


class ValorUF(db.Model):
    __tablename__ = 'valores_uf'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False, unique=True)
    valor = db.Column(db.Float, nullable=False)


class Prestamo(db.Model):
    __tablename__ = 'prestamos'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    empresa_relacionada_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    nombre = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)       # PAGAR | COBRAR
    moneda = db.Column(db.String(5), default='PESOS')     # PESOS | UF
    monto_original = db.Column(db.Float, nullable=False)
    tasa_interes_anual = db.Column(db.Float, default=0.0)
    fecha_inicio = db.Column(db.Date, nullable=False)
    n_cuotas = db.Column(db.Integer, nullable=True)       # None = LIBRE
    periodicidad = db.Column(db.String(10), default='MENSUAL')  # MENSUAL|TRIMESTRAL|ANUAL|LIBRE
    acreedor_deudor = db.Column(db.String(200))
    acreedor_rut = db.Column(db.String(20))
    activo = db.Column(db.Boolean, default=True)
    notas = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

    empresa = db.relationship('Empresa', foreign_keys=[empresa_id],
                              backref=db.backref('prestamos', lazy='dynamic'))
    empresa_relacionada = db.relationship('Empresa', foreign_keys=[empresa_relacionada_id])
    cuotas = db.relationship('CuotaPrestamo', backref='prestamo',
                             order_by='CuotaPrestamo.numero_cuota',
                             cascade='all, delete-orphan', lazy='select')


class CuotaPrestamo(db.Model):
    __tablename__ = 'cuotas_prestamo'
    id = db.Column(db.Integer, primary_key=True)
    prestamo_id = db.Column(db.Integer, db.ForeignKey('prestamos.id'), nullable=False)
    numero_cuota = db.Column(db.Integer, nullable=False)
    fecha_vencimiento = db.Column(db.Date, nullable=False)
    capital = db.Column(db.Float, default=0.0)
    interes = db.Column(db.Float, default=0.0)
    cuota_total = db.Column(db.Float, default=0.0)
    saldo_insoluto = db.Column(db.Float, default=0.0)
    pagada = db.Column(db.Boolean, default=False)
    fecha_pago = db.Column(db.Date, nullable=True)
    movimiento_banco_id = db.Column(db.Integer, db.ForeignKey('movimientos_banco.id'), nullable=True)
    notas = db.Column(db.String(300))
    asiento_id = db.Column(db.Integer, db.ForeignKey('asientos.id'), nullable=True)
    uf_valor_pago = db.Column(db.Float, nullable=True)
    cuota_total_pesos = db.Column(db.Float, nullable=True)

    asiento = db.relationship('Asiento', foreign_keys=[asiento_id])


class VacacionEmpleado(db.Model):
    __tablename__ = 'vacaciones_empleado'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleados.id'), nullable=False)
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_fin = db.Column(db.Date, nullable=False)
    dias_habiles = db.Column(db.Integer, default=0)
    notas = db.Column(db.String(300))
    asiento_id = db.Column(db.Integer, db.ForeignKey('asientos.id'), nullable=True)
    creado_en = db.Column(db.DateTime, default=datetime.now)

    empleado = db.relationship('Empleado', backref=db.backref('vacaciones', lazy='dynamic'))
    asiento = db.relationship('Asiento', foreign_keys=[asiento_id])


class ReglaClasificacion(db.Model):
    __tablename__ = 'reglas_clasificacion'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    tipo_origen = db.Column(db.String(20))      # LIBRO_COMPRAS, LIBRO_VENTAS, HONORARIOS, BANCO
    tipo_dte = db.Column(db.String(10))          # para filtrar por tipo de DTE (opcional)
    patron_descripcion = db.Column(db.String(200))  # texto a buscar en descripcion (opcional)
    cuenta_debe_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=True)
    cuenta_haber_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=True)
    descripcion_asiento = db.Column(db.String(300))
    orden = db.Column(db.Integer, default=0)
    activa = db.Column(db.Boolean, default=True)

    cuenta_debe = db.relationship('Cuenta', foreign_keys=[cuenta_debe_id])
    cuenta_haber = db.relationship('Cuenta', foreign_keys=[cuenta_haber_id])


class ArchivoImportado(db.Model):
    __tablename__ = 'archivos_importados'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)   # COMPRAS, VENTAS, HONORARIOS, BANCO
    nombre_archivo = db.Column(db.String(300))
    sha256 = db.Column(db.String(64), nullable=False)
    fecha_importacion = db.Column(db.DateTime, default=datetime.now)
    ndocs = db.Column(db.Integer, default=0)
    periodo = db.Column(db.String(7))        # "YYYY-MM" of most docs, nullable
    banco = db.Column(db.String(100))        # only for BANCO tipo
    cuenta_bancaria = db.Column(db.String(50))

    empresa = db.relationship('Empresa', backref=db.backref('archivos', lazy='dynamic'))


class ActivoFijo(db.Model):
    __tablename__ = 'activos_fijos'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    nombre = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.String(500))
    categoria = db.Column(db.String(15), nullable=False)
    # TERRENO | CONSTRUCCION | MAQUINARIA | VEHICULO | MUEBLE | EQUIPO_COMP
    valor_compra = db.Column(db.Float, nullable=False)
    valor_residual = db.Column(db.Float, default=0.0)
    vida_util_meses = db.Column(db.Integer, nullable=False, default=60)
    fecha_compra = db.Column(db.Date, nullable=False)
    metodo = db.Column(db.String(10), default='LINEAL')  # LINEAL | ACELERADO
    cuenta_activo_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=True)
    cuenta_dep_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=True)
    activo = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=datetime.now)

    empresa = db.relationship('Empresa', backref=db.backref('activos_fijos', lazy='dynamic'))
    cuenta_activo = db.relationship('Cuenta', foreign_keys=[cuenta_activo_id])
    cuenta_dep = db.relationship('Cuenta', foreign_keys=[cuenta_dep_id])


class DepreciacionRegistro(db.Model):
    __tablename__ = 'depreciacion_registros'
    id = db.Column(db.Integer, primary_key=True)
    activo_fijo_id = db.Column(db.Integer, db.ForeignKey('activos_fijos.id'), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)  # YYYY-MM
    monto = db.Column(db.Float, nullable=False)
    asiento_id = db.Column(db.Integer, db.ForeignKey('asientos.id'), nullable=True)

    activo_fijo = db.relationship('ActivoFijo', backref=db.backref('depreciaciones', lazy='dynamic'))
    asiento = db.relationship('Asiento')
