"""Procesa cartola mayo 2026 Los Robles SpA.

Especiales:
- Revertir mov 464 (04-24 Carlos Ocampo CBR F-7) de wash 2.1.13 → flujo gross con doc 188
- mov 504 (05-05 Walker $165.500) → 4.2.03 Otros Ingresos (reembolso CBR F-7)
- doc 180 Asesorías Ecox $52.5M factura exenta tipo 34 → concilia con 10 movs abril + 1 mayo
- Aplicar reglas: Banco/Caja como 1ra línea + contraparte_id en 2.1.01 Proveedores
"""
import sys, json
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date, datetime, timedelta
from app import create_app
from models import (db, MovimientoBanco, DocumentoSII, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, DeclaracionF29, Contraparte, Papelera)
from sqlalchemy import func as sa_func

EMP = 5
CP_ASESORIAS_ECOX = 3
CP_FELIPE_HIRIART = 5
CP_FUTRONO = 6
CP_FELIPE_CHAVEZ = 46


def cuenta(cod):
    c = Cuenta.query.filter_by(empresa_id=EMP, codigo=cod).first()
    if not c: raise SystemExit(f'Cuenta {cod} no existe')
    return c


def next_num():
    n = db.session.query(sa_func.coalesce(sa_func.max(Asiento.numero), 0)).filter(Asiento.empresa_id == EMP).scalar() or 0
    return n + 1


def get_or_create_cp(rut, razon_social, tipo):
    cp = Contraparte.query.filter_by(rut=rut).first()
    if cp:
        return cp.id
    cp = Contraparte.query.filter(Contraparte.razon_social == razon_social).first()
    if cp:
        return cp.id
    cp = Contraparte(empresa_id=EMP, rut=rut, razon_social=razon_social, tipo=tipo, activo=True)
    db.session.add(cp); db.session.flush()
    print(f"  + Creada contraparte: {razon_social} ({rut}) id={cp.id}")
    return cp.id


# Helpers banco-primero + contraparte en 2.1.01
def hacer_manual_simple(mov, cod_contra, glosa_asiento, cp_id=None):
    """Asiento simple banco-vs-cuenta. Banco siempre primera línea (orden=1)."""
    c_banco = cuenta('1.1.02')
    c = cuenta(cod_contra)
    monto = float(mov.cargo or mov.abono or 0)
    a = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                descripcion=glosa_asiento[:120], origen='BANCO', estado='BORRADOR')
    db.session.add(a); db.session.flush()
    gl = (mov.descripcion or '')[:80]
    # Si la cuenta contra es Proveedores/Clientes, asegurar contraparte
    if c.codigo in ('2.1.01', '1.1.03') and not cp_id:
        cp_id = None  # NO debería pasar — flagging
    if mov.cargo and mov.cargo > 0:
        # Salida: Banco haber (orden 1), contra debe (orden 2)
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                    debe=0, haber=monto, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                    debe=monto, haber=0, descripcion=gl, orden=2))
    else:
        # Entrada: Banco debe (orden 1), contra haber (orden 2)
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                    debe=monto, haber=0, descripcion=gl, orden=1))
        db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c.id, contraparte_id=cp_id,
                                    debe=0, haber=monto, descripcion=gl, orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                descripcion='Conciliación manual cartola mayo'))
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
    cp_macal = get_or_create_cp('79546430-1', 'SOCIEDAD COMERCIAL Y DE SERVICIOS MACAL LTDA', 'PROVEEDOR')
    total_venta = float(mov.abono) + comision_total
    # Venta compuesto: Banco primero
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
                                debe=comision_neto, haber=0, descripcion='Comisión Macal (neto)', orden=1, contraparte_id=cp_macal))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_iva.id,
                                debe=iva, haber=0, descripcion='IVA CF Macal', orden=2))
    db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_prov.id,
                                debe=0, haber=comision_total, descripcion='Macal Ltda', orden=3, contraparte_id=cp_macal))
    db.session.add(AsientoAudit(asiento_id=a_compra.id, accion='CREAR',
                                descripcion=f'Compra factura Macal folio {doc.folio}'))
    doc.asiento_id = a_compra.id
    doc.procesado = True

    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago Macal folio {doc.folio} vía Caja",
                     origen='MANUAL', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    # Caja primero (es la "salida" de pago aunque no sea Banco)
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_caja.id,
                                debe=0, haber=comision_total, descripcion='Pago vía Caja', orden=1))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=comision_total, haber=0, descripcion=f'Pago Macal folio {doc.folio}', orden=2, contraparte_id=cp_macal))
    db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR', descripcion='Pago Macal vía Caja'))

    conc = Conciliacion(empresa_id=EMP, fecha=mov.fecha,
                        descripcion=f'Venta Macal + factura comisión folio {doc.folio}',
                        tipo='SII', contraparte_id=cp_macal)
    db.session.add(conc); db.session.flush()
    doc.conciliacion_id = conc.id
    mov.conciliacion_id = conc.id
    mov.asiento_id = a_venta.id
    mov.procesado = True
    return a_venta, a_compra, a_pago


