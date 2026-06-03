"""Conciliar enero 2026 Agrícola Los Chilcos SpA (id=8).

Nuevo: tarjeta de crédito (cuenta 2.1.14). Compras internacionales Facebook USD → 5.2.10.
Comisiones/intereses/impuesto TC → 5.2.12 Gastos Bancarios.
Pago automático TC: 1 asiento via mov banco, mov TC equivalente concilia sin asiento.
"""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, Contraparte)
from sqlalchemy import func as sa_func

EMP = 8  # Chilcos
CP_PEDRO = 4
CP_FELIPE = 5
CP_ASESORIAS_ECOX = 3
CP_BENJAMIN = 45
CP_MAITEN = 12
CP_JOPA = 37
CP_PARRO = 36
CP_ANELISE = 52
CP_ELSA = 41
CP_RIEUTORD = 55  # ya creada para Asesorías Ecox
# Nuevas
CP_MADERAS = None
CP_HECTOR = None
CP_DENIS = None
CP_RIOS = None


def cuenta(cod):
    c = Cuenta.query.filter_by(empresa_id=EMP, codigo=cod).first()
    if not c: raise SystemExit(f'Cuenta {cod} no existe')
    return c


def next_num():
    n = db.session.query(sa_func.coalesce(sa_func.max(Asiento.numero), 0)).filter(Asiento.empresa_id == EMP).scalar() or 0
    return n + 1


def get_or_create_cp(rut, razon_social, tipo):
    if rut:
        cp = Contraparte.query.filter_by(rut=rut).first()
        if cp: return cp.id
    cp = Contraparte.query.filter(Contraparte.razon_social == razon_social).first()
    if cp: return cp.id
    cp = Contraparte(empresa_id=EMP, rut=rut or '', razon_social=razon_social, tipo=tipo, activo=True)
    db.session.add(cp); db.session.flush()
    print(f"  + Creada: {razon_social}")
    return cp.id


def asiento_simple(mov, cod_contra, glosa, cp_id=None):
    """Banco/TC line 1. cuenta automática según mov.banco."""
    c_banco_lado = cuenta('2.1.14') if 'TC' in (mov.banco or '') else cuenta('1.1.02')
    c = cuenta(cod_contra)
    monto = float(mov.cargo or mov.abono or 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    gl = (mov.descripcion or '')[:80]
    if mov.cargo and mov.cargo > 0:
        # Salida: Banco/TC haber (orden 1), contra debe
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco_lado.id, debe=0, haber=monto, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=monto, haber=0, descripcion=gl, orden=2))
    else:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco_lado.id, debe=monto, haber=0, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=0, haber=monto, descripcion=gl, orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Conciliación cartola enero Chilcos'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Manual: {glosa}'[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def venta_directa(mov, glosa_venta):
    """Cobro venta parcela sin factura SII. Banco DEBE / 4.1.02 Ventas HABER."""
    c_banco = cuenta('1.1.02')
    c_vta = cuenta('4.1.02')
    monto = float(mov.abono)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa_venta[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_vta.id, debe=0, haber=monto, descripcion=glosa_venta[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Venta parcela (sin SII)'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Venta: {glosa_venta}'[:280], tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def hon_sii_pago(mov, doc, cp_id, cod_gasto='5.2.02', con_retencion=True):
    c_banco = cuenta('1.1.02')
    c_hon = cuenta(cod_gasto)
    c_prov = cuenta('2.1.01')
    c_ret = cuenta('2.1.04')
    bruto = float(doc.total)
    retencion = float(doc.iva or 0) if con_retencion else 0
    liquido = bruto - retencion
    rs = (doc.razon_social_contraparte or '')[:60]
    a_hon = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                    descripcion=f"Boleta honorarios N°{doc.folio} - {rs[:30]}",
                    origen='HONORARIOS', estado='BORRADOR')
    db.session.add(a_hon); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_hon.id, debe=bruto, haber=0, descripcion=f'{rs} (bruto)', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_prov.id, debe=0, haber=liquido, descripcion=f'Líquido {rs}', orden=2, contraparte_id=cp_id))
    if retencion:
        db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_ret.id, debe=0, haber=retencion, descripcion='Retención 15,25%', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_hon.id, accion='CREAR', descripcion=f'Honorario folio {doc.folio}'))
    doc.asiento_id = a_hon.id
    doc.procesado = True
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Pago boleta hon. N°{doc.folio} - {rs[:30]}",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=liquido, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=liquido, haber=0, descripcion=f'Pago {rs} bol {doc.folio}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago hon folio {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Hon+pago {rs[:30]} bol {doc.folio}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_p.id
    mov.procesado = True
    return a_hon, a_p


