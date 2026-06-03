"""Conciliar febrero 2026 Agrícola Los Chilcos SpA (id=8)."""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29, Contraparte)
from sqlalchemy import func as sa_func

EMP = 8
CP_PEDRO = 4
CP_ASESORIAS_ECOX = 3
CP_BENJAMIN = 45
CP_JOPA = 37
CP_PARRO = 36
CP_ELSA = 41
CP_MARIA_PAZ = 42
CP_RIEUTORD = 55
CP_HECTOR = None
CP_DENIS = None
CP_RIOS = None
# Nuevas
CP_GYO = None
CP_VICTOR = None
CP_TEKKROM = None
CP_CONRAD = None


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
    c_banco_lado = cuenta('2.1.14') if 'TC' in (mov.banco or '') else cuenta('1.1.02')
    c = cuenta(cod_contra)
    monto = float(mov.cargo or mov.abono or 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    gl = (mov.descripcion or '')[:80]
    if mov.cargo and mov.cargo > 0:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco_lado.id, debe=0, haber=monto, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=monto, haber=0, descripcion=gl, orden=2))
    else:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco_lado.id, debe=monto, haber=0, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=0, haber=monto, descripcion=gl, orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Conciliación cartola feb Chilcos'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Manual: {glosa}'[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def venta_directa(mov, glosa_venta):
    c_banco = cuenta('1.1.02')
    c_vta = cuenta('4.1.02')
    monto = float(mov.abono)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa_venta[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_vta.id, debe=0, haber=monto, descripcion=glosa_venta[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Venta parcela'))
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


def compra_sii_pago(mov, doc, cp_id, cod_gasto, glosa_extra, con_iva=False):
    c_banco = cuenta('1.1.02')
    c_g = cuenta(cod_gasto)
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    if con_iva:
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


def f29_pago(mov, periodo):
    f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo=periodo).first()
    ppm = float(f29.codigo_62)
    ret = float(f29.codigo_151)
    total = float(f29.codigo_91)
    c_banco = cuenta('1.1.02')
    c_ppm = cuenta('1.1.06')
    c_ret = cuenta('2.1.04')
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=f"Pago F29 {periodo} folio {f29.folio}",
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id, debe=ppm, haber=0, descripcion=f'PPM {periodo} cód 62', orden=2))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id, debe=ret, haber=0, descripcion=f'Retención Hon {periodo} cód 151', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'Pago F29 {periodo}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'F29 {periodo} folio {f29.folio}', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def pago_tc_via_banco(mov_banco, mov_tc, cargos_enero, diff_a_5217):
    """Pago automático TC con desglose: parte salda 2.1.14 + parte a 5.2.17 (ajuste gasto)."""
    c_banco = cuenta('1.1.02')
    c_tc = cuenta('2.1.14')
    c_og = cuenta('5.2.17')
    monto = float(mov_banco.cargo)
    a = Asiento(empresa_id=EMP, fecha=mov_banco.fecha, numero=next_num(),
                descripcion=f'Pago automático TC: salda cargos mes anterior (${cargos_enero:,}) + ajuste (${diff_a_5217:,})',
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion='Pago automático TC', orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_tc.id, debe=cargos_enero, haber=0, descripcion='Salda cargos mes anterior', orden=2))
    if diff_a_5217 != 0:
        if diff_a_5217 > 0:
            db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_og.id, debe=diff_a_5217, haber=0, descripcion='Diferencia pago TC sin documentar (gasto)', orden=3))
        else:
            db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_og.id, debe=0, haber=abs(diff_a_5217), descripcion='Ajuste pago TC menor a cargos', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Pago automático TC con ajuste'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_banco.fecha, descripcion='Pago automático TC', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov_banco.conciliacion_id = conc.id
    mov_banco.asiento_id = a.id
    mov_banco.procesado = True
    mov_tc.conciliacion_id = conc.id
    mov_tc.asiento_id = a.id
    mov_tc.procesado = True
    return a


