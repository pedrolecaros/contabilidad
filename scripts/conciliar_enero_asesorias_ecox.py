"""Conciliar enero 2026 Asesorías Ecox Limitada (id=6).

Reglas aplicadas:
- Cartola = fuente de verdad, libros SII complementan
- Banco como 1ra línea + contraparte_id en 2.1.01 Proveedores y 1.1.03 Clientes
- Cobros EREF vía Cerro Colorado SpA → aux EREF
- Cuotas Parque Sur con componente capital + interés (4.2.01)
- Préstamo intermediado Pablo/Benjamín → mismo aux Pablo Wichmann (se elimina solo)
- Asientos en BORRADOR
"""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, Contraparte)
from sqlalchemy import func as sa_func

EMP = 6  # Asesorías Ecox Limitada
CP_PARQUE_SUR = 1
CP_ECOX_SPA = 17       # Ecox SpA (no es EREF — crear EREF aparte)
CP_INV_AYSEN = 18
CP_PEDRO = 4
CP_FELIPE = 5
CP_BENJAMIN = 45       # BENJAMIN JOSE LECAROS SOTOMAYO con RUT
CP_LOS_ROBLES = 25
CP_FUTRONO = 6
CP_CHILCOS = None      # ¿existe? buscar
CP_CERRO = 7
CP_TREGUALEMU = 40
CP_PUERTO_OCTAY = 22


def cuenta(cod):
    c = Cuenta.query.filter_by(empresa_id=EMP, codigo=cod).first()
    if not c: raise SystemExit(f'Cuenta {cod} no existe para Asesorías Ecox')
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
    print(f"  + Creada contraparte: {razon_social} ({rut or 'sin RUT'}) id={cp.id}")
    return cp.id


def asiento_simple(mov, cod_contra, glosa, cp_id=None):
    """Asiento banco-vs-cuenta simple. Banco siempre línea 1."""
    c_banco = cuenta('1.1.02')
    c = cuenta(cod_contra)
    monto = float(mov.cargo or mov.abono or 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    gl = (mov.descripcion or '')[:80]
    if mov.cargo and mov.cargo > 0:
        # Salida
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                    debe=0, haber=monto, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                    debe=monto, haber=0, descripcion=gl, orden=2))
    else:
        # Entrada
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                    debe=monto, haber=0, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                    debe=0, haber=monto, descripcion=gl, orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                descripcion='Conciliación cartola enero Asesorías Ecox'))
    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Manual: {glosa}'[:280], tipo='MANUAL', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    mov.conciliacion_id = conc.id
    mov.asiento_id = a.id
    mov.procesado = True
    return a


