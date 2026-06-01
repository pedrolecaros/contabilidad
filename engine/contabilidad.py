"""
Funciones de cálculo contable reutilizables.

Dependen de SQLAlchemy (models) pero no de Flask ni de routes.
"""
from models import db, Cuenta, LineaAsiento, Asiento
from sqlalchemy import func


def sumas_balance(eid, desde, hasta):
    """Devuelve {cuenta_id: (suma_debe, suma_haber)} para el período.

    Cuentas de balance (ACTIVO/PASIVO/PATRIMONIO): saldo acumulado histórico hasta `hasta`.
    Cuentas de resultado (INGRESO/GASTO): solo movimientos del período desde..hasta.
    """
    rows_periodo = (db.session.query(
                        LineaAsiento.cuenta_id,
                        func.sum(LineaAsiento.debe).label('sd'),
                        func.sum(LineaAsiento.haber).label('sh'),
                    )
                    .join(Asiento)
                    .filter(
                        Asiento.empresa_id == eid,
                        Asiento.estado == 'CONFIRMADO',
                        Asiento.fecha >= desde,
                        Asiento.fecha <= hasta,
                    )
                    .group_by(LineaAsiento.cuenta_id)
                    .all())
    sumas_periodo = {r.cuenta_id: (r.sd or 0, r.sh or 0) for r in rows_periodo}

    rows_ant = (db.session.query(
                    LineaAsiento.cuenta_id,
                    func.sum(LineaAsiento.debe).label('sd'),
                    func.sum(LineaAsiento.haber).label('sh'),
                )
                .join(Asiento)
                .filter(
                    Asiento.empresa_id == eid,
                    Asiento.estado == 'CONFIRMADO',
                    Asiento.fecha < desde,
                )
                .group_by(LineaAsiento.cuenta_id)
                .all())
    sumas_ant = {r.cuenta_id: (r.sd or 0, r.sh or 0) for r in rows_ant}

    cuentas = Cuenta.query.filter_by(empresa_id=eid, activa=True, es_titulo=False).all()
    sumas = {}
    for c in cuentas:
        sd_p, sh_p = sumas_periodo.get(c.id, (0.0, 0.0))
        if c.tipo in ('ACTIVO', 'PASIVO', 'PATRIMONIO'):
            sd_a, sh_a = sumas_ant.get(c.id, (0.0, 0.0))
            sumas[c.id] = (sd_p + sd_a, sh_p + sh_a)
        else:
            sumas[c.id] = (sd_p, sh_p)
    return sumas


def clasificar_cuenta_efe(cuenta):
    """Clasifica una cuenta en la categoría del Estado de Flujo de Efectivo.

    Retorna (categoria, descripcion) donde categoria es
    'OPERACIONAL', 'INVERSION' o 'FINANCIAMIENTO'.
    """
    c = cuenta.codigo
    t = cuenta.tipo
    if t == 'INGRESO':
        return 'OPERACIONAL', 'Cobros operacionales'
    if t == 'GASTO':
        return 'OPERACIONAL', 'Pagos operacionales'
    if c.startswith('1.1.03') or c.startswith('1.1.04'):
        return 'OPERACIONAL', 'Cobros / Clientes'
    if c.startswith('2.1.01'):
        return 'OPERACIONAL', 'Pagos a proveedores'
    if c.startswith('2.1.02') or c.startswith('2.1.03'):
        return 'OPERACIONAL', 'Pagos de remuneraciones'
    if any(c.startswith(f'2.1.0{d}') for d in [4, 5, 6, 7, 8]):
        return 'OPERACIONAL', 'Pagos de impuestos'
    if c.startswith('2.1.'):
        return 'OPERACIONAL', 'Otros pasivos circulantes'
    if c.startswith('1.1.'):
        return 'OPERACIONAL', 'Otros activos circulantes'
    if c.startswith('1.2.') or c.startswith('1.3.'):
        return 'INVERSION', cuenta.nombre
    if c.startswith('2.2.') or c.startswith('2.3.'):
        return 'FINANCIAMIENTO', 'Préstamos y deudas LP'
    if c.startswith('3.'):
        return 'FINANCIAMIENTO', 'Capital y retiros'
    return 'OPERACIONAL', 'Otros operacionales'