def hon_sii_multi_pago(mov, docs, cp_id, cod_gasto='5.2.02', con_retencion=True):
    """1 pago salda 2 boletas mismo profesional (Elsa Tania)."""
    c_banco = cuenta('1.1.02')
    c_hon = cuenta(cod_gasto)
    c_prov = cuenta('2.1.01')
    c_ret = cuenta('2.1.04')
    rs = (docs[0].razon_social_contraparte or '')[:60]

    # Crear asientos boleta por cada doc
    for doc in docs:
        bruto = float(doc.total)
        retencion = float(doc.iva or 0) if con_retencion else 0
        liquido = bruto - retencion
        a = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                    descripcion=f"Boleta honorarios N°{doc.folio} - {rs[:30]}",
                    origen='HONORARIOS', estado='BORRADOR')
        db.session.add(a); db.session.flush()
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_hon.id, debe=bruto, haber=0, descripcion=f'{rs} (bruto)', orden=1, contraparte_id=cp_id))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_prov.id, debe=0, haber=liquido, descripcion=f'Líquido {rs}', orden=2, contraparte_id=cp_id))
        if retencion:
            db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id, debe=0, haber=retencion, descripcion='Retención 15,25%', orden=3))
        db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'Honorario folio {doc.folio}'))
        doc.asiento_id = a.id
        doc.procesado = True

    # 1 pago combinado
    total_liquido = sum(float(d.total) - float(d.iva or 0) for d in docs)
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Pago combinado {len(docs)} boletas hon - {rs[:30]}",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=total_liquido, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=total_liquido, haber=0, descripcion=f'Pago boletas {", ".join(d.folio for d in docs)}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago combinado {len(docs)} boletas'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Hon+pago combinado {rs[:30]}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    for d in docs:
        d.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_p.id
    mov.procesado = True
    return a_p


def compra_sii_pago(mov, doc, cp_id, cod_gasto, glosa_extra, con_iva=False):
    c_banco = cuenta('1.1.02')
    c_g = cuenta(cod_gasto)
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    if con_iva:
        # Calcular IVA real (campo a veces viene 0)
        iva = float(doc.iva or 0)
        if iva == 0 and doc.tipo_dte == '33':
            neto = round(total / 1.19)
            iva = total - neto
        else:
            neto = total - iva
    else:
        iva = 0
        neto = total
    rs = (doc.razon_social_contraparte or '')[:60]
    a_c = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                  descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - {rs[:30]}",
                  origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_c); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_g.id, debe=neto, haber=0, descripcion=glosa_extra or rs, orden=1, contraparte_id=cp_id))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_prov.id, debe=0, haber=total, descripcion=rs, orden=3, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR', descripcion=f'Compra folio {doc.folio}'))
    doc.asiento_id = a_c.id
    doc.procesado = True
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Pago factura {doc.folio} - {rs[:30]}",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=total, haber=0, descripcion=f'Pago folio {doc.folio}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago folio {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Factura+pago {rs[:30]} folio {doc.folio}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_p.id
    mov.procesado = True
    return a_c, a_p