def cobro_cliente_sii(mov, doc, cp_id, glosa_ventas):
    """Factura venta tipo 34 (exenta) + cobro mismo día.
    Asiento factura: DEBE 1.1.03 Clientes (aux) / HABER 4.1.02 Ventas Exentas
    Asiento cobro:   DEBE 1.1.02 Banco / HABER 1.1.03 Clientes (aux)
    Conciliación SII linkando doc + mov."""
    c_banco = cuenta('1.1.02')
    c_cli = cuenta('1.1.03')
    c_vta = cuenta('4.1.02')
    rs = (doc.razon_social_contraparte or '')[:60]
    total = float(doc.total)

    # Asiento factura
    a_fact = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                     descripcion=f"Factura venta exenta 34 N°{doc.folio} - {rs[:40]}",
                     origen='LIBRO_VENTAS', estado='BORRADOR')
    db.session.add(a_fact); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_fact.id, cuenta_id=c_cli.id, contraparte_id=cp_id,
                                debe=total, haber=0, descripcion=rs, orden=1))
    db.session.add(LineaAsiento(asiento_id=a_fact.id, cuenta_id=c_vta.id,
                                debe=0, haber=total, descripcion=glosa_ventas, orden=2))
    db.session.add(AsientoAudit(asiento_id=a_fact.id, accion='CREAR',
                                descripcion=f'Factura venta folio {doc.folio}'))
    doc.asiento_id = a_fact.id
    doc.procesado = True

    # Asiento cobro
    a_cob = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                    descripcion=f"Cobro factura {doc.folio} - {rs[:40]}",
                    origen='BANCO', estado='BORRADOR')
    db.session.add(a_cob); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_cob.id, cuenta_id=c_banco.id,
                                debe=total, haber=0, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_cob.id, cuenta_id=c_cli.id, contraparte_id=cp_id,
                                debe=0, haber=total, descripcion=f'Cobro factura {doc.folio}', orden=2))
    db.session.add(AsientoAudit(asiento_id=a_cob.id, accion='CREAR',
                                descripcion=f'Cobro factura folio {doc.folio}'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Factura+cobro {rs[:30]} folio {doc.folio}',
                        tipo='SII', contraparte_id=cp_id)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_cob.id
    mov.procesado = True
    return a_fact, a_cob


def main():
    app = create_app()
    with app.app_context():
        # 1. Crear contrapartes faltantes
        global CP_CHILCOS
        cp_eref = get_or_create_cp('', 'Ecox Real Estate Florida LLC', 'CLIENTE')
        cp_pablo = get_or_create_cp('', 'Pablo Tomás Wichmann', 'OTRO')
        cp_jal = get_or_create_cp('', 'Comercializadora jal Ltda.', 'PROVEEDOR')
        CP_CHILCOS = get_or_create_cp('77.871.401-9', 'Agrícola Los Chilcos SpA', 'CLIENTE')
        cp_rosa = get_or_create_cp('12137187-1', 'Rosa Gladys Cifuentes Paredes', 'HONORARIOS')
        cp_sanchez = get_or_create_cp('', 'Patricia Sanchez Miller', 'PROVEEDOR')
        cp_santander = get_or_create_cp('97.036.000-K', 'Banco Santander - Chile', 'PROVEEDOR')

        # 2. Actualizar apertura: aux EREF en línea 1.1.03 Clientes $1.200.000
        c_cli = cuenta('1.1.03')
        for la in LineaAsiento.query.filter_by(asiento_id=18, cuenta_id=c_cli.id).all():
            if la.contraparte_id is None and abs(la.debe - 1200000) < 1:
                la.contraparte_id = cp_eref
                la.descripcion = 'Clientes – desde 102101 (EREF vía Cerro Colorado)'
                print(f"  ✓ Apertura A#1 línea 1.1.03 Clientes $1.200.000 → aux EREF")
                break

        # 3. Cargar movs enero
        movs = (MovimientoBanco.query
                .filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 1, 1),
                        MovimientoBanco.fecha < date(2026, 2, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"\nMov enero Asesorías Ecox: {len(movs)}")

        # Plan: (mov_id, accion, params)
        # acciones: 'manual'(cod_contra, glosa, cp), 'cobro_sii'(doc_id, cp), 'cobro_apertura'(cp),
        #           'parque_sur_cuota'(capital, interes), 'wash_pablo_in', 'wash_pablo_out',
        #           'hon_sii_pago'(doc_id, cp), 'compra_sii_pago'(doc_id, cp, cod_gasto, glosa_extra)
        plan = {
            531: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            532: ('cobro_apertura', cp_eref, 'Cobro Clientes apertura — EREF vía Cerro Colorado'),
            533: ('cobro_apertura', cp_eref, 'Cobro Clientes apertura — EREF vía Cerro Colorado'),
            534: ('manual', '5.2.17', 'Google Workspace', None),
            535: ('cobro_sii', 16, CP_CHILCOS, 'Servicios contables — Agrícola Los Chilcos'),
            536: ('cobro_sii', 17, CP_CHILCOS, 'Servicios contables — Agrícola Los Chilcos'),
            537: ('cobro_sii', 18, CP_LOS_ROBLES, 'Servicios contables — Los Robles'),
            538: ('cobro_sii', 19, CP_PARQUE_SUR, 'Servicios contables — Parque Sur'),
            539: ('cobro_sii', 20, CP_FUTRONO, 'Servicios contables — Futrono'),
            540: ('manual', '1.1.12', 'Préstamo a Pedro Lecaros (PLS)', CP_PEDRO),
            541: ('manual', '1.1.12', 'Préstamo a Felipe Hiriart (FHB)', CP_FELIPE),
            542: ('manual', '3.1.06', 'Retiro Felipe Hiriart', CP_FELIPE),
            543: ('manual', '3.1.06', 'Retiro Pedro Lecaros', CP_PEDRO),
            544: ('manual', '1.1.09', 'Inversión en Fondo Mutuo', None),
            545: ('manual', '5.2.03', 'Arriendo oficina enero (Sanchez Miller)', None),
            546: ('manual', '5.2.01', 'Sueldo Benjamín Lecaros enero', None),
            547: ('hon_sii_pago', 22, cp_rosa),  # Rosa folio 684 bruto $707.965
            548: ('manual', '5.2.07', 'Aseo oficina 3 días (Jeannette del Carmen)', None),
            549: ('manual', '5.2.04', 'Servipag (agua/luz/gas)', None),
            550: ('manual', '5.2.17', 'Compra MercadoPago MICOCACOLA', None),
            551: ('manual', '5.2.17', 'Reembolso Tottus a Benjamín', None),
            552: ('manual', '1.1.09', 'Rescate Fondos Mutuos', None),
            553: ('manual', '2.1.07', 'Pago F29 dic 2025 (salda apertura)', None),
            554: ('manual', '5.2.17', 'Reembolso café a Felipe Hiriart', None),
            555: ('parque_sur_cuota', 162085, 12290),   # cuota $174.375
            556: ('parque_sur_cuota', 88788, 26685),    # cuota $115.473
            557: ('parque_sur_cuota', 108470, 38778),   # cuota $147.248
            558: ('manual', '5.2.09', 'Reembolso Ubereats a Felipe', CP_FELIPE),
            559: ('manual', '5.2.09', 'Reembolso bencina BSF a Pedro', CP_PEDRO),
            560: ('cobro_sii', 21, cp_eref, 'Servicios contables — EREF (vía Cerro Colorado mandatario)'),
            561: ('manual', '5.2.04', 'Servipag (agua/luz/gas)', None),
            562: ('manual', '2.1.11', 'Préstamo intermediado: Pablo deposita (se gira luego a Benjamín)', cp_pablo),
            563: ('manual', '2.1.11', 'Préstamo intermediado: pago a Benjamín por cuenta de Pablo', cp_pablo),
            564: ('manual', '5.2.17', 'Gastos comunes oficina (Comunidad Edificio)', None),
            565: ('manual', '5.2.04', 'Servipag (agua/luz/gas)', None),
            566: ('compra_sii_pago', 14, cp_santander, '5.2.12', 'Gastos bancarios Santander'),
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
                _, cod, glosa, cp = spec
                a = asiento_simple(m, cod, glosa, cp)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod} A#{a.numero} — {glosa[:55]}")

            elif accion == 'cobro_apertura':
                _, cp_id, glosa = spec
                # Cobro contra 1.1.03 Clientes con aux EREF (salda apertura)
                a = asiento_simple(m, '1.1.03', glosa, cp_id)
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → 1.1.03 (aux EREF) A#{a.numero} — {glosa[:55]}")

            elif accion == 'cobro_sii':
                _, doc_id, cp_id, glosa_v = spec
                doc = db.session.get(DocumentoSII, doc_id)
                a_f, a_c = cobro_cliente_sii(m, doc, cp_id, glosa_v)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ venta doc{doc_id} folio {doc.folio} — fact A#{a_f.numero} + cobro A#{a_c.numero}")

            elif accion == 'parque_sur_cuota':
                _, capital, interes = spec
                c_banco = cuenta('1.1.02')
                c_prest = cuenta('1.1.12')
                c_intfin = cuenta('4.2.01')
                monto = float(m.abono)
                a = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                            descripcion=f"Cuota mensual Parque Sur (capital ${capital:,} + interés ${interes:,})",
                            origen='BANCO', estado='BORRADOR')
                db.session.add(a); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                            debe=monto, haber=0, descripcion=(m.descripcion or '')[:80], orden=1))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_prest.id, contraparte_id=CP_PARQUE_SUR,
                                            debe=0, haber=capital, descripcion='Capital cuota Parque Sur', orden=2))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_intfin.id,
                                            debe=0, haber=interes, descripcion='Interés cuota Parque Sur', orden=3))
                db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                            descripcion='Cuota Parque Sur — capital+interés'))
                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'Manual: cuota Parque Sur — cap ${capital:,} + int ${interes:,}',
                                    tipo='MANUAL', contraparte_id=CP_PARQUE_SUR)
                db.session.add(conc); db.session.flush()
                m.conciliacion_id = conc.id
                m.asiento_id = a.id
                m.procesado = True
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → cuota Parque Sur A#{a.numero}: cap ${capital:,}, int ${interes:,}")

            elif accion == 'hon_sii_pago':
                _, doc_id, cp_id = spec
                doc = db.session.get(DocumentoSII, doc_id)
                c_banco = cuenta('1.1.02')
                c_hon = cuenta('5.2.02')
                c_prov = cuenta('2.1.01')
                c_ret = cuenta('2.1.04')
                bruto = float(doc.total)
                # Para boletas Rosa: iva column del doc tiene la retención
                retencion = float(doc.iva or 0)
                liquido = bruto - retencion
                rs = (doc.razon_social_contraparte or '')[:60]

                # Asiento boleta
                a_hon = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                                descripcion=f"Boleta honorarios N°{doc.folio} - {rs[:30]}",
                                origen='HONORARIOS', estado='BORRADOR')
                db.session.add(a_hon); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_hon.id,
                                            debe=bruto, haber=0, descripcion=f'{rs} (bruto)',
                                            orden=1, contraparte_id=cp_id))
                db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_prov.id,
                                            debe=0, haber=liquido, descripcion=f'Líquido {rs}',
                                            orden=2, contraparte_id=cp_id))
                db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_ret.id,
                                            debe=0, haber=retencion, descripcion='Retención 15,25%', orden=3))
                db.session.add(AsientoAudit(asiento_id=a_hon.id, accion='CREAR',
                                            descripcion=f'Honorario folio {doc.folio}'))
                doc.asiento_id = a_hon.id
                doc.procesado = True

                # Asiento pago
                a_p = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                              descripcion=f"Pago boleta hon. N°{doc.folio} - {rs[:30]}",
                              origen='BANCO', estado='BORRADOR')
                db.session.add(a_p); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id,
                                            debe=0, haber=liquido, descripcion=(m.descripcion or '')[:80], orden=1))
                db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id,
                                            debe=liquido, haber=0, descripcion=f'Pago {rs} bol {doc.folio}',
                                            orden=2, contraparte_id=cp_id))
                db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR',
                                            descripcion=f'Pago honorario folio {doc.folio}'))

                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'Hon+pago {rs[:30]} bol {doc.folio}',
                                    tipo='SII', contraparte_id=cp_id)
                db.session.add(conc); db.session.flush()
                doc.conciliacion_id = conc.id
                m.conciliacion_id = conc.id
                m.asiento_id = a_p.id
                m.procesado = True
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ hon doc{doc_id} folio {doc.folio} — A#{a_hon.numero}+A#{a_p.numero}")

            elif accion == 'compra_sii_pago':
                _, doc_id, cp_id, cod_gasto, glosa_extra = spec
                doc = db.session.get(DocumentoSII, doc_id)
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
                db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_g.id,
                                            debe=neto, haber=0, descripcion=glosa_extra or rs,
                                            orden=1, contraparte_id=cp_id))
                if iva:
                    db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_iva.id,
                                                debe=iva, haber=0, descripcion='IVA CF', orden=2))
                db.session.add(LineaAsiento(asiento_id=a_c.id, cuenta_id=c_prov.id,
                                            debe=0, haber=total, descripcion=rs,
                                            orden=3, contraparte_id=cp_id))
                db.session.add(AsientoAudit(asiento_id=a_c.id, accion='CREAR',
                                            descripcion=f'Compra folio {doc.folio}'))
                doc.asiento_id = a_c.id
                doc.procesado = True

                a_p = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                              descripcion=f"Pago factura {doc.folio} - {rs[:30]}",
                              origen='BANCO', estado='BORRADOR')
                db.session.add(a_p); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_banco.id,
                                            debe=0, haber=total, descripcion=(m.descripcion or '')[:80], orden=1))
                db.session.add(LineaAsiento(asiento_id=a_p.id, cuenta_id=c_prov.id,
                                            debe=total, haber=0, descripcion=f'Pago folio {doc.folio}',
                                            orden=2, contraparte_id=cp_id))
                db.session.add(AsientoAudit(asiento_id=a_p.id, accion='CREAR',
                                            descripcion=f'Pago folio {doc.folio}'))

                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'Factura+pago {rs[:30]} folio {doc.folio}',
                                    tipo='SII', contraparte_id=cp_id)
                db.session.add(conc); db.session.flush()
                doc.conciliacion_id = conc.id
                m.conciliacion_id = conc.id
                m.asiento_id = a_p.id
                m.procesado = True
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ compra doc{doc_id} folio {doc.folio} — A#{a_c.numero}+A#{a_p.numero}")

        db.session.commit()
        print(f"\nResumen enero Asesorías Ecox:")
        print(f"  SII:    {sii}")
        print(f"  MANUAL: {manual}")
        print(f"  Total mov procesados: {sii+manual} / {len(movs)}")


if __name__ == '__main__':
    main()
