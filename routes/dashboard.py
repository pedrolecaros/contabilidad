from flask import Blueprint, render_template
from datetime import date
from dateutil.relativedelta import relativedelta
from models import db, Empresa, Asiento, LineaAsiento, Cuenta, DocumentoSII, MovimientoBanco, Liquidacion, Prestamo

bp = Blueprint('dashboard', __name__)


@bp.route('/empresa/<int:eid>/dashboard')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()

    # Documentos SII sin procesar
    docs_pendientes = DocumentoSII.query.filter_by(empresa_id=eid, procesado=False).count()

    # Movimientos bancarios sin conciliar
    movs_sin_conc = MovimientoBanco.query.filter_by(empresa_id=eid, procesado=False).count()

    # Asientos en borrador
    asientos_borrador = Asiento.query.filter_by(empresa_id=eid, estado='BORRADOR').count()

    # Saldos CxC / CxP
    prestamos_empresa = Prestamo.query.filter_by(empresa_id=eid, activo=True).all()
    total_por_pagar  = sum(p.saldo_actual() for p in prestamos_empresa if p.tipo == 'PAGAR')
    total_por_cobrar = sum(p.saldo_actual() for p in prestamos_empresa if p.tipo == 'COBRAR')

    # Último período con liquidaciones emitidas
    ultima_liq = (db.session.query(Liquidacion.periodo)
                  .filter_by(empresa_id=eid, estado='EMITIDA')
                  .order_by(Liquidacion.periodo.desc())
                  .first())
    ultimo_periodo_rem = ultima_liq[0] if ultima_liq else None

    # Asientos del mes actual
    inicio_mes = date(hoy.year, hoy.month, 1)
    asientos_mes = Asiento.query.filter(
        Asiento.empresa_id == eid,
        Asiento.fecha >= inicio_mes,
        Asiento.estado != 'ANULADO',
    ).count()

    # Saldo banco: suma movimientos banco (abonos - cargos)
    from sqlalchemy import func
    saldo_banco = (db.session.query(
        func.coalesce(func.sum(MovimientoBanco.abono), 0) -
        func.coalesce(func.sum(MovimientoBanco.cargo), 0)
    ).filter_by(empresa_id=eid).scalar() or 0)

    # Cuentas de activo líquido con saldo acreedor (anómalo para cuentas DEUDORA)
    from engine.plan_cuentas_default import CODIGOS_LIQUIDEZ as _CODIGOS_BANCO
    cuentas_banco = (Cuenta.query
                     .filter_by(empresa_id=eid, es_titulo=False, activa=True)
                     .filter(Cuenta.codigo.in_(_CODIGOS_BANCO))
                     .all())
    _saldos_libro = dict(
        db.session.query(LineaAsiento.cuenta_id,
                         func.sum(LineaAsiento.debe) - func.sum(LineaAsiento.haber))
        .join(Asiento, Asiento.id == LineaAsiento.asiento_id)
        .filter(Asiento.empresa_id == eid, Asiento.estado == 'CONFIRMADO',
                LineaAsiento.cuenta_id.in_([c.id for c in cuentas_banco]))
        .group_by(LineaAsiento.cuenta_id)
        .all()
    ) if cuentas_banco else {}
    cuentas_saldo_acreedor = [
        {'nombre': c.nombre, 'codigo': c.codigo,
         'saldo': round(_saldos_libro.get(c.id, 0))}
        for c in cuentas_banco
        if _saldos_libro.get(c.id, 0) < -0.5  # DEBE - HABER < 0 → saldo acreedor
    ]

    # Actividad reciente: últimos 5 asientos
    recientes = (Asiento.query
                 .filter_by(empresa_id=eid)
                 .filter(Asiento.estado != 'ANULADO')
                 .order_by(Asiento.fecha.desc(), Asiento.numero.desc())
                 .limit(5).all())

    return render_template('dashboard.html',
                           empresa=empresa,
                           docs_pendientes=docs_pendientes,
                           movs_sin_conc=movs_sin_conc,
                           asientos_borrador=asientos_borrador,
                           total_por_pagar=total_por_pagar,
                           total_por_cobrar=total_por_cobrar,
                           ultimo_periodo_rem=ultimo_periodo_rem,
                           asientos_mes=asientos_mes,
                           saldo_banco=saldo_banco,
                           cuentas_saldo_acreedor=cuentas_saldo_acreedor,
                           recientes=recientes,
                           hoy=hoy)
