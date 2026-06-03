"""Conciliar mayo 2026 Agrícola Los Chilcos SpA (id=8) con docs SII."""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29, Contraparte)
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
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Cartola mayo Chilcos'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Manual: {glosa}'[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id; mov.asiento_id = a.id; mov.procesado = True
    return a


def venta_directa(mov, glosa_venta):
    c_banco = cuenta('1.1.02'); c_vta = cuenta('4.1.02')
    monto = float(mov.abono)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(), descripcion=glosa_venta[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=monto, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_vta.id, debe=0, haber=monto, descripcion=glosa_venta[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Venta parcela'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Venta: {glosa_venta}'[:280], tipo='MANUAL', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id; mov.asiento_id = a.id; mov.procesado = True
    return a


def hon_sii_pago(mov, doc, cp_id, cod_gasto='5.2.11', con_retencion=True):
    """Boleta honorarios afecta retención (líquido = total - iva-retención)."""
    c_banco = cuenta('1.1.02'); c_hon = cuenta(cod_gasto); c_prov = cuenta('2.1.01'); c_ret = cuenta('2.1.04')
    bruto = float(doc.total); retencion = float(doc.iva or 0) if con_retencion else 0
    liquido = bruto - retencion
    rs = (doc.razon_social_contraparte or '')[:60]
    a_h = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                  descripcion=f"Boleta hon. N°{doc.folio} - {rs[:30]}", origen='HONORARIOS', estado='BORRADOR')
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


def compra_sii_pago(mov, doc, cp_id, cod_gasto, glosa_extra, con_iva=False):
    c_banco = cuenta('1.1.02'); c_g = cuenta(cod_gasto); c_iva = cuenta('1.1.05'); c_prov = cuenta('2.1.01')
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
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id, debe=total, haber=0, descripcion=f'Pago folio {doc.folio}', orden=2, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR', descripcion=f'Pago folio {doc.folio}'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha, descripcion=f'Factura+pago {rs[:30]} folio {doc.folio}', tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id; mov.conciliacion_id = conc.id; mov.asiento_id = a_p.id; mov.procesado = True
    return a_c, a_p


def doc_solo(doc, cp_id, cod_gasto, glosa, con_iva=False, con_retencion=False):
    """Factura/boleta sin pago en el mes — registra como factura compra → 2.1.01 pendiente."""
    c_g = cuenta(cod_gasto); c_iva = cuenta('1.1.05'); c_prov = cuenta('2.1.01'); c_ret = cuenta('2.1.04')
    total = float(doc.total)
    if con_iva:
        iva = float(doc.iva or 0)
        if iva == 0 and doc.tipo_dte == '33':
            neto = round(total / 1.19); iva = total - neto
        else:
            neto = total - iva
        retencion = 0
    elif con_retencion:
        retencion = float(doc.iva or 0)
        neto = total; iva = 0
    else:
        iva = 0; neto = total; retencion = 0
    rs = (doc.razon_social_contraparte or '')[:60]
    a = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                descripcion=f"{glosa} {doc.tipo_dte} N°{doc.folio} - {rs[:30]}", origen='LIBRO_COMPRAS' if con_iva else 'HONORARIOS', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_g.id, debe=neto, haber=0, descripcion=glosa, orden=1, contraparte_id=cp_id))
    if iva: db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_iva.id, debe=iva, haber=0, descripcion='IVA CF', orden=2))
    liquido = total - retencion
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_prov.id, debe=0, haber=liquido, descripcion=rs, orden=3, contraparte_id=cp_id))
    if retencion: db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id, debe=0, haber=retencion, descripcion='Retención', orden=4))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion=f'Doc pendiente {doc.folio}'))
    doc.asiento_id = a.id; doc.procesado = True


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


def rendicion_felipe_mayo(mov_banco, cp_felipe):
    c_banco = cuenta('1.1.02'); c_viaje = cuenta('5.2.09'); c_gg = cuenta('5.2.17')
    total = float(mov_banco.cargo)
    monto_viaje = 143347 + 57148 + 25800 + 3600 + 3600 + 3600
    monto_gg = 152240 + 24970 + 630000 + 100000
    assert monto_viaje + monto_gg == int(total)
    a = Asiento(empresa_id=EMP, fecha=mov_banco.fecha, numero=next_num(),
                descripcion='Rendición 3 Felipe Hiriart — Notarías Pto Octay + viaje 29-30 abril',
                origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id, debe=0, haber=total, descripcion=(mov_banco.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_viaje.id, debe=monto_viaje, haber=0, descripcion='Hotel Frutillar + bencina + peajes + estacionamiento', orden=2, contraparte_id=cp_felipe))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_gg.id, debe=monto_gg, haber=0, descripcion='Notaría Compraventa Lote 3 + Extensión Promesa Lote 2 + alimentación', orden=3, contraparte_id=cp_felipe))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR', descripcion='Rendición 3 Felipe'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov_banco.fecha, descripcion='Rendición 3 Felipe Hiriart', tipo='MANUAL', contraparte_id=cp_felipe)
    db.session.add(conc); db.session.flush()
    mov_banco.conciliacion_id = conc.id; mov_banco.asiento_id = a.id; mov_banco.procesado = True


