"""Procesa cartola marzo 2026 Los Robles SpA."""
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
CP_BENJAMIN_LECAROS = 45
CP_FRANCISCO_SANDOVAL = None  # no existe contraparte

def cuenta(cod):
    c = Cuenta.query.filter_by(empresa_id=EMP, codigo=cod).first()
    if not c: raise SystemExit(f'Cuenta {cod} no existe')
    return c

def next_num():
    n = db.session.query(sa_func.coalesce(sa_func.max(Asiento.numero), 0)).filter(Asiento.empresa_id == EMP).scalar() or 0
    return n + 1


def hacer_macal(mov, doc, comision_total, comision_neto, iva, glosa_venta):
    """Procesa 1 venta Macal + 1 factura comisión Macal vía Caja.

    - Mov banco (ingreso neto) → asiento venta compuesto: Banco + Caja / Ventas
    - Doc SII Macal → asiento compra: 5.2.13 + 1.1.05 IVA / 2.1.01
    - Asiento pago Macal vía Caja: 2.1.01 / 1.1.01
    - Conciliación SII enlaza doc + mov
    """
    c_banco = cuenta('1.1.02')
    c_caja = cuenta('1.1.01')
    c_ventas = cuenta('4.1.02')
    c_com = cuenta('5.2.13')
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total_venta = float(mov.abono) + comision_total

    # 1. Asiento venta compuesto
    a_venta = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                      descripcion=f"Venta lote vía Macal — pago neto + comisión retenida",
                      origen='BANCO', estado='BORRADOR')
    db.session.add(a_venta); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_venta.id, cuenta_id=c_banco.id,
                                debe=mov.abono, haber=0,
                                descripcion=f'Recibido de Macal (neto)', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_venta.id, cuenta_id=c_caja.id,
                                debe=comision_total, haber=0,
                                descripcion='Comisión Macal retenida', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_venta.id, cuenta_id=c_ventas.id,
                                debe=0, haber=total_venta,
                                descripcion=glosa_venta, orden=3))
    db.session.add(AsientoAudit(asiento_id=a_venta.id, accion='CREAR',
                                descripcion='Venta vía Macal (Banco+Caja / Ventas)'))

    # 2. Asiento compra factura Macal
    a_compra = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                       descripcion=f"Factura compra Macal N°{doc.folio} - comisión venta",
                       origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_compra); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_com.id,
                                debe=comision_neto, haber=0,
                                descripcion='Comisión Macal (neto)', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_iva.id,
                                debe=iva, haber=0,
                                descripcion='IVA CF Macal', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_prov.id,
                                debe=0, haber=comision_total,
                                descripcion='Proveedor Macal Ltda', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_compra.id, accion='CREAR',
                                descripcion=f'Compra factura Macal folio {doc.folio}'))
    doc.asiento_id = a_compra.id
    doc.procesado = True

    # 3. Asiento pago Macal vía Caja
    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago Macal folio {doc.folio} vía Caja (comisión retenida en venta)",
                     origen='MANUAL', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=comision_total, haber=0,
                                descripcion=f'Pago Macal folio {doc.folio}', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_caja.id,
                                debe=0, haber=comision_total,
                                descripcion='Pago vía Caja', orden=2))
    db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR',
                                descripcion='Pago Macal vía Caja (compensación)'))

    # 4. Conciliación SII enlazando doc + mov
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
    """Compra con IVA: 5.2.x + 1.1.05 IVA / 2.1.01; luego pago 2.1.01 / Banco."""
    c_banco = cuenta('1.1.02')
    c_gasto = cuenta(codigo_gasto)
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    iva = float(doc.iva) if con_iva else 0.0
    neto = total - iva

    a_compra = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                       descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - {(doc.razon_social_contraparte or '')[:40]}",
                       origen='LIBRO_COMPRAS', estado='BORRADOR')
    db.session.add(a_compra); db.session.flush()
    rs = (doc.razon_social_contraparte or '')[:60]
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_gasto.id,
                                debe=neto, haber=0,
                                descripcion=f'{glosa_extra or rs}', orden=1, contraparte_id=cp_id))
    if iva:
        db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_iva.id,
                                    debe=iva, haber=0, descripcion='IVA CF', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_prov.id,
                                debe=0, haber=total, descripcion=rs, orden=3, contraparte_id=cp_id))
    db.session.add(AsientoAudit(asiento_id=a_compra.id, accion='CREAR',
                                descripcion=f'Compra folio {doc.folio}'))
    doc.asiento_id = a_compra.id
    doc.procesado = True

    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago {rs} - folio {doc.folio}",
                     origen='BANCO', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=total, haber=0,
                                descripcion=f'Pago folio {doc.folio}', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR',
                                descripcion=f'Pago folio {doc.folio}'))

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
    """Honorario: 5.2.x bruto / 2.1.01 líquido / 2.1.04 retención (si aplica).
    Pago: 2.1.01 líquido / banco líquido."""
    c_banco = cuenta('1.1.02')
    c_gasto = cuenta(codigo_gasto)
    c_prov = cuenta('2.1.01')
    c_ret = cuenta('2.1.04')
    bruto = float(doc.total)
    if con_retencion:
        retencion = round(bruto * 0.1525)
        liquido = bruto - retencion
    else:
        retencion = 0
        liquido = bruto

    rs = (doc.razon_social_contraparte or '')[:60]
    a_hon = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                    descripcion=f"Boleta hon. N°{doc.folio} - {rs[:30]}",
                    origen='HONORARIOS', estado='BORRADOR')
    db.session.add(a_hon); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_gasto.id,
                                debe=bruto, haber=0,
                                descripcion=f'{rs} (bruto)', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_prov.id,
                                debe=0, haber=liquido,
                                descripcion=f'Líquido {rs}', orden=2, contraparte_id=cp_id))
    if retencion:
        db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_ret.id,
                                    debe=0, haber=retencion,
                                    descripcion='Retención 15,25%', orden=3))
    db.session.add(AsientoAudit(asiento_id=a_hon.id, accion='CREAR',
                                descripcion=f'Honorario folio {doc.folio}'))
    doc.asiento_id = a_hon.id
    doc.procesado = True

    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago hon. N°{doc.folio} - {rs[:30]}",
                     origen='BANCO', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=liquido, haber=0,
                                descripcion=f'Pago {rs} bol {doc.folio}', orden=1, contraparte_id=cp_id))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                debe=0, haber=liquido, descripcion=(mov.descripcion or '')[:80], orden=2))
    db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR',
                                descripcion=f'Pago hon. folio {doc.folio}'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Hon+pago {rs[:30]} bol {doc.folio}',
                        tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_pago.id
    mov.procesado = True
    return a_hon, a_pago


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
                                descripcion='Conciliación manual cartola marzo'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Manual: {glosa_asiento}'[:280],
                        tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def main():
    app = create_app()
    with app.app_context():
        movs = (MovimientoBanco.query
                .filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 3, 1),
                        MovimientoBanco.fecha < date(2026, 4, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov marzo: {len(movs)}")

        # Plan
        plan = {
            391: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            392: ('honorario', 165, '5.2.11', CP_BENJAMIN_LECAROS, True),  # Benjamin Asesoría
            393: ('manual', '4.1.02', 'Cuota 6/48 Hector Varela Nancho (I-2)', None),
            394: ('manual', '4.1.02', 'Cuota 1/60 Javier Gomez (M-6)', None),
            395: ('manual', '4.1.02', 'Cuota 33/36 Reina Mar / Francisco Sandoval (B-1)', None),
            396: ('manual', '5.2.01', 'Previred marzo', None),
            397: ('manual', '5.2.01', 'Sueldo Hector Varela marzo', None),
            398: ('manual', '4.1.02', 'Cuota 7/12 Cristian Vidal (J-5)', None),
            399: ('manual', '4.1.02', 'Reserva parcela H-7 Walker', None),
            400: ('manual', '4.1.02', 'Cuota 22/48 Viviana Molina (I-1)', None),
            401: ('macal', 154, 1487500.0, 1250000.0, 237500.0, 'Venta lote vía Macal — fact folio 153302'),
            402: ('manual', '4.1.02', 'Cuota 23/72 Eduardo Araya (B-6)', None),
            403: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            404: ('manual', '5.2.17', 'Reembolso gastos Hector Varela', None),
            405: ('manual', '4.1.02', 'Cuota 12/72 Nicole Isamitt (H-2)', None),
            406: ('compra', 164, '5.2.11', CP_ASESORIAS_ECOX, False, 'Contabilidad mensual'),  # Asesorías Ecox mar↔mar
            407: ('manual', '4.1.02', 'Devolución reserva N-3 a Denisse Martel (reversa)', None),
            408: 'F29_FEBRERO',
            409: ('manual', '4.1.02', 'Cuota 35/48 Matias Donoso (E-6)', None),
            410: ('compra', 163, '5.2.10', None, True, 'Renovación La Nube Fotografía'),
            411: ('honorario', 158, '5.2.02', None, False),  # Carlos Ocampo bol feb — sin retención
            412: ('manual', '5.2.07', 'Mantención portón de abajo (Francisco Sandoval)', None),
            413: ('manual', '1.1.09', 'Rescate FFMM 96571220-8', None),
            414: ('manual', '5.2.07', 'Mantención portón de abajo (Francisco Sandoval)', None),
            415: ('honorario', 168, '5.2.02', None, False),  # Rieutord — sin retención
            416: ('manual', '4.1.02', 'Cuotas 7-8/72 Yasna Vidal (E-9)', None),
            417: ('honorario', 169, '5.2.02', None, True),  # Troncoso — con retención
            418: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            419: ('macal', 160, 1666000.0, 1400000.0, 266000.0, 'Venta lote vía Macal — fact folio 153487'),
            420: ('manual', '4.1.02', 'Venta J-7 Joan Valdivia (depósito efectivo)', None),
            421: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            422: ('manual', '4.1.02', 'Cuota 15/72 Jaime Contreras (E-7)', None),
            423: ('manual', '4.1.02', 'Cuota 32/48 Jose Urra (A-5)', None),
            424: ('compra', 159, '5.2.07', None, True, 'Estacado F-7'),  # GEOTIM
            425: ('manual', '4.1.02', 'Cuota 39/48 Isaias Yanez (A-1)', None),
        }

        ids_db = {m.id for m in movs}
        if ids_db != set(plan.keys()):
            print(f"FALTAN: {ids_db - set(plan.keys())}")
            print(f"SOBRAN: {set(plan.keys()) - ids_db}")
            return

        manual = sii = 0
        for m in movs:
            spec = plan[m.id]

            if spec == 'F29_FEBRERO':
                f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo='2026-02').first()
                ppm = float(f29.codigo_62)         # 77.217
                ret_hon = float(f29.codigo_151)    # 359.882
                total = float(f29.codigo_91)       # 437.099
                c_ppm = cuenta('1.1.06')
                c_ret = cuenta('2.1.04')
                c_banco = cuenta('1.1.02')
                a = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                            descripcion=f"Pago F29 feb 2026 folio {f29.folio} (PPM + Retención Hon)",
                            origen='BANCO', estado='BORRADOR')
                db.session.add(a); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id,
                                            debe=ppm, haber=0, descripcion='PPM feb 2026 cód 62', orden=1))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id,
                                            debe=ret_hon, haber=0,
                                            descripcion='Retención Hon (salda Sindy bol 114) cód 151', orden=2))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                            debe=0, haber=total, descripcion=(m.descripcion or '')[:80], orden=3))
                db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                            descripcion=f'Pago F29 feb 2026 folio {f29.folio}'))
                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'F29 feb 2026 folio {f29.folio} (PPM ${ppm:.0f} + Ret Hon ${ret_hon:.0f})',
                                    tipo='MANUAL', contraparte_id=None)
                db.session.add(conc); db.session.flush()
                m.conciliacion_id = conc.id
                m.asiento_id = a.id
                m.procesado = True
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → F29 feb A#{a.numero}: PPM={ppm}, RetHon={ret_hon}")
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
                print(f"  ✓ SII mov#{m.id} ↔ hon doc{doc_id} folio {doc.folio} — A#{a_hon.numero}+A#{a_pago.numero} ({'con' if con_ret else 'sin'} ret)")
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
        print(f"\nResumen marzo:")
        print(f"  SII:        {sii}")
        print(f"  MANUAL:     {manual}")
        print(f"  Total: {sii + manual} / {len(movs)}")


if __name__ == '__main__':
    main()
