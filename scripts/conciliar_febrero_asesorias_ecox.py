"""Conciliar febrero 2026 Asesorías Ecox Limitada (id=6)."""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29, Contraparte)
from sqlalchemy import func as sa_func

EMP = 6
CP_PARQUE_SUR = 1
CP_ECOX_SPA = 17
CP_PEDRO = 4
CP_FELIPE = 5
CP_BENJAMIN = 45
CP_LOS_ROBLES = 25
CP_FUTRONO = 6
CP_CHILCOS = 60
CP_EREF = None  # buscar
CP_ROSA = 61
CP_CBR = None  # crear
CP_BLANCAMAR = None  # crear
CP_SANTANDER = 63


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
    print(f"  + Creada: {razon_social} ({rut or 'sin RUT'}) id={cp.id}")
    return cp.id


def asiento_simple(mov, cod_contra, glosa, cp_id=None):
    """Banco-vs-cuenta. Banco siempre línea 1."""
    c_banco = cuenta('1.1.02')
    c = cuenta(cod_contra)
    monto = float(mov.cargo or mov.abono or 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    gl = (mov.descripcion or '')[:80]
    if mov.cargo and mov.cargo > 0:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=monto, haber=0, descripcion=gl, orden=2))
    else:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id, debe=0, haber=monto, descripcion=gl, orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Conciliación cartola feb Asesorías Ecox'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Manual: {glosa}'[:280], tipo='MANUAL', contraparte_id=cp_id)
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


def hon_sii_pago(mov, doc, cp_id):
    """Boleta honorario emitida por proveedor + pago Asesorías."""
    c_banco = cuenta('1.1.02')
    c_hon = cuenta('5.2.02')
    c_prov = cuenta('2.1.01')
    c_ret = cuenta('2.1.04')
    bruto = float(doc.total)
    retencion = float(doc.iva or 0)
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
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago honorario folio {doc.folio}'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Hon+pago {rs[:30]} bol {doc.folio}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_p.id
    mov.procesado = True
    return a_hon, a_p


def cbr_pago(mov, doc, diff_extra):
    """Pago CBR: boleta exenta de retención + pago con diff (comisión SERVIPAG)."""
    c_banco = cuenta('1.1.02')
    c_hon = cuenta('5.2.02')
    c_prov = cuenta('2.1.01')
    c_otros = cuenta('5.2.17')
    bruto = float(doc.total)
    rs = (doc.razon_social_contraparte or '')[:60]
    cp_id = CP_CBR

    a_hon = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                    descripcion=f"Boleta CBR N°{doc.folio} - {rs[:30]}",
                    origen='HONORARIOS', estado='BORRADOR')
    db.session.add(a_hon); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_hon.id, debe=bruto, haber=0, descripcion=f'CBR {doc.folio}', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_prov.id, debe=0, haber=bruto, descripcion=rs, orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_hon.id, accion='CREAR', descripcion=f'Boleta CBR folio {doc.folio}'))
    doc.asiento_id = a_hon.id
    doc.procesado = True

    monto_pago = float(mov.cargo or mov.abono or 0)
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Pago CBR N°{doc.folio} (vía SERVIPAG/TGR)",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=monto_pago, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=bruto, haber=0, descripcion=f'Pago CBR {doc.folio}', orden=2, contraparte_id=cp_id))
    if diff_extra > 0:
        # comisión bancaria adicional (SERVIPAG)
        db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_otros.id, debe=diff_extra, haber=0, descripcion='Comisión SERVIPAG / cargo extra', orden=3))
    elif diff_extra < 0:
        # cobro de menos (ajuste a Otros Ingresos? — caso raro)
        c_oing = cuenta('4.2.03')
        db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_oing.id, debe=0, haber=abs(diff_extra), descripcion='Ajuste a favor', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago CBR folio {doc.folio}'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Boleta+pago CBR folio {doc.folio}', tipo='SII', contraparte_id=cp_id)
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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Cuota Parque Sur capital+interés'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Cuota Parque Sur cap ${capital:,} + int ${interes:,}', tipo='MANUAL', contraparte_id=CP_PARQUE_SUR)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def blancamar_dual_cobro(movs_blanca, doc, cp_id):
    """Doc 196 fact 206 Blancamar $500.000 cobrado en 2 movs ($250K + $250K).
    Factura el 02-19, cobros 02-04 y 02-05 (anticipo).
    Compras asiento: 1.1.03 Clientes / 4.1.02 Ventas con fecha factura.
    2 asientos cobro: Banco / 1.1.03 Clientes con fecha cada mov.
    1 conciliación SII linkando doc + ambos movs."""
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
    db.session.add(LineaAsiento(asiento_id=a_fact.id, cuenta_id=c_vta.id, debe=0, haber=total, descripcion='Servicios — Inversiones Blancamar', orden=2))
    db.session.add(AsientoAudit(asiento_id=a_fact.id, accion='CREAR', descripcion=f'Factura venta folio {doc.folio}'))
    doc.asiento_id = a_fact.id
    doc.procesado = True

    conc = Conciliacion(empresa_id=EMP, fecha=max(m.fecha for m in movs_blanca),
                        descripcion=f'Factura+cobros Blancamar folio {doc.folio} (2 movs anticipo)',
                        tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id

    for m in movs_blanca:
        monto = float(m.abono)
        a_c = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                      descripcion=f"Anticipo/cobro factura {doc.folio} - Blancamar (parte ${monto:,})",
                      origen='BANCO', estado='BORRADOR')
        db.session.add(a_c); db.session.flush()
        db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(m.descripcion or '')[:80], orden=1))
        db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_cli.id, contraparte_id=cp_id, debe=0, haber=monto, descripcion=f'Anticipo factura {doc.folio}', orden=2))
        db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR', descripcion=f'Anticipo/cobro factura {doc.folio}'))
        m.conciliacion_id = conc.id
        m.asiento_id = a_c.id
        m.procesado = True

    return a_fact


