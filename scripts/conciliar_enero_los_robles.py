"""Procesa los 27 movimientos bancarios de enero 2026 de Los Robles SpA.

Estrategia:
- 26 movimientos sin doc SII → conciliación MANUAL con cuenta sugerida.
- 1 movimiento (Asesorías Ecox 01-05 -$100K) se conciliará con doc SII feb folio 202
  ($100K factura 02-05) → genera asiento compra + pago, una Conciliación tipo SII.
- Asesorías Ecox 02-05 mov bancario NO existe aún (cartola feb no importada) — pendiente.
"""
import sys
sys.path.insert(0, '/home/pedro/contabilidad')

from app import create_app
from models import db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion, Asiento
from engine import asientos as motor
from engine.asientos import confirmar_asiento  # noqa

EMP = 5

# Cuentas por código
def cid(codigo):
    c = Cuenta.query.filter_by(empresa_id=EMP, codigo=codigo).first()
    if not c:
        raise SystemExit(f'Cuenta {codigo} no existe')
    return c.id

# Contrapartes
CP_ASESORIAS_ECOX = 3
CP_FELIPE_HIRIART = 5
CP_FUTRONO = 6
CP_LOS_ROBLES = 25


def main():
    app = create_app()
    with app.app_context():
        # Cargar mov_banco enero ordenados
        movs = (MovimientoBanco.query
                .filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha < __import__('datetime').date(2026, 2, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())
        print(f"Mov enero: {len(movs)}")

        # Mapping por id de mov → (descripcion conc, cuenta_codigo, contraparte_id)
        # Construido a partir del análisis previo
        # IDs son de la DB: 338..364
        plan = {
            338: ('Cuota 34 parte 2/2 Isaias Yanez (A-1)', '4.1.02', None),
            339: ('Cuota 34 parte 1/2 Isaias Yanez (A-1)', '4.1.02', None),
            340: ('Pago sueldo Hector Varela (salda Rem por Pagar apertura)', '2.1.05', None),
            341: 'ASESORIAS_ECOX_SII',  # Especial — concilia con doc folio 202
            342: ('Aporte Fondo Mutuo 96571220-8', '1.1.09', None),
            343: ('Reembolso gastos Hector Varela', '5.2.17', None),
            344: ('Publicidad Facebook (reembolso Felipe Hiriart)', '5.2.10', CP_FELIPE_HIRIART),
            345: ('Cuota 4/48 Hector Varela Nancho (I-2)', '4.1.02', None),
            346: ('Cuota 21/72 Eduardo Araya (B-6)', '4.1.02', None),
            347: ('Pago Previred (salda Cot. Previs. apertura)', '2.1.06', None),
            348: ('Patente municipal', '5.2.16', None),
            349: ('Cuota 33/48 Matias Donoso (E-6)', '4.1.02', None),
            350: ('Cuota 10/72 Nicole Isamitt (H-2)', '4.1.02', None),
            351: ('Cuota 20/48 Viviana Molina (I-1)', '4.1.02', None),
            352: ('Reembolso Uber Felipe Hiriart', '5.2.09', CP_FELIPE_HIRIART),
            353: ('Cuota 30/48 Jose Urra (A-5)', '4.1.02', None),
            354: ('Pago SII (salda Imp. Renta por Pagar apertura)', '2.1.07', None),
            355: ('Cuotas 35-36/48 Isaias Yanez (A-1)', '4.1.02', None),
            356: ('Reserva parcela J-7 Joan Cerda', '4.1.02', None),
            357: ('Depósito Nicole para inscripción CBR H-2 (wash)', '2.1.13', None),
            358: ('Pago CBR H-2 a Carlos Ocampo (wash)', '2.1.13', None),
            359: ('Reserva parcela M-6 Javier Gomez', '4.1.02', None),
            360: ('Publicidad Facebook (reembolso Felipe Hiriart)', '5.2.10', CP_FELIPE_HIRIART),
            361: ('Cuota 6/12 Cristian Vidal (J-5)', '4.1.02', None),
            362: ('Cuota 32/36 Reina Mar / Francisco Sandoval (B-1)', '4.1.02', None),
            363: ('Cuota 24/60 Solange Molina (O-5)', '4.1.02', None),
            364: ('Cuota 13/72 Jaime Contreras (E-7)', '4.1.02', None),
        }

        # Validar plan cubre todos
        ids_db = {m.id for m in movs}
        ids_plan = set(plan.keys())
        if ids_db != ids_plan:
            print(f"FALTAN en plan: {ids_db - ids_plan}")
            print(f"SOBRAN en plan: {ids_plan - ids_db}")
            return

        manual_creados = 0
        sii_creados = 0

        for m in movs:
            spec = plan[m.id]

            if spec == 'ASESORIAS_ECOX_SII':
                # Conciliar mov bancario con doc SII folio 202
                doc = DocumentoSII.query.filter_by(empresa_id=EMP, tipo_libro='COMPRAS', folio='202').first()
                if not doc:
                    print(f"!! No se encontró doc SII folio 202 — mov #{m.id} queda pendiente")
                    continue

                # Asiento compra (5.2.17 default — pero queremos 5.2.11 Asesorías). El motor pone GASTO_GENERAL.
                # Lo creamos manualmente para usar 5.2.11.
                # (Replicamos generar_asiento_compra pero con cuenta 5.2.11)
                c_gasto = Cuenta.query.filter_by(empresa_id=EMP, codigo='5.2.11').first()
                c_prov  = Cuenta.query.filter_by(empresa_id=EMP, codigo='2.1.01').first()
                total = float(doc.total)

                from models import Asiento as A, LineaAsiento as L, AsientoAudit as AA
                from datetime import datetime
                from sqlalchemy import func as sa_func

                next_num = (db.session.query(sa_func.coalesce(sa_func.max(A.numero), 0))
                            .filter(A.empresa_id == EMP).scalar() or 0) + 1

                asiento_compra = A(empresa_id=EMP, fecha=doc.fecha, numero=next_num,
                                   descripcion=f"Factura compra {doc.tipo_dte} N°{doc.folio} - {doc.razon_social_contraparte[:40]}",
                                   origen='LIBRO_COMPRAS', estado='BORRADOR')
                db.session.add(asiento_compra); db.session.flush()
                db.session.add(L(asiento_id=asiento_compra.id, cuenta_id=c_gasto.id,
                                 debe=total, haber=0, descripcion='Contabilidad mensual', orden=1,
                                 contraparte_id=CP_ASESORIAS_ECOX))
                db.session.add(L(asiento_id=asiento_compra.id, cuenta_id=c_prov.id,
                                 debe=0, haber=total, descripcion=doc.razon_social_contraparte[:60], orden=2,
                                 contraparte_id=CP_ASESORIAS_ECOX))
                db.session.add(AA(asiento_id=asiento_compra.id, accion='CREAR',
                                  descripcion=f'Carga manual desde doc SII folio {doc.folio}'))
                doc.asiento_id = asiento_compra.id
                doc.procesado = True

                # Asiento pago proveedor (banco)
                next_num += 1
                asiento_pago = A(empresa_id=EMP, fecha=m.fecha, numero=next_num,
                                 descripcion=f"Pago Asesorías Ecox - folio {doc.folio}",
                                 origen='BANCO', estado='BORRADOR')
                db.session.add(asiento_pago); db.session.flush()
                c_banco = Cuenta.query.filter_by(empresa_id=EMP, codigo='1.1.02').first()
                monto_pago = float(m.cargo or 0)
                db.session.add(L(asiento_id=asiento_pago.id, cuenta_id=c_prov.id,
                                 debe=monto_pago, haber=0, descripcion=f'Pago Asesorías Ecox folio {doc.folio}', orden=1,
                                 contraparte_id=CP_ASESORIAS_ECOX))
                db.session.add(L(asiento_id=asiento_pago.id, cuenta_id=c_banco.id,
                                 debe=0, haber=monto_pago, descripcion=(m.descripcion or '')[:80], orden=2))
                db.session.add(AA(asiento_id=asiento_pago.id, accion='CREAR',
                                  descripcion=f'Pago de doc SII folio {doc.folio}'))

                # Conciliación tipo SII enlazando doc + mov
                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'Factura+pago Asesorías Ecox folio {doc.folio}',
                                    tipo='SII', contraparte_id=CP_ASESORIAS_ECOX)
                db.session.add(conc); db.session.flush()
                doc.conciliacion_id = conc.id
                m.conciliacion_id = conc.id
                m.asiento_id = asiento_pago.id
                m.procesado = True
                sii_creados += 1
                print(f"  ✓ SII mov#{m.id} {m.fecha} ↔ doc folio {doc.folio} — compra A#{asiento_compra.numero} + pago A#{asiento_pago.numero}")
                continue

            descripcion_conc, codigo_cta, cp_id = spec

            # Crear asiento banco vs cuenta (siguiendo el patrón del motor)
            from models import Asiento as A, LineaAsiento as L, AsientoAudit as AA
            from sqlalchemy import func as sa_func

            next_num = (db.session.query(sa_func.coalesce(sa_func.max(A.numero), 0))
                        .filter(A.empresa_id == EMP).scalar() or 0) + 1

            c_contra = Cuenta.query.filter_by(empresa_id=EMP, codigo=codigo_cta).first()
            c_banco = Cuenta.query.filter_by(empresa_id=EMP, codigo='1.1.02').first()
            monto = float(m.cargo or m.abono or 0)

            asiento = A(empresa_id=EMP, fecha=m.fecha, numero=next_num,
                        descripcion=descripcion_conc[:120], origen='BANCO', estado='BORRADOR')
            db.session.add(asiento); db.session.flush()

            desc_l = (m.descripcion or '')[:80]
            if m.cargo and m.cargo > 0:
                # cargo: contra debe / banco haber
                db.session.add(L(asiento_id=asiento.id, cuenta_id=c_contra.id,
                                 contraparte_id=cp_id,
                                 debe=monto, haber=0, descripcion=desc_l, orden=1))
                db.session.add(L(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                 debe=0, haber=monto, descripcion=desc_l, orden=2))
            else:
                # abono: banco debe / contra haber
                db.session.add(L(asiento_id=asiento.id, cuenta_id=c_banco.id,
                                 debe=monto, haber=0, descripcion=desc_l, orden=1))
                db.session.add(L(asiento_id=asiento.id, cuenta_id=c_contra.id,
                                 contraparte_id=cp_id,
                                 debe=0, haber=monto, descripcion=desc_l, orden=2))
            db.session.add(AA(asiento_id=asiento.id, accion='CREAR',
                              descripcion='Conciliación manual cartola enero'))

            # Conciliación tipo MANUAL
            conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                descripcion=f'Manual: {descripcion_conc}'[:280],
                                tipo='MANUAL', contraparte_id=cp_id)
            db.session.add(conc); db.session.flush()
            m.conciliacion_id = conc.id
            m.asiento_id = asiento.id
            m.procesado = True
            manual_creados += 1
            print(f"  ✓ MANUAL mov#{m.id} {m.fecha} → {codigo_cta} A#{asiento.numero} — {descripcion_conc[:60]}")

        db.session.commit()
        print(f"\nResumen enero:")
        print(f"  MANUAL: {manual_creados}")
        print(f"  SII:    {sii_creados}")
        print(f"  Total mov procesados: {manual_creados + sii_creados}")


if __name__ == '__main__':
    main()
