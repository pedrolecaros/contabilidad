"""Consolida pagos grandes de misma fecha en un solo asiento.

Casos:
- 10 movs Asesorías 04-24 (BORRADOR, $47.5M) → 1 asiento BORRADOR (mantiene conciliación SII doc 180)
- 10 movs Futrono 04-24 (CONFIRMADO, $50M) → 1 asiento CONFIRMADO + 1 conciliación MANUAL
- 15 movs Futrono 04-27 (CONFIRMADO, $75M) → 1 asiento CONFIRMADO + 1 conciliación MANUAL

Banco como 1ra línea + contraparte en 2.1.01/2.1.11.
"""
import sys, json
sys.path.insert(0, '/home/pedro/contabilidad')
from datetime import date, datetime, timedelta
from app import create_app
from models import (db, MovimientoBanco, Cuenta, Conciliacion,
                    Asiento, LineaAsiento, AsientoAudit, Papelera)
from sqlalchemy import func as sa_func

EMP = 5
CP_ASESORIAS_ECOX = 3
CP_FUTRONO = 6


def cuenta(cod):
    c = Cuenta.query.filter_by(empresa_id=EMP, codigo=cod).first()
    if not c: raise SystemExit(f'Cuenta {cod} no existe')
    return c


def next_num():
    n = db.session.query(sa_func.coalesce(sa_func.max(Asiento.numero), 0)).filter(Asiento.empresa_id == EMP).scalar() or 0
    return n + 1


def enviar_papelera(asiento, motivo=''):
    lineas = [{'cuenta_id':l.cuenta_id,'contraparte_id':l.contraparte_id,'debe':l.debe,'haber':l.haber,
               'descripcion':l.descripcion,'orden':l.orden} for l in asiento.lineas]
    datos = {'fecha':str(asiento.fecha),'numero':asiento.numero,'descripcion':asiento.descripcion,
             'respaldo_url':asiento.respaldo_url,'origen':asiento.origen,'estado':asiento.estado,'lineas':lineas}
    ahora = datetime.now()
    p = Papelera(empresa_id=EMP, tipo='ASIENTO', objeto_id=asiento.id,
                 descripcion=f'Asiento #{asiento.numero} – {asiento.descripcion} ({motivo})'[:200],
                 datos_json=json.dumps(datos, ensure_ascii=False, default=str),
                 deleted_at=ahora, expires_at=ahora + timedelta(days=180))
    db.session.add(p)


def consolidar(mov_ids, cuenta_contra_cod, cp_id, glosa, estado_destino, fecha, mantener_conc_id=None, motivo=''):
    """Consolida varios mov_banco a UN asiento.

    - Envía a papelera los asientos individuales
    - Crea 1 asiento consolidado (Banco primero) con la suma de los movs
    - Actualiza mov.asiento_id apuntando al consolidado
    - Si mantener_conc_id: deja todos los movs con esa conciliación
    - Si no: crea 1 nueva conciliación MANUAL y elimina las viejas
    """
    movs = [MovimientoBanco.query.get(mid) for mid in mov_ids]
    total = sum(float(m.cargo or 0) for m in movs)

    # Recolectar asientos antiguos a borrar (únicos)
    asientos_viejos_ids = set(m.asiento_id for m in movs if m.asiento_id)
    conc_viejas_ids = set(m.conciliacion_id for m in movs if m.conciliacion_id and m.conciliacion_id != mantener_conc_id)

    # Crear asiento consolidado
    c_banco = cuenta('1.1.02')
    c_contra = cuenta(cuenta_contra_cod)
    a = Asiento(empresa_id=EMP, fecha=fecha, numero=next_num(),
                descripcion=glosa[:120], origen='BANCO', estado=estado_destino)
    db.session.add(a); db.session.flush()
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_banco.id,
                                debe=0, haber=total, descripcion=f'Pago consolidado {len(movs)} transferencias',
                                orden=1))
    db.session.add(LineaAsiento(asiento_id=a.id, cuenta_id=c_contra.id, contraparte_id=cp_id,
                                debe=total, haber=0, descripcion=glosa[:80],
                                orden=2))
    db.session.add(AsientoAudit(asiento_id=a.id, accion='CREAR',
                                descripcion=f'Consolidación {len(movs)} pagos {fecha} — {motivo}'))

    # Crear/mantener conciliación
    if mantener_conc_id:
        conc_id = mantener_conc_id
    else:
        conc = Conciliacion(empresa_id=EMP, fecha=fecha,
                            descripcion=f'Manual: {glosa}'[:280],
                            tipo='MANUAL', contraparte_id=cp_id)
        db.session.add(conc); db.session.flush()
        conc_id = conc.id

    # Repuntar movs
    for m in movs:
        m.asiento_id = a.id
        m.conciliacion_id = conc_id
        m.procesado = True

    db.session.flush()

    # Eliminar asientos viejos
    for aid in asientos_viejos_ids:
        aold = Asiento.query.get(aid)
        if aold:
            enviar_papelera(aold, motivo=f'reemplazado por asiento consolidado #{a.numero}')
            AsientoAudit.query.filter_by(asiento_id=aid).delete()
            LineaAsiento.query.filter_by(asiento_id=aid).delete()
            db.session.delete(aold)

    # Eliminar conciliaciones viejas (si no mantener)
    for cid in conc_viejas_ids:
        cold = Conciliacion.query.get(cid)
        if cold:
            db.session.delete(cold)

    return a


