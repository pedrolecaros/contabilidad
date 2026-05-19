"""
Motor de journalización automática.
Genera asientos en partida doble a partir de documentos SII o movimientos bancarios.
"""
from models import db, Asiento, LineaAsiento, Cuenta, DocumentoSII, MovimientoBanco, Conciliacion


TASA_IVA = 0.19
TASA_RETENCION_HONORARIOS = 0.1075

# Tipos DTE que son notas de crédito (invierten el asiento)
TIPOS_NC = {'61', '56'}
# Tipos DTE que son facturas de compra (en libro ventas)
TIPOS_FACTURA_COMPRA = {'46'}


def _buscar_cuenta(empresa_id, codigo):
    return Cuenta.query.filter_by(empresa_id=empresa_id, codigo=codigo, activa=True).first()


def _proximo_numero(empresa_id):
    ultimo = (Asiento.query
              .filter_by(empresa_id=empresa_id)
              .order_by(Asiento.numero.desc())
              .first())
    return (ultimo.numero or 0) + 1 if ultimo else 1


def _validar_doc(doc):
    if not doc.fecha:
        raise ValueError(f"Folio {doc.folio} sin fecha válida — revisa el archivo importado")


def generar_asiento_compra(doc: DocumentoSII) -> Asiento:
    """
    Factura de compra (libro de compras):
      DEBE  Gasto (5.2.17 por defecto)       monto_neto
      DEBE  IVA Crédito Fiscal (1.1.05)      iva
      HABER Proveedores (2.1.01)             total

    Nota de crédito de compra: asiento inverso.
    """
    _validar_doc(doc)
    emp_id = doc.empresa_id
    es_nc = str(doc.tipo_dte) in TIPOS_NC

    c_gasto = _buscar_cuenta(emp_id, '5.2.17')
    c_iva_cf = _buscar_cuenta(emp_id, '1.1.05')
    c_prov = _buscar_cuenta(emp_id, '2.1.01')

    if not all([c_gasto, c_iva_cf, c_prov]):
        raise ValueError("Faltan cuentas del plan de cuentas (5.2.17, 1.1.05, 2.1.01)")

    iva   = abs(doc.iva)    # solo IVA recuperable
    total = abs(doc.total)
    # Gasto = total - IVA recuperable (absorbe IVA no recuperable si lo hay)
    gasto = total - iva

    contraparte = (doc.razon_social_contraparte or doc.rut_contraparte or '')[:60]
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=doc.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio}" + (f" - {contraparte}" if contraparte else ""),
        origen='LIBRO_COMPRAS',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    lineas = []
    if not es_nc:
        if gasto:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_gasto.id,
                                       debe=gasto, haber=0, descripcion=contraparte or 'Gasto', orden=1))
        if iva:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_iva_cf.id,
                                       debe=iva, haber=0, descripcion='IVA CF', orden=2))
        if total:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_prov.id,
                                       debe=0, haber=total, descripcion=contraparte or 'Proveedor', orden=3))
    else:
        # Nota de crédito: inverso
        if total:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_prov.id,
                                       debe=total, haber=0, descripcion=f'{contraparte} NC' if contraparte else 'Proveedor NC', orden=1))
        if gasto:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_gasto.id,
                                       debe=0, haber=gasto, descripcion='Reverso gasto NC', orden=2))
        if iva:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_iva_cf.id,
                                       debe=0, haber=iva, descripcion='Reverso IVA CF NC', orden=3))

    db.session.add_all(lineas)
    return asiento


def generar_asiento_venta(doc: DocumentoSII) -> Asiento:
    """
    Factura de venta (libro de ventas):
      DEBE  Clientes (1.1.03)               total
      HABER Ventas Afectas (4.1.01)         monto_neto
      HABER IVA Débito Fiscal (2.1.03)      iva

    Facturas exentas usan 4.1.02 y no tienen IVA.
    Nota de crédito de venta: asiento inverso.
    """
    _validar_doc(doc)
    emp_id = doc.empresa_id
    es_nc = str(doc.tipo_dte) in TIPOS_NC
    es_exenta = doc.iva == 0 and doc.monto_exento > 0

    c_clientes = _buscar_cuenta(emp_id, '1.1.03')
    c_ventas = _buscar_cuenta(emp_id, '4.1.02' if es_exenta else '4.1.01')
    c_iva_df = _buscar_cuenta(emp_id, '2.1.03')

    if not all([c_clientes, c_ventas, c_iva_df]):
        raise ValueError("Faltan cuentas del plan de cuentas (1.1.03, 4.1.01/02, 2.1.03)")

    neto = abs(doc.monto_neto) + abs(doc.monto_exento)
    iva = abs(doc.iva)
    total = abs(doc.total)

    contraparte = (doc.razon_social_contraparte or doc.rut_contraparte or '')[:60]
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=doc.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=f"Factura venta {doc.tipo_dte} N°{doc.folio}" + (f" - {contraparte}" if contraparte else ""),
        origen='LIBRO_VENTAS',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    lineas = []
    if not es_nc:
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_clientes.id,
                                   debe=total, haber=0, descripcion=contraparte or 'Cliente', orden=1))
        if neto:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_ventas.id,
                                       debe=0, haber=neto, descripcion=contraparte or 'Ingreso neto', orden=2))
        if iva:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_iva_df.id,
                                       debe=0, haber=iva, descripcion='IVA DF', orden=3))
    else:
        if neto:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_ventas.id,
                                       debe=neto, haber=0, descripcion='Reverso ingreso NC', orden=1))
        if iva:
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_iva_df.id,
                                       debe=iva, haber=0, descripcion='Reverso IVA DF NC', orden=2))
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_clientes.id,
                                   debe=0, haber=total, descripcion=f'{contraparte} NC' if contraparte else 'Cliente NC', orden=3))

    db.session.add_all(lineas)
    return asiento


