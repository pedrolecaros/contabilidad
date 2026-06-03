"""Conciliar marzo 2026 Asesorías Ecox Limitada (id=6)."""
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
CP_CBR = 65
CP_BLANCAMAR = 66
CP_GREENE = None  # crear


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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Conciliación cartola marzo Asesorías Ecox'))
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


def cbr_pago(mov, doc):
    c_banco = cuenta('1.1.02')
    c_hon = cuenta('5.2.02')
    c_prov = cuenta('2.1.01')
    bruto = float(doc.total)
    rs = (doc.razon_social_contraparte or '')[:60]
    a_hon = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                    descripcion=f"Boleta CBR N°{doc.folio} - {rs[:30]}",
                    origen='HONORARIOS', estado='BORRADOR')
    db.session.add(a_hon); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_hon.id, debe=bruto, haber=0, descripcion=f'CBR {doc.folio}', orden=1, contraparte_id=CP_CBR))
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_prov.id, debe=0, haber=bruto, descripcion=rs, orden=2, contraparte_id=CP_CBR))
    db.session.add(AsientoAudit(asiento_id=a_hon.id, accion='CREAR', descripcion=f'Boleta CBR folio {doc.folio}'))
    doc.asiento_id = a_hon.id
    doc.procesado = True
    monto = float(mov.cargo or mov.abono or 0)
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Pago CBR N°{doc.folio}",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=bruto, haber=0, descripcion=f'Pago CBR {doc.folio}', orden=2, contraparte_id=CP_CBR))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago CBR folio {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Boleta+pago CBR folio {doc.folio}', tipo='SII', contraparte_id=CP_CBR)
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