def parro_con_interes(mov, capital, interes):
    """Pago Parro: salda apertura capital + interés extra."""
    c_banco = cuenta('1.1.02')
    c_prest = cuenta('2.1.11')
    c_int = cuenta('5.2.12')
    monto = float(mov.cargo)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=f'Pago préstamo Parro (capital ${capital:,} + interés ${interes:,})',
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_prest.id, contraparte_id=CP_PARRO, debe=capital, haber=0, descripcion='Capital préstamo Parro (salda apertura)', orden=2))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_int.id, debe=interes, haber=0, descripcion='Interés préstamo Parro', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Pago Parro capital+interés'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Pago Parro cap ${capital:,} + int ${interes:,}', tipo='MANUAL', contraparte_id=CP_PARRO)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def maderas_con_reembolso(mov, doc, cp_maderas, cp_hector):
    """Doc Maderas (compra factura) + mov reembolso a Hector que pagó."""
    c_banco = cuenta('1.1.02')
    c_g = cuenta('5.2.06')  # Materiales y Suministros
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    iva = float(doc.iva or 0)
    if iva == 0 and doc.tipo_dte == '33':
        neto = round(total / 1.19)
        iva = total - neto
    else:
        neto = total - iva
    rs = (doc.razon_social_contraparte or '')[:60]
    # A compra
    a_c = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                  descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - Maderas (pagada por Hector)",
                  origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_c); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_g.id, debe=neto, haber=0, descripcion='Materiales/maderas', orden=1, contraparte_id=cp_maderas))
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_prov.id, debe=0, haber=total, descripcion='Proveedor Maderas Masefor', orden=3, contraparte_id=cp_maderas))
    db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR', descripcion=f'Compra Maderas folio {doc.folio} (Hector reembolsa)'))
    doc.asiento_id = a_c.id
    doc.procesado = True
    # A reembolso (mov 777)
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Reembolso a Hector Varela por compra Maderas folio {doc.folio}",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=total, haber=0, descripcion=f'Pago Maderas vía reembolso Hector folio {doc.folio}', orden=2, contraparte_id=cp_maderas))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion='Reembolso Hector por compra Maderas'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Compra+reembolso Maderas folio {doc.folio}', tipo='SII', contraparte_id=cp_maderas)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_p.id
    mov.procesado = True
    return a_c, a_p