def main():
    app = create_app()
    with app.app_context():
        # 1) Asesorías Ecox 04-24 (10 movs BORRADOR, $47.5M)
        movs_aes_apr24 = [455, 456, 458, 459, 460, 462, 465, 467, 471, 472]
        # Conciliación a mantener: la del doc 180 (cross-month)
        conc_div7 = Conciliacion.query.filter(
            Conciliacion.empresa_id == EMP,
            Conciliacion.descripcion.like('%dividendo 7%')).first()
        if not conc_div7:
            print("!! No se encontró conciliación dividendo 7"); return
        print(f"\nConciliación dividendo 7: #{conc_div7.id}")
        a1 = consolidar(movs_aes_apr24, '2.1.01', CP_ASESORIAS_ECOX,
                        'Pagos Asesorías Ecox 04-24 (10 transferencias $47.5M — Honorario dividendo 7)',
                        estado_destino='BORRADOR', fecha=date(2026, 4, 24),
                        mantener_conc_id=conc_div7.id, motivo='dividendo 7 consolidado abril')
        print(f"✓ Asesorías 04-24 → asiento A#{a1.numero} (consolidado $47.5M, 10 movs)")

        # 2) Futrono 04-24 (10 movs CONFIRMADO, $50M)
        movs_fut_apr24 = [453, 454, 457, 461, 463, 466, 468, 469, 470, 473]
        a2 = consolidar(movs_fut_apr24, '2.1.11', CP_FUTRONO,
                        'Abono préstamo Futrono SpA — 10 transferencias 04-24 ($50M)',
                        estado_destino='CONFIRMADO', fecha=date(2026, 4, 24),
                        motivo='Futrono consolidado 04-24')
        print(f"✓ Futrono 04-24 → asiento A#{a2.numero} (consolidado $50M, 10 movs)")

        # 3) Futrono 04-27 (15 movs CONFIRMADO, $75M)
        movs_fut_apr27 = [474, 475, 476, 477, 478, 479, 480, 481, 482, 483, 484, 485, 486, 487, 488]
        a3 = consolidar(movs_fut_apr27, '2.1.11', CP_FUTRONO,
                        'Abono préstamo Futrono SpA — 15 transferencias 04-27 ($75M)',
                        estado_destino='CONFIRMADO', fecha=date(2026, 4, 27),
                        motivo='Futrono consolidado 04-27')
        print(f"✓ Futrono 04-27 → asiento A#{a3.numero} (consolidado $75M, 15 movs)")

        db.session.commit()
        print("\nConsolidación completada.")


if __name__ == '__main__':
    main()