def hacer_compra_simple(mov, doc, codigo_gasto, cp_id, con_iva=True, glosa_extra=''):
    """Compra factura + pago. Banco-primero en pago. Contraparte en 2.1.01."""
    c_banco = cuenta('1.1.02')
    c_gasto = cuenta(codigo_gasto)
    c_iva = cuenta('1.1.05')
    c_prov = cuenta('2.1.01')
    total = float(doc.total)
    iva = float(doc.iva) if con_iva else 0.0
    neto = total - iva
    rs = (doc.razon_social_contraparte or '')[:60]
    # Compra
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

    # Pago: Banco primero
    a_pago = Asiento(empresa_id=EMP, fecha=mov.fecha, numero=next_num(),
                     descripcion=f"Pago {rs} - folio {doc.folio}",
                     origen='BANCO', estado='BORRADOR')
    db.session.add(a_pago); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                debe=0, haber=total, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=total, haber=0, descripcion=f'Pago folio {doc.folio}', orden=2, contraparte_id=cp_id))
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
    """Honorario + pago. Banco-primero en pago. Contraparte en 2.1.01."""
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
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                debe=0, haber=liquido, descripcion=(mov.descripcion or '')[:80], orden=1))
    db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                debe=liquido, haber=0, descripcion=f'Pago {rs} bol {doc.folio}', orden=2, contraparte_id=cp_id))
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


def revertir_f7_wash():
    """Borra asiento 633 (wash 2.1.13) y conciliación 495 para mov 464."""
    asiento = Asiento.query.get(633)
    if not asiento:
        print("  ! Asiento 633 (wash F-7) no existe, saltando revert")
        return False
    # Enviar a papelera
    lineas = [{'cuenta_id':l.cuenta_id,'contraparte_id':l.contraparte_id,'debe':l.debe,'haber':l.haber,
               'descripcion':l.descripcion,'orden':l.orden} for l in asiento.lineas]
    datos = {'fecha':str(asiento.fecha),'numero':asiento.numero,'descripcion':asiento.descripcion,
             'respaldo_url':asiento.respaldo_url,'origen':asiento.origen,'estado':asiento.estado,'lineas':lineas}
    ahora = datetime.now()
    p = Papelera(empresa_id=EMP, tipo='ASIENTO', objeto_id=asiento.id,
                 descripcion=f'Asiento #{asiento.numero} – {asiento.descripcion} (revert wash F-7)'[:200],
                 datos_json=json.dumps(datos, ensure_ascii=False, default=str),
                 deleted_at=ahora, expires_at=ahora + timedelta(days=180))
    db.session.add(p)
    # Limpiar mov 464
    mov = MovimientoBanco.query.get(464)
    conc_id = mov.conciliacion_id
    mov.asiento_id = None
    mov.conciliacion_id = None
    mov.procesado = False
    # Borrar conciliación
    conc = Conciliacion.query.get(conc_id)
    if conc:
        db.session.delete(conc)
    # Borrar lineas y asiento
    AsientoAudit.query.filter_by(asiento_id=asiento.id).delete()
    LineaAsiento.query.filter_by(asiento_id=asiento.id).delete()
    db.session.delete(asiento)
    print(f"  ✓ Asiento 633 (wash F-7) enviado a papelera; mov 464 reseteado para reprocesar con doc 188")
    return True


