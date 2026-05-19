from flask import Blueprint, render_template, request
from datetime import date
from models import db, Empresa, Asiento, LineaAsiento, DocumentoSII, MovimientoBanco, Cuenta, Conciliacion
from sqlalchemy import func

bp = Blueprint('validacion', __name__)


@bp.route('/empresa/<int:eid>/validar')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()

    ano  = request.args.get('ano',  hoy.year,  type=int)
    mes  = request.args.get('mes',  hoy.month, type=int)
    periodo = request.args.get('periodo', '')
    if periodo:
        try:
            ano = int(periodo[:4])
            mes = int(periodo[5:7])
        except (ValueError, IndexError):
            pass
    ultimo_dia = _ultimo_dia(ano, mes)
    desde = date(ano, mes, 1)
    hasta = date(ano, mes, ultimo_dia)

    # ── Asientos no cuadrados en el período ─────────────────────────────────
    asientos_mes = (Asiento.query
                    .filter_by(empresa_id=eid)
                    .filter(Asiento.fecha >= desde, Asiento.fecha <= hasta)
                    .filter(Asiento.estado != 'ANULADO')
                    .all())

    no_cuadrados = [a for a in asientos_mes if not a.cuadrado]
    borradores   = [a for a in asientos_mes if a.estado == 'BORRADOR']

    # ── Pendientes en el período ─────────────────────────────────────────────
    docs_pendientes = (DocumentoSII.query
                       .filter_by(empresa_id=eid, procesado=False)
                       .filter(DocumentoSII.fecha >= desde, DocumentoSII.fecha <= hasta)
                       .count())
    movs_pendientes = (MovimientoBanco.query
                       .filter_by(empresa_id=eid, procesado=False)
                       .filter(MovimientoBanco.conciliacion_id == None)
                       .filter(MovimientoBanco.fecha >= desde, MovimientoBanco.fecha <= hasta)
                       .count())

    # ── Movimientos bancarios del período ────────────────────────────────────
    c_banco = Cuenta.query.filter_by(empresa_id=eid, codigo='1.1.02').first()
    saldo_banco_sistema = c_banco.saldo(hasta=hasta) if c_banco else 0.0

    movs_periodo = (MovimientoBanco.query
                    .filter_by(empresa_id=eid)
                    .filter(MovimientoBanco.fecha >= desde, MovimientoBanco.fecha <= hasta)
                    .all())
    movs_total_periodo   = len(movs_periodo)
    movs_procesados      = sum(1 for m in movs_periodo if m.procesado)
    movs_sin_procesar    = movs_total_periodo - movs_procesados

    # ── Totales del período ─────────────────────────────────────────────────
    ingresos = sum(
        c.saldo(desde=desde, hasta=hasta)
        for c in Cuenta.query.filter_by(empresa_id=eid, tipo='INGRESO', es_titulo=False, activa=True).all()
    )
    gastos = sum(
        c.saldo(desde=desde, hasta=hasta)
        for c in Cuenta.query.filter_by(empresa_id=eid, tipo='GASTO', es_titulo=False, activa=True).all()
    )

    # ── Sin respaldo: conciliaciones MANUAL y asientos manuales sin respaldo_url ─
    conc_sin_respaldo = (Conciliacion.query
                         .filter_by(empresa_id=eid)
                         .filter(Conciliacion.tipo != 'SII')
                         .filter(Conciliacion.respaldo_url == None)
                         .filter(Conciliacion.fecha >= desde, Conciliacion.fecha <= hasta)
                         .order_by(Conciliacion.fecha)
                         .all())
    asientos_sin_respaldo = (Asiento.query
                             .filter_by(empresa_id=eid, origen='MANUAL')
                             .filter(Asiento.estado != 'ANULADO')
                             .filter(Asiento.respaldo_url == None)
                             .filter(Asiento.fecha >= desde, Asiento.fecha <= hasta)
                             .order_by(Asiento.fecha)
                             .all())

    return render_template('validacion/index.html',
        empresa=empresa,
        ano=ano, mes=mes, desde=desde, hasta=hasta,
        asientos_mes=asientos_mes,
        no_cuadrados=no_cuadrados,
        borradores=borradores,
        docs_pendientes=docs_pendientes,
        movs_pendientes=movs_pendientes,
        saldo_banco_sistema=saldo_banco_sistema,
        movs_total_periodo=movs_total_periodo,
        movs_procesados=movs_procesados,
        movs_sin_procesar=movs_sin_procesar,
        ingresos=ingresos,
        gastos=gastos,
        resultado=ingresos - gastos,
        conc_sin_respaldo=conc_sin_respaldo,
        asientos_sin_respaldo=asientos_sin_respaldo,
    )


def _ultimo_dia(ano, mes):
    import calendar
    return calendar.monthrange(ano, mes)[1]