def generar_asiento_honorario(doc: DocumentoSII) -> Asiento:
    """
    Boleta de honorarios:
      DEBE  Honorarios (5.2.02)               monto_neto (bruto)
      HABER Proveedores (2.1.01)              líquido a pagar
      HABER Retención Honorarios por Pagar    retención (10.75%)
    """
    _validar_doc(doc)
    emp_id = doc.empresa_id

    c_honor = _buscar_cuenta(emp_id, '5.2.02')
    c_prov = _buscar_cuenta(emp_id, '2.1.01')
    c_reten = _buscar_cuenta(emp_id, '2.1.04')

    if not all([c_honor, c_prov, c_reten]):
        raise ValueError("Faltan cuentas del plan de cuentas (5.2.02, 2.1.01, 2.1.04)")

    bruto = abs(doc.total or doc.monto_neto)
    retencion = round(bruto * TASA_RETENCION_HONORARIOS)
    liquido = bruto - retencion

    contraparte = (doc.razon_social_contraparte or doc.rut_contraparte or '')[:60]
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=doc.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=f"Honorarios N°{doc.folio}" + (f" - {contraparte}" if contraparte else ""),
        origen='HONORARIOS',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()
    lineas = [
        LineaAsiento(asiento_id=asiento.id, cuenta_id=c_honor.id,
                     debe=bruto, haber=0, descripcion=contraparte or 'Honorario bruto', orden=1),
        LineaAsiento(asiento_id=asiento.id, cuenta_id=c_prov.id,
                     debe=0, haber=liquido, descripcion=f'Líquido {contraparte}' if contraparte else 'Líquido a pagar', orden=2),
        LineaAsiento(asiento_id=asiento.id, cuenta_id=c_reten.id,
                     debe=0, haber=retencion, descripcion='Retención 10.75%', orden=3),
    ]
    db.session.add_all(lineas)
    return asiento


def generar_asiento_banco(mov: MovimientoBanco, cuenta_contraparte_id: int) -> Asiento:
    """
    Movimiento bancario con cuenta contraparte asignada por el usuario.
    Cargo  (salida): DEBE contraparte / HABER banco
    Abono  (entrada): DEBE banco / HABER contraparte
    """
    emp_id = mov.empresa_id
    c_banco = _buscar_cuenta(emp_id, '1.1.02')
    c_contra = Cuenta.query.get(cuenta_contraparte_id)

    if not c_banco or not c_contra:
        raise ValueError("Cuenta banco (1.1.02) o cuenta contraparte no encontrada")

    asiento = Asiento(
        empresa_id=emp_id,
        fecha=mov.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=mov.descripcion,
        origen='BANCO',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    desc = (mov.descripcion or '')[:80]
    if mov.cargo > 0:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_contra.id,
                         debe=mov.cargo, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=0, haber=mov.cargo, descripcion=desc, orden=2),
        ]
    else:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=mov.abono, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_contra.id,
                         debe=0, haber=mov.abono, descripcion=desc, orden=2),
        ]

    db.session.add_all(lineas)
    return asiento


def generar_asiento_banco_compuesto(mov: MovimientoBanco,
                                    cuenta_ids: list,
                                    montos: list) -> Asiento:
    """
    Movimiento bancario con múltiples cuentas contraparte (ej. F29: retención + IVA + PPM).
    Cargo  (salida): DEBE cada cuenta por su monto / HABER banco total
    Abono  (entrada): DEBE banco total / HABER cada cuenta por su monto
    """
    emp_id = mov.empresa_id
    c_banco = _buscar_cuenta(emp_id, '1.1.02')
    if not c_banco:
        raise ValueError("Cuenta banco (1.1.02) no encontrada")

    if not cuenta_ids:
        raise ValueError("Se requiere al menos una cuenta contraparte")
    if not montos or len(montos) != len(cuenta_ids):
        total = (mov.cargo or 0) + (mov.abono or 0)
        montos = [round(total / len(cuenta_ids))] * len(cuenta_ids)

    cuentas_contra = []
    for cid in cuenta_ids:
        c = Cuenta.query.get(cid)
        if not c:
            raise ValueError(f"Cuenta id={cid} no encontrada")
        cuentas_contra.append(c)

    total = sum(montos)
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=mov.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=(mov.descripcion or '')[:120],
        origen='BANCO',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    lineas = []
    if (mov.cargo or 0) > 0:
        for i, (c, monto) in enumerate(zip(cuentas_contra, montos)):
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c.id,
                                       debe=monto, haber=0, orden=i + 1))
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                   debe=0, haber=total, orden=len(cuenta_ids) + 1))
    else:
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                   debe=total, haber=0, orden=1))
        for i, (c, monto) in enumerate(zip(cuentas_contra, montos)):
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c.id,
                                       debe=0, haber=monto, orden=i + 2))
    db.session.add_all(lineas)
    return asiento


