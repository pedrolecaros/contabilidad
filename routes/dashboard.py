from flask import Blueprint, render_template
from datetime import date
from dateutil.relativedelta import relativedelta
from models import db, Empresa, Asiento, DocumentoSII, MovimientoBanco, CuotaPrestamo, Liquidacion

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

    # Cuotas de préstamos vencidas (sin pagar)
    cuotas_vencidas = (CuotaPrestamo.query
                       .join(CuotaPrestamo.prestamo)
                       .filter(
                           CuotaPrestamo.pagada == False,
                           CuotaPrestamo.fecha_vencimiento < hoy,
                           db.text('prestamos.empresa_id = :eid').bindparams(eid=eid)
                       ).count())

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
                           cuotas_vencidas=cuotas_vencidas,
                           ultimo_periodo_rem=ultimo_periodo_rem,
                           asientos_mes=asientos_mes,
                           saldo_banco=saldo_banco,
                           recientes=recientes,
                           hoy=hoy)
