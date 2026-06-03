"""Conciliar mayo 2026 Asesorías Ecox Limitada (id=6).

Notas:
- Restaurantes/alimentación con descripción 'Gasto alimentación' (no nombre del comercio)
- CLAUDE.AI / Google → 5.2.17 con descripción 'Software/SaaS oficina'
"""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29, Contraparte)
from sqlalchemy import func as sa_func

EMP = 6
CP_PARQUE_SUR = 1
CP_PEDRO = 4
CP_FELIPE = 5
CP_BENJAMIN = 45
CP_LOS_ROBLES = 25
CP_FUTRONO = 6
CP_CHILCOS = 60
CP_ROSA = 61
CP_SANTANDER = 63
CP_EREF = 64
CP_AYSEN = 18
CP_RIEUTORD = None  # crear si no existe


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


def asiento_simple(mov, cod_contra, glosa_asiento, glosa_linea, cp_id=None):
    """Banco-vs-cuenta. Banco línea 1. Glosa línea diferenciada de glosa banco."""
    c_banco = cuenta('1.1.02')
    c = cuenta(cod_contra)
    monto = float(mov.cargo or mov.abono or 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa_asiento[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    banco_glosa = (mov.descripcion or '')[:80]
    if mov.cargo and mov.cargo > 0:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=banco_glosa, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=monto, haber=0, descripcion=glosa_linea[:80], orden=2))
    else:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=banco_glosa, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=0, haber=monto, descripcion=glosa_linea[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Conciliación cartola mayo Asesorías Ecox'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Manual: {glosa_asiento}'[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def cobro_venta_sii(mov, doc, cp_id, glosa_venta):
    c_banco = cuenta('1.1.02')
    c_cli = cuenta('1.1.03')
    c_vta = cuenta('4.1.02')
    rs = (doc.razon_social_contraparte or '')[:60]
    total = float(doc.total)
    a_fact = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                     descripcion=f"Factura venta exenta 34 N°{doc.folio} - {rs[:40]}",
                     origen='LIBRO_VENTAS', estado='BORRADOR')
    db.session.add(a_fact); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_fact.id, cuenta_id=c_cli.id, contraparte_id=cp_id, debe=total, haber=0, descripcion=rs, orden=1))
    db.session.add(LineaAsiento(asiento_id=a_fact.id, cuenta_id=c_vta.id, debe=0, haber=total, descripcion=glosa_venta, orden=2))
    db.session.add(AsientoAudit(asiento_id=a_fact.id, accion='CREAR', descripcion=f'Factura venta folio {doc.folio}'))
    doc.asiento_id = a_fact.id
    doc.procesado = True
    a_cob = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                    descripcion=f"Cobro factura {doc.folio} - {rs[:40]}",
                    origen='BANCO', estado='BORRADOR')
    db.session.add(a_cob); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_cob.id, cuenta_id=c_banco.id, debe=total, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_cob.id, cuenta_id=c_cli.id, contraparte_id=cp_id, debe=0, haber=total, descripcion=f'Cobro factura {doc.folio}', orden=2))
    db.session.add(AsientoAudit(asiento_id=a_cob.id, accion='CREAR', descripcion=f'Cobro factura folio {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Factura+cobro {rs[:30]} folio {doc.folio}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_cob.id
    mov.procesado = True
    return a_fact, a_cob


def cobro_solo(mov, cp_id, glosa, doc_ref=None):
    """Solo cobro contra 1.1.03 ya existente (factura emitida en período anterior)."""
    c_banco = cuenta('1.1.02')
    c_cli = cuenta('1.1.03')
    monto = float(mov.abono)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_cli.id, contraparte_id=cp_id, debe=0, haber=monto, descripcion=glosa[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Cobro factura periodo anterior'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=glosa[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def pago_hon_existente(mov, doc, cp_id):
    """Doc ya tiene asiento devengado. Solo crear el pago."""
    c_banco = cuenta('1.1.02')
    c_prov = cuenta('2.1.01')
    liquido = float(doc.total) - float(doc.iva or 0)
    rs = (doc.razon_social_contraparte or '')[:60]
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Pago boleta hon. N°{doc.folio} - {rs[:30]}",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=liquido, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=liquido, haber=0, descripcion=f'Pago {rs} bol {doc.folio}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago hon folio {doc.folio} (devengo previo)'))
    # Vincular el mov a la conciliación existente del devengo (o crear nueva conc tipo SII multi)
    if doc.conciliacion_id:
        mov.conciliacion_id = doc.conciliacion_id
    else:
        conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Pago hon {rs[:30]} bol {doc.folio}', tipo='SII', contraparte_id=cp_id)
        db.session.add(conc); db.session.flush()
        mov.conciliacion_id = conc.id
    mov.asiento_id = a_p.id
    mov.procesado = True
    return a_p


def hon_sii_pago(mov, doc, cp_id, con_retencion=True):
    c_banco = cuenta('1.1.02')
    c_hon = cuenta('5.2.02')
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


def compra_sii_pago(mov, doc, cp_id, cod_gasto, glosa_extra):
    c_banco = cuenta('1.1.02')
    c_g = cuenta(cod_gasto)
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    iva = float(doc.iva or 0)
    neto = total - iva
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


def parque_sur_cuota(mov, capital, interes):
    c_banco = cuenta('1.1.02')
    c_prest = cuenta('1.1.12')
    c_intfin = cuenta('4.2.01')
    monto = float(mov.abono)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=f"Cuota Parque Sur (capital ${capital:,} + interés ${interes:,})",
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_prest.id, contraparte_id=CP_PARQUE_SUR, debe=0, haber=capital, descripcion='Capital cuota Parque Sur', orden=2))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_intfin.id, debe=0, haber=interes, descripcion='Interés cuota Parque Sur', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Cuota Parque Sur'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Cuota Parque Sur cap ${capital:,} + int ${interes:,}', tipo='MANUAL', contraparte_id=CP_PARQUE_SUR)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


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


