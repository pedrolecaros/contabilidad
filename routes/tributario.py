from datetime import date
from flask import Blueprint, render_template, request
from models import db, Empresa, Asiento, LineaAsiento, Cuenta
from sqlalchemy import func

bp = Blueprint('tributario', __name__)


# ── Renta Líquida Imponible ────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/tributario/rli')
def rli(eid):
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    anio = int(request.args.get('anio', hoy.year))

    desde = date(anio, 1, 1)
    hasta = date(anio, 12, 31)

    # Totales por tipo de cuenta usando asientos CONFIRMADOS
    rows = (db.session.query(
            Cuenta.tipo,
            func.sum(LineaAsiento.debe).label('td'),
            func.sum(LineaAsiento.haber).label('th'),
        )
        .join(LineaAsiento, LineaAsiento.cuenta_id == Cuenta.id)
        .join(Asiento, Asiento.id == LineaAsiento.asiento_id)
        .filter(
            Cuenta.empresa_id == eid,
            Asiento.empresa_id == eid,
            Asiento.estado == 'CONFIRMADO',
            Asiento.fecha >= desde,
            Asiento.fecha <= hasta,
        )
        .group_by(Cuenta.tipo)
        .all())

    saldos = {}
    for r in rows:
        debe = float(r.td or 0)
        haber = float(r.th or 0)
        if r.tipo == 'INGRESO':
            saldos['INGRESO'] = haber - debe   # ingresos: naturaleza acreedora
        elif r.tipo == 'GASTO':
            saldos['GASTO'] = debe - haber     # gastos: naturaleza deudora

    ingresos = saldos.get('INGRESO', 0)
    gastos = saldos.get('GASTO', 0)
    resultado = ingresos - gastos

    # RLI por régimen
    if empresa.regimen == 'PYME':
        # Régimen PYME: base = ingresos percibidos - egresos pagados
        # Usamos los saldos contables como aproximación
        rli_monto = resultado
        rli_metodo = 'PYME simplificado (ingresos – egresos)'
    else:
        # Régimen general: resultado contable como punto de partida
        rli_monto = resultado
        rli_metodo = 'Régimen general (resultado contable, requiere ajustes tributarios)'

    # Detalle por subcuenta de ingresos y gastos
    detalle_rows = (db.session.query(
            Cuenta.codigo,
            Cuenta.nombre,
            Cuenta.tipo,
            func.sum(LineaAsiento.debe).label('td'),
            func.sum(LineaAsiento.haber).label('th'),
        )
        .join(LineaAsiento, LineaAsiento.cuenta_id == Cuenta.id)
        .join(Asiento, Asiento.id == LineaAsiento.asiento_id)
        .filter(
            Cuenta.empresa_id == eid,
            Cuenta.tipo.in_(['INGRESO', 'GASTO']),
            Cuenta.es_titulo == False,
            Asiento.empresa_id == eid,
            Asiento.estado == 'CONFIRMADO',
            Asiento.fecha >= desde,
            Asiento.fecha <= hasta,
        )
        .group_by(Cuenta.id)
        .order_by(Cuenta.codigo)
        .all())

    detalle_ingresos = []
    detalle_gastos = []
    for r in detalle_rows:
        debe = float(r.td or 0)
        haber = float(r.th or 0)
        if r.tipo == 'INGRESO':
            detalle_ingresos.append({'codigo': r.codigo, 'nombre': r.nombre, 'monto': haber - debe})
        elif r.tipo == 'GASTO':
            detalle_gastos.append({'codigo': r.codigo, 'nombre': r.nombre, 'monto': debe - haber})

    return render_template('tributario/rli.html',
        empresa=empresa,
        anio=anio,
        ingresos=ingresos,
        gastos=gastos,
        resultado=resultado,
        rli_monto=rli_monto,
        rli_metodo=rli_metodo,
        detalle_ingresos=detalle_ingresos,
        detalle_gastos=detalle_gastos,
    )


# ── Capital Propio Tributario ──────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/tributario/cpt')
def cpt(eid):
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    anio = int(request.args.get('anio', hoy.year - 1))  # default: año tributario anterior
    hasta = date(anio, 12, 31)

    rows = (db.session.query(
            Cuenta.tipo,
            Cuenta.naturaleza,
            func.sum(LineaAsiento.debe).label('td'),
            func.sum(LineaAsiento.haber).label('th'),
        )
        .join(LineaAsiento, LineaAsiento.cuenta_id == Cuenta.id)
        .join(Asiento, Asiento.id == LineaAsiento.asiento_id)
        .filter(
            Cuenta.empresa_id == eid,
            Asiento.empresa_id == eid,
            Asiento.estado == 'CONFIRMADO',
            Asiento.fecha <= hasta,
        )
        .group_by(Cuenta.tipo, Cuenta.naturaleza)
        .all())

    totales = {}
    for r in rows:
        debe = float(r.td or 0)
        haber = float(r.th or 0)
        saldo = (debe - haber) if r.naturaleza == 'DEUDORA' else (haber - debe)
        totales[r.tipo] = totales.get(r.tipo, 0) + saldo

    activos = totales.get('ACTIVO', 0)
    pasivos = totales.get('PASIVO', 0)
    patrimonio = totales.get('PATRIMONIO', 0)
    resultado_acum = totales.get('INGRESO', 0) - totales.get('GASTO', 0)

    # CPT = Activos - Pasivos (activo neto tributario)
    cpt_monto = activos - pasivos

    return render_template('tributario/cpt.html',
        empresa=empresa,
        anio=anio,
        activos=activos,
        pasivos=pasivos,
        patrimonio=patrimonio,
        resultado_acum=resultado_acum,
        cpt_monto=cpt_monto,
    )


# ── F22 (stub) ─────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/tributario/f22')
def f22(eid):
    empresa = Empresa.query.get_or_404(eid)
    return render_template('tributario/f22.html', empresa=empresa)


# ── Registro de Rentas Empresariales (stub) ────────────────────────────────────

@bp.route('/empresa/<int:eid>/tributario/rre')
def rre(eid):
    empresa = Empresa.query.get_or_404(eid)
    return render_template('tributario/rre.html', empresa=empresa)
