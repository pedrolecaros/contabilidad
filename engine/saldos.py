from sqlalchemy import func
from models import db, Asiento, LineaAsiento, Cuenta, Contraparte


def saldo_por_contraparte(empresa_id, cuenta_codigo, desde=None, hasta=None):
    """Saldo agregado por contraparte para una cuenta del plan, opcionalmente
    acotado entre fechas `desde` y `hasta` (inclusive).

    Returns (nombre_cuenta, saldo_total, [{'cp': Contraparte, 'saldo': float}, ...])
    o (None, None, []) si la cuenta no existe.
    """
    c = Cuenta.query.filter_by(empresa_id=empresa_id, codigo=cuenta_codigo).first()
    if not c:
        return None, None, []

    q = (db.session.query(
                LineaAsiento.contraparte_id,
                func.sum(LineaAsiento.debe).label('debe'),
                func.sum(LineaAsiento.haber).label('haber'),
            )
            .join(Asiento)
            .filter(
                Asiento.empresa_id == empresa_id,
                Asiento.estado == 'CONFIRMADO',
                LineaAsiento.cuenta_id == c.id,
                LineaAsiento.contraparte_id.isnot(None),
            ))
    if desde:
        q = q.filter(Asiento.fecha >= desde)
    if hasta:
        q = q.filter(Asiento.fecha <= hasta)
    rows = q.group_by(LineaAsiento.contraparte_id).all()

    filas = []
    for r in rows:
        cp = Contraparte.query.get(r.contraparte_id)
        if not cp:
            continue
        saldo = round((r.haber - r.debe) if c.naturaleza == 'ACREEDORA' else (r.debe - r.haber), 2)
        if abs(saldo) >= 1:
            filas.append({'cp': cp, 'saldo': saldo})
    filas.sort(key=lambda x: abs(x['saldo']), reverse=True)
    return c.nombre, round(c.saldo(desde=desde, hasta=hasta)), filas
