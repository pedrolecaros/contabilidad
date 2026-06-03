"""Conciliar abril 2026 Agrícola Los Chilcos SpA (id=8)."""
import sys, json
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date, datetime, timedelta
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29, DeclaracionF22, Contraparte)
from sqlalchemy import func as sa_func

EMP = 8


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


# CP existentes
def get_cps():
    cps = {}
    for rs, key in [
        ('Hector Varela','hector'),('GYO Servicios Maquinaria SpA','gyo'),
        ('TEKKROM Acción Gráfica Cía Ltda','tekkrom'),('GEOTIM Limitada','geotim'),
        ('Jorge Ignacio Troncoso Vidal','troncoso'),
        ('Ríos y Compañía Abogados Limitada','rios'),
        ('Denis Marcelo Bustos Fuentes','denis'),
    ]:
        cps[key] = Contraparte.query.filter_by(razon_social=rs).first().id
    cps['pedro'] = 4
    cps['felipe'] = 5
    cps['asesorias'] = 3
    cps['benjamin'] = 45
    cps['jopa'] = 37
    cps['parro'] = 36
    cps['elsa'] = 41
    cps['rieutord'] = 55
    cps['victor'] = Contraparte.query.filter_by(rut='7979697-2').first().id
    cps['conrad'] = Contraparte.query.filter_by(rut='10775640-K').first().id
    cps['felipe_chavez'] = 46
    cps['sindy'] = 49
    cps['abdallah'] = 48
    cps['ciuffardi'] = 51
    cps['parque_sur'] = 1
    return cps


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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Cartola abril Chilcos'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Manual: {glosa}'[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id; mov.asiento_id = a.id; mov.procesado = True
    return a


def venta_directa(mov, glosa_venta):
    c_banco = cuenta('1.1.02'); c_vta = cuenta('4.1.02')
    monto = float(mov.abono or mov.cargo or 0)
    es_reembolso = bool(mov.cargo and mov.cargo > 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(), descripcion=glosa_venta[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    if es_reembolso:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov.descripcion or '')[:80], orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_vta.id, debe=monto, haber=0, descripcion=glosa_venta[:80], orden=2))
    else:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_vta.id, debe=0, haber=monto, descripcion=glosa_venta[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Venta parcela'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Venta: {glosa_venta}'[:280], tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id; mov.asiento_id = a.id; mov.procesado = True
    return a


def hon_sii_pago(mov, doc, cp_id, cod_gasto='5.2.02', con_retencion=True):
    c_banco = cuenta('1.1.02'); c_hon = cuenta(cod_gasto); c_prov = cuenta('2.1.01'); c_ret = cuenta('2.1.04')
    bruto = float(doc.total); retencion = float(doc.iva or 0) if con_retencion else 0
    liquido = bruto - retencion
    rs = (doc.razon_social_contraparte or '')[:60]
    a_h = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(), descripcion=f"Boleta hon. N°{doc.folio} - {rs[:30]}", origen='HONORARIOS', estado='BORRADOR')
    db.session.add(a_h); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_h.id, cuenta_id=c_hon.id, debe=bruto, haber=0, descripcion=f'{rs} bruto', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_h.id, cuenta_id=c_prov.id, debe=0, haber=liquido, descripcion=f'Líquido {rs}', orden=2, contraparte_id=cp_id))
    if retencion:
        db.session.add(LineaAsiento(asiento_id=a_h.id, cuenta_id=c_ret.id, debe=0, haber=retencion, descripcion='Retención', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_h.id, accion='CREAR', descripcion=f'Hon folio {doc.folio}'))
    doc.asiento_id = a_h.id; doc.procesado = True
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(), descripcion=f"Pago hon. {doc.folio} - {rs[:30]}", origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=liquido, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=liquido, haber=0, descripcion=f'Pago {rs}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago hon folio {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Hon+pago bol {doc.folio}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id; mov.conciliacion_id = conc.id; mov.asiento_id = a_p.id; mov.procesado = True
    return a_h, a_p


