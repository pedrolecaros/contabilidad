"""Procesa cartola febrero 2026 Los Robles SpA.

- 26 mov banco feb
- Match 1 doc SII (honorarios Sindy bol 114) con mov 368 → conc SII
- F29 ene 2026 pago 02-09 → compuesto (1.1.06 PPM + 5.2.01 Imp 2ª Cat Hernán)
- Asesorías Ecox 02-05 -$100K → pendiente (esperando factura folio 203 libro marzo)
- Macal, Notaría Rieutord, Carlos Ocampo bol feb → quedan pendientes
"""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date
from app import create_app
from models import db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion, Asiento, LineaAsiento, AsientoAudit, DeclaracionF29
from sqlalchemy import func as sa_func

EMP = 5
CP_ASESORIAS_ECOX = 3
CP_FELIPE_HIRIART = 5

def main():
    app = create_app()
    with app.app_context():
        movs = (MovimientoBanco.query
                .filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 2, 1),
                        MovimientoBanco.fecha < date(2026, 3, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov feb: {len(movs)}")

        def cuenta(cod):
            c = Cuenta.query.filter_by(empresa_id=EMP, codigo=cod).first()
            if not c: raise SystemExit(f'Cuenta {cod} no existe')
            return c

        c_banco = cuenta('1.1.02')

        def next_num():
            n = db.session.query(sa_func.coalesce(sa_func.max(Asiento.numero), 0)).filter(Asiento.empresa_id == EMP).scalar() or 0
            return n + 1

        def asiento_simple_cargo(mov, cod_contra, glosa_asiento, cp_id=None, glosa_linea=None):
            """Cargo: contra debe / banco haber."""
            c = cuenta(cod_contra)
            a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                        descripcion=glosa_asiento[:120], origen='BANCO', estado='BORRADOR')
            db.session.add(a); db.session.flush()
            gl = (glosa_linea or mov.descripcion or '')[:80]
            db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                        debe=mov.cargo, haber=0, descripcion=gl, orden=1))
            db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                        debe=0, haber=mov.cargo, descripcion=gl, orden=2))
            db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                        descripcion='Conciliación manual cartola febrero'))
            return a

        def asiento_simple_abono(mov, cod_contra, glosa_asiento, cp_id=None, glosa_linea=None):
            """Abono: banco debe / contra haber."""
            c = cuenta(cod_contra)
            a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                        descripcion=glosa_asiento[:120], origen='BANCO', estado='BORRADOR')
            db.session.add(a); db.session.flush()
            gl = (glosa_linea or mov.descripcion or '')[:80]
            db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                        debe=mov.abono, haber=0, descripcion=gl, orden=1))
            db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                        debe=0, haber=mov.abono, descripcion=gl, orden=2))
            db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                        descripcion='Conciliación manual cartola febrero'))
            return a

        def conciliar_manual(mov, asiento, descripcion, cp_id=None):
            conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                                descripcion=f'Manual: {descripcion}'[:280],
                                tipo='MANUAL', contraparte_id=cp_id)
            db.session.add(conc); db.session.flush()
            mov.conciliacion_id = conc.id
            mov.asiento_id = asiento.id
            mov.procesado = True
            return conc

        # Plan febrero. (mov_id, accion)
        # accion = ('ventas', glosa) | ('cargo', cod, glosa, cp_id) | 'F29' | 'SINDY' | 'PENDIENTE' | 'ABONO_FFMM' | 'REVERSA_VENTA'
        plan = {
            365: ('abono_simple', '4.1.02', 'Cuota 37/48 Isaias Yanez (A-1)', None),
            366: ('abono_simple', '4.1.02', 'Pie parcela M-6 Javier Gomez', None),
            367: ('cargo_simple', '5.2.01', 'Sueldo Hector Varela (líquido feb)', None),
            368: 'SINDY',  # Match con doc SII bol 114
            369: ('abono_simple', '4.1.02', 'Cuota 22/72 Eduardo Araya (B-6)', None),
            370: ('abono_simple', '4.1.02', 'Cuota 11/72 Nicole Isamitt (H-2)', None),
            371: ('cargo_simple', '1.1.09', 'Aporte FFMM 96571220-8', None),
            372: 'PENDIENTE',  # Asesorías Ecox $100K — espera factura libro marzo
            373: ('abono_simple', '4.1.02', 'Reserva parcela N-3 Denisse Martel', None),
            374: ('cargo_simple', '5.2.01', 'Previred (cotizaciones sueldo Hernán)', None),
            375: ('abono_simple', '4.1.02', 'Cuota 21/48 Viviana Molina (I-1)', None),
            376: ('abono_simple', '1.1.09', 'Rescate FFMM 96571220-8', None),
            377: 'F29_ENERO',
            378: ('cargo_simple', '5.2.17', 'Reembolso gastos Hector Varela', None),
            379: ('cargo_simple', '1.1.09', 'Aporte FFMM', None),
            380: ('abono_simple', '4.1.02', 'Cuota 34/48 Matias Donoso (E-6)', None),
            381: ('abono_simple', '4.1.02', 'Cuota 5/48 Hector Varela Nancho (I-2)', None),
            382: ('abono_simple', '4.1.02', 'Pago hipotecario parcela F-2', None),
            383: ('cargo_simple', '5.2.10', 'Publicidad Facebook (reembolso Felipe Hiriart)', CP_FELIPE_HIRIART),
            384: ('cargo_simple', '1.1.09', 'Aporte FFMM', None),
            385: ('abono_simple', '4.1.02', 'Cuota 31/48 Jose Urra (A-5)', None),
            386: ('abono_simple', '4.1.02', 'Cuota 25/60 Solange Molina (O-5)', None),
            387: ('cargo_simple', '4.1.02', 'Reembolso reserva F-2 a Mario Cofre (reversa)', None),
            388: ('abono_simple', '4.1.02', 'Cuota 14/72 Jaime Contreras (E-7)', None),
            389: ('abono_simple', '4.1.02', 'Cuota 38/48 Isaias Yanez (A-1)', None),
            390: ('abono_simple', '4.1.02', 'Pie parcela J-7 + F-2 (depósito cheque)', None),
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
                print(f"  ⏳ mov#{m.id} {m.fecha} DEJADO PENDIENTE — {(m.descripcion or '')[:60]}")
                pendiente += 1
                continue

            if spec == 'SINDY':
                doc = DocumentoSII.query.filter_by(empresa_id=EMP, folio='114').first()
                if not doc:
                    print(f"!! No se encontró doc SII Sindy folio 114"); continue
                c_hon = cuenta('5.2.13')  # Comisiones (Sindy = comisionista venta M-6)
                c_prov = cuenta('2.1.01')
                c_reten = cuenta('2.1.04')
                bruto = float(doc.total)
                retencion = round(bruto * 0.1525)
                liquido = bruto - retencion
                CP_SINDY = 49

                a_hon = Asiento(empresa_id=EMP, fecha=doc.fecha, numero=next_num(),
                                descripcion=f"Comisión venta M-6 — boleta hon. N°{doc.folio} Sindy Sandoval",
                                origen='HONORARIOS', estado='BORRADOR')
                db.session.add(a_hon); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_hon.id,
                                            debe=bruto, haber=0, descripcion='Comisión M-6 (bruto)',
                                            orden=1, contraparte_id=CP_SINDY))
                db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_prov.id,
                                            debe=0, haber=liquido, descripcion='Líquido Sindy',
                                            orden=2, contraparte_id=CP_SINDY))
                db.session.add(LineaAsiento(asiento_id=a_hon.id, cuenta_id=c_reten.id,
                                            debe=0, haber=retencion, descripcion='Retención 15,25%',
                                            orden=3))
                db.session.add(AsientoAudit(asiento_id=a_hon.id, accion='CREAR',
                                            descripcion=f'Honorario doc SII bol {doc.folio}'))
                doc.asiento_id = a_hon.id
                doc.procesado = True

                a_pago = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                                 descripcion=f"Pago boleta hon. N°{doc.folio} Sindy Sandoval",
                                 origen='BANCO', estado='BORRADOR')
                db.session.add(a_pago); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                            debe=liquido, haber=0, descripcion=f'Pago Sindy bol {doc.folio}',
                                            orden=1, contraparte_id=CP_SINDY))
                db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                            debe=0, haber=liquido, descripcion=(m.descripcion or '')[:80],
                                            orden=2))
                db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR',
                                            descripcion=f'Pago doc SII bol {doc.folio}'))

                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'Boleta+pago Sindy bol {doc.folio}',
                                    tipo='SII', contraparte_id=CP_SINDY)
                db.session.add(conc); db.session.flush()
                doc.conciliacion_id = conc.id
                m.conciliacion_id = conc.id
                m.asiento_id = a_pago.id
                m.procesado = True
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ Sindy bol {doc.folio} — hon A#{a_hon.numero} + pago A#{a_pago.numero}")
                continue

            if spec == 'F29_ENERO':
                f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo='2026-01').first()
                ppm = float(f29.codigo_62)         # 9.967
                imp_2cat = float(f29.codigo_48)    # 141.030 (impuesto 2ª cat Hernán)
                total = float(f29.codigo_91)       # 150.997
                c_ppm = cuenta('1.1.06')
                c_rem = cuenta('5.2.01')
                a = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                            descripcion=f"Pago F29 ene 2026 folio {f29.folio} (PPM + Imp 2ª Cat Hernán)",
                            origen='BANCO', estado='BORRADOR')
                db.session.add(a); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id,
                                            debe=ppm, haber=0, descripcion='PPM ene 2026 cód 62', orden=1))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_rem.id,
                                            debe=imp_2cat, haber=0, descripcion='Imp 2ª Cat Hernán cód 48', orden=2))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                            debe=0, haber=total, descripcion=(m.descripcion or '')[:80], orden=3))
                db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                            descripcion=f'Pago F29 ene 2026 folio {f29.folio}'))
                conciliar_manual(m, a, f'F29 ene 2026 folio {f29.folio} (PPM ${ppm:.0f} + Imp 2ª Cat ${imp_2cat:.0f})')
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → F29 ene A#{a.numero}: PPM={ppm}, Imp2Cat={imp_2cat}")
                continue

            tipo, cod, glosa, cp = spec
            if tipo == 'cargo_simple':
                a = asiento_simple_cargo(m, cod, glosa, cp)
            else:
                a = asiento_simple_abono(m, cod, glosa, cp)
            conciliar_manual(m, a, glosa, cp)
            manual += 1
            print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {cod} A#{a.numero} — {glosa[:55]}")

        db.session.commit()
        print(f"\nResumen feb:")
        print(f"  SII:        {sii}")
        print(f"  MANUAL:     {manual}")
        print(f"  PENDIENTE:  {pendiente}")
        print(f"  Total: {sii + manual + pendiente} / 26")


if __name__ == '__main__':
    main()
