"""
Motor de journalización automática.
Genera asientos en partida doble a partir de documentos SII o movimientos bancarios.
"""
from models import db, Asiento, LineaAsiento, Cuenta, DocumentoSII, MovimientoBanco, Conciliacion
from engine.plan_cuentas_default import CUENTAS_SISTEMA as _C, es_movimiento_tc


TASA_IVA = 0.19

# Retención honorarios — escalada por Ley 21.133 (años calendario).
# Para años no tabulados se usa el último valor conocido (≥ 2027 → 17%).
_RETENCION_HONOR_POR_ANIO = {
    2019: 0.1000, 2020: 0.1075, 2021: 0.1150, 2022: 0.1225,
    2023: 0.1300, 2024: 0.1375, 2025: 0.1450, 2026: 0.1525,
    2027: 0.1700,
}


def tasa_retencion_honorarios(anio: int | None = None) -> float:
    """Tasa de retención de honorarios para el año dado (default: año en curso)."""
    if anio is None:
        from datetime import date
        anio = date.today().year
    if anio in _RETENCION_HONOR_POR_ANIO:
        return _RETENCION_HONOR_POR_ANIO[anio]
    # Antes del 2019 → 10% histórico; después de 2027 → 17%
    return 0.1000 if anio < 2019 else 0.1700


# Compat: constante para código legado (usa el año en curso)
TASA_RETENCION_HONORARIOS = tasa_retencion_honorarios()

# Tipos DTE que son notas de crédito (invierten el asiento)
TIPOS_NC = {'61', '56'}
# Tipos DTE que son facturas de compra (en libro ventas)
TIPOS_FACTURA_COMPRA = {'46'}


def _buscar_cuenta(empresa_id, codigo):
    return Cuenta.query.filter_by(empresa_id=empresa_id, codigo=codigo, activa=True).first()


