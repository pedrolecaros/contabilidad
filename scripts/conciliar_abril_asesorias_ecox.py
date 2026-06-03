"""Conciliar abril 2026 Asesorías Ecox Limitada (id=6)."""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29, DeclaracionF22, Contraparte)
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
CP_CBR = 65
CP_BLANCAMAR = 66
CP_GREENE = 67
CP_ECOX_SPA = 17
CP_AYSEN = 18
CP_PUERTO_OCTAY = 22
CP_CONTALIVE = None  # crear


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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Conciliación cartola abril Asesorías'))
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


def cobro_multi_pago(movs, doc, cp_id, glosa_venta):
    """Una venta cobrada en múltiples movs."""
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
    conc = Conciliacion(empresa_id=EMP, fecha=max(m.fecha for m in movs),
                        descripcion=f'Factura+cobros {rs[:30]} folio {doc.folio} ({len(movs)} pagos)',
                        tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    for m in movs:
        monto = float(m.abono)
        a_c = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                      descripcion=f"Cobro parcial factura {doc.folio} - {rs[:30]} (${monto:,})",
                      origen='BANCO', estado='BORRADOR')
        db.session.add(a_c); db.session.flush()
        db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(m.descripcion or '')[:80], orden=1))
        db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_cli.id, contraparte_id=cp_id, debe=0, haber=monto, descripcion=f'Cobro parcial factura {doc.folio}', orden=2))
        db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR', descripcion=f'Cobro parcial folio {doc.folio}'))
        m.conciliacion_id = conc.id
        m.asiento_id = a_c.id
        m.procesado = True
    return a_fact


def hon_sii_pago(mov, doc, cp_id):
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


def compra_multi_pago(movs, doc, cp_id, cod_gasto, glosa_extra):
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
    conc = Conciliacion(empresa_id=EMP, fecha=max(m.fecha for m in movs), descripcion=f'Factura+pagos {rs[:30]} folio {doc.folio} ({len(movs)} pagos)', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    for m in movs:
        monto = float(m.cargo)
        a_p = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                      descripcion=f"Pago parcial factura {doc.folio} - {rs[:30]} (${monto:,})",
                      origen='BANCO', estado='BORRADOR')
        db.session.add(a_p); db.session.flush()
        db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(m.descripcion or '')[:80], orden=1))
        db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=monto, haber=0, descripcion=f'Pago parcial folio {doc.folio}', orden=2, contraparte_id=cp_id))
        db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago parcial folio {doc.folio}'))
        m.conciliacion_id = conc.id
        m.asiento_id = a_p.id
        m.procesado = True
    return a_c


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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'Pago F29 {periodo} folio {f29.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'F29 {periodo} folio {f29.folio}', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def f22_devengo_y_pago(mov_pago):
    """F22 Asesorías: devengo del impuesto + pago F22."""
    f22 = DeclaracionF22.query.filter_by(empresa_id=EMP, anio=2026).first()
    import json
    codes = json.loads(f22.codigos_json)
    ppm_imputado = float(codes.get('36', 0))     # 481.133
    impuesto_neto = float(codes.get('305', 0))   # 10.376.673
    recargo = float(codes.get('39', 0))          # 124.520
    total = float(codes.get('91', 0))            # 10.501.193
    impuesto_bruto = impuesto_neto + ppm_imputado
    ppm_actual = 481132.0  # apertura
    reajuste_ipc = ppm_imputado - ppm_actual  # 1

    c_banco = cuenta('1.1.02')
    c_5216 = cuenta('5.2.16')
    c_1106 = cuenta('1.1.06')
    c_2107 = cuenta('2.1.07')
    c_4203 = cuenta('4.2.03')

    # Asiento devengo F22
    a_dev = Asiento(empresa_id=EMP, fecha=mov_pago.fecha, numero=next_num(),
                    descripcion=f'Ajuste F22 2026 (ej 2025): devenga impuesto + descarga PPM',
                    origen='MANUAL', estado='BORRADOR')
    db.session.add(a_dev); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_5216.id, debe=impuesto_bruto, haber=0,
                                descripcion=f'Impuesto IDPC 2025 (F22 305+36={impuesto_bruto:,.0f})', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_5216.id, debe=recargo, haber=0,
                                descripcion='Reajuste/recargo F22 (cód 39)', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_1106.id, debe=0, haber=ppm_actual,
                                descripcion='Descarga PPM 2025 imputado en F22', orden=3))
    if reajuste_ipc > 0:
        db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_4203.id, debe=0, haber=reajuste_ipc,
                                    descripcion='Reajuste IPC PPM', orden=4))
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_2107.id, debe=0, haber=total,
                                descripcion='Devengo impuesto F22 — pasa a por pagar', orden=5))
    db.session.add(AsientoAudit(asiento_id=a_dev.id, accion='CREAR',
                                descripcion=f'Devengo F22 2026 folio {f22.folio}'))

    # Asiento pago F22 (mov 734)
    a_p = Asiento(empresa_id=EMP, fecha=mov_pago.fecha, numero=next_num(),
                  descripcion=f"Pago F22 2026 folio {f22.folio}",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=total,
                                descripcion=(mov_pago.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_2107.id, debe=total, haber=0,
                                descripcion='Pago F22 — salda imp por pagar', orden=2))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago F22 2026 folio {f22.folio}'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov_pago.fecha,
                        descripcion=f'F22 2026 folio {f22.folio} (devengo + pago)',
                        tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov_pago.conciliacion_id = conc.id
    mov_pago.asiento_id = a_p.id
    mov_pago.procesado = True
    return a_dev, a_p