def f29_pago(mov):
    f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo='2026-01').first()
    ppm = float(f29.codigo_62)        # 6.750
    ret = float(f29.codigo_151)        # 431.859
    total = float(f29.codigo_91)       # 438.609
    c_banco = cuenta('1.1.02')
    c_ppm = cuenta('1.1.06')
    c_ret = cuenta('2.1.04')
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=f"Pago F29 ene 2026 folio {f29.folio} (PPM + Ret Hon)",
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id, debe=ppm, haber=0, descripcion='PPM ene 2026 cód 62', orden=2))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id, debe=ret, haber=0, descripcion='Retención Hon ene 2026 cód 151 (Rosa+Benjamín)', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'Pago F29 ene 2026 folio {f29.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'F29 ene 2026 folio {f29.folio} (PPM ${ppm:.0f} + Ret ${ret:.0f})', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def main():
    app = create_app()
    with app.app_context():
        # Contrapartes
        global CP_EREF, CP_CBR, CP_BLANCAMAR
        CP_EREF = get_or_create_cp('', 'Ecox Real Estate Florida LLC', 'CLIENTE')
        CP_CBR = get_or_create_cp('', 'Conservador de Bienes Raíces', 'PROVEEDOR')
        CP_BLANCAMAR = get_or_create_cp('', 'Inversiones Blancamar SpA', 'CLIENTE')

        movs = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 2, 1),
                        MovimientoBanco.fecha < date(2026, 3, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov feb Asesorías Ecox: {len(movs)}")

        # Procesar Blancamar primero (multi-mov)
        movs_blanca = [m for m in movs if m.id in (575, 580)]
        doc_blanca = db.session.get(DocumentoSII, 196)
        if movs_blanca and doc_blanca:
            a_f = blancamar_dual_cobro(movs_blanca, doc_blanca, CP_BLANCAMAR)
            print(f"  ✓ SII doc 196 Blancamar $500K ↔ movs 575+580 — fact A#{a_f.numero}")

        # Plan resto
        plan = {
            567: ('manual', '1.1.12', 'Préstamo corto a Parque Sur (entrará y saldrá)', CP_PARQUE_SUR),
            568: ('manual', '5.2.17', 'Google Workspace', None),
            569: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            570: ('cobro_sii', 190, CP_CHILCOS, 'Servicios contables — Agrícola Los Chilcos'),
            571: ('hon_sii_pago', 24, CP_BENJAMIN),    # boleta ene folio 91
            572: ('manual', '5.2.03', 'Arriendo oficina feb (Sanchez Miller)', None),
            573: ('hon_sii_pago', 23, CP_ROSA),        # boleta ene folio 682
            574: ('manual', '5.2.17', 'Compra MercadoPago MICOCACOLA', None),
            575: ('skip',),  # ya procesado en blancamar
            576: ('manual', '3.1.06', 'Retiro Pedro Lecaros', CP_PEDRO),
            577: ('manual', '3.1.06', 'Retiro Felipe Hiriart', CP_FELIPE),
            578: ('manual', '5.2.17', 'Reembolso comida Felipe Hiriart', CP_FELIPE),
            579: ('manual', '5.2.04', 'Servipag (agua oficina)', None),
            580: ('skip',),  # blancamar
            581: ('cobro_sii', 191, CP_CHILCOS, 'Servicios contables — Agrícola Los Chilcos'),
            582: ('cobro_sii', 192, CP_LOS_ROBLES, 'Servicios contables — Los Robles'),
            583: ('cobro_sii', 194, CP_FUTRONO, 'Servicios contables — Futrono'),
            584: ('manual', '5.2.07', 'Aseo oficina 4 días (Jeannette del Carmen)', None),
            585: ('manual', '1.1.12', 'Devolución préstamo corto Parque Sur', CP_PARQUE_SUR),
            586: ('cobro_sii', 193, CP_PARQUE_SUR, 'Servicios contables — Parque Sur'),
            587: ('cbr_pago', 197, 7),      # diff $59.007 - $59.000 = +$7 cargo extra
            588: ('cbr_pago', 198, -7),     # diff $33.603 - $33.610 = -$7 ajuste
            589: ('manual', '1.1.09', 'Inversión en Fondo Mutuo', None),
            590: ('f29_pago',),
            591: ('cobro_sii', 195, CP_EREF, 'Servicios — EREF (vía Cerro Colorado mandatario)'),
            592: ('manual', '5.2.17', 'Reembolso CBRS a Benjamín', CP_BENJAMIN),
            593: ('parque_sur_cuota', 163210, 11213),   # $174.423
            594: ('parque_sur_cuota', 109223, 38066),   # $147.289
            595: ('parque_sur_cuota', 89405, 32772),    # $122.177
            596: ('cbr_pago', 199, 0),      # $6.600 exacto
            597: ('manual', '5.2.17', 'Gastos comunes oficina (Comunidad Edificio)', None),
            598: ('manual', '5.2.04', 'Servipag (cuentas oficina)', None),
            599: ('compra_sii_pago', 189, CP_SANTANDER, '5.2.12', 'Gastos bancarios Santander'),
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
            if accion == 'skip':
                continue
            if accion == 'manual':
                _, cod, glosa, cp = spec
                # Para 1.1.12 cuando es ENTRADA (devolución mov 585) → banco debe / 1.1.12 haber
                a = asiento_simple(m, cod, glosa, cp)
                manual += 1
                # Para mov 567 (salida -$260K) el helper produce: Banco haber / 1.1.12 debe (sale plata, sube CxC) ✓
                # Para mov 585 (entrada +$260K) el helper produce: Banco debe / 1.1.12 haber (entra plata, baja CxC) ✓
                print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod} A#{a.numero}")
            elif accion == 'cobro_sii':
                _, doc_id, cp_id, glosa_v = spec
                doc = db.session.get(DocumentoSII, doc_id)
                a_f, a_c = cobro_venta_sii(m, doc, cp_id, glosa_v)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ venta doc{doc_id} folio {doc.folio}")
            elif accion == 'hon_sii_pago':
                _, doc_id, cp_id = spec
                doc = db.session.get(DocumentoSII, doc_id)
                a_h, a_p = hon_sii_pago(m, doc, cp_id)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ hon doc{doc_id} folio {doc.folio}")
            elif accion == 'cbr_pago':
                _, doc_id, diff = spec
                doc = db.session.get(DocumentoSII, doc_id)
                a_h, a_p = cbr_pago(m, doc, diff)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ CBR doc{doc_id} folio {doc.folio} (diff {diff:+})")
            elif accion == 'compra_sii_pago':
                _, doc_id, cp_id, cod_g, glosa_x = spec
                doc = db.session.get(DocumentoSII, doc_id)
                a_c, a_p = compra_sii_pago(m, doc, cp_id, cod_g, glosa_x)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ compra doc{doc_id} folio {doc.folio}")
            elif accion == 'parque_sur_cuota':
                _, cap, inte = spec
                a = parque_sur_cuota(m, cap, inte)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} cuota Parque Sur: cap ${cap:,} int ${inte:,}")
            elif accion == 'f29_pago':
                a = f29_pago(m)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} F29 ene 2026")

        db.session.commit()
        print(f"\nResumen feb Asesorías:")
        print(f"  SII:    {sii}")
        print(f"  MANUAL: {manual}")


if __name__ == '__main__':
    main()