def main():
    app = create_app()
    with app.app_context():
        global CP_RIEUTORD
        CP_RIEUTORD = get_or_create_cp('10755410-6', 'ANDRES FELIPE RIEUTORD ALVARADO', 'HONORARIOS')
        cp_ing_inf = get_or_create_cp('', 'Ingenieria Informatica Asociada Ltda.', 'PROVEEDOR')

        movs = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 5, 1),
                        MovimientoBanco.fecha < date(2026, 6, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"\nMov mayo Asesorías: {len(movs)}")

        plan = {
            655: ('cobro_sii', 235, CP_PARQUE_SUR, 'Servicios contables — Parque Sur'),
            656: ('cobro_sii', 234, CP_LOS_ROBLES, 'Servicios contables — Los Robles'),
            657: ('cobro_sii', 233, CP_CHILCOS, 'Servicios contables — Chilcos'),
            658: ('cobro_sii', 232, CP_FUTRONO, 'Servicios contables — Futrono'),
            659: ('manual', '3.1.06', 'Retiro Felipe Hiriart', 'Retiro Felipe Hiriart Blome', CP_FELIPE),
            660: ('manual', '3.1.06', 'Retiro Pedro Lecaros', 'Retiro Pedro Lecaros Sotomayor', CP_PEDRO),
            661: ('manual', '5.2.03', 'Arriendo oficina mayo', 'Arriendo oficina mayo (Sanchez Miller)', None),
            662: ('pago_hon_devengado', 229, CP_BENJAMIN),   # Benjamín bol 102 abril ya devengado
            663: ('manual', '5.2.17', 'Gasto alimentación', 'Gasto alimentación (kiosko/bebidas oficina)', None),
            664: ('manual', '5.2.17', 'Software/SaaS oficina', 'Suscripción Google Workspace', None),
            665: ('cobro_solo', CP_LOS_ROBLES, 'Cobro saldo factura 221 Los Robles ($5M restante dividendo 7)'),
            666: ('manual', '5.2.17', 'Reembolso pago cuentas a Benjamín', 'Reembolso pago cuentas a Benjamín Lecaros', CP_BENJAMIN),
            667: ('manual', '5.2.07', 'Aseo oficina 2 días (Jeannette)', 'Aseo oficina 2 días', None),
            668: ('manual', '5.2.17', 'Gasto alimentación', 'Gasto alimentación (LBB)', None),
            669: ('manual', '1.1.09', 'Inversión en Fondo Mutuo', 'Inversión FFMM', None),
            670: ('manual', '5.2.17', 'Gasto menor MercadoPago', 'Gasto menor MercadoPago', None),
            671: ('compra_sii_pago', 230, cp_ing_inf, '5.2.06', 'Compra toner oficina (Ingeniería Informática)'),
            672: ('manual', '5.2.09', 'Reembolso Uber a Felipe', 'Reembolso Uber a Felipe Hiriart', CP_FELIPE),
            673: ('f29_pago', '2026-04'),
            674: ('hon_sii_pago', 237, CP_RIEUTORD, False),  # Rieutord sin retención
            675: ('manual', '5.2.17', 'Software/SaaS oficina', 'Suscripción Claude.ai (mensual)', None),
            676: ('manual', '5.2.17', 'Gasto alimentación', 'Gasto alimentación (Domani)', None),
            677: ('manual', '5.2.17', 'Gasto menor MercadoPago', 'Gasto menor MercadoPago', None),
            678: ('cobro_sii', 236, CP_AYSEN, 'Servicios — Inversiones Aysen'),
            679: ('manual', '5.2.17', 'Software/SaaS oficina', 'Suscripción Claude.ai (anual)', None),
            680: ('manual', '5.2.17', 'Gasto alimentación', 'Gasto alimentación (Carnal)', None),
            681: ('parque_sur_cuota', 169343, 8063),    # $177.406
            682: ('parque_sur_cuota', 113328, 36480),   # $149.808
            683: ('parque_sur_cuota', 92764, 31502),    # $124.266
            684: ('manual', '5.2.17', 'Gasto menor', 'Inversiones Maral (gasto menor)', None),
            685: ('pago_hon_devengado', 228, CP_ROSA),   # Rosa folio 728 abril ya devengado
            686: ('compra_sii_pago', 231, CP_SANTANDER, '5.2.12', 'Gastos bancarios Santander'),
        }

        ids_db = {m.id for m in movs}
        if ids_db != set(plan.keys()):
            print(f"FALTAN: {ids_db - set(plan.keys())}")
            print(f"SOBRAN: {set(plan.keys()) - ids_db}")
            return

        manual = sii = 0
        for m in movs:
            spec = plan[m.id]
            accion = spec[0]
            if accion == 'manual':
                _, cod, glosa_asiento, glosa_linea, cp = spec
                asiento_simple(m, cod, glosa_asiento, glosa_linea, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod} — {glosa_asiento}")
            elif accion == 'cobro_sii':
                _, doc_id, cp_id, glosa_v = spec
                doc = db.session.get(DocumentoSII, doc_id)
                cobro_venta_sii(m, doc, cp_id, glosa_v)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ venta doc{doc_id} folio {doc.folio}")
            elif accion == 'cobro_solo':
                _, cp_id, glosa = spec
                cobro_solo(m, cp_id, glosa)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} cobro factura período anterior")
            elif accion == 'pago_hon_devengado':
                _, doc_id, cp_id = spec
                doc = db.session.get(DocumentoSII, doc_id)
                pago_hon_existente(m, doc, cp_id)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ pago hon devengado doc{doc_id} folio {doc.folio}")
            elif accion == 'hon_sii_pago':
                _, doc_id, cp_id, con_ret = spec
                doc = db.session.get(DocumentoSII, doc_id)
                hon_sii_pago(m, doc, cp_id, con_retencion=con_ret)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ hon doc{doc_id} folio {doc.folio}")
            elif accion == 'compra_sii_pago':
                _, doc_id, cp_id, cod_g, glosa_x = spec
                doc = db.session.get(DocumentoSII, doc_id)
                compra_sii_pago(m, doc, cp_id, cod_g, glosa_x)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ compra doc{doc_id} folio {doc.folio}")
            elif accion == 'parque_sur_cuota':
                _, cap, inte = spec
                parque_sur_cuota(m, cap, inte)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} cuota PS: cap ${cap:,} int ${inte:,}")
            elif accion == 'f29_pago':
                _, periodo = spec
                f29_pago(m, periodo)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} F29 {periodo}")

        db.session.commit()
        print(f"\nResumen mayo Asesorías:")
        print(f"  SII:    {sii}")
        print(f"  MANUAL: {manual}")


if __name__ == '__main__':
    main()
