"""Procesa cartola abril 2026 Los Robles SpA."""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29)
from sqlalchemy import func as sa_func

EMP = 5
CP_ASESORIAS_ECOX = 3
CP_FELIPE_HIRIART = 5
CP_FUTRONO = 6
CP_BENJAMIN_LECAROS = 45
CP_FELIPE_CHAVEZ = 46

def cuenta(cod):
    c = Cuenta.query.filter_by(empresa_id=EMP, codigo=cod).first()
    if not c: raise SystemExit(f'Cuenta {cod} no existe')
    return c

def next_num():
    n = db.session.query(sa_func.coalesce(sa_func.max(Asiento.numero), 0)).filter(Asiento.empresa_id == EMP).scalar() or 0
    return n + 1


def hacer_manual_simple(mov, cod_contra, glosa_asiento, cp_id=None):
    c_banco = cuenta('1.1.02')
    c = cuenta(cod_contra)
    monto = float(mov.cargo or mov.abono or 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa_asiento[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    gl = (mov.descripcion or '')[:80]
    if mov.cargo and mov.cargo > 0:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                    debe=monto, haber=0, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                    debe=0, haber=monto, descripcion=gl, orden=2))
    else:
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                    debe=monto, haber=0, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                    debe=0, haber=monto, descripcion=gl, orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                descripcion='Conciliación manual cartola abril'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Manual: {glosa_asiento}'[:280],
                        tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def hacer_macal(mov, doc, comision_total, comision_neto, iva, glosa_venta):
    c_banco = cuenta('1.1.02')
    c_caja = cuenta('1.1.01')
    c_ventas = cuenta('4.1.02')
    c_com = cuenta('5.2.13')
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total_venta = float(mov.abono) + comision_total
    a_venta = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                      descripcion="Venta lote vía Macal — pago neto + comisión retenida",
                      origen='BANCO', estado='BORRADOR')
    db.session.add(a_venta); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_venta.id, cuenta_id=c_banco.id,
                                debe=mov.abono, haber=0, descripcion='Recibido de Macal (neto)', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_venta.id, cuenta_id=c_caja.id,
                                debe=comision_total, haber=0, descripcion='Comisión Macal retenida', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_venta.id, cuenta_id=c_ventas.id,
                                debe=0, haber=total_venta, descripcion=glosa_venta, orden=3))
    db.session.add(AsientoAudit(asiento_id=a_venta.id, accion='CREAR', descripcion='Venta vía Macal'))

    a_compra = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                       descripcion=f"Factura compra Macal N°{doc.folio} - comisión venta",
                       origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_compra); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_com.id,
                                debe=comision_neto, haber=0, descripcion='Comisión Macal (neto)', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_iva.id,
                                debe=iva, haber=0, descripcion='IVA CF Macal', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_prov.id,
                                debe=0, haber=comision_total, descripcion='Proveedor Macal Ltda', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_compra.id, accion='CREAR',
                                descripcion=f'Compra factura Macal folio {doc.folio}'))
    doc.asiento_id = a_compra.id
    doc.procesado = True

    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago Macal folio {doc.folio} vía Caja",
                     origen='MANUAL', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=comision_total, haber=0, descripcion=f'Pago Macal folio {doc.folio}', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_caja.id,
                                debe=0, haber=comision_total, descripcion='Pago vía Caja', orden=2))
    db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR',
                                descripcion='Pago Macal vía Caja'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Venta Macal + factura comisión folio {doc.folio}',
                        tipo='SII', contraparte_id=None)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_venta.id
    mov.procesado = True
    return a_venta, a_compra, a_pago


def hacer_compra(mov, doc, codigo_gasto, cp_id, con_iva=True, glosa_extra=''):
    c_banco = cuenta('1.1.02')
    c_gasto = cuenta(codigo_gasto)
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    iva = float(doc.iva) if con_iva else 0.0
    neto = total - iva
    rs = (doc.razon_social_contraparte or '')[:60]
    a_compra = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                       descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - {rs[:40]}",
                       origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_compra); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_gasto.id,
                                debe=neto, haber=0, descripcion=glosa_extra or rs, orden=1, contraparte_id=cp_id))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_iva.id,
                                    debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_prov.id,
                                debe=0, haber=total, descripcion=rs, orden=3, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_compra.id, accion='CREAR', descripcion=f'Compra folio {doc.folio}'))
    doc.asiento_id = a_compra.id
    doc.procesado = True

    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago {rs} - folio {doc.folio}",
                     origen='BANCO', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=total, haber=0, descripcion=f'Pago folio {doc.folio}', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR', descripcion=f'Pago folio {doc.folio}'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Factura+pago {rs[:30]} folio {doc.folio}',
                        tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_pago.id
    mov.procesado = True
    return a_compra, a_pago