def generar_asiento_pago_proveedor(mov: MovimientoBanco) -> Asiento:
    """
    Pago a proveedor vinculado a doc SII (compra/honorario):
      DEBE  Proveedores (2.1.01)   monto del cargo bancario
      HABER Banco       (1.1.02)   monto del cargo bancario
    """
    emp_id = mov.empresa_id
    c_banco = _buscar_cuenta(emp_id, '1.1.02')
    c_prov  = _buscar_cuenta(emp_id, '2.1.01')
    if not c_banco or not c_prov:
        raise ValueError("Faltan cuentas 1.1.02 o 2.1.01 en el plan de cuentas")

    monto = mov.cargo if (mov.cargo or 0) > 0 else (mov.abono or 0)
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=mov.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=f"Pago proveedor: {(mov.descripcion or '')[:60]}",
        origen='BANCO',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    desc = (mov.descripcion or '')[:80]
    if (mov.cargo or 0) > 0:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_prov.id,
                         debe=monto, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=0, haber=monto, descripcion=desc, orden=2),
        ]
    else:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=monto, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_prov.id,
                         debe=0, haber=monto, descripcion=desc, orden=2),
        ]
    db.session.add_all(lineas)
    return asiento


def generar_asiento_cobro_cliente(mov: MovimientoBanco) -> Asiento:
    """
    Cobro de cliente vinculado a doc SII (venta):
      DEBE  Banco     (1.1.02)   monto del abono bancario
      HABER Clientes  (1.1.03)   monto del abono bancario
    """
    emp_id = mov.empresa_id
    c_banco = _buscar_cuenta(emp_id, '1.1.02')
    c_cli   = _buscar_cuenta(emp_id, '1.1.03')
    if not c_banco or not c_cli:
        raise ValueError("Faltan cuentas 1.1.02 o 1.1.03 en el plan de cuentas")

    monto = mov.abono if (mov.abono or 0) > 0 else (mov.cargo or 0)
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=mov.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=f"Cobro cliente: {(mov.descripcion or '')[:60]}",
        origen='BANCO',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    desc = (mov.descripcion or '')[:80]
    if (mov.abono or 0) > 0:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=monto, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_cli.id,
                         debe=0, haber=monto, descripcion=desc, orden=2),
        ]
    else:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_cli.id,
                         debe=monto, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=0, haber=monto, descripcion=desc, orden=2),
        ]
    db.session.add_all(lineas)
    return asiento




def confirmar_asiento(asiento: Asiento):
    if not asiento.cuadrado:
        raise ValueError(f"Asiento no cuadra: Debe={asiento.total_debe} Haber={asiento.total_haber}")
    for linea in asiento.lineas:
        cuenta = Cuenta.query.get(linea.cuenta_id)
        if cuenta and not cuenta.activa:
            raise ValueError(f"La cuenta '{cuenta.nombre}' ({cuenta.codigo}) está inactiva")
        if cuenta and cuenta.es_titulo:
            raise ValueError(f"La cuenta '{cuenta.nombre}' ({cuenta.codigo}) es de título y no puede recibir movimientos")
    asiento.estado = 'CONFIRMADO'


def anular_asiento(asiento: Asiento):
    asiento.estado = 'ANULADO'

    # Collect conciliation IDs before clearing them
    conc_ids = set()
    for d in DocumentoSII.query.filter_by(asiento_id=asiento.id).all():
        if d.conciliacion_id:
            conc_ids.add(d.conciliacion_id)
    for m in MovimientoBanco.query.filter_by(asiento_id=asiento.id).all():
        if m.conciliacion_id:
            conc_ids.add(m.conciliacion_id)

    DocumentoSII.query.filter_by(asiento_id=asiento.id).update(
        {'procesado': False, 'asiento_id': None, 'conciliacion_id': None})
    MovimientoBanco.query.filter_by(asiento_id=asiento.id).update(
        {'procesado': False, 'asiento_id': None, 'conciliacion_id': None})

    # Clean up orphaned Conciliacion records
    for cid in conc_ids:
        if (not DocumentoSII.query.filter_by(conciliacion_id=cid).first()
                and not MovimientoBanco.query.filter_by(conciliacion_id=cid).first()):
            conc = Conciliacion.query.get(cid)
            if conc:
                db.session.delete(conc)

    # CuotaPrestamo system removed; no cuota cleanup needed here