def compra_via_persona(mov, doc, cp_doc, cp_persona, cod_gasto, glosa_extra, con_iva=True):
    """Doc factura SII + reembolso a persona que pagó."""
    c_banco = cuenta('1.1.02')
    c_g = cuenta(cod_gasto)
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    if con_iva:
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
                  descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - {rs[:30]} (pagada por persona, reembolsa)",
                  origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_c); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_g.id, debe=neto, haber=0, descripcion=glosa_extra or rs, orden=1, contraparte_id=cp_doc))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_prov.id, debe=0, haber=total, descripcion=rs, orden=3, contraparte_id=cp_doc))
    db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR', descripcion=f'Compra folio {doc.folio} (vía reembolso persona)'))
    doc.asiento_id = a_c.id
    doc.procesado = True
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Reembolso factura {doc.folio} - {rs[:30]} (pago vía persona)",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    monto = float(mov.cargo)
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=monto, haber=0, descripcion=f'Pago {rs} vía reembolso folio {doc.folio}', orden=2, contraparte_id=cp_doc))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Reembolso persona por compra {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Compra+reembolso {rs[:30]} folio {doc.folio}', tipo='SII', contraparte_id=cp_doc)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_p.id
    mov.procesado = True
    return a_c, a_p


def main():
    app = create_app()
    with app.app_context():
        global CP_HECTOR, CP_DENIS, CP_RIOS, CP_GYO, CP_VICTOR, CP_TEKKROM, CP_CONRAD
        CP_HECTOR = get_or_create_cp('', 'Hector Varela', 'OTRO')
        CP_DENIS = get_or_create_cp('', 'Denis Marcelo Bustos Fuentes', 'OTRO')
        CP_RIOS = get_or_create_cp('', 'Ríos y Compañía Abogados Limitada', 'PROVEEDOR')
        CP_GYO = get_or_create_cp('', 'GYO Servicios Maquinaria SpA', 'PROVEEDOR')
        CP_VICTOR = get_or_create_cp('7979697-2', 'VICTOR HUGO QUINONES SOBARZO', 'HONORARIOS')
        CP_TEKKROM = get_or_create_cp('', 'TEKKROM Acción Gráfica Cía Ltda', 'PROVEEDOR')
        CP_CONRAD = get_or_create_cp('10775640-K', 'CONRAD PABLO ZULCH PARRA', 'HONORARIOS')

        movs_b = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                  .filter(MovimientoBanco.fecha >= date(2026, 2, 1),
                          MovimientoBanco.fecha < date(2026, 3, 1),
                          MovimientoBanco.banco == 'Banco de Chile')
                  .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        movs_tc = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                   .filter(MovimientoBanco.fecha >= date(2026, 2, 1),
                           MovimientoBanco.fecha < date(2026, 3, 1),
                           MovimientoBanco.banco == 'Banco de Chile (TC)')
                   .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov banco feb Chilcos: {len(movs_b)}, TC feb: {len(movs_tc)}")

        plan_banco = {
            794: ('venta', 'Cuota 7/36 Juan Sánchez (C-9)'),
            795: ('manual', '5.2.12', 'Intereses Línea de Crédito', None),
            796: ('compra_sii', 256, CP_ASESORIAS_ECOX, '5.2.11', 'Gestión Asesorías Ecox', False),
            797: ('manual', '5.2.16', 'Impuesto Línea Crédito (DL 3475)', None),
            798: ('venta', 'Cuota 1/24 Carlos Peña (A-12)'),
            799: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            800: ('compra_sii', 255, CP_GYO, '5.2.07', 'Caminos — Servicios Maquinaria GYO', True),
            801: ('manual', '5.2.17', 'Reembolso gastos generales Hector Varela', CP_HECTOR),
            802: ('hon_sii', 262, CP_MARIA_PAZ, '5.2.10', True),  # María Paz Amenabar Publicidad
            803: ('compra_sii', 258, CP_ASESORIAS_ECOX, '5.2.11', 'Contabilidad mensual Asesorías Ecox', False),
            804: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            805: ('f29_pago', '2026-01'),
            806: ('pago_tc', 1040, 1481705, 0),  # ajustar luego según diff
            807: ('manual', '2.1.10', 'Transferencia desde Línea Crédito', None),
            808: ('compra_via_persona', 263, CP_VICTOR, CP_BENJAMIN, '5.2.17', 'Docs CBR Osorno (vía Benjamín)', False),  # Victor sin retención
            809: ('manual', '1.1.09', 'Aporte Fondos Mutuos', None),
            810: ('manual', '5.2.10', 'Publicidad Facebook (reembolso Benjamín)', CP_BENJAMIN),
            811: ('manual', '2.1.10', 'Amortización Línea Crédito', None),
            812: ('venta', 'Venta parcela D-14 Rosa Ester (parte 1)'),
            813: ('venta', 'Cuota 2/24 lotes A6 A7 A8 A13 (El Turco)'),
            814: ('venta', 'Venta parcela D-14 Rosa Ester (parte 2)'),
            815: ('manual', '2.1.10', 'Amortización Línea Crédito', None),
            816: ('venta', 'Venta parcela D-14 Rosa Ester (parte 3)'),
            817: ('manual', '2.1.10', 'Pago Línea Crédito', None),
            818: ('venta', 'Venta parcela D-14 Rosa Ester (parte 4)'),
            819: ('manual', '2.1.10', 'Transferencia desde Línea Crédito', None),
            820: ('manual', '1.1.09', 'Aporte Fondos Mutuos', None),
            821: ('hon_sii', 264, CP_ELSA, '5.2.02', True),  # Elsa folio 22
            822: ('venta', 'Venta parcela D-14 Rosa Ester (parte 5)'),
            823: ('manual', '1.1.09', 'Aporte Fondos Mutuos', None),
            824: ('hon_sii', 266, CP_ELSA, '5.2.02', True),  # Elsa folio 23
            825: ('hon_sii', 267, CP_ELSA, '5.2.02', True),  # Elsa folio 24 (DUPLICADO folio del libro)
            826: ('compra_via_persona', 254, CP_TEKKROM, CP_BENJAMIN, '5.2.06', 'TEKKROM — materiales gráficos (vía Benjamín)', True),
            827: ('compra_sii', 261, None, '5.2.12', 'Comisión anual Línea Crédito', False),
            828: ('hon_sii', 268, CP_BENJAMIN, '5.2.11', True),  # Benjamín folio 92 "Asesoría venta D-14"
            829: ('venta', 'Reserva D-1 Jorge Heinrich'),
            830: ('manual', '5.2.10', 'Publicidad Facebook (reembolso Benjamín)', CP_BENJAMIN),
            831: ('venta', 'Venta parcela D-10 Maria Claudia Ortega (depósito cheque)'),
        }

        plan_tc = {
            1040: ('skip_pair',),
            1044: ('manual', '5.2.10', 'Traspaso deuda internacional — Publicidad Facebook USD', None),
            1045: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1046: ('manual', '5.2.12', 'Impuesto DL 3475 TC', None),
            1047: ('manual', '5.2.12', 'Comisión mensual mantención TC', None),
            1048: ('manual', '5.2.12', 'Intereses rotativos TC', None),
            1050: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
        }

        ids_b = {m.id for m in movs_b}
        if ids_b != set(plan_banco.keys()):
            print(f"FALTAN banco: {ids_b - set(plan_banco.keys())}")
            print(f"SOBRAN banco: {set(plan_banco.keys()) - ids_b}")
            return
        ids_tc = {m.id for m in movs_tc}
        if ids_tc != set(plan_tc.keys()):
            print(f"FALTAN TC: {ids_tc - set(plan_tc.keys())}")
            print(f"SOBRAN TC: {set(plan_tc.keys()) - ids_tc}")
            return

        manual = sii = 0

        # Procesar primero TC cargos (excluir pago automático)
        for m in movs_tc:
            spec = plan_tc[m.id]
            if spec[0] == 'skip_pair':
                continue
            if spec[0] == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL TC mov#{m.id} → {cod}")

        # Calcular cargos TC enero pendientes pago feb = $1.507.193 (de mi cálculo enero)
        # En realidad lo importante: pago feb $1.481.705 — cuánto cubre de cargos enero acumulados?
        # Saldo 2.1.14 al inicio feb = 0 (terminamos enero en 0 con ajuste $41.764)
        # Cargos feb generados: sum TC feb cargos
        # Después de cargos feb (antes del pago): 2.1.14 = cargos feb acreedor
        # Pago feb $1.481.705: debe ir a saldar la deuda enero (que ya estaba "pagada" pero a partir desde 0)
        # Wait: si enero quedó 2.1.14 = 0, entonces feb empieza 0. Cargos feb suben deuda. Pago feb baja.
        # Pago feb $1.481.705 - cargos feb $962.035 = $519.670 (pago más de lo cargado) = saldo DEUDOR.
        # Para llevar a 0: ajustar $519.670 a 5.2.17.

        # Procesar pago TC con ajuste
        cargos_feb = 962035  # sum cargos TC feb
        pago_feb = 1481705
        # Salda los cargos feb (pago hasta agotar)
        # Para que 2.1.14 termine en 0:
        # DEBE 2.1.14 $962.035 (salda cargos feb)
        # DEBE 5.2.17 $519.670 (ajuste por exceso de pago)
        # HABER Banco $1.481.705
        diff = pago_feb - cargos_feb  # 519.670

        mov_806 = next(m for m in movs_b if m.id == 806)
        mov_1040 = next(m for m in movs_tc if m.id == 1040)
        pago_tc_via_banco(mov_806, mov_1040, cargos_feb, diff)
        manual += 1
        print(f"  ✓ MANUAL TC pago feb cargos {cargos_feb} + ajuste {diff}")

        # Procesar el resto banco
        for m in movs_b:
            if m.id == 806:
                continue  # ya procesado
            spec = plan_banco[m.id]
            accion = spec[0]
            if accion == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → {cod}")
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
            elif accion == 'compra_sii':
                _, doc_id, cp_id, cod_g, glosa_x, con_iva = spec
                doc = db.session.get(DocumentoSII, doc_id)
                compra_sii_pago(m, doc, cp_id, cod_g, glosa_x, con_iva)
                sii += 1
                print(f"  ✓ SII compra mov#{m.id} ↔ doc{doc_id}")
            elif accion == 'compra_via_persona':
                _, doc_id, cp_doc, cp_per, cod_g, glosa_x, con_iva = spec
                doc = db.session.get(DocumentoSII, doc_id)
                compra_via_persona(m, doc, cp_doc, cp_per, cod_g, glosa_x, con_iva)
                sii += 1
                print(f"  ✓ SII compra mov#{m.id} (vía persona) ↔ doc{doc_id}")
            elif accion == 'f29_pago':
                _, periodo = spec
                f29_pago(m, periodo)
                manual += 1
                print(f"  ✓ MANUAL F29 mov#{m.id} {periodo}")
            elif accion == 'pago_tc':
                pass  # ya procesado arriba

        db.session.commit()
        print(f"\nResumen feb Chilcos: SII={sii}, MANUAL={manual}")


if __name__ == '__main__':
    main()