def compra_sii_pago(mov, doc, cp_id, cod_gasto, glosa_extra, con_iva=False, mov_es_tc=False):
    c_banco_lado = cuenta('2.1.14') if mov_es_tc else cuenta('1.1.02')
    c_g = cuenta(cod_gasto); c_iva = cuenta('1.1.05'); c_prov = cuenta('2.1.01')
    total = float(doc.total)
    if con_iva:
        iva = float(doc.iva or 0)
        if iva == 0 and doc.tipo_dte == '33':
            neto = round(total / 1.19); iva = total - neto
        else:
            neto = total - iva
    else:
        iva = 0; neto = total
    rs = (doc.razon_social_contraparte or '')[:60]
    a_c = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                  descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - {rs[:30]}", origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_c); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_g.id, debe=neto, haber=0, descripcion=glosa_extra or rs, orden=1, contraparte_id=cp_id))
    if iva: db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_prov.id, debe=0, haber=total, descripcion=rs, orden=3, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR', descripcion=f'Compra folio {doc.folio}'))
    doc.asiento_id = a_c.id; doc.procesado = True
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(), descripcion=f"Pago factura {doc.folio} - {rs[:30]}", origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco_lado.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=total, haber=0, descripcion=f'Pago folio {doc.folio}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago folio {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Factura+pago {rs[:30]} folio {doc.folio}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id; mov.conciliacion_id = conc.id; mov.asiento_id = a_p.id; mov.procesado = True
    return a_c, a_p


def nc_anula_fact(doc_nc, cp_id, glosa_referencia, cod_gasto='5.2.11', con_iva=False):
    """NC tipo 61 que anula factura previa (de 2025 generalmente)."""
    c_g = cuenta(cod_gasto); c_iva = cuenta('1.1.05'); c_prov = cuenta('2.1.01')
    total = float(doc_nc.total); iva = 0; neto = total
    if con_iva:
        iva = float(doc_nc.iva or 0)
        if iva == 0 and doc_nc.tipo_dte == '33':
            neto = round(total / 1.19); iva = total - neto
        else:
            neto = total - iva
    rs = (doc_nc.razon_social_contraparte or '')[:60]
    # Asiento NC reverso: DEBE 2.1.01 / HABER 5.2.x
    a = Asiento(empresa_id=EMP, fecha=doc_nc.fecha, numero=next_num(),
                descripcion=f"NC 61 N°{doc_nc.folio} anula {glosa_referencia} - {rs[:30]}", origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_prov.id, debe=total, haber=0, descripcion=f'NC anula {glosa_referencia}', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_g.id, debe=0, haber=neto, descripcion='Reverso gasto NC', orden=2, contraparte_id=cp_id))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_iva.id, debe=0, haber=iva, descripcion='Reverso IVA CF NC', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'NC {doc_nc.folio} anula {glosa_referencia}'))
    doc_nc.asiento_id = a.id; doc_nc.procesado = True
    conc = Conciliacion(empresa_id=EMP, fecha=doc_nc.fecha, descripcion=f'NC {doc_nc.folio} anula {glosa_referencia}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc_nc.conciliacion_id = conc.id


def nc_aumenta_pasivo(doc_nc, cp_id, glosa_referencia, cod_gasto='5.2.11'):
    """NC que anula otra NC previa (efecto inverso: aumenta pasivo y gasto)."""
    c_g = cuenta(cod_gasto); c_prov = cuenta('2.1.01')
    total = float(doc_nc.total)
    rs = (doc_nc.razon_social_contraparte or '')[:60]
    a = Asiento(empresa_id=EMP, fecha=doc_nc.fecha, numero=next_num(),
                descripcion=f"NC 61 N°{doc_nc.folio} anula NC previa ({glosa_referencia}) - {rs[:30]}", origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_g.id, debe=total, haber=0, descripcion=f'Reinstala gasto anulado por NC', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_prov.id, debe=0, haber=total, descripcion=f'Reinstala pasivo {rs}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'NC {doc_nc.folio} anula NC previa'))
    doc_nc.asiento_id = a.id; doc_nc.procesado = True
    conc = Conciliacion(empresa_id=EMP, fecha=doc_nc.fecha, descripcion=f'NC {doc_nc.folio} anula NC previa', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc_nc.conciliacion_id = conc.id


def f29_pago(mov, periodo):
    f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo=periodo).first()
    ppm = float(f29.codigo_62); ret = float(f29.codigo_151); total = float(f29.codigo_91)
    c_banco = cuenta('1.1.02'); c_ppm = cuenta('1.1.06'); c_ret = cuenta('2.1.04')
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=f"Pago F29 {periodo} folio {f29.folio}", origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id, debe=ppm, haber=0, descripcion=f'PPM {periodo} cód 62', orden=2))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id, debe=ret, haber=0, descripcion=f'Retención Hon {periodo} cód 151', orden=3))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'F29 {periodo}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'F29 {periodo} folio {f29.folio}', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id; mov.asiento_id = a.id; mov.procesado = True
    return a


