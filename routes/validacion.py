from flask import Blueprint, render_template, request
from datetime import date
from models import db, Empresa, Asiento, LineaAsiento, DocumentoSII, MovimientoBanco, Cuenta, Conciliacion
from sqlalchemy import func, text

bp = Blueprint('validacion', __name__)


def _saldo_manual_get(eid, periodo):
    """Lee saldo manual persistido para (empresa, periodo YYYY-MM). None si no existe."""
    r = db.session.execute(
        text("SELECT saldo FROM saldos_cartola_manual WHERE empresa_id=:e AND periodo=:p"),
        {'e': eid, 'p': periodo}).fetchone()
    return r[0] if r else None


def _saldo_manual_set(eid, periodo, saldo):
    """Upsert saldo manual para (empresa, periodo)."""
    db.session.execute(
        text("""INSERT INTO saldos_cartola_manual (empresa_id, periodo, saldo, actualizado_en)
                VALUES (:e, :p, :s, CURRENT_TIMESTAMP)
                ON CONFLICT(empresa_id, periodo) DO UPDATE SET
                    saldo=excluded.saldo, actualizado_en=CURRENT_TIMESTAMP"""),
        {'e': eid, 'p': periodo, 's': saldo})
    db.session.commit()


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

    # Solo cuentas no-TC para el cuadre con cartola del banco corriente
    from engine.plan_cuentas_default import es_movimiento_tc
    movs_periodo = (MovimientoBanco.query
                    .filter_by(empresa_id=eid)
                    .filter(MovimientoBanco.fecha >= desde, MovimientoBanco.fecha <= hasta)
                    .all())
    movs_total_periodo   = len(movs_periodo)
    movs_procesados      = sum(1 for m in movs_periodo if m.procesado)
    movs_sin_procesar    = movs_total_periodo - movs_procesados

    # Saldo según cartola — prioridad:
    # 1) Override manual via querystring (recién enviado por el form) → guarda y usa
    # 2) Saldo manual persistido para (empresa, periodo)
    # 3) último mov con saldo informado dentro del mes (excluyendo TC).
    periodo_str = f"{ano:04d}-{mes:02d}"
    cartola_manual_raw = request.args.get('saldo_cartola', '').replace('.', '').replace(',', '.').strip()
    try:
        cartola_manual = float(cartola_manual_raw) if cartola_manual_raw else None
    except ValueError:
        cartola_manual = None

    if cartola_manual is not None:
        _saldo_manual_set(eid, periodo_str, cartola_manual)
    else:
        cartola_manual = _saldo_manual_get(eid, periodo_str)
        if cartola_manual is not None:
            cartola_manual_raw = f"{cartola_manual:,.0f}".replace(',', '.')

    saldo_banco_cartola = None
    cartola_fecha = None
    cartola_origen = None  # 'manual' | 'cartola' | None

    if cartola_manual is not None:
        saldo_banco_cartola = cartola_manual
        cartola_fecha = hasta
        cartola_origen = 'manual'
    else:
        movs_con_saldo = (MovimientoBanco.query
                          .filter_by(empresa_id=eid)
                          .filter(MovimientoBanco.fecha <= hasta,
                                  MovimientoBanco.saldo != None)
                          .order_by(MovimientoBanco.fecha.desc(),
                                    MovimientoBanco.id.desc())
                          .all())
        for m in movs_con_saldo:
            if es_movimiento_tc(m.banco):
                continue
            saldo_banco_cartola = m.saldo
            cartola_fecha = m.fecha
            cartola_origen = 'cartola'
            break

    # Cantidad de movs no-TC en el período (para distinguir "no hay cartola"
    # vs "cartola sin saldo informado")
    movs_no_tc_periodo = sum(1 for m in movs_periodo if not es_movimiento_tc(m.banco))

    if saldo_banco_cartola is not None:
        cuadre_banco_diff = round(saldo_banco_sistema - saldo_banco_cartola, 0)
        cuadre_banco_ok = abs(cuadre_banco_diff) < 1
    else:
        cuadre_banco_diff = None
        cuadre_banco_ok = None

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
        saldo_banco_cartola=saldo_banco_cartola,
        cartola_fecha=cartola_fecha,
        cartola_origen=cartola_origen,
        cartola_manual_raw=cartola_manual_raw,
        movs_no_tc_periodo=movs_no_tc_periodo,
        cuadre_banco_diff=cuadre_banco_diff,
        cuadre_banco_ok=cuadre_banco_ok,
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