def main():
    app = create_app()
    with app.app_context():
        global CP_CONTALIVE
        CP_CONTALIVE = get_or_create_cp('', 'CONTALIVE SPA', 'PROVEEDOR')

        movs = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 4, 1),
                        MovimientoBanco.fecha < date(2026, 5, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"\nMov abril Asesorías: {len(movs)}")

        # Multi-pago Aysen fact 220 ($1.161.505 = $250K + $911.505)
        movs_aysen_fact = [m for m in movs if m.id in (706, 709)]
        doc_aysen = db.session.get(DocumentoSII, 223)
        if movs_aysen_fact and doc_aysen:
            cobro_multi_pago(movs_aysen_fact, doc_aysen, CP_AYSEN, 'Servicios — Inversiones Aysen')
            print(f"  ✓ SII doc 223 Aysen $1.161.505 ↔ movs 706+709")

        # Multi-pago Contalive fact $359.856 (movs 710+712 = $250K + $109.856)
        movs_contalive = [m for m in movs if m.id in (710, 712)]
        doc_contalive = db.session.get(DocumentoSII, 216)
        if movs_contalive and doc_contalive:
            compra_multi_pago(movs_contalive, doc_contalive, CP_CONTALIVE, '5.2.11', 'Servicios contables Contalive')
            print(f"  ✓ SII doc 216 Contalive $359.856 ↔ movs 710+712")

        # Multi-cobro Los Robles fact 221 $52.500.000 (movs 723-732 = $47.5M abril)
        # NOTA: el otro $5M es mov mayo 502 — se procesará en mayo. Por ahora solo 10 movs abril.
        movs_lr_div7 = [m for m in movs if m.id in (723, 724, 725, 726, 727, 728, 729, 730, 731, 732)]
        doc_lr = db.session.get(DocumentoSII, 224)
        # OJO: monto factura $52.5M, 10 movs abril suman $47.5M. Para cuadrar, hago la conciliación
        # incluyendo SOLO los 10 movs abril por ahora. El mov mayo 502 ($5M) se agregará en mayo.
        # La factura se emite ahora con su total $52.5M en 1.1.03, los pagos cierran $47.5M, queda $5M deudor.
        if movs_lr_div7 and doc_lr:
            cobro_multi_pago(movs_lr_div7, doc_lr, CP_LOS_ROBLES,
                             'Honorario dividendo 7 — Los Robles (52.5M facturado, 47.5M cobrado abril, 5M pendiente mayo)')
            print(f"  ✓ SII doc 224 Los Robles $52.5M ↔ 10 movs abril (queda $5M por cobrar)")

        plan = {
            687: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            688: ('cobro_sii', 218, CP_CHILCOS, 'Servicios — Agrícola Los Chilcos'),
            689: ('manual', '1.1.14', 'Reembolso Parque Sur (Rendición 13 — viajes marzo)', CP_PARQUE_SUR),
            690: ('manual', '3.1.06', 'Retiro Felipe Hiriart', CP_FELIPE),
            691: ('manual', '3.1.06', 'Retiro Pedro Lecaros', CP_PEDRO),
            692: ('hon_sii_pago', 215, CP_BENJAMIN),  # Benjamín mar líquido $1.2M
            693: ('manual', '5.2.03', 'Arriendo oficina abril (Sanchez Miller)', None),
            694: ('hon_sii_pago', 214, CP_ROSA),       # Rosa mar folio 715 líquido $600K
            695: ('manual', '5.2.17', 'Google Workspace', None),
            696: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            697: ('cobro_sii', 221, CP_LOS_ROBLES, 'Servicios — Los Robles'),
            698: ('cobro_sii', 220, CP_CHILCOS, 'Servicios — Chilcos'),
            699: ('cobro_sii', 222, CP_PARQUE_SUR, 'Servicios — Parque Sur'),
            700: ('cobro_sii', 219, CP_FUTRONO, 'Servicios — Futrono'),
            701: ('manual', '3.1.06', 'Retiro Felipe Hiriart', CP_FELIPE),
            702: ('manual', '3.1.06', 'Retiro Pedro Lecaros', CP_PEDRO),
            703: ('manual', '5.2.07', 'Aseo oficina (Jeannette)', None),
            704: ('manual', '5.2.17', 'Compra EL BACO', None),
            705: ('f29_pago', '2026-03'),  # F29 mar
            706: ('skip',),  # Aysen multi-pago doc 223
            707: ('manual', '5.2.17', 'Compra Inversiones Maral', None),
            708: ('manual', '1.1.12', 'Aysen paga préstamo (devolución)', CP_AYSEN),
            709: ('skip',),
            710: ('skip',),  # Contalive multi-pago doc 216
            711: ('manual', '5.2.17', 'Compra TOTTUS', None),
            712: ('skip',),
            713: ('parque_sur_cuota', 166400, 9085),    # $175.485
            714: ('parque_sur_cuota', 111358, 36828),   # $148.186
            715: ('parque_sur_cuota', 91153, 31768),    # $122.921
            716: ('manual', '5.2.17', 'Café (REALCOFFEEWEB)', None),
            717: ('manual', '5.2.17', 'Reembolso Pedro Lecaros', CP_PEDRO),
            718: ('manual', '5.2.16', 'Contribuciones (T.G.R.)', None),
            719: ('manual', '5.2.17', 'TIP Y TAP', None),
            720: ('manual', '5.2.17', 'MercadoPago CANT', None),
            721: ('manual', '5.2.16', 'Pago SII (pendiente identificar concepto)', None),
            722: ('manual', '1.1.12', 'Pago de Ecox SpA (abona cuenta corriente)', CP_ECOX_SPA),
            723: ('skip',),  # Los Robles fact 221 multi-pago
            724: ('skip',),
            725: ('skip',),
            726: ('skip',),
            727: ('skip',),
            728: ('skip',),
            729: ('skip',),
            730: ('skip',),
            731: ('skip',),
            732: ('skip',),
            733: ('manual', '1.1.12', 'Transferencia errónea de Aysen (wash con 735)', CP_AYSEN),
            734: ('f22_pago',),
            735: ('manual', '1.1.12', 'Devolución transferencia errónea a Aysen (wash con 733)', CP_AYSEN),
            736: ('manual', '1.1.12', 'Préstamo a Puerto Octay', CP_PUERTO_OCTAY),
            737: ('manual', '1.1.12', 'Aysen paga préstamo (devolución grande)', CP_AYSEN),
            738: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            739: ('manual', '1.1.12', 'Pago de Ecox SpA (abona cuenta corriente)', CP_ECOX_SPA),
            740: ('manual', '1.1.12', 'Préstamo a Puerto Octay', CP_PUERTO_OCTAY),
            741: ('manual', '1.1.09', 'Inversión en Fondo Mutuo', None),
            742: ('manual', '1.1.12', 'Préstamo a Puerto Octay', CP_PUERTO_OCTAY),
            743: ('manual', '5.2.07', 'Aseo oficina (Jeannette)', None),
            744: ('manual', '5.2.17', 'Gastos comunes oficina marzo (Comunidad Edificio)', None),
            745: ('compra_sii_pago', 217, CP_SANTANDER, '5.2.12', 'Gastos bancarios Santander'),
            746: ('cobro_sii', 225, CP_CHILCOS, 'Servicios — Chilcos'),
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
                asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod}")
            elif accion == 'cobro_sii':
                _, doc_id, cp_id, glosa_v = spec
                doc = db.session.get(DocumentoSII, doc_id)
                cobro_venta_sii(m, doc, cp_id, glosa_v)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ venta doc{doc_id} folio {doc.folio}")
            elif accion == 'hon_sii_pago':
                _, doc_id, cp_id = spec
                doc = db.session.get(DocumentoSII, doc_id)
                hon_sii_pago(m, doc, cp_id)
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
            elif accion == 'f22_pago':
                a_dev, a_p = f22_devengo_y_pago(m)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} F22 devengo A#{a_dev.numero} + pago A#{a_p.numero}")

        db.session.commit()
        print(f"\nResumen abril Asesorías:")
        print(f"  SII:    {sii}")
        print(f"  MANUAL: {manual}")


if __name__ == '__main__':
    main()