def f22_pago(mov):
    """F22 anual: devengo + pago en mismo flujo."""
    f22 = DeclaracionF22.query.filter_by(empresa_id=EMP, anio=2026).first()
    codes = json.loads(f22.codigos_json)
    ppm = float(codes.get('36', 0))           # 1.133.637
    imp_neto = float(codes.get('305', 0))     # 14.378.403
    recargo = float(codes.get('39', 0))       # 172.541
    total = float(codes.get('91', 0))         # 14.550.944
    imp_bruto = imp_neto + ppm
    ppm_apertura = 1116960.0
    reajuste = ppm - ppm_apertura  # ~16.677

    c_banco = cuenta('1.1.02'); c_5216 = cuenta('5.2.16'); c_1106 = cuenta('1.1.06')
    c_4203 = cuenta('4.2.03'); c_2107 = cuenta('2.1.07')

    # Devengo
    a_dev = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                    descripcion=f"Ajuste F22 2026 — devenga impuesto + descarga PPM", origen='MANUAL', estado='BORRADOR')
    db.session.add(a_dev); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_5216.id, debe=imp_bruto, haber=0, descripcion=f'IDPC 2025 (305+36)', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_5216.id, debe=recargo, haber=0, descripcion='Reajuste/recargo (cód 39)', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_1106.id, debe=0, haber=ppm_apertura, descripcion='Descarga PPM 2025 imputado', orden=3))
    if reajuste > 0:
        db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_4203.id, debe=0, haber=reajuste, descripcion='Reajuste IPC PPM', orden=4))
    db.session.add(LineaAsiento(asiento_id=a_dev.id, cuenta_id=c_2107.id, debe=0, haber=total, descripcion='Devengo F22 por pagar', orden=5))
    db.session.add(AsientoAudit(asiento_id=a_dev.id, accion='CREAR', descripcion=f'Devengo F22 folio {f22.folio}'))

    # Pago
    a_p = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                  descripcion=f"Pago F22 folio {f22.folio}", origen='BANCO', estado='BORRADOR')
    db.session.add(a_p); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_2107.id, debe=total, haber=0, descripcion='Pago F22', orden=2))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago F22'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'F22 2026 folio {f22.folio} (devengo + pago)', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id; mov.asiento_id = a_p.id; mov.procesado = True
    return a_dev, a_p