def pago_tc_via_banco(mov_banco, mov_tc):
    """Pago automático TC: 1 asiento via banco. mov_tc concilia sin asiento propio."""
    c_banco = cuenta('1.1.02')
    c_tc = cuenta('2.1.14')
    monto = float(mov_banco.cargo)
    a = Asiento(empresa_id=EMP, fecha=mov_banco.fecha, numero=next_num(),
                descripcion='Pago automático tarjeta de crédito (salda 2.1.14)',
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov_banco.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_tc.id, debe=monto, haber=0, descripcion='Pago deuda tarjeta crédito', orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Pago automático TC'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_banco.fecha, descripcion='Pago automático TC (banco + cuenta TC)', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov_banco.conciliacion_id = conc.id
    mov_banco.asiento_id = a.id
    mov_banco.procesado = True
    # mov_tc: misma conciliación, mismo asiento, sin nuevo asiento propio
    mov_tc.conciliacion_id = conc.id
    mov_tc.asiento_id = a.id
    mov_tc.procesado = True
    return a


def main():
    app = create_app()
    with app.app_context():
        global CP_MADERAS, CP_HECTOR, CP_DENIS, CP_RIOS
        CP_MADERAS = get_or_create_cp('', 'MADERAS MASEFOR SpA', 'PROVEEDOR')
        CP_HECTOR = get_or_create_cp('', 'Hector Varela', 'OTRO')
        CP_DENIS = get_or_create_cp('', 'Denis Marcelo Bustos Fuentes', 'OTRO')
        CP_RIOS = get_or_create_cp('', 'Ríos y Compañía Abogados Limitada', 'PROVEEDOR')

        movs_b = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                  .filter(MovimientoBanco.fecha >= date(2026, 1, 1),
                          MovimientoBanco.fecha < date(2026, 2, 1),
                          MovimientoBanco.banco == 'Banco de Chile')
                  .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        # Incluimos mov TC dic 25 (id 1031) como parte de enero (gasto reconocido en enero)
        movs_tc = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                   .filter(MovimientoBanco.fecha >= date(2025, 12, 1),
                           MovimientoBanco.fecha < date(2026, 2, 1),
                           MovimientoBanco.banco == 'Banco de Chile (TC)')
                   .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov banco enero Chilcos: {len(movs_b)}, TC enero: {len(movs_tc)}")

        # Plan banco
        plan_banco = {
            747: ('manual', '2.1.04', 'Reembolso rendición 14 Benjamín (boletas CBR Paillaco 2025, salda apertura)', None),
            748: ('hon_sii', 246, CP_RIEUTORD, '5.2.17', False),  # Rieutord sin retención
            749: ('venta', 'Cuota 9/24 Valentina Aranda (A-5)'),
            750: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            751: ('venta', 'Pie parcela C-6 Teresa Sanhueza'),
            752: ('manual', '1.1.09', 'Aporte Fondos Mutuos', None),
            753: ('hon_sii', 248, CP_ELSA, '5.2.02', True),  # Elsa folio 19
            754: ('manual', '5.2.12', 'Intereses Línea de Crédito', None),
            755: ('manual', '5.2.10', 'Publicidad Facebook (reembolso Benjamín)', CP_BENJAMIN),
            756: ('manual', '5.2.17', 'Reembolso gastos Hector Varela', CP_HECTOR),
            757: ('compra_sii', 240, CP_ASESORIAS_ECOX, '5.2.11', 'Gestión Asesorías Ecox', False),  # exenta
            758: ('compra_sii', 241, CP_ASESORIAS_ECOX, '5.2.11', 'Contabilidad mensual Asesorías Ecox', False),  # exenta
            759: ('manual', '5.2.16', 'Impuesto Línea Crédito (DL 3475)', None),
            760: ('venta', 'Pie parcela C-6 Teresa Sanhueza'),
            761: ('venta', 'Reserva parcela C-3 Cristobal Aubel'),
            762: ('venta', 'Pie parcela C-6 Teresa Sanhueza'),
            763: ('venta', 'Cuota 9/48 Rodolfo Utreras (B-9)'),
            764: ('hon_sii', 249, CP_BENJAMIN, '5.2.11', True),  # Benjamín folio 89 "Asesoría venta C6"
            765: ('manual', '1.1.09', 'Aporte Fondos Mutuos', None),
            766: ('venta', 'Pie parcela C-6 Teresa Sanhueza'),
            767: ('venta', 'Pie parcela C-6 Teresa Sanhueza'),
            768: ('venta', 'Cuota 6/36 Juan Sanchez (C-9)'),
            769: ('pago_tc',),
            770: ('manual', '2.1.10', 'Transferencia desde Línea Crédito', None),
            771: ('manual', '2.1.10', 'Amortización Línea Crédito', None),
            772: ('manual', '2.1.07', 'Pago F29 dic 2025 (salda apertura)', None),
            773: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            774: ('manual', '2.1.10', 'Pago Línea Crédito', None),
            775: ('manual', '2.1.10', 'Transferencia desde Línea Crédito', None),
            776: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            777: ('maderas', 239),  # Maderas Masefor doc 239 + reembolso Hector
            778: ('manual', '5.2.17', 'Firma en Temuco (Denis Bustos)', CP_DENIS),
            779: ('venta', 'Venta parcela D-12 (depósito cheque otros bancos)'),
            780: ('manual', '2.1.11', 'Pago préstamo Inversiones JOPA', CP_JOPA),
            781: ('parro',),  # Parro: capital + interés
            782: ('hon_sii', 250, CP_BENJAMIN, '5.2.11', True),  # Benjamín folio 90 "Asesoría D-12"
            783: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', CP_PEDRO),
            784: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', CP_PEDRO),
            785: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', CP_PEDRO),
            786: ('venta', 'Reserva parcela D-13 (depósito efectivo)'),
            787: ('venta', 'Reserva parcela D-10 Maria Ortega'),
            788: ('venta', 'Reserva parcela D-14 (depósito efectivo)'),
            789: ('hon_sii_multi', [251, 252], CP_ELSA, '5.2.02', True),  # Elsa folios 20+21
            790: ('compra_sii', 245, CP_RIOS, '5.2.11', 'Gastos legales Ríos y Compañía', False),  # exenta
            791: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            792: ('venta', 'Cuota 10/48 Rodolfo Utreras (B-9)'),
            793: ('venta', 'Cuota 10/24 Valentina Aranda (A-5)'),
        }

        # Plan TC (excluyendo el dic 25 1031 — pendiente decisión)
        plan_tc = {
            1031: ('manual', '5.2.12', 'Comisión compra internacional TC (dic 25 — cargado como gasto enero 2026)', None),
            1032: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1033: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1034: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1030: ('pago_tc_par',),  # par del banco 769
            1035: ('manual', '5.2.10', 'Traspaso deuda internacional — Publicidad Facebook USD', None),
            1036: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1037: ('manual', '5.2.12', 'Impuesto DL 3475 TC', None),
            1038: ('manual', '5.2.12', 'Comisión mensual mantención TC', None),
            1039: ('manual', '5.2.12', 'Intereses rotativos TC', None),
            1041: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1042: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1043: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
        }

        # Validar
        ids_banco = {m.id for m in movs_b}
        if ids_banco != set(plan_banco.keys()):
            print(f"FALTAN banco: {ids_banco - set(plan_banco.keys())}")
            print(f"SOBRAN banco: {set(plan_banco.keys()) - ids_banco}")
            return
        ids_tc = {m.id for m in movs_tc}
        if ids_tc != set(plan_tc.keys()):
            print(f"FALTAN TC: {ids_tc - set(plan_tc.keys())}")
            print(f"SOBRAN TC: {set(plan_tc.keys()) - ids_tc}")
            return

        manual = sii = pendiente = 0

        # Procesar banco
        for m in movs_b:
            spec = plan_banco[m.id]
            accion = spec[0]
            if accion == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod}")
            elif accion == 'venta':
                _, glosa = spec
                venta_directa(m, glosa)
                manual += 1
                print(f"  ✓ VENTA mov#{m.id} → 4.1.02 — {glosa[:50]}")
            elif accion == 'hon_sii':
                _, doc_id, cp_id, cod_g, con_ret = spec
                doc = db.session.get(DocumentoSII, doc_id)
                hon_sii_pago(m, doc, cp_id, cod_g, con_ret)
                sii += 1
                print(f"  ✓ SII hon mov#{m.id} ↔ doc{doc_id}")
            elif accion == 'hon_sii_multi':
                _, doc_ids, cp_id, cod_g, con_ret = spec
                docs = [db.session.get(DocumentoSII, d) for d in doc_ids]
                hon_sii_multi_pago(m, docs, cp_id, cod_g, con_ret)
                sii += 1
                print(f"  ✓ SII hon multi mov#{m.id} ↔ docs{doc_ids}")
            elif accion == 'compra_sii':
                _, doc_id, cp_id, cod_g, glosa_x, con_iva = spec
                doc = db.session.get(DocumentoSII, doc_id)
                compra_sii_pago(m, doc, cp_id, cod_g, glosa_x, con_iva)
                sii += 1
                print(f"  ✓ SII compra mov#{m.id} ↔ doc{doc_id}")
            elif accion == 'maderas':
                _, doc_id = spec
                doc = db.session.get(DocumentoSII, doc_id)
                maderas_con_reembolso(m, doc, CP_MADERAS, CP_HECTOR)
                sii += 1
                print(f"  ✓ Maderas mov#{m.id} ↔ doc{doc_id}")
            elif accion == 'parro':
                # 1.310.053 capital + 722 interés
                parro_con_interes(m, capital=1310053, interes=722)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} pago Parro cap+int")
            elif accion == 'pago_tc':
                mov_tc = next((t for t in movs_tc if t.id == 1030), None)
                if mov_tc:
                    pago_tc_via_banco(m, mov_tc)
                    manual += 1
                    print(f"  ✓ MANUAL mov#{m.id} pago TC (linkea mov TC 1030)")

        # Procesar TC restantes
        for m in movs_tc:
            spec = plan_tc[m.id]
            accion = spec[0]
            if accion == 'skip':
                pendiente += 1
                print(f"  ⏳ TC mov#{m.id} {m.fecha} PENDIENTE — dic 25 (decisión usuario)")
                continue
            if accion == 'pago_tc_par':
                # Ya procesado al ejecutar pago_tc del banco
                continue
            if accion == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL TC mov#{m.id} {m.fecha} → {cod}")

        db.session.commit()
        print(f"\nResumen enero Chilcos:")
        print(f"  SII:        {sii}")
        print(f"  MANUAL:     {manual}")
        print(f"  PENDIENTE:  {pendiente}")


if __name__ == '__main__':
    main()