def compra_multi_pago(movs_pago, doc, cp_id, cod_gasto, glosa_extra):
    """Factura única con múltiples pagos."""
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
    conc = Conciliacion(empresa_id=EMP, fecha=max(m.fecha for m in movs_pago), descripcion=f'Factura+pagos {rs[:30]} folio {doc.folio} ({len(movs_pago)} pagos)', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id

    for m in movs_pago:
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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Cuota Parque Sur capital+interés'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Cuota Parque Sur cap ${capital:,} + int ${interes:,}', tipo='MANUAL', contraparte_id=CP_PARQUE_SUR)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def f29_pago_feb(mov):
    f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo='2026-02').first()
    ppm = float(f29.codigo_62)
    ret = float(f29.codigo_151)
    total = float(f29.codigo_91)
    c_banco = cuenta('1.1.02')
    c_ppm = cuenta('1.1.06')
    c_ret = cuenta('2.1.04')
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=f"Pago F29 feb 2026 folio {f29.folio} (PPM + Ret Hon)",
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id, debe=ppm, haber=0, descripcion='PPM feb 2026 cód 62', orden=2))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id, debe=ret, haber=0, descripcion='Retención Hon feb 2026 cód 151 (Benjamín feb)', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'Pago F29 feb 2026 folio {f29.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'F29 feb 2026 folio {f29.folio} (PPM ${ppm:.0f} + Ret ${ret:.0f})', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def main():
    app = create_app()
    with app.app_context():
        global CP_GREENE
        CP_GREENE = get_or_create_cp('', 'Inmobiliaria e Inversiones Greene SpA', 'PROVEEDOR')

        movs = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 3, 1),
                        MovimientoBanco.fecha < date(2026, 4, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"\nMov marzo Asesorías Ecox: {len(movs)}")

        # Doc 204 Greene: pago en 2 movs (637 + 638)
        movs_greene = [m for m in movs if m.id in (637, 638)]
        doc_greene = db.session.get(DocumentoSII, 204)
        if movs_greene and doc_greene:
            a = compra_multi_pago(movs_greene, doc_greene, CP_GREENE, '5.2.11', 'Asesoría venta')
            print(f"  ✓ SII doc 204 Greene $878.573 ↔ 2 movs — fact A#{a.numero}")

        plan = {
            600: ('cobro_sii', 205, CP_CHILCOS, 'Servicios contables — Agrícola Los Chilcos'),
            601: ('manual', '1.1.12', 'Préstamo corto Parque Sur', CP_PARQUE_SUR),
            602: ('hon_sii_pago', 202, CP_BENJAMIN),  # boleta 93 feb líquido $1.2M
            603: ('manual', '5.2.03', 'Arriendo oficina marzo (Sanchez Miller)', None),
            604: ('hon_sii_pago', 213, CP_ROSA),       # anticipo boleta 714 mar (líquido $600K)
            605: ('manual', '5.2.17', 'Google Workspace', None),
            606: ('cbr_pago', 200),                     # CBR doc 200 feb $6.600
            607: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            608: ('manual', '1.1.09', 'Inversión en Fondo Mutuo', None),
            609: ('manual', '1.1.12', 'Préstamo corto Parque Sur', CP_PARQUE_SUR),
            610: ('manual', '3.1.06', 'Retiro Felipe Hiriart', CP_FELIPE),
            611: ('manual', '3.1.06', 'Retiro Pedro Lecaros', CP_PEDRO),
            612: ('manual', '1.1.12', 'Devolución préstamo corto Parque Sur', CP_PARQUE_SUR),
            613: ('manual', '1.1.12', 'Devolución préstamo corto Parque Sur', CP_PARQUE_SUR),
            614: ('cobro_sii', 208, CP_PARQUE_SUR, 'Servicios contables — Parque Sur'),
            615: ('cobro_sii', 207, CP_LOS_ROBLES, 'Servicios contables — Los Robles'),
            616: ('cobro_sii', 206, CP_CHILCOS, 'Servicios contables — Agrícola Los Chilcos'),
            617: ('cobro_sii', 210, CP_EREF, 'Servicios — EREF (vía Cerro Colorado)'),
            618: ('cobro_sii', 209, CP_FUTRONO, 'Servicios contables — Futrono'),
            619: ('manual', '3.1.06', 'Retiro Pedro Lecaros', CP_PEDRO),
            620: ('manual', '3.1.06', 'Retiro Felipe Hiriart', CP_FELIPE),
            621: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            622: ('f29_pago_feb',),
            623: ('manual', '5.2.17', 'Cooperativa raulí — Cert. factibilidad agua Santa Delfina', None),
            624: ('manual', '5.2.17', 'INVERSIONES MARAL', None),
            625: ('manual', '5.2.04', 'Servipag', None),
            626: ('manual', '5.2.07', 'Aseo oficina 3 días (Jeannette)', None),
            627: ('manual', '1.1.12', 'Felipe paga préstamo (devolución)', CP_FELIPE),
            628: ('manual', '1.1.12', 'Pedro paga préstamo (devolución)', CP_PEDRO),
            629: ('manual', '5.2.17', 'Compra MercadoPago MICOCACOLA', None),
            630: ('manual', '5.2.17', 'Compra TOTTUS Kennedy II', None),
            631: ('manual', '1.1.12', 'Felipe paga préstamo (devolución)', CP_FELIPE),
            632: ('manual', '1.1.12', 'Pedro paga préstamo (devolución)', CP_PEDRO),
            633: ('manual', '1.1.12', 'Pedro paga préstamo (devolución)', CP_PEDRO),
            634: ('manual', '1.1.12', 'Pedro paga préstamo (devolución)', CP_PEDRO),
            635: ('manual', '1.1.12', 'Felipe paga préstamo (devolución)', CP_FELIPE),
            636: ('manual', '1.1.09', 'Inversión en Fondo Mutuo', None),
            637: ('skip',),  # ya procesado en Greene multi-pago
            638: ('skip',),
            639: ('manual', '1.1.12', 'Pedro paga préstamo (devolución)', CP_PEDRO),
            640: ('manual', '1.1.09', 'Inversión en Fondo Mutuo', None),
            641: ('manual', '5.2.17', 'Compra TOTTUS Kennedy II', None),
            642: ('cobro_sii', 211, CP_EREF, 'Servicios legales — EREF (vía Cerro Colorado)'),
            643: ('manual', '5.2.09', 'Viaje Parque Sur (MercadoPago TROP)', None),
            644: ('manual', '5.2.09', 'Viaje Parque Sur (MercadoPago TROP)', None),
            645: ('parque_sur_cuota', 164697, 10149),   # $174.846
            646: ('parque_sur_cuota', 110218, 37428),   # $147.646
            647: ('parque_sur_cuota', 90219, 32254),    # $122.473
            648: ('manual', '5.2.09', 'Viaje Parque Sur (MAITEM)', None),
            649: ('manual', '5.2.04', 'Servipag', None),
            650: ('compra_sii_pago', 203, CP_SANTANDER, '5.2.12', 'Gastos bancarios Santander'),
            651: ('manual', '5.2.17', 'Gastos comunes oficina febrero (Comunidad Edificio)', None),
            652: ('manual', '5.2.17', 'Reembolso compra Tottus a Benjamín', CP_BENJAMIN),
            653: ('manual', '5.2.04', 'Servipag', None),
            654: ('manual', '5.2.17', 'Rendición gastos 1/2026 Pedro Lecaros', CP_PEDRO),
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
                a = asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod} A#{a.numero}")
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
            elif accion == 'cbr_pago':
                _, doc_id = spec
                doc = db.session.get(DocumentoSII, doc_id)
                cbr_pago(m, doc)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ CBR doc{doc_id} folio {doc.folio}")
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
                print(f"  ✓ MANUAL mov#{m.id} cuota Parque Sur: cap ${cap:,} int ${inte:,}")
            elif accion == 'f29_pago_feb':
                f29_pago_feb(m)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} F29 feb 2026")

        db.session.commit()
        print(f"\nResumen marzo Asesorías:")
        print(f"  SII:    {sii}")
        print(f"  MANUAL: {manual}")


if __name__ == '__main__':
    main()
