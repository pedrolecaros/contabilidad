"""Conciliar marzo 2026 Agrícola Los Chilcos SpA (id=8)."""
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
CP_ELSA = 41
CP_RIEUTORD = 55
CP_HECTOR = 56  # creado en enero
CP_GYO = None
CP_VICTOR = None
CP_TEKKROM = None
CP_CONRAD = None
CP_TRONCOSO = None
CP_GEOTIM = None
CP_PARQUE_SUR = 1
CP_SAESA = 44  # ya existe global

# Buscar IDs reales
def boot_cps():
    global CP_GYO, CP_VICTOR, CP_TEKKROM, CP_CONRAD, CP_TRONCOSO, CP_GEOTIM, CP_HECTOR
    CP_HECTOR = Contraparte.query.filter_by(razon_social='Hector Varela').first().id
    CP_GYO = Contraparte.query.filter_by(razon_social='GYO Servicios Maquinaria SpA').first().id
    CP_VICTOR = Contraparte.query.filter_by(rut='7979697-2').first().id
    CP_TEKKROM = Contraparte.query.filter_by(razon_social='TEKKROM Acción Gráfica Cía Ltda').first().id
    CP_CONRAD = Contraparte.query.filter_by(rut='10775640-K').first().id


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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Conciliación cartola marzo Chilcos'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Manual: {glosa}'[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def venta_directa(mov, glosa_venta):
    c_banco = cuenta('1.1.02')
    c_vta = cuenta('4.1.02')
    monto = float(mov.abono or mov.cargo or 0)
    es_reembolso = bool(mov.cargo and mov.cargo > 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa_venta[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    if es_reembolso:
        # Reverso: 4.1.02 DEBE / Banco HABER
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov.descripcion or '')[:80], orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_vta.id, debe=monto, haber=0, descripcion=glosa_venta[:80], orden=2))
    else:
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


def compra_via_persona(mov, doc, cp_doc, cp_persona, cod_gasto, glosa_extra, con_iva=True):
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
                  descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - {rs[:30]} (vía reembolso)",
                  origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_c); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_g.id, debe=neto, haber=0, descripcion=glosa_extra or rs, orden=1, contraparte_id=cp_doc))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_prov.id, debe=0, haber=total, descripcion=rs, orden=3, contraparte_id=cp_doc))
    db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR', descripcion=f'Compra folio {doc.folio} vía reembolso'))
    doc.asiento_id = a_c.id
    doc.procesado = True
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Reembolso folio {doc.folio} (pago vía persona)",
                  origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    monto = float(mov.cargo)
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=monto, haber=0, descripcion=f'Pago {rs} vía reembolso folio {doc.folio}', orden=2, contraparte_id=cp_doc))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Reembolso persona compra {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Compra+reembolso {rs[:30]} folio {doc.folio}', tipo='SII', contraparte_id=cp_doc)
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


def pago_tc(mov_banco, mov_tc, glosa):
    """Pago TC simple: DEBE 2.1.14 / HABER Banco. mov_tc concilia sin asiento propio."""
    c_banco = cuenta('1.1.02')
    c_tc = cuenta('2.1.14')
    monto = float(mov_banco.cargo)
    a = Asiento(empresa_id=EMP, fecha=mov_banco.fecha, numero=next_num(),
                descripcion=glosa[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov_banco.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_tc.id, debe=monto, haber=0, descripcion='Pago deuda TC', orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=glosa))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_banco.fecha, descripcion=glosa, tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov_banco.conciliacion_id = conc.id
    mov_banco.asiento_id = a.id
    mov_banco.procesado = True
    if mov_tc:
        mov_tc.conciliacion_id = conc.id
        mov_tc.asiento_id = a.id
        mov_tc.procesado = True
    return a


def wash_tasacion(mov_in, mov_out, glosa):
    """Wash de 2 movs mismo monto: entra y sale por 2.1.13."""
    c_banco = cuenta('1.1.02')
    c_pas = cuenta('2.1.13')
    monto = float(mov_in.abono)
    # Entrada
    a_in = Asiento(empresa_id=EMP, fecha=mov_in.fecha, numero=next_num(),
                   descripcion=f'{glosa} — entrada', origen='BANCO', estado='BORRADOR')
    db.session.add(a_in); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_in.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov_in.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_in.id, cuenta_id=c_pas.id, debe=0, haber=monto, descripcion=glosa, orden=2))
    db.session.add(AsientoAudit(asiento_id=a_in.id, accion='CREAR', descripcion=f'Wash {glosa} entrada'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_in.fecha, descripcion=f'Wash: {glosa}', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov_in.conciliacion_id = conc.id
    mov_in.asiento_id = a_in.id
    mov_in.procesado = True
    # Salida
    a_out = Asiento(empresa_id=EMP, fecha=mov_out.fecha, numero=next_num(),
                    descripcion=f'{glosa} — salida', origen='BANCO', estado='BORRADOR')
    db.session.add(a_out); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_out.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov_out.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_out.id, cuenta_id=c_pas.id, debe=monto, haber=0, descripcion=glosa, orden=2))
    db.session.add(AsientoAudit(asiento_id=a_out.id, accion='CREAR', descripcion=f'Wash {glosa} salida'))
    mov_out.conciliacion_id = conc.id
    mov_out.asiento_id = a_out.id
    mov_out.procesado = True