def main():
    app = create_app()
    with app.app_context():
        cp_pedro = 4; cp_felipe = 5; cp_asesorias = 3; cp_benjamin = 45
        cp_hector = Contraparte.query.filter_by(razon_social='Hector Varela').first().id
        cp_rieutord = 55
        cp_troncoso = Contraparte.query.filter_by(razon_social='Jorge Ignacio Troncoso Vidal').first().id
        cp_felipe_chavez = 46
        cp_elsa = 41
        cp_conrad = Contraparte.query.filter_by(rut='10775640-K').first().id
        cp_daniel = Contraparte.query.filter_by(razon_social='Daniel Gebauer').first().id
        cp_victor = Contraparte.query.filter_by(rut='7979697-2').first().id
        cp_abdallah = 48
        cp_serpan = Contraparte.query.filter_by(razon_social='Comercial Serpan SpA').first().id

        movs_b = MovimientoBanco.query.filter_by(empresa_id=EMP, banco='Banco de Chile').filter(
            MovimientoBanco.fecha >= date(2026,5,1), MovimientoBanco.fecha < date(2026,6,1)).order_by(MovimientoBanco.fecha, MovimientoBanco.id).all()
        movs_tc = MovimientoBanco.query.filter_by(empresa_id=EMP, banco='Banco de Chile (TC)').filter(
            MovimientoBanco.fecha >= date(2026,5,1), MovimientoBanco.fecha < date(2026,6,1)).order_by(MovimientoBanco.fecha, MovimientoBanco.id).all()

        # plan_b: (acción, [args...])
        # SII doc IDs y matches:
        # mov 998 ($100K Asesorías) ↔ doc 331 (factura t34 Asesorías $100K)
        # mov 999 ($709.5K Conrad)  ↔ doc 334 (boleta f64996 Conrad $709.5K — sin retención visible)
        # mov 1001 ($1.5M Felipe Ch) ↔ doc 338 (f108 $1.769.912 líquido $1.5M)
        # mov 1005 ($300K Rieutord) ↔ doc 340 (f168561 $300K sin retención)
        # mov 1010 ($90K Rieutord)  ↔ doc 341 (f168615 $90K)
        # mov 1011 ($120K Rieutord) ↔ doc 339 (f168559 $120K)
        # mov 1014 ($300K Benjamín) ↔ doc 342 (f103 $353.982 líquido $300K)
        # mov 1022 ($50K Troncoso)  ↔ doc 343 (f11 $58.997 líquido $50K)
        # mov 1024 ($90K Elsa)      ↔ doc 346 (f25 $106.195 líquido $90K)
        # mov 1028 ($960K Felipe Ch)↔ doc 347 (f110 $1.132.744 líquido $960.001)
        plan_b = {
            998:  ('compra_sii', 331, cp_asesorias, '5.2.11', 'Asesorías Ecox SpA — gestión', False),
            999:  ('hon_sii', 334, cp_conrad, '5.2.17', False),  # boleta exenta sin retención
            1000: ('venta', 'Cuota cliente Rodolfo Utreras'),
            1001: ('hon_sii', 338, cp_felipe_chavez, '5.2.11', True),
            1002: ('manual', '5.2.17', 'Pedro Lecaros — gasto menor', cp_pedro),
            1003: ('f29_pago', '2026-04'),
            1004: ('pago_tc_b',),
            1005: ('hon_sii', 340, cp_rieutord, '5.2.17', False),
            1006: ('manual', '1.1.09', 'Rescate FFMM', None),
            1007: ('venta', 'Venta D-16 Perez Carrillo'),
            1008: ('manual', '1.1.09', 'Aporte FFMM', None),
            1009: ('manual', '5.2.17', 'Hector Varela — reembolso gastos', cp_hector),
            1010: ('hon_sii', 341, cp_rieutord, '5.2.17', False),
            1011: ('hon_sii', 339, cp_rieutord, '5.2.17', False),
            1012: ('venta', 'Cuota cliente Carlos Pena'),
            1013: ('venta', 'Venta D-16 Perez Carrillo'),
            1014: ('hon_sii', 342, cp_benjamin, '5.2.11', True),
            1015: ('manual', '1.1.09', 'Aporte FFMM', None),
            1016: ('venta', 'Venta D-16 Perez Carrillo'),
            1017: ('venta', 'Venta D-16 Perez Carrillo'),
            1018: ('venta', 'Cuota Juan Sánchez (C-9)'),
            1019: ('manual', '1.1.09', 'Aporte FFMM', None),
            1020: ('venta', 'Venta D-16 Perez Carrillo'),
            1021: ('rendicion_felipe',),
            1022: ('hon_sii', 343, cp_troncoso, '5.2.17', True),
            1023: ('manual', '1.1.09', 'Rescate FFMM', None),
            1024: ('hon_sii', 346, cp_elsa, '5.2.11', True),
            1025: ('manual', '5.2.17', 'Felipe Chávez — gasto menor Pto Octay', cp_felipe_chavez),
            1026: ('venta', 'Cuota 1/24 Luis Anaya'),
            1027: ('manual', '1.1.09', 'Rescate FFMM', None),
            1028: ('hon_sii', 347, cp_felipe_chavez, '5.2.11', True),
            1029: ('manual', '5.2.17', 'Notaría Pto Octay — Daniel Gebauer', cp_daniel),
        }

        plan_tc = {
            1072: ('skip_pair',),
            1079: ('manual', '5.2.10', 'Traspaso deuda internacional — Publicidad Facebook USD', None),
            1076: ('manual', '5.2.09', 'SKY Airlines — viaje', None),
            1077: ('manual', '5.2.09', 'ECONORENT — transporte', None),
            1080: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1081: ('manual', '5.2.12', 'Comisión compra internacional TC', None),
            1082: ('manual', '5.2.12', 'Impuesto DL 3475 TC', None),
            1083: ('manual', '5.2.12', 'Mantención mensual TC', None),
            1084: ('manual', '5.2.12', 'Intereses rotativos TC', None),
        }

        ids_b = {m.id for m in movs_b}
        ids_tc = {m.id for m in movs_tc}
        if ids_b != set(plan_b.keys()):
            print(f"FALTAN/SOBRAN banco: {ids_b ^ set(plan_b.keys())}"); return
        if ids_tc != set(plan_tc.keys()):
            print(f"FALTAN/SOBRAN TC: {ids_tc ^ set(plan_tc.keys())}"); return

        # Docs SII mayo sin pago (quedan en 2.1.01 pendientes)
        # doc 335 Felipe Chavez f104 $339.181 — sin pago
        # doc 336 Felipe Chavez f105 $327.485 — sin pago
        # doc 337 Felipe Chavez f106 $116.959 — sin pago
        # doc 344 Conrad f65224 $137.500 — sin pago
        # doc 345 Victor Quinones f249788 $3.780.591 — sin pago
        # doc 348 Abdallah f168609 $45.000 — sin pago
        # doc 349 Victor Quinones f250693 $87.100 — sin pago
        # doc 329 Comercial Serpan $211.570 — sin pago
        docs_pendientes = [
            (335, cp_felipe_chavez, '5.2.11', 'Boleta hon Felipe Chávez (sin pago mayo)', True),
            (336, cp_felipe_chavez, '5.2.11', 'Boleta hon Felipe Chávez (sin pago mayo)', True),
            (337, cp_felipe_chavez, '5.2.11', 'Boleta hon Felipe Chávez (sin pago mayo)', True),
            (344, cp_conrad,        '5.2.17', 'Boleta CBR Conrad Zulch (sin pago mayo)', False),
            (345, cp_victor,        '5.2.17', 'Boleta CBR Victor Quinones (sin pago mayo)', False),
            (348, cp_abdallah,      '5.2.17', 'Boleta CBR Abdallah Fernandez (sin pago mayo)', False),
            (349, cp_victor,        '5.2.17', 'Boleta CBR Victor Quinones (sin pago mayo)', False),
        ]
        # doc 329 Serpan factura tipo 33 (con IVA): sin pago
        for did, cp, cod, glosa, retencion in docs_pendientes:
            d = db.session.get(DocumentoSII, did)
            doc_solo(d, cp, cod, glosa, con_iva=False, con_retencion=retencion)
        doc_329 = db.session.get(DocumentoSII, 329)
        doc_solo(doc_329, cp_serpan, '5.2.06', 'Factura Comercial Serpan (sin pago mayo)', con_iva=True)
        print(f"  ✓ {len(docs_pendientes)+1} docs SII mayo sin pago → 2.1.01 pendientes")

        manual = sii = 0
        # TC cargos
        for m in movs_tc:
            spec = plan_tc[m.id]
            if spec[0] == 'skip_pair': continue
            _, cod, glosa, cp = spec
            asiento_simple(m, cod, glosa, cp)
            manual += 1

        # Pago TC pareado
        mov_1004 = next(m for m in movs_b if m.id == 1004)
        mov_1072 = next(m for m in movs_tc if m.id == 1072)
        pago_tc(mov_1004, mov_1072, 'Pago automático TC (07-05)')
        manual += 1

        # Resto banco
        for m in movs_b:
            if m.id == 1004: continue
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
            elif accion == 'f29_pago':
                _, periodo = spec
                f29_pago(m, periodo); manual += 1
            elif accion == 'rendicion_felipe':
                rendicion_felipe_mayo(m, cp_felipe); manual += 1
            elif accion == 'hon_sii':
                _, doc_id, cp_id, cod_g, con_ret = spec
                doc = db.session.get(DocumentoSII, doc_id)
                hon_sii_pago(m, doc, cp_id, cod_g, con_ret); sii += 1
            elif accion == 'compra_sii':
                _, doc_id, cp_id, cod_g, glosa_x, con_iva = spec
                doc = db.session.get(DocumentoSII, doc_id)
                compra_sii_pago(m, doc, cp_id, cod_g, glosa_x, con_iva); sii += 1

        db.session.commit()
        print(f"\nResumen mayo Chilcos: SII={sii}, MANUAL={manual}")


if __name__ == '__main__':
    main()