def rendicion_felipe_abril(mov_banco, doc_hotel, cp_felipe):
    """Rendición 2 Felipe Hiriart: hotel Frutillar (factura) + alimentación/peajes/bencina."""
    c_banco = cuenta('1.1.02'); c_viaje = cuenta('5.2.09'); c_iva = cuenta('1.1.05'); c_gg = cuenta('5.2.17')
    total = float(mov_banco.cargo)
    iva = float(doc_hotel.iva)
    neto_hotel = float(doc_hotel.total) - iva
    resto = total - float(doc_hotel.total)
    a = Asiento(empresa_id=EMP, fecha=mov_banco.fecha, numero=next_num(),
                descripcion='Rendición 2 Felipe Hiriart — Hotel Frutillar + alimentación + peajes + bencina',
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov_banco.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_viaje.id, debe=neto_hotel, haber=0, descripcion=f'Hotel Frutillar fact {doc_hotel.folio}', orden=2))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF Hotel Frutillar', orden=3))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_gg.id, debe=resto, haber=0, descripcion='Alimentación, peajes, bencina, estacionamiento (boletas)', orden=4, contraparte_id=cp_felipe))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Rendición 2 Felipe'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_banco.fecha, descripcion='Rendición 2 Felipe Hiriart', tipo='SII', contraparte_id=cp_felipe)
    db.session.add(conc); db.session.flush()
    mov_banco.conciliacion_id = conc.id; mov_banco.asiento_id = a.id; mov_banco.procesado = True
    doc_hotel.asiento_id = a.id; doc_hotel.procesado = True; doc_hotel.conciliacion_id = conc.id


def pago_mixto_con_boleta(mov_banco, doc, cp_id, glosa_principal, cod_gasto='5.2.17'):
    """Pago cubre boleta + gasto adicional sin boleta. Ej Abdallah EP servidumbre $210K = boleta $50K + extras $160K."""
    c_banco = cuenta('1.1.02'); c_g = cuenta(cod_gasto)
    total = float(mov_banco.cargo)
    monto_doc = float(doc.total)
    extra = total - monto_doc
    a = Asiento(empresa_id=EMP, fecha=mov_banco.fecha, numero=next_num(),
                descripcion=glosa_principal[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov_banco.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_g.id, debe=monto_doc, haber=0, descripcion=f'Boleta {doc.folio} {(doc.razon_social_contraparte or "")[:30]}', orden=2, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_g.id, debe=extra, haber=0, descripcion='Gasto adicional asociado al pago', orden=3, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=glosa_principal))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_banco.fecha, descripcion=glosa_principal[:280], tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov_banco.conciliacion_id = conc.id; mov_banco.asiento_id = a.id; mov_banco.procesado = True
    doc.asiento_id = a.id; doc.procesado = True; doc.conciliacion_id = conc.id


def pago_tc(mov_banco, mov_tc, glosa):
    c_banco = cuenta('1.1.02'); c_tc = cuenta('2.1.14')
    monto = float(mov_banco.cargo)
    a = Asiento(empresa_id=EMP, fecha=mov_banco.fecha, numero=next_num(), descripcion=glosa[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov_banco.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_tc.id, debe=monto, haber=0, descripcion='Pago deuda TC', orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=glosa))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_banco.fecha, descripcion=glosa, tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov_banco.conciliacion_id = conc.id; mov_banco.asiento_id = a.id; mov_banco.procesado = True
    mov_tc.conciliacion_id = conc.id; mov_tc.asiento_id = a.id; mov_tc.procesado = True
    return a