def geotim_anula_fact_y_nc(doc_fact, doc_nc, cp_geotim):
    """Factura + NC que la anula (compensan a 0)."""
    c_g = cuenta('5.2.07')
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc_fact.total)
    iva = float(doc_fact.iva or 0)
    if iva == 0 and doc_fact.tipo_dte == '33':
        neto = round(total / 1.19)
        iva = total - neto
    else:
        neto = total - iva
    rs = (doc_fact.razon_social_contraparte or '')[:60]
    # Factura
    a_f = Asiento(empresa_id=EMP, fecha=doc_fact.fecha, numero=next_num(),
                  descripcion=f"Factura compra {doc_fact.tipo_dte} N°{doc_fact.folio} - {rs[:30]} (anulada por NC)",
                  origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_f); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_f.id, cuenta_id=c_g.id, debe=neto, haber=0, descripcion=f'{rs} (será anulada)', orden=1, contraparte_id=cp_geotim))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a_f.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_f.id, cuenta_id=c_prov.id, debe=0, haber=total, descripcion=rs, orden=3, contraparte_id=cp_geotim))
    db.session.add(AsientoAudit(asiento_id=a_f.id, accion='CREAR', descripcion=f'Factura {doc_fact.folio} (será anulada por NC {doc_nc.folio})'))
    doc_fact.asiento_id = a_f.id
    doc_fact.procesado = True
    # NC (inverso)
    a_nc = Asiento(empresa_id=EMP, fecha=doc_nc.fecha, numero=next_num(),
                   descripcion=f"NC 61 N°{doc_nc.folio} anula fact 351 - {rs[:30]}",
                   origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_nc); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_nc.id, cuenta_id=c_prov.id, debe=total, haber=0, descripcion=f'NC anula fact {doc_fact.folio}', orden=1, contraparte_id=cp_geotim))
    db.session.add(LineaAsiento(asiento_id=a_nc.id, cuenta_id=c_g.id, debe=0, haber=neto, descripcion='Reverso gasto NC', orden=2, contraparte_id=cp_geotim))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a_nc.id, cuenta_id=c_iva.id, debe=0, haber=iva, descripcion='Reverso IVA CF NC', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_nc.id, accion='CREAR', descripcion=f'NC {doc_nc.folio} anula factura {doc_fact.folio}'))
    doc_nc.asiento_id = a_nc.id
    doc_nc.procesado = True
    # Conciliación común
    conc = Conciliacion(empresa_id=EMP, fecha=doc_nc.fecha, descripcion=f'Factura {doc_fact.folio} + NC {doc_nc.folio} anulación', tipo='SII', contraparte_id=cp_geotim)
    db.session.add(conc); db.session.flush()
    doc_fact.conciliacion_id = conc.id
    doc_nc.conciliacion_id = conc.id