def main():
    app = create_app()
    with app.app_context():
        # 0. Crear contrapartes faltantes
        cp_carlos = get_or_create_cp('13319718-4', 'CARLOS ANDRES OCAMPO BUSTOS', 'HONORARIOS')
        cp_rieutord = get_or_create_cp('10755410-6', 'ANDRES FELIPE RIEUTORD ALVARADO', 'HONORARIOS')
        cp_troncoso = get_or_create_cp('', 'JORGE IGNACIO TRONCOSO VIDAL', 'HONORARIOS')
        cp_walker = get_or_create_cp('', 'YOLANDA ANDREA WALKER SILVA', 'CLIENTE')

        # 1. Revertir wash F-7 (mov 464)
        print("\n=== Revertir wash F-7 ===")
        revertir_f7_wash()

        # 2. Procesar SII cross-month: doc 188 ↔ mov 464
        print("\n=== Conciliar doc 188 ↔ mov 464 (cross-month) ===")
        mov464 = MovimientoBanco.query.get(464)
        doc188 = DocumentoSII.query.get(188)
        hacer_honorario(mov464, doc188, '5.2.02', cp_carlos, con_retencion=False)
        print(f"  ✓ doc 188 Ocampo F-7 conciliado con mov 464 abril")

        # 3. Procesar doc 180 Asesorías Ecox $52.5M (cross-month: 10 abril movs + 1 mayo mov)
        print("\n=== Procesar doc 180 Asesorías Ecox $52.5M (Honorario dividendo 7) ===")
        doc180 = DocumentoSII.query.get(180)
        movs_div7_abril = [455, 456, 458, 459, 460, 462, 465, 467, 471, 472]
        mov_div7_mayo = [502]
        movs_div7 = movs_div7_abril + mov_div7_mayo
        c_hon = cuenta('5.2.02')
        c_prov = cuenta('2.1.01')
        c_banco = cuenta('1.1.02')
        total = float(doc180.total)
        # Compra factura
        a_compra = Asiento(empresa_id=EMP, fecha=doc180.fecha, numero=next_num(),
                           descripcion=f"Factura compra exenta 34 N°{doc180.folio} - Asesorías Ecox (Honorario dividendo 7)",
                           origen='LIBRO_COMPRAS', estado='BORRADOR')
        db.session.add(a_compra); db.session.flush()
        db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_hon.id,
                                    debe=total, haber=0, descripcion='Honorario dividendo 7 — Asesorías Ecox',
                                    orden=1, contraparte_id=CP_ASESORIAS_ECOX))
        db.session.add(LineaAsiento(asiento_id=a_compra.id, cuenta_id=c_prov.id,
                                    debe=0, haber=total, descripcion='Asesorías Ecox Ltda',
                                    orden=2, contraparte_id=CP_ASESORIAS_ECOX))
        db.session.add(AsientoAudit(asiento_id=a_compra.id, accion='CREAR',
                                    descripcion=f'Compra exenta folio {doc180.folio} (Honorario dividendo 7)'))
        doc180.asiento_id = a_compra.id
        doc180.procesado = True

        conc_div7 = Conciliacion(empresa_id=EMP, fecha=date(2026, 5, 5),  # max(fechas)
                                 descripcion=f'Honorario dividendo 7 — fact {doc180.folio} + 11 pagos',
                                 tipo='SII', contraparte_id=CP_ASESORIAS_ECOX)
        db.session.add(conc_div7); db.session.flush()
        doc180.conciliacion_id = conc_div7.id

        # Pago asientos (uno por mov)
        for mid in movs_div7:
            m = MovimientoBanco.query.get(mid)
            monto = float(m.cargo)
            a_pago = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                             descripcion=f"Pago Asesorías Ecox folio {doc180.folio} (parte $52.5M dividendo 7)",
                             origen='BANCO', estado='BORRADOR')
            db.session.add(a_pago); db.session.flush()
            db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_banco.id,
                                        debe=0, haber=monto, descripcion=(m.descripcion or '')[:80], orden=1))
            db.session.add(LineaAsiento(asiento_id=a_pago.id, cuenta_id=c_prov.id,
                                        debe=monto, haber=0, descripcion=f'Pago Asesorías Ecox',
                                        orden=2, contraparte_id=CP_ASESORIAS_ECOX))
            db.session.add(AsientoAudit(asiento_id=a_pago.id, accion='CREAR',
                                        descripcion=f'Pago doc {doc180.folio}'))
            m.conciliacion_id = conc_div7.id
            m.asiento_id = a_pago.id
            m.procesado = True
        print(f"  ✓ doc 180 ${total/1e6:.1f}M conciliado con {len(movs_div7)} movs (10 abril + 1 mayo)")

        # 4. Procesar resto mayo
        print("\n=== Procesar mov mayo (resto) ===")
        cp_nicolas_piquer = get_or_create_cp('', 'NICOLAS PIQUER FRANCO', 'CLIENTE')

        movs = (MovimientoBanco.query
                .filter_by(empresa_id=EMP)
                .filter(MovimientoBanco.fecha >= date(2026, 5, 1),
                        MovimientoBanco.fecha < date(2026, 6, 1))
                .order_by(MovimientoBanco.fecha, MovimientoBanco.id).all())

        plan = {
            494: ('compra', 181, '5.2.11', CP_ASESORIAS_ECOX, False, 'Contabilidad mensual'),
            495: ('manual', '5.2.01', 'Sueldo Hector Varela mayo', None),
            496: ('manual', '4.1.02', 'Cuota Isaias Yanez (A-1)', None),
            497: ('manual', '4.1.02', 'Cuotas Yasna Vidal (E-9)', None),
            498: ('manual', '4.1.02', 'Reserva Nicolas Piquer Franco', None),
            499: ('manual', '4.1.02', 'Cuota Matias Donoso (E-6)', None),
            500: ('manual', '4.1.02', 'Cuota Hector Varela Nancho (I-2)', None),
            501: ('manual', '4.1.02', 'Cuota Javier Gomez (M-6)', None),
            502: 'YA_HECHO_DIV7',
            503: ('manual', '5.2.01', 'Previred mayo', None),
            504: ('manual', '4.2.03', 'Reembolso CBR F-7 desde Walker (cierre F-7)', None),
            505: ('manual', '4.1.02', 'Cuota Eduardo Araya (B-6)', None),
            506: ('manual', '4.1.02', 'Cuota Viviana Molina (I-1)', None),
            507: ('manual', '4.1.02', 'Cuota Jose Urra (A-5)', None),
            508: ('honorario', 183, '5.2.02', CP_FELIPE_CHAVEZ, True),
            509: ('honorario', 185, '5.2.02', cp_rieutord, False),
            510: ('manual', '1.1.09', 'Rescate FFMM 96571220-8', None),
            511: ('manual', '4.1.02', 'Cuota Nicole Isamitt (H-2)', None),
            512: ('honorario', 184, '5.2.02', cp_rieutord, False),
            513: ('manual', '5.2.17', 'Reembolso gastos Hector Varela', None),
            514: ('honorario', 186, '5.2.02', cp_rieutord, False),
            515: 'F29_ABRIL',
            516: ('manual', '5.2.16', 'Pago Tesorería', None),
            517: ('manual', '4.1.02', 'Reserva Cristian Leng', None),
            518: ('manual', '4.1.02', 'Cuota Nicolas Piquer Franco', None),
            519: ('manual', '4.1.02', 'Cuota Javier Gomez (M-6)', None),
            520: ('manual', '4.1.02', 'Cuota Nicolas Piquer Franco', None),
            521: 'DEV_IMPUESTO',
            522: ('manual', '4.1.02', 'Cuota Nicolas Piquer Franco', None),
            523: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
            524: ('manual', '4.1.02', 'Cuota Nicolas Piquer Franco', None),
            525: ('manual', '4.1.02', 'Cuota Nicolas Piquer Franco', None),
            526: ('manual', '4.1.02', 'Cuota Nicolas Piquer Franco', None),
            527: ('honorario', 187, '5.2.02', cp_troncoso, True),
            528: ('manual', '4.1.02', 'Cuota Jaime Contreras (E-7)', None),
            529: ('macal', 179, 1487500.0, 1250000.0, 237500.0, 'Venta lote vía Macal — fact folio 153886'),
            530: ('manual', '1.1.09', 'Aporte FFMM 96571220-8', None),
        }

        manual = sii = 0
        for m in movs:
            spec = plan[m.id]
            if spec == 'YA_HECHO_DIV7':
                continue

            if spec == 'F29_ABRIL':
                f29 = DeclaracionF29.query.filter_by(empresa_id=EMP, periodo='2026-04').first()
                ppm = float(f29.codigo_62)
                ret_hon = float(f29.codigo_151)
                total = float(f29.codigo_91)
                c_ppm = cuenta('1.1.06')
                c_ret = cuenta('2.1.04')
                a = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                            descripcion=f"Pago F29 abril 2026 folio {f29.folio} (PPM + Ret Hon)",
                            origen='BANCO', estado='BORRADOR')
                db.session.add(a); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                            debe=0, haber=total, descripcion=(m.descripcion or '')[:80], orden=1))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id,
                                            debe=ppm, haber=0, descripcion='PPM abr cód 62', orden=2))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ret.id,
                                            debe=ret_hon, haber=0,
                                            descripcion='Retención Hon abr cód 151 (salda Troncoso+Felipe Chávez)', orden=3))
                db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                            descripcion=f'Pago F29 abril folio {f29.folio}'))
                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion=f'F29 abril 2026 folio {f29.folio} (PPM ${ppm:.0f} + Ret Hon ${ret_hon:.0f})',
                                    tipo='MANUAL', contraparte_id=None)
                db.session.add(conc); db.session.flush()
                m.conciliacion_id = conc.id
                m.asiento_id = a.id
                m.procesado = True
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → F29 abril A#{a.numero}: PPM={ppm}, RetHon={ret_hon}")
                continue

            if spec == 'DEV_IMPUESTO':
                # Devolución PPM 2025 + reajuste IPC
                ppm_2025 = 444447.0  # del apertura
                reajuste = float(m.abono) - ppm_2025
                c_ppm = cuenta('1.1.06')
                c_ingreso = cuenta('4.2.03')
                a = Asiento(empresa_id=EMP, fecha=m.fecha, numero=next_num(),
                            descripcion=f"Devolución PPM 2025 (Op Renta) + reajuste IPC",
                            origen='BANCO', estado='BORRADOR')
                db.session.add(a); db.session.flush()
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                            debe=m.abono, haber=0, descripcion=(m.descripcion or '')[:80], orden=1))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ppm.id,
                                            debe=0, haber=ppm_2025, descripcion='Devolución PPM 2025', orden=2))
                db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_ingreso.id,
                                            debe=0, haber=reajuste, descripcion='Reajuste IPC devolución', orden=3))
                db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                            descripcion='Devolución PPM 2025 Op Renta'))
                conc = Conciliacion(empresa_id=EMP, fecha=m.fecha,
                                    descripcion='Devolución PPM 2025 + reajuste IPC',
                                    tipo='MANUAL', contraparte_id=None)
                db.session.add(conc); db.session.flush()
                m.conciliacion_id = conc.id
                m.asiento_id = a.id
                m.procesado = True
                manual += 1
                print(f"  ✓ MANUAL mov#{m.id} → Devolución PPM 2025 A#{a.numero}: PPM={ppm_2025}, reajuste={reajuste:.0f}")
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
                a_compra, a_pago = hacer_compra_simple(m, doc, cod_gasto, cp, con_iva, glosa_extra)
                sii += 1
                print(f"  ✓ SII mov#{m.id} ↔ compra doc{doc_id} folio {doc.folio} — A#{a_compra.numero}+A#{a_pago.numero}")
            elif tipo == 'macal':
                _, doc_id, com_total, com_neto, iva, glosa = spec
                doc = DocumentoSII.query.get(doc_id)
                a_v, a_c, a_p = hacer_macal(m, doc, com_total, com_neto, iva, glosa)
                sii += 1
                print(f"  ✓ MACAL mov#{m.id} ↔ doc{doc_id} folio {doc.folio} — Venta A#{a_v.numero} / Compra A#{a_c.numero} / PagoCaja A#{a_p.numero}")

        db.session.commit()
        print(f"\nResumen mayo:")
        print(f"  SII:        {sii} (+ doc 180 cross-month + doc 188 cross-month)")
        print(f"  MANUAL:     {manual}")


if __name__ == '__main__':
    main()