def _cuenta_lado_banco(mov):
    """Devuelve la cuenta del lado bancario para un movimiento:
    - Banco corriente normal → 1.1.02 Banco
    - Tarjeta de crédito     → 2.1.14 Tarjeta de Crédito (con fallback a Banco si no existe)
    """
    if es_movimiento_tc(mov.banco):
        c = _buscar_cuenta(mov.empresa_id, _C['TARJETA_CREDITO'])
        if c:
            return c
    return _buscar_cuenta(mov.empresa_id, _C['BANCO'])


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

    c_gasto = _buscar_cuenta(emp_id, _C['GASTO_GENERAL'])
    c_iva_cf = _buscar_cuenta(emp_id, _C['IVA_CF'])
    c_prov = _buscar_cuenta(emp_id, _C['PROVEEDORES'])

    if not all([c_gasto, c_iva_cf, c_prov]):
        raise ValueError(f"Faltan cuentas del plan de cuentas ({_C['GASTO_GENERAL']}, {_C['IVA_CF']}, {_C['PROVEEDORES']})")

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

    c_clientes = _buscar_cuenta(emp_id, _C['CLIENTES'])
    c_ventas = _buscar_cuenta(emp_id, _C['VENTAS_EXENTAS'] if es_exenta else _C['VENTAS_AFECTAS'])
    c_iva_df = _buscar_cuenta(emp_id, _C['IVA_DF'])

    if not all([c_clientes, c_ventas, c_iva_df]):
        raise ValueError(f"Faltan cuentas del plan de cuentas ({_C['CLIENTES']}, {_C['VENTAS_AFECTAS']}/{_C['VENTAS_EXENTAS']}, {_C['IVA_DF']})")

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
      HABER Retención Honorarios por Pagar    retención (tasa anual, 15.25% en 2026)
    """
    _validar_doc(doc)
    emp_id = doc.empresa_id

    c_honor = _buscar_cuenta(emp_id, _C['HONORARIOS'])
    c_prov = _buscar_cuenta(emp_id, _C['PROVEEDORES'])
    c_reten = _buscar_cuenta(emp_id, _C['RET_HONORARIOS'])

    if not all([c_honor, c_prov, c_reten]):
        raise ValueError(f"Faltan cuentas del plan de cuentas ({_C['HONORARIOS']}, {_C['PROVEEDORES']}, {_C['RET_HONORARIOS']})")

    bruto = abs(doc.total or doc.monto_neto)
    # Tasa según el año del documento (no del año en curso)
    anio_doc = doc.fecha.year if doc.fecha else None
    tasa = tasa_retencion_honorarios(anio_doc)
    retencion = round(bruto * tasa)
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
                     debe=0, haber=retencion, descripcion=f'Retención {tasa*100:.2f}%', orden=3),
    ]
    db.session.add_all(lineas)
    return asiento


def generar_asiento_banco(mov: MovimientoBanco, cuenta_contraparte_id: int,
                          contraparte_id: int | None = None) -> Asiento:
    """
    Movimiento bancario con cuenta contraparte asignada por el usuario.
    Cargo  (salida): DEBE contraparte / HABER banco
    Abono  (entrada): DEBE banco / HABER contraparte
    """
    emp_id = mov.empresa_id
    c_banco = _cuenta_lado_banco(mov)
    c_contra = Cuenta.query.get(cuenta_contraparte_id)

    if not c_banco or not c_contra:
        raise ValueError(f"Cuenta banco/tarjeta o cuenta contraparte no encontrada")

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
                         contraparte_id=contraparte_id,
                         debe=mov.cargo, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=0, haber=mov.cargo, descripcion=desc, orden=2),
        ]
    else:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=mov.abono, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_contra.id,
                         contraparte_id=contraparte_id,
                         debe=0, haber=mov.abono, descripcion=desc, orden=2),
        ]

    db.session.add_all(lineas)
    return asiento


def generar_asiento_banco_multi(movs: list,
                                cuenta_ids: list,
                                montos: list,
                                aux_ids: list | None = None) -> Asiento:
    """Consolida varios movimientos bancarios del mismo lado (todos cargo o todos abono)
    en UN solo asiento. Las cuentas contraparte aparecen una vez con sus montos totales.
    Cada movimiento queda vinculado al mismo asiento.

    Use case: dos transferencias a la misma persona (préstamo) → un asiento único
    con 1.1.12 Préstamo a Tercero (aux: persona) HABER banco total.
    """
    if not movs:
        raise ValueError("Se requiere al menos un movimiento")
    emp_id = movs[0].empresa_id
    # Todos los movs en un asiento consolidado deben ser del mismo lado (TC o banco)
    es_tc = es_movimiento_tc(movs[0].banco)
    if any(es_movimiento_tc(m.banco) != es_tc for m in movs):
        raise ValueError("No se pueden consolidar movimientos de tarjeta de crédito con movimientos de banco corriente en un mismo asiento.")
    c_banco = _cuenta_lado_banco(movs[0])
    if not c_banco:
        raise ValueError("Cuenta banco/tarjeta no encontrada")

    if not cuenta_ids:
        raise ValueError("Se requiere al menos una cuenta contraparte")
    if aux_ids is None or len(aux_ids) != len(cuenta_ids):
        aux_ids = [None] * len(cuenta_ids)

    total_cargo = sum((m.cargo or 0) for m in movs)
    total_abono = sum((m.abono or 0) for m in movs)
    if total_cargo and total_abono:
        raise ValueError("Los movimientos seleccionados mezclan cargos y abonos; no se pueden consolidar.")
    monto_total = total_cargo + total_abono

    if not montos or len(montos) != len(cuenta_ids):
        montos = [round(monto_total / len(cuenta_ids))] * len(cuenta_ids)

    cuentas_contra = []
    for cid in cuenta_ids:
        c = Cuenta.query.get(cid)
        if not c:
            raise ValueError(f"Cuenta id={cid} no encontrada")
        cuentas_contra.append(c)

    fechas = [m.fecha for m in movs if m.fecha]
    descs = [(m.descripcion or '')[:60] for m in movs[:3]]
    desc = ' / '.join(d for d in descs if d)
    if len(movs) > 3:
        desc = (desc + f' (+{len(movs)-3} más)')[:120]
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=max(fechas) if fechas else None,
        numero=_proximo_numero(emp_id),
        descripcion=desc[:120] or 'Consolidado banco',
        origen='BANCO',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    lineas = []
    if total_cargo > 0:
        for i, (c, monto, aux) in enumerate(zip(cuentas_contra, montos, aux_ids)):
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c.id,
                                       contraparte_id=aux,
                                       debe=monto, haber=0, orden=i + 1))
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                   debe=0, haber=monto_total, orden=len(cuenta_ids) + 1))
    else:
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                   debe=monto_total, haber=0, orden=1))
        for i, (c, monto, aux) in enumerate(zip(cuentas_contra, montos, aux_ids)):
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c.id,
                                       contraparte_id=aux,
                                       debe=0, haber=monto, orden=i + 2))
    db.session.add_all(lineas)
    return asiento


def generar_asiento_banco_compuesto(mov: MovimientoBanco,
                                    cuenta_ids: list,
                                    montos: list,
                                    aux_ids: list | None = None) -> Asiento:
    """
    Movimiento bancario con múltiples cuentas contraparte (ej. F29: retención + IVA + PPM).
    Cargo  (salida): DEBE cada cuenta por su monto / HABER banco total
    Abono  (entrada): DEBE banco total / HABER cada cuenta por su monto

    aux_ids: lista paralela a cuenta_ids con contraparte_id (auxiliar) por línea, o None.
    """
    emp_id = mov.empresa_id
    c_banco = _cuenta_lado_banco(mov)
    if not c_banco:
        raise ValueError("Cuenta banco/tarjeta no encontrada")

    if not cuenta_ids:
        raise ValueError("Se requiere al menos una cuenta contraparte")
    if not montos or len(montos) != len(cuenta_ids):
        total = (mov.cargo or 0) + (mov.abono or 0)
        montos = [round(total / len(cuenta_ids))] * len(cuenta_ids)
    if aux_ids is None or len(aux_ids) != len(cuenta_ids):
        aux_ids = [None] * len(cuenta_ids)

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
        for i, (c, monto, aux) in enumerate(zip(cuentas_contra, montos, aux_ids)):
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c.id,
                                       contraparte_id=aux,
                                       debe=monto, haber=0, orden=i + 1))
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                   debe=0, haber=total, orden=len(cuenta_ids) + 1))
    else:
        lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                   debe=total, haber=0, orden=1))
        for i, (c, monto, aux) in enumerate(zip(cuentas_contra, montos, aux_ids)):
            lineas.append(LineaAsiento(asiento_id=asiento.id, cuenta_id=c.id,
                                       contraparte_id=aux,
                                       debe=0, haber=monto, orden=i + 2))
    db.session.add_all(lineas)
    return asiento


def _generar_asiento_banco_vs_cuenta(mov: MovimientoBanco,
                                     codigo_cuenta: str,
                                     descripcion_prefijo: str) -> Asiento:
    """Asiento cierre de cargo/abono bancario contra una cuenta del plan.

    Cargo (salida): DEBE cuenta / HABER banco
    Abono (entrada): DEBE banco / HABER cuenta
    """
    emp_id = mov.empresa_id
    c_banco = _cuenta_lado_banco(mov)
    c_contra = _buscar_cuenta(emp_id, codigo_cuenta)
    if not c_banco or not c_contra:
        raise ValueError(f"Faltan cuentas {_C['BANCO']} o {codigo_cuenta} en el plan de cuentas")

    es_cargo = (mov.cargo or 0) > 0
    monto = mov.cargo if es_cargo else (mov.abono or 0)
    asiento = Asiento(
        empresa_id=emp_id,
        fecha=mov.fecha,
        numero=_proximo_numero(emp_id),
        descripcion=f"{descripcion_prefijo}: {(mov.descripcion or '')[:60]}",
        origen='BANCO',
        estado='BORRADOR',
    )
    db.session.add(asiento)
    db.session.flush()

    desc = (mov.descripcion or '')[:80]
    if es_cargo:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_contra.id,
                         debe=monto, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=0, haber=monto, descripcion=desc, orden=2),
        ]
    else:
        lineas = [
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_banco.id,
                         debe=monto, haber=0, descripcion=desc, orden=1),
            LineaAsiento(asiento_id=asiento.id, cuenta_id=c_contra.id,
                         debe=0, haber=monto, descripcion=desc, orden=2),
        ]
    db.session.add_all(lineas)
    return asiento


def generar_asiento_pago_proveedor(mov: MovimientoBanco) -> Asiento:
    """Pago a proveedor vinculado a doc SII (compra/honorario)."""
    return _generar_asiento_banco_vs_cuenta(mov, _C['PROVEEDORES'], 'Pago proveedor')


def generar_asiento_cobro_cliente(mov: MovimientoBanco) -> Asiento:
    """Cobro de cliente vinculado a doc SII (venta)."""
    return _generar_asiento_banco_vs_cuenta(mov, _C['CLIENTES'], 'Cobro cliente')




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
    _autocrear_conciliacion(asiento)


def _autocrear_conciliacion(asiento: Asiento):
    """Si el asiento tiene docs SII o movs banco vinculados (asiento_id) que aún
    no tienen conciliacion_id, crea automáticamente una Conciliacion que los una.
    Previene huérfanos: cada asiento con doc/mov queda con su trazabilidad."""
    docs = DocumentoSII.query.filter_by(asiento_id=asiento.id, conciliacion_id=None).all()
    movs = MovimientoBanco.query.filter_by(asiento_id=asiento.id, conciliacion_id=None).all()
    if not docs and not movs:
        return

    contraparte_id = None
    for d in docs:
        if d.rut_contraparte:
            from models import Contraparte
            cp = Contraparte.query.filter_by(rut=d.rut_contraparte).first()
            if cp:
                contraparte_id = cp.id
                break

    conc = Conciliacion(
        empresa_id=asiento.empresa_id,
        fecha=asiento.fecha,
        descripcion=(asiento.descripcion or 'Auto-conciliación')[:280],
        tipo='SII' if docs else 'MANUAL',
        respaldo_url=asiento.respaldo_url,
        contraparte_id=contraparte_id,
    )
    db.session.add(conc)
    db.session.flush()
    for d in docs:
        d.conciliacion_id = conc.id
        d.procesado = True
    for m in movs:
        m.conciliacion_id = conc.id
        m.procesado = True


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