def main():
    app = create_app()
    with app.app_context():
        boot_cps()
        global CP_TRONCOSO, CP_GEOTIM
        CP_TRONCOSO = get_or_create_cp('', 'Jorge Ignacio Troncoso Vidal', 'HONORARIOS')
        CP_GEOTIM = get_or_create_cp('', 'GEOTIM Limitada', 'PROVEEDOR')

        movs_b = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                  .filter(MovimientoBanco.fecha >= date(2026, 3, 1),
                          MovimientoBanco.fecha < date(2026, 4, 1),
                          MovimientoBanco.banco == 'Banco de Chile')
                  .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        movs_tc = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                   .filter(MovimientoBanco.fecha >= date(2026, 3, 1),
                           MovimientoBanco.fecha < date(2026, 4, 1),
                           MovimientoBanco.banco == 'Banco de Chile (TC)')
                   .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov banco mar: {len(movs_b)}, TC mar: {len(movs_tc)}")

        # GEOTIM fact 351 + NC primero (sin mov banco)
        doc271 = db.session.get(DocumentoSII, 271)  # fact 351 $511.700
        doc284 = db.session.get(DocumentoSII, 284)  # NC tipo 61 folio 25
        geotim_anula_fact_y_nc(doc271, doc284, CP_GEOTIM)
        print(f"  ✓ GEOTIM: fact 351 + NC 25 (anula)")

        plan_b = {
            850: ('compra_via_persona', 285, CP_VICTOR, CP_BENJAMIN, '5.2.17', 'Doc CBR vía Benjamín (Victor folio 240637)', False),
            851: ('hon_sii', 287, CP_BENJAMIN, '5.2.11', True),  # Benjamín folio 94 "Asesoría venta D-10"
            852: ('compra_sii', 278, CP_ASESORIAS_ECOX, '5.2.11', 'Gestión Asesorías Ecox', False),
            853: ('compra_via_persona', 253, CP_TEKKROM, CP_BENJAMIN, '5.2.06', 'TEKKROM (pago doc feb vía Benjamín)', True),  # doc feb pendiente
            854: ('venta', 'Reserva B-1 Italo Rivas'),
            855: ('venta', 'Cuota 11/48 Rodolfo Utreras (B-9)'),
            856: ('venta', 'Cuota 11/24 Valentina Aranda (A-5)'),
            857: ('manual', '5.2.16', 'Impuesto Línea Crédito (DL 3475)', None),
            858: ('compra_via_persona', 288, CP_VICTOR, CP_BENJAMIN, '5.2.17', 'Doc CBR vía Benjamín (Victor folio 240781)', False),
            859: ('manual', '2.1.11', 'Pago préstamo Inversiones JOPA', CP_JOPA),
            860: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', CP_PEDRO),
            861: ('hon_sii', 286, CP_RIEUTORD, '5.2.17', False),  # Rieutord folio 164263 sin retención
            862: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', CP_PEDRO),
            863: ('manual', '5.2.12', 'Intereses Línea de Crédito', None),
            864: ('manual', '1.1.09', 'Aporte Fondos Mutuos', None),
            865: ('manual', '5.2.17', 'Reembolso menor a Benjamín', CP_BENJAMIN),
            866: ('compra_via_persona', 277, CP_TEKKROM, CP_BENJAMIN, '5.2.06', 'TEKKROM (vía Benjamín)', True),
            867: ('venta', 'Reserva C-5 Francisco Garrido (vía Katherine M.)'),
            868: ('manual', '5.2.17', 'Reembolso gastos Valle del Ranco a Hector', CP_HECTOR),
            869: ('compra_sii', 280, CP_ASESORIAS_ECOX, '5.2.11', 'Contabilidad mensual Asesorías Ecox', False),
            870: ('hon_sii', 290, CP_RIEUTORD, '5.2.17', False),  # Rieutord folio 164575
            871: ('pago_tc1',),  # PAGO AUTOMATICO 09-03
            872: ('f29_pago', '2026-02'),
            873: ('venta', 'Cuota 8/36 Juan Sánchez (C-9)'),
            874: ('venta', 'Reserva D-15 Annie Parra'),
            875: ('venta', 'Reembolso reserva B-1 Italo Rivas (reversa)'),
            876: ('wash_in', 'Tasación C-5 (Garrido paga)'),
            877: ('compra_sii', 276, CP_GYO, '5.2.07', 'Caminos — GYO Servicios Maquinaria', True),
            878: ('wash_out', 'Tasación C-5 (Chilcos paga Barrientos)'),
            879: ('hon_sii', 291, CP_TRONCOSO, '5.2.17', True),  # Troncoso "Firma D1 Valdivia" — con retención
            880: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            881: ('venta', 'Reserva C-3 Flores Sepúlveda'),
            882: ('hon_sii', 294, CP_CONRAD, '5.2.17', False),  # Conrad folio 63861 sin retención
            883: ('venta', 'Reembolso excedente vale vista D-14 Rosa Ester (reversa)'),
            884: ('manual', '5.2.17', 'Rendición 1 2026 Benjamín', CP_BENJAMIN),
            885: ('hon_sii', 293, CP_BENJAMIN, '5.2.11', True),  # Benjamín folio 97 "Asesoría D-15"
            886: ('venta', 'Pie D-15 Annie Parra'),
            887: ('venta', 'Pie D-15 Annie Parra'),
            888: ('venta', 'Pie D-15 Annie Parra'),
            889: ('manual', '1.1.09', 'Aporte Fondos Mutuos', None),
            890: ('pago_tc2',),  # CARGO POR PAGO TC 27-03
            891: ('venta', 'Venta parcela C-3 Jonathan Sepúlveda'),
            892: ('compra_sii', 270, CP_GEOTIM, '5.2.07', 'Correcciones planos Puerto Octay — GEOTIM', True),
            893: ('venta', 'Cuota 1/6 Teresa Sanhueza (C-6)'),
            894: ('venta', 'Cuota 2/24 Carlos Peña (A-12)'),
            895: ('venta', 'Cuota 12/48 Rodolfo Utreras (B-9)'),
        }

        plan_tc = {
            1049: ('skip_pair', 871),
            1051: ('manual', '5.2.10', 'Traspaso deuda internacional — Publicidad Facebook USD', None),
            1052: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1053: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1054: ('manual', '5.2.12', 'Comisión mensual mantención TC', None),
            1055: ('manual', '5.2.12', 'Impuesto DL 3475 TC', None),
            1056: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1057: ('manual', '5.2.12', 'Intereses rotativos TC', None),
            1058: ('skip_pair', 890),
            1065: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1066: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1067: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
        }

        ids_b = {m.id for m in movs_b}
        if ids_b != set(plan_b.keys()):
            print(f"FALTAN banco: {ids_b - set(plan_b.keys())}")
            print(f"SOBRAN banco: {set(plan_b.keys()) - ids_b}")
            return
        ids_tc = {m.id for m in movs_tc}
        if ids_tc != set(plan_tc.keys()):
            print(f"FALTAN TC: {ids_tc - set(plan_tc.keys())}")
            print(f"SOBRAN TC: {set(plan_tc.keys()) - ids_tc}")
            return

        manual = sii = 0

        # Procesar TC cargos primero
        for m in movs_tc:
            spec = plan_tc[m.id]
            if spec[0] == 'skip_pair':
                continue
            if spec[0] == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL TC mov#{m.id} → {cod}")

        # Procesar pagos TC (pares banco + tc)
        mov_871 = next(m for m in movs_b if m.id == 871)
        mov_1049 = next(m for m in movs_tc if m.id == 1049)
        pago_tc(mov_871, mov_1049, 'Pago automático TC (1er pago marzo, 09-03)')
        manual += 1
        print(f"  ✓ MANUAL pago TC 1 marzo")

        mov_890 = next(m for m in movs_b if m.id == 890)
        mov_1058 = next(m for m in movs_tc if m.id == 1058)
        pago_tc(mov_890, mov_1058, 'Pago TC adicional (2do pago marzo, 27-03)')
        manual += 1
        print(f"  ✓ MANUAL pago TC 2 marzo")

        # Wash Tasación C-5 (mov 876 + 878)
        mov_876 = next(m for m in movs_b if m.id == 876)
        mov_878 = next(m for m in movs_b if m.id == 878)
        wash_tasacion(mov_876, mov_878, 'Tasación C-5 (Garrido paga, Chilcos paga Barrientos)')
        manual += 1
        print(f"  ✓ Wash tasación C-5")

        # Resto banco
        for m in movs_b:
            spec = plan_b[m.id]
            accion = spec[0]
            if accion in ('pago_tc1','pago_tc2','wash_in','wash_out'):
                continue
            if accion == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → {cod}")
            elif accion == 'venta':
                _, glosa = spec
                venta_directa(m, glosa)
                manual += 1
                print(f"  ✓ VENTA mov#{m.id} — {glosa[:55]}")
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

        db.session.commit()
        print(f"\nResumen marzo Chilcos: SII={sii}, MANUAL={manual}")


if __name__ == '__main__':
    main()
