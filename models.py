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
    requiere_aux = db.Column(db.Boolean, default=False, nullable=False)

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
    prestamo_id = db.Column(db.Integer, db.ForeignKey('prestamos.id'), nullable=True)
    prestamo_sentido = db.Column(db.String(5), nullable=True)

    prestamo = db.relationship('Prestamo', foreign_keys=[prestamo_id],
                               backref=db.backref('asientos_vinculados', lazy='dynamic'))
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
    contraparte_id = db.Column(db.Integer, db.ForeignKey('contrapartes.id'), nullable=True)
    debe = db.Column(db.Float, default=0.0)
    haber = db.Column(db.Float, default=0.0)
    descripcion = db.Column(db.String(300))
    orden = db.Column(db.Integer, default=0)

    contraparte = db.relationship('Contraparte', backref=db.backref('lineas_asiento', lazy='dynamic'))


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
    # empresa_id = empresa donde se creó (legacy / informativo). Las contrapartes son globales.
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    rut = db.Column(db.String(12), nullable=False)
    razon_social = db.Column(db.String(200), nullable=False)
    # PROVEEDOR, CLIENTE, AMBOS, HONORARIOS, RELACIONADA, OTRO
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


class DeclaracionF29(db.Model):
    """F29 mensual descargado del portal SII. Guarda los códigos clave parseados
    (PPM, retenciones, IVA débito/crédito, total a pagar) para conciliar contra
    los pasivos tributarios."""
    __tablename__ = 'declaraciones_f29'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)  # YYYY-MM
    folio = db.Column(db.String(30))
    fecha_descarga = db.Column(db.DateTime, default=datetime.now)
    # Códigos típicos parseados (campos rápidos para query)
    codigo_62  = db.Column(db.Float, default=0.0)  # PPM Neto Determinado
    codigo_48  = db.Column(db.Float, default=0.0)  # Retención Imp. Único Trabajadores (art 74 N°1)
    codigo_39  = db.Column(db.Float, default=0.0)  # Retención honorarios 10% (Ley antigua)
    codigo_151 = db.Column(db.Float, default=0.0)  # Retención honorarios Ley 21.133
    codigo_89  = db.Column(db.Float, default=0.0)  # Imp. determ. IVA (débito - crédito)
    codigo_538 = db.Column(db.Float, default=0.0)  # IVA Débito Fiscal
    codigo_537 = db.Column(db.Float, default=0.0)  # IVA Crédito Fiscal
    codigo_547 = db.Column(db.Float, default=0.0)  # Total Determinado (formulario)
    codigo_91  = db.Column(db.Float, default=0.0)  # Total a pagar
    codigo_92  = db.Column(db.Float, default=0.0)  # Reajustes / IPC
    # Todos los códigos parseados como JSON (para inspección/futuros)
    codigos_json = db.Column(db.Text, default='{}')
    respaldo_url = db.Column(db.String(500))  # HTML/PDF guardado en storage

    empresa = db.relationship('Empresa', backref=db.backref('declaraciones_f29', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('empresa_id', 'periodo', name='uix_f29_emp_periodo'),)


class Empleado(db.Model):
    __tablename__ = 'empleados'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    rut = db.Column(db.String(12), nullable=False)
    nombre = db.Column(db.String(200), nullable=False)
    apellido_paterno = db.Column(db.String(100))
    apellido_materno = db.Column(db.String(100))
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

    @property
    def nombre_completo(self):
        if self.apellido_paterno:
            am = (' ' + self.apellido_materno) if self.apellido_materno else ''
            return f"{self.apellido_paterno}{am}, {self.nombre}"
        return self.nombre


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
    afp_emp = db.Column(db.Float, default=0.0)
    ev_emp = db.Column(db.Float, default=0.0)
    cesantia_emp = db.Column(db.Float, default=0.0)
    mutual = db.Column(db.Float, default=0.0)
    costo_empresa = db.Column(db.Float, default=0.0)
    apv = db.Column(db.Float, default=0.0)
    # Referencia UTM usada
    utm = db.Column(db.Float, default=68306.0)
    estado = db.Column(db.String(15), default='BORRADOR')   # BORRADOR | EMITIDA
    creado_en = db.Column(db.DateTime, default=datetime.now)
    archivo_url = db.Column(db.String(500))   # PDF generado al emitir

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
    subtipo = db.Column(db.String(15), default='BANCARIO')  # BANCARIO | TERCEROS | RELACIONADA
    moneda = db.Column(db.String(5), default='PESOS')     # PESOS | UF
    monto_original = db.Column(db.Float, nullable=False)
    fecha_inicio = db.Column(db.Date, nullable=False)
    acreedor_deudor = db.Column(db.String(200))
    acreedor_rut = db.Column(db.String(20))
    activo = db.Column(db.Boolean, default=True)
    notas = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

    empresa = db.relationship('Empresa', foreign_keys=[empresa_id],
                              backref=db.backref('prestamos', lazy='dynamic'))
    empresa_relacionada = db.relationship('Empresa', foreign_keys=[empresa_relacionada_id])

    def saldo_actual(self):
        saldo = float(self.monto_original or 0)
        for a in self.asientos_vinculados.filter_by(estado='CONFIRMADO').all():
            monto = max(a.total_debe or 0, a.total_haber or 0)
            saldo += monto if (a.prestamo_sentido or '-') == '+' else -monto
        return saldo


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


class Papelera(db.Model):
    __tablename__ = 'papelera'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    tipo = db.Column(db.String(30), nullable=False)
    # ASIENTO, LIQUIDACION, DOCUMENTO_SII, MOVIMIENTO_BANCO
    objeto_id = db.Column(db.Integer, nullable=False)   # original PK (for display)
    descripcion = db.Column(db.String(500))             # human-readable label
    datos_json = db.Column(db.Text, nullable=False)     # JSON snapshot
    deleted_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

    empresa = db.relationship('Empresa', backref=db.backref('papelera_items', lazy='dynamic'))


class Historial(db.Model):
    """Bitácora de acciones por empresa: cada operación importante deja una línea
    con snapshot JSON del objeto (útil para auditar y, cuando aplica, revertir)."""
    __tablename__ = 'historial'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    fecha = db.Column(db.DateTime, default=datetime.now, nullable=False)
    accion = db.Column(db.String(20), nullable=False)
    # CREAR | EDITAR | ELIMINAR | CONFIRMAR | ANULAR | IMPORTAR | CONCILIAR | REVERTIR | OTRO
    tipo_objeto = db.Column(db.String(30), nullable=False)
    # ASIENTO | LINEA_ASIENTO | CONCILIACION | DOCUMENTO_SII | MOVIMIENTO_BANCO |
    # ARCHIVO_IMPORTADO | CONTRAPARTE | CUENTA | PRESTAMO | LIQUIDACION | NOTA | OTRO
    objeto_id = db.Column(db.Integer)
    descripcion = db.Column(db.String(500))
    datos_json = db.Column(db.Text)  # snapshot del objeto (pre o post)
    revertible = db.Column(db.Boolean, default=False)

    empresa = db.relationship('Empresa', backref=db.backref('historial', lazy='dynamic',
                                                             order_by='Historial.fecha.desc()'))


class NotaContable(db.Model):
    __tablename__ = 'notas_contables'
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), primary_key=True)
    contenido = db.Column(db.Text, default='')
    actualizado_en = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    empresa = db.relationship('Empresa', backref=db.backref('nota', uselist=False))


class DocumentoEmpleado(db.Model):
    __tablename__ = 'documentos_empleado'
    id = db.Column(db.Integer, primary_key=True)
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleados.id'), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    # CONTRATO | ANEXO | FINIQUITO | OTRO
    tipo = db.Column(db.String(20), nullable=False, default='CONTRATO')
    descripcion = db.Column(db.String(300))
    fecha_documento = db.Column(db.Date)
    archivo_url = db.Column(db.String(500), nullable=False)
    creado_en = db.Column(db.DateTime, default=datetime.now)

    empleado = db.relationship('Empleado', backref=db.backref('documentos', lazy='dynamic',
                                                               order_by='DocumentoEmpleado.fecha_documento.desc()'))
    empresa = db.relationship('Empresa')