def wash_2113(mov_in, mov_out, glosa):
    c_banco = cuenta('1.1.02'); c_pas = cuenta('2.1.13')
    monto = float(mov_in.abono)
    a_in = Asiento(empresa_id=EMP, fecha=mov_in.fecha, numero=next_num(), descripcion=f'{glosa} — entrada', origen='BANCO', estado='BORRADOR')
    db.session.add(a_in); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_in.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov_in.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_in.id, cuenta_id=c_pas.id, debe=0, haber=monto, descripcion=glosa, orden=2))
    db.session.add(AsientoAudit(asiento_id=a_in.id, accion='CREAR', descripcion=f'Wash {glosa}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_in.fecha, descripcion=f'Wash: {glosa}', tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov_in.conciliacion_id = conc.id; mov_in.asiento_id = a_in.id; mov_in.procesado = True
    a_out = Asiento(empresa_id=EMP, fecha=mov_out.fecha, numero=next_num(), descripcion=f'{glosa} — salida', origen='BANCO', estado='BORRADOR')
    db.session.add(a_out); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_out.id, cuenta_id=c_banco.id, debe=0, haber=monto, descripcion=(mov_out.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_out.id, cuenta_id=c_pas.id, debe=monto, haber=0, descripcion=glosa, orden=2))
    db.session.add(AsientoAudit(asiento_id=a_out.id, accion='CREAR', descripcion=f'Wash {glosa} salida'))
    mov_out.conciliacion_id = conc.id; mov_out.asiento_id = a_out.id; mov_out.procesado = True


def main():
    app = create_app()
    with app.app_context():
        cps = get_cps()
        cp_susana = get_or_create_cp('', 'Susana Paola Belmonte Aguirre', 'HONORARIOS')
        cp_serpan = get_or_create_cp('', 'Comercial Serpan SpA', 'PROVEEDOR')
        cp_frutillar = get_or_create_cp('', 'Inversiones Frutillar SpA', 'PROVEEDOR')
        cp_autorentas = get_or_create_cp('', 'AutoRentas del Pacifico SpA', 'PROVEEDOR')
        cp_saesa = 44
        cp_pablo_ei = get_or_create_cp('', 'Pablo Eisendecher Berti', 'HONORARIOS')
        cp_daniel_g = get_or_create_cp('', 'Daniel Gebauer', 'HONORARIOS')
        cp_elizabeth = get_or_create_cp('', 'Elizabeth Blome', 'OTRO')

        movs_b = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                  .filter(MovimientoBanco.fecha >= date(2026, 4, 1),
                          MovimientoBanco.fecha < date(2026, 5, 1),
                          MovimientoBanco.banco == 'Banco de Chile')
                  .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        movs_tc = (MovimientoBanco.query.filter_by(empresa_id=EMP)
                   .filter(MovimientoBanco.fecha >= date(2026, 4, 1),
                           MovimientoBanco.fecha < date(2026, 5, 1),
                           MovimientoBanco.banco == 'Banco de Chile (TC)')
                   .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov banco abr: {len(movs_b)}, TC abr: {len(movs_tc)}")

        # Procesar NCs Asesorías Ecox primero (sin pago)
        doc306 = db.session.get(DocumentoSII, 306)  # NC 12 anula fact 123 prev
        doc307 = db.session.get(DocumentoSII, 307)  # NC 13 anula NC 56 folio 1
        nc_anula_fact(doc306, cps['asesorias'], 'Factura 123 (2025)', '5.2.11', False)
        nc_aumenta_pasivo(doc307, cps['asesorias'], 'NC 56 folio 1 (2025)', '5.2.11')
        print(f"  ✓ NCs Asesorías Ecox abril (12 y 13)")

        plan_b = {
            906: ('compra_sii', 300, cps['asesorias'], '5.2.11', 'Gestión Asesorías Ecox', False),
            907: ('compra_sii', 269, cps['parque_sur'], '5.2.07', 'Factura Parque Sur (mar)', True),
            908: ('venta', 'Cuota 12/24 Valentina Aranda (A-5)'),
            909: ('compra_sii', 295, cp_saesa, '5.2.07', 'Estudio eléctrico Pto Octay — Saesa', True),
            910: ('compra_sii', 301, cps['asesorias'], '5.2.11', 'Contabilidad Asesorías Ecox', False),
            911: ('manual', '5.2.17', 'Hector Varela — reembolso gastos', cps['hector']),
            912: ('manual', '1.1.09', 'Aporte FFMM', None),
            913: ('f29_pago', '2026-03'),
            914: ('pago_tc1',),
            915: ('venta', 'Reserva D-2 Francisco Lillo Garrido'),
            916: ('venta', 'Venta parcela C-3 Flores Sepúlveda (parte)'),
            917: ('venta', 'Venta D-1 (depósito cheque)'),
            918: ('manual', '1.1.09', 'Aporte FFMM', None),
            919: ('manual', '1.1.09', 'Aporte FFMM', None),
            920: ('venta', 'Cuota 1 Juan Sánchez (C-9)'),
            921: ('venta', 'Venta parcela D-13 Pedro Lecaros (parte)'),
            922: ('venta', 'Venta parcela D-13 Pedro Lecaros (parte)'),
            923: ('venta', 'Venta parcela C-3 Flores Sepúlveda (parte)'),
            924: ('hon_sii', 310, cps['benjamin'], '5.2.11', True),  # Benjamín folio 99 — Asesoría D-1
            925: ('manual', '1.1.09', 'Aporte FFMM', None),
            926: ('compra_via_persona', 308, cps['victor'], cps['benjamin'], '5.2.17', 'CBR Pto Octay (Victor Quinones) vía Benjamín', False),
            927: ('compra_via_persona', 309, cps['victor'], cps['benjamin'], '5.2.17', 'CBR Pto Octay (Victor Quinones) vía Benjamín', False),
            928: ('venta', 'Reserva B-1 Claudio Santibañez'),
            929: ('venta', 'Venta parcela D-13 Pedro Lecaros (parte)'),
            930: ('manual', '5.2.17', 'Felipe Hiriart — gasto menor', cps['felipe']),
            931: ('hon_sii', 311, cps['victor'], '5.2.17', False),  # Victor folio 245383
            932: ('manual', '5.2.16', 'Tesorería — Puerto Octay', None),
            933: ('manual', '5.2.17', 'CBR Pto Octay — Victor Quinones (sin folio match)', cps['victor']),
            934: ('manual', '1.1.09', 'Aporte FFMM', None),
            935: ('manual', '5.2.17', 'Notaría Pto Octay — Pablo Eisendecher', cp_pablo_ei),
            936: ('venta', 'Venta parcela C-3 Flores Sepúlveda (parte)'),
            937: ('venta', 'Venta parcela D-13 Pedro Lecaros (parte)'),
            938: ('manual', '5.2.17', 'Notaría Pto Octay — Daniel Gebauer', cp_daniel_g),
            939: ('hon_sii', 317, cps['benjamin'], '5.2.11', True),  # Benjamín folio 101 (líquido $254.250)
            940: ('manual', '1.1.09', 'Aporte FFMM', None),
            941: ('venta', 'Reembolso exceso vale vista D-14 Rosa Ester (reversa)'),
            942: ('pago_tc2',),
            943: ('hon_sii', 316, cps['benjamin'], '5.2.11', True),  # Benjamín folio 100 (líquido $300K)
            944: ('venta', 'Venta parcela D-13 Pedro Lecaros (parte)'),
            945: ('venta', 'Venta parcela C-3 Flores Sepúlveda (parte)'),
            946: ('manual', '1.1.09', 'Aporte FFMM', None),
            947: ('manual', '5.2.17', 'Benjamín — reembolso solicitud Arnad', cps['benjamin']),
            948: ('venta', 'Pie D-2 Francisco Lillo'),
            949: ('venta', 'Pie D-2 Francisco Lillo'),
            950: ('manual', '1.1.09', 'Aporte FFMM', None),
            951: ('manual', '1.1.09', 'Aporte FFMM', None),
            952: ('manual', '5.2.11', 'Felipe Chávez Torres — asesoría Puerto Octay', cps['felipe_chavez']),
            953: ('pago_mixto', 318, cps['abdallah'], 'EP servidumbre — Abdallah Fernandez (boleta 165755 + gastos asociados)'),
            954: ('venta', 'Pie D-2 Francisco Lillo'),
            955: ('venta', 'Cuotas A-6 A-7 A-8 A-13 (El Turco)'),
            956: ('hon_sii', 321, cps['sindy'], '5.2.11', True),  # Sindy folio 116
            957: ('venta', 'Pie D-2 Francisco Lillo'),
            958: ('hon_sii', 320, cps['victor'], '5.2.17', False),  # Victor folio 246126
            959: ('manual', '5.2.17', 'Pedro Lecaros — gasto menor', cps['pedro']),
            960: ('manual', '5.2.17', 'Benjamín — reembolso gastos varios', cps['benjamin']),
            961: ('hon_sii', 323, cps['victor'], '5.2.17', False),  # Victor folio 246455
            962: ('manual', '5.2.16', 'Contribuciones Tesorería', None),
            963: ('rendicion_felipe',),
            964: ('venta', 'Reserva D-16 Perez Carrillo'),
            965: ('venta', 'Reserva D-17 José Mahuzier'),
            966: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            967: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            968: ('manual', '5.2.17', 'Felipe Hiriart — gasto adicional', cps['felipe']),
            969: ('manual', '2.1.11', 'Pago préstamo Inversiones JOPA', cps['jopa']),
            970: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            971: ('manual', '1.1.09', 'Aporte FFMM', None),
            972: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            973: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            974: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            975: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            976: ('manual', '2.1.11', 'Pago préstamo Pedro Lecaros', cps['pedro']),
            977: ('manual', '1.1.09', 'Rescate FFMM grande', None),
            978: ('manual', '1.1.09', 'Rescate FFMM', None),
            979: ('manual', '1.1.09', 'Rescate FFMM', None),
            980: ('wash_in',),  # Flores +$140.500 / Conrad -$140.500 wash
            981: ('wash_out',),
            982: ('hon_sii', 324, cps['victor'], '5.2.17', False),  # Victor folio 247019
            983: ('compra_sii', 298, cps['gyo'], '5.2.07', 'Caminos — GYO', True),
            984: ('manual', '5.2.16', 'Tesorería Municipal Pto Octay — Cert. No expropiación', None),
            985: ('venta', 'Venta parcela B-1 Claudio Santibañez'),
            986: ('manual', '1.1.09', 'Aporte FFMM', None),
            987: ('manual', '5.2.17', 'Elizabeth Blome — Puerto Octay', cp_elizabeth),
            988: ('f22_pago',),
            989: ('venta', 'Venta parcela B-1 Claudio Santibañez'),
            990: ('venta', 'Venta parcela B-1 Claudio Santibañez'),
            991: ('manual', '1.1.09', 'Aporte FFMM', None),
            992: ('venta', 'Venta parcela B-1 Claudio Santibañez'),
            993: ('manual', '1.1.09', 'Aporte FFMM', None),
            994: ('venta', 'Venta parcela B-1 Claudio Santibañez'),
            995: ('hon_sii', 327, cps['ciuffardi'], '5.2.11', True),  # Ciuffardi folio 18
            996: ('compra_sii', 0, cps['asesorias'], '5.2.11', 'Asesorías Ecox SpA — gestión', False),
            997: ('venta', 'Cuota Valentina Aranda (A-5)'),
        }

        plan_tc = {
            1059: ('skip_pair', 914),
            1061: ('manual', '5.2.09', 'SKY Airlines — viaje', None),
            1068: ('manual', '5.2.10', 'Traspaso deuda internacional — Publicidad Facebook USD', None),
            1062: ('compra_sii_tc', 296, cp_serpan, '5.2.06', 'Comercial Serpan (vía TC)', True),
            1060: ('skip_pair', 942),
            1063: ('manual', '5.2.09', 'SKY Airlines — viaje', None),
            1064: ('manual', '5.2.17', 'CBR/Notaría Victor Quinones (vía TC)', cps['victor']),
            1069: ('manual', '5.2.12', 'Impuesto DL 3475 TC', None),
            1070: ('compra_sii_tc', 305, get_or_create_cp('97.036.000-K','Banco Santander - Chile','PROVEEDOR'), '5.2.12', 'Mantención TC Banco', False),
            1071: ('manual', '5.2.12', 'Intereses rotativos TC', None),
            1078: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1073: ('manual', '5.2.09', 'Inversiones AYP Pto Montt — viaje', None),
            1074: ('manual', '5.2.09', 'Mall Paseo del Mar Pto Montt — estacionamiento viaje', None),
            1075: ('manual', '5.2.17', 'MercadoPago Losaler — compra suministros', None),
        }

        ids_b = {m.id for m in movs_b}
        ids_tc = {m.id for m in movs_tc}
        if ids_b != set(plan_b.keys()):
            print(f"FALTAN banco: {ids_b - set(plan_b.keys())}")
            print(f"SOBRAN banco: {set(plan_b.keys()) - ids_b}")
            return
        if ids_tc != set(plan_tc.keys()):
            print(f"FALTAN TC: {ids_tc - set(plan_tc.keys())}")
            print(f"SOBRAN TC: {set(plan_tc.keys()) - ids_tc}")
            return

        manual = sii = 0

        # TC cargos
        for m in movs_tc:
            spec = plan_tc[m.id]
            if spec[0] == 'skip_pair':
                continue
            elif spec[0] == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
            elif spec[0] == 'compra_sii_tc':
                _, doc_id, cp_id, cod_g, glosa_x, con_iva = spec
                doc = db.session.get(DocumentoSII, doc_id)
                compra_sii_pago(m, doc, cp_id, cod_g, glosa_x, con_iva, mov_es_tc=True)
                sii += 1

        # Pagos TC
        mov_914 = next(m for m in movs_b if m.id == 914)
        mov_1059 = next(m for m in movs_tc if m.id == 1059)
        pago_tc(mov_914, mov_1059, 'Pago automático TC (1er, 07-04)')
        manual += 1
        mov_942 = next(m for m in movs_b if m.id == 942)
        mov_1060 = next(m for m in movs_tc if m.id == 1060)
        pago_tc(mov_942, mov_1060, 'Pago TC (2do, 13-04 TEF $1M)')
        manual += 1

        # Wash 980/981
        mov_980 = next(m for m in movs_b if m.id == 980)
        mov_981 = next(m for m in movs_b if m.id == 981)
        wash_2113(mov_980, mov_981, 'Tasación Flores Sepúlveda/Conrad Zulch')
        manual += 1

        # Resto banco
        for m in movs_b:
            if m.id in (914, 942, 980, 981):
                continue
            spec = plan_b[m.id]
            accion = spec[0]
            if accion == 'manual':
                _, cod, glosa, cp = spec
                asiento_simple(m, cod, glosa, cp)
                manual += 1
            elif accion == 'venta':
                _, glosa = spec
                venta_directa(m, glosa)
                manual += 1
            elif accion == 'hon_sii':
                _, doc_id, cp_id, cod_g, con_ret = spec
                doc = db.session.get(DocumentoSII, doc_id)
                hon_sii_pago(m, doc, cp_id, cod_g, con_ret)
                sii += 1
            elif accion == 'compra_sii':
                _, doc_id, cp_id, cod_g, glosa_x, con_iva = spec
                if doc_id == 0:
                    # mov 996 $4M Asesorías sin doc abril match — tratamiento como gasto directo
                    asiento_simple(m, '5.2.11', 'Gestión Asesorías Ecox (sin doc identificado)', cps['asesorias'])
                    manual += 1
                else:
                    doc = db.session.get(DocumentoSII, doc_id)
                    compra_sii_pago(m, doc, cp_id, cod_g, glosa_x, con_iva)
                    sii += 1
            elif accion == 'compra_via_persona':
                _, doc_id, cp_doc, cp_per, cod_g, glosa_x, con_iva = spec
                doc = db.session.get(DocumentoSII, doc_id)
                # Función simple usando compra_sii_pago modificado
                compra_sii_pago(m, doc, cp_doc, cod_g, glosa_x, con_iva)
                sii += 1
            elif accion == 'f29_pago':
                _, periodo = spec
                f29_pago(m, periodo)
                manual += 1
            elif accion == 'f22_pago':
                f22_pago(m)
                manual += 1
            elif accion == 'rendicion_felipe':
                doc_hotel = db.session.get(DocumentoSII, 297)
                rendicion_felipe_abril(m, doc_hotel, cps['felipe'])
                sii += 1
            elif accion == 'pago_mixto':
                _, doc_id, cp_id, glosa_p = spec
                doc = db.session.get(DocumentoSII, doc_id)
                pago_mixto_con_boleta(m, doc, cp_id, glosa_p)
                sii += 1

        db.session.commit()
        print(f"\nResumen abril Chilcos: SII={sii}, MANUAL={manual}")


if __name__ == '__main__':
    main()