def hacer_honorario(mov, doc, codigo_gasto, cp_id, con_retencion):
    c_banco = cuenta('1.1.02')
    c_gasto = cuenta(codigo_gasto)
    c_prov = cuenta('2.1.01')
    c_ret = cuenta('2.1.04')
    bruto = float(doc.total)
    retencion = round(bruto * 0.1525) if con_retencion else 0
    liquido = bruto - retencion
    rs = (doc.razon_social_contraparte or '')[:60]
    a_hon = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                    descripcion=f"Boleta hon. N°{doc.folio} - {rs[:30]}",
                    origen='HONORARIOS', estado='BORRADOR')
    db.session.add(a_hon); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_gasto.id,
                                debe=bruto, haber=0, descripcion=f'{rs} (bruto)', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_prov.id,
                                debe=0, haber=liquido, descripcion=f'Líquido {rs}', orden=2, contraparte_id=cp_id))
    if retencion:
        db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_ret.id,
                                    debe=0, haber=retencion, descripcion='Retención 15,25%', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_hon.id, accion='CREAR', descripcion=f'Honorario folio {doc.folio}'))
    doc.asiento_id = a_hon.id
    doc.procesado = True

    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago hon. N°{doc.folio} - {rs[:30]}",
                     origen='BANCO', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=liquido, haber=0, descripcion=f'Pago {rs} bol {doc.folio}', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                debe=0, haber=liquido, descripcion=(mov.descripcion or '')[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR', descripcion=f'Pago hon folio {doc.folio}'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Hon+pago {rs[:30]} bol {doc.folio}',
                        tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_pago.id
    mov.procesado = True
    return a_hon, a_pago


def main():
    app = create_app()
    with app.app_context():
        movs = (MovimientoBanco.query
                .filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 4, 1),
                        MovimientoBanco.fecha < date(2026, 5, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov abril: {len(movs)}")

        # IDs de movs Asesorías Ecox "Honorario dividendo 7" — PENDIENTES (espera factura mayo)
        ASESORIAS_DIVIDENDO_7 = {455, 456, 458, 459, 460, 462, 465, 467, 471, 472}

        plan = {
            # 04-02
            426: ('compra', 172, '5.2.11', CP_ASESORIAS_ECOX, False, 'Contabilidad mensual'),
            427: ('manual', '5.2.10', 'Publicidad Facebook (reembolso Felipe Hiriart)', CP_FELIPE_HIRIART),
            428: ('manual', '5.2.01', 'Sueldo Hector Varela abril', None),
            429: ('manual', '4.1.02', 'Cuota 7/48 Hector Varela Nancho (I-2)', None),
            430: ('manual', '4.1.02', 'Cuota 26/60 Solange Molina (O-5)', None),
            431: ('manual', '4.1.02', 'Cuota 36/48 Matias Donoso (E-6)', None),
            432: ('manual', '4.1.02', 'Cuota M-6 Javier Gomez', None),
            # 04-06
            433: ('manual', '5.2.17', 'Reembolso gastos Hector Varela', None),
            434: ('manual', '4.1.02', 'Cuota 13/72 Nicole Isamitt (H-2)', None),
            435: ('manual', '4.1.02', 'Cuotas 34-36/36 Reina Mar (B-1) - completa parcela', None),
            436: ('manual', '4.1.02', 'Cuotas Yasna Vidal (E-9)', None),
            437: ('manual', '4.1.02', 'Ajuste cuota Javier Gomez (M-6)', None),
            438: ('manual', '4.1.02', 'Cuota 23/48 Viviana Molina (I-1)', None),
            # 04-07
            439: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            440: ('manual', '5.2.01', 'Previred abril', None),
            441: 'F29_MARZO',
            442: ('macal', 170, 1814750.0, 1525000.0, 289750.0, 'Venta lote vía Macal — fact folio 153586'),
            # 04-08
            443: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            444: ('manual', '4.1.02', 'Cuota 24/72 Eduardo Araya (B-6)', None),
            # 04-09
            445: ('manual', '5.2.17', 'Reembolso Felipe Hiriart (sin detalle)', CP_FELIPE_HIRIART),
            446: ('honorario', 173, '5.2.02', None, True),  # Troncoso
            # 04-15
            447: ('honorario', 174, '5.2.02', CP_FELIPE_CHAVEZ, True),  # Felipe Chávez
            448: ('honorario', 176, '5.2.02', None, False),  # Carlos Ocampo $5.200 sin ret
            # 04-17
            449: ('manual', '4.1.02', 'Cuota 9/12 Cristian Vidal (J-5)', None),
            # 04-20
            450: ('manual', '5.2.16', 'Contribuciones Tesorería', None),
            451: ('manual', '5.2.16', 'Contribuciones Tesorería', None),
            # 04-23
            452: ('manual', '1.1.09', 'Rescate FFMM 96571220-8 (gran rescate)', None),
            # 04-24 — Futrono x10, Asesorías x10 (pendientes), Ocampo wash F-7
            453: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            454: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            455: 'PENDIENTE',  # Asesorías $5M
            456: 'PENDIENTE',
            457: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            458: 'PENDIENTE',
            459: 'PENDIENTE',
            460: 'PENDIENTE',
            461: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            462: 'PENDIENTE',
            463: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            464: ('manual', '2.1.13', 'Pago CBR F-7 a Carlos Ocampo (wash, Walker reembolsará)', None),
            465: 'PENDIENTE',
            466: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            467: 'PENDIENTE',  # $2.5M
            468: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            469: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            470: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            471: 'PENDIENTE',
            472: 'PENDIENTE',
            473: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            # 04-27 — Futrono x15
            474: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            475: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            476: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            477: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            478: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            479: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            480: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            481: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            482: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            483: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            484: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            485: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            486: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            487: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            488: ('manual', '2.1.11', 'Abono préstamo Futrono SpA', CP_FUTRONO),
            # 04-28
            489: ('compra', 171, '5.2.07', None, True, 'Estacado J-6 + H-3'),  # GEOTIM $142.800
            # 04-29
            490: ('manual', '4.1.02', 'Cuota 10/12 Cristian Vidal (J-5)', None),
            # 04-30
            491: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            492: ('manual', '4.1.02', 'Cuota 27/60 Solange Molina (O-5)', None),
            493: ('manual', '4.1.02', 'Cuota 16/72 Jaime Contreras (E-7)', None),
        }

        ids_db = {m.id for m in movs}
        if ids_db != set(plan.keys()):
            print(f"FALTAN: {ids_db - set(plan.keys())}")
            print(f"SOBRAN: {set(plan.keys()) - ids_db}")
            return

        manual = sii = pendiente = 0
        for m in movs:
            spec = plan[m.id]

            if spec == 'PENDIENTE':
                print(f"  ⏳ mov#{m.id} {m.fecha} PENDIENTE — espera factura mayo Asesorías 'Honorario dividendo 7'")
                pendiente += 1
                continue

            if spec == 'F29_MARZO':
                f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo='2026-03').first()
                ppm = float(f29.codigo_62)         # 100.302
                ret_hon = float(f29.codigo_151)    # 100.497
                total = float(f29.codigo_91)       # 200.799
                c_ppm = cuenta('1.1.06')
                c_ret = cuenta('2.1.04')
                c_banco = cuenta('1.1.02')
                a = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                            descripcion=f"Pago F29 mar 2026 folio {f29.folio} (PPM + Ret Hon)",
                            origen='BANCO', estado='BORRADOR')
                db.session.add(a); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id,
                                            debe=ppm, haber=0, descripcion='PPM mar 2026 cód 62', orden=1))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id,
                                            debe=ret_hon, haber=0,
                                            descripcion='Retención Hon mar cód 151 (salda Benjamin+Troncoso)', orden=2))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                            debe=0, haber=total, descripcion=(m.descripcion or '')[:80], orden=3))
                db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                            descripcion=f'Pago F29 mar 2026 folio {f29.folio}'))
                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'F29 mar 2026 folio {f29.folio} (PPM ${ppm:.0f} + Ret Hon ${ret_hon:.0f})',
                                    tipo='MANUAL', contraparte_id=None)
                db.session.add(conc); db.session.flush()
                m.conciliacion_id = conc.id
                m.asiento_id = a.id
                m.procesado = True
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → F29 mar A#{a.numero}: PPM={ppm}, RetHon={ret_hon}")
                continue

            tipo = spec[0]
            if tipo == 'manual':
                _, cod, glosa, cp = spec
                a = hacer_manual_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod} A#{a.numero} — {glosa[:55]}")
            elif tipo == 'honorario':
                _, doc_id, cod_gasto, cp, con_ret = spec
                doc = DocumentoSII.query.get(doc_id)
                a_hon, a_pago = hacer_honorario(m, doc, cod_gasto, cp, con_ret)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ hon doc{doc_id} folio {doc.folio} — A#{a_hon.numero}+A#{a_pago.numero}")
            elif tipo == 'compra':
                _, doc_id, cod_gasto, cp, con_iva, glosa_extra = spec
                doc = DocumentoSII.query.get(doc_id)
                a_compra, a_pago = hacer_compra(m, doc, cod_gasto, cp, con_iva, glosa_extra)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ compra doc{doc_id} folio {doc.folio} — A#{a_compra.numero}+A#{a_pago.numero}")
            elif tipo == 'macal':
                _, doc_id, com_total, com_neto, iva, glosa = spec
                doc = DocumentoSII.query.get(doc_id)
                a_v, a_c, a_p = hacer_macal(m, doc, com_total, com_neto, iva, glosa)
                sii += 1
                print(f"  ✓ MACAL mov#{m.id} ↔ doc{doc_id} folio {doc.folio} — Venta A#{a_v.numero} / Compra A#{a_c.numero} / PagoCaja A#{a_p.numero}")

        db.session.commit()
        print(f"\nResumen abril:")
        print(f"  SII:        {sii}")
        print(f"  MANUAL:     {manual}")
        print(f"  PENDIENTE:  {pendiente} (Asesorías $5M/2.5M — total $47.5M abril, factura $52.5M llega mayo)")
        print(f"  Total: {sii + manual + pendiente} / {len(movs)}")


if __name__ == '__main__':
    main()
