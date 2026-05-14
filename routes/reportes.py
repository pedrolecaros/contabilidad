from flask import Blueprint, render_template, request
from datetime import date
import calendar
from models import db, Empresa, Cuenta, LineaAsiento, Asiento, DocumentoSII
from sqlalchemy import func, case

bp = Blueprint('reportes', __name__)


def _rango_fechas():
    hoy = date.today()
    d_def = date(hoy.year, 1, 1)
    h_def = date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])
    desde_str = request.args.get('desde', d_def.isoformat())
    hasta_str = request.args.get('hasta', h_def.isoformat())
    try:
        desde = date.fromisoformat(desde_str)
        hasta = date.fromisoformat(hasta_str)
    except ValueError:
        desde, hasta = d_def, h_def
    return desde, hasta


@bp.route('/empresa/<int:eid>/reportes/mayor')
def mayor(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    cuenta_id = request.args.get('cuenta_id', type=int)

    cuentas_detalle = (Cuenta.query
                       .filter_by(empresa_id=eid, es_titulo=False, activa=True)
                       .order_by(Cuenta.codigo).all())

    movimientos = []
    cuenta_sel = None
    saldo_inicial = 0.0
    if cuenta_id:
        cuenta_sel = Cuenta.query.get(cuenta_id)
        movimientos = (LineaAsiento.query
                       .join(Asiento)
                       .filter(
                           LineaAsiento.cuenta_id == cuenta_id,
                           Asiento.estado == 'CONFIRMADO',
                           Asiento.fecha >= desde,
                           Asiento.fecha <= hasta,
                       )
                       .order_by(Asiento.fecha, Asiento.numero)
                       .all())

        # Saldo acumulado de todos los asientos ANTERIORES al período
        si = (db.session.query(
                  func.sum(LineaAsiento.debe).label('debe'),
                  func.sum(LineaAsiento.haber).label('haber'),
              )
              .join(Asiento)
              .filter(
                  LineaAsiento.cuenta_id == cuenta_id,
                  Asiento.estado == 'CONFIRMADO',
                  Asiento.fecha < desde,
              )
              .first())
        si_debe  = si.debe  or 0.0
        si_haber = si.haber or 0.0
        if cuenta_sel and cuenta_sel.naturaleza == 'DEUDORA':
            saldo_inicial = si_debe - si_haber
        else:
            saldo_inicial = si_haber - si_debe

    return render_template('reportes/mayor.html', empresa=empresa,
                           cuentas=cuentas_detalle, cuenta_sel=cuenta_sel,
                           movimientos=movimientos, desde=desde, hasta=hasta,
                           saldo_inicial=saldo_inicial)


def _sumas_balance(eid, desde, hasta):
    """
    Cuentas de balance (ACTIVO/PASIVO/PATRIMONIO): saldo acumulado histórico hasta `hasta`.
    Cuentas de resultado (INGRESO/GASTO): solo movimientos del período desde..hasta.
    """
    from sqlalchemy import func as sqlfunc
    # Sumas dentro del período (todas las cuentas)
    rows_periodo = (db.session.query(
                        LineaAsiento.cuenta_id,
                        sqlfunc.sum(LineaAsiento.debe).label('sd'),
                        sqlfunc.sum(LineaAsiento.haber).label('sh'),
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

    # Saldo anterior al período (solo cuentas de balance)
    rows_ant = (db.session.query(
                    LineaAsiento.cuenta_id,
                    sqlfunc.sum(LineaAsiento.debe).label('sd'),
                    sqlfunc.sum(LineaAsiento.haber).label('sh'),
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

    # Combinar: para cuentas de balance sumar anterior + período; para resultado solo período
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


@bp.route('/empresa/<int:eid>/reportes/balance')
def balance(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()

    sumas = _sumas_balance(eid, desde, hasta)

    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .order_by(Cuenta.codigo).all())

    filas = []
    tot = {k: 0.0 for k in ['sd','sh','sald','sala','bgd','bgh','erd','erh']}

    for c in cuentas:
        sd, sh = sumas.get(c.id, (0.0, 0.0))
        # Saldo deudor / acreedor (raw, sin naturaleza)
        sald = max(sd - sh, 0)   # saldo deudor
        sala = max(sh - sd, 0)   # saldo acreedor

        erd = erh = bgd = bgh = 0.0
        if not c.es_titulo:
            if c.tipo in ('GASTO', 'INGRESO'):
                erd = sald   # pérdidas
                erh = sala   # ganancias
            else:            # ACTIVO, PASIVO, PATRIMONIO
                bgd = sald   # activos
                bgh = sala   # pasivos / patrimonio

            tot['sd']  += sd;   tot['sh']  += sh
            tot['sald']+= sald; tot['sala']+= sala
            tot['bgd'] += bgd;  tot['bgh'] += bgh
            tot['erd'] += erd;  tot['erh'] += erh

        if sd or sh or c.es_titulo:
            filas.append({
                'cuenta': c,
                'sd': sd, 'sh': sh,
                'sald': sald, 'sala': sala,
                'bgd': bgd, 'bgh': bgh,
                'erd': erd, 'erh': erh,
            })

    resultado_er = tot['erh'] - tot['erd']

    return render_template('reportes/balance.html', empresa=empresa,
                           filas=filas, desde=desde, hasta=hasta,
                           tot=tot, resultado_er=resultado_er)


@bp.route('/empresa/<int:eid>/reportes/resultado')
def resultado(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()

    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .filter(Cuenta.tipo.in_(['INGRESO', 'GASTO']))
               .order_by(Cuenta.codigo).all())

    total_ingresos = 0.0
    total_gastos = 0.0
    filas = []
    for c in cuentas:
        saldo = c.saldo(desde=desde, hasta=hasta)
        filas.append({'cuenta': c, 'saldo': saldo})
        if not c.es_titulo:
            if c.tipo == 'INGRESO':
                total_ingresos += saldo
            else:
                total_gastos += saldo

    resultado_neto = total_ingresos - total_gastos

    return render_template('reportes/resultado.html', empresa=empresa,
                           filas=filas, total_ingresos=total_ingresos,
                           total_gastos=total_gastos, resultado_neto=resultado_neto,
                           desde=desde, hasta=hasta)


@bp.route('/empresa/<int:eid>/reportes/ap-ar')
def ap_ar(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    rut_detalle = request.args.get('rut')
    tipo_detalle = request.args.get('tipo')  # COMPRAS, VENTAS, HONORARIOS

    def _resumen(tipo_libro):
        return (db.session.query(
            DocumentoSII.rut_contraparte,
            func.max(DocumentoSII.razon_social_contraparte).label('razon_social'),
            func.count(DocumentoSII.id).label('ndocs'),
            func.sum(DocumentoSII.monto_neto).label('total_neto'),
            func.sum(DocumentoSII.iva).label('total_iva'),
            func.sum(DocumentoSII.total).label('total_bruto'),
            func.sum(case(
                (DocumentoSII.procesado == False, DocumentoSII.total),
                else_=0
            )).label('sin_contabilizar'),
        )
        .filter(
            DocumentoSII.empresa_id == eid,
            DocumentoSII.tipo_libro == tipo_libro,
            DocumentoSII.fecha >= desde,
            DocumentoSII.fecha <= hasta,
        )
        .group_by(DocumentoSII.rut_contraparte)
        .order_by(func.sum(DocumentoSII.total).desc())
        .all())

    proveedores = _resumen('COMPRAS')
    clientes    = _resumen('VENTAS')
    honorarios  = _resumen('HONORARIOS')

    # Saldos contables de las cuentas de control
    def _saldo_cuenta(codigo):
        c = Cuenta.query.filter_by(empresa_id=eid, codigo=codigo).first()
        return c.saldo(hasta=hasta) if c else 0.0

    saldo_proveedores = _saldo_cuenta('2.1.01')
    saldo_clientes    = _saldo_cuenta('1.1.03')
    saldo_honorarios  = _saldo_cuenta('2.1.04')

    # Detalle por RUT cuando se hace click en una fila
    docs_detalle = []
    if rut_detalle and tipo_detalle:
        docs_detalle = (DocumentoSII.query
            .filter_by(empresa_id=eid, tipo_libro=tipo_detalle,
                       rut_contraparte=rut_detalle)
            .filter(DocumentoSII.fecha >= desde, DocumentoSII.fecha <= hasta)
            .order_by(DocumentoSII.fecha)
            .all())

    return render_template('reportes/ap_ar.html',
        empresa=empresa, desde=desde, hasta=hasta,
        proveedores=proveedores, clientes=clientes, honorarios=honorarios,
        saldo_proveedores=saldo_proveedores,
        saldo_clientes=saldo_clientes,
        saldo_honorarios=saldo_honorarios,
        docs_detalle=docs_detalle,
        rut_detalle=rut_detalle,
        tipo_detalle=tipo_detalle,
    )


@bp.route('/empresa/<int:eid>/reportes/balance/imprimir')
def balance_imprimir(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()

    sumas = _sumas_balance(eid, desde, hasta)

    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .order_by(Cuenta.codigo).all())

    filas = []
    tot = {k: 0.0 for k in ['sd','sh','sald','sala','bgd','bgh','erd','erh']}

    for c in cuentas:
        sd, sh = sumas.get(c.id, (0.0, 0.0))
        sald = max(sd - sh, 0)
        sala = max(sh - sd, 0)

        erd = erh = bgd = bgh = 0.0
        if not c.es_titulo:
            if c.tipo in ('GASTO', 'INGRESO'):
                erd = sald; erh = sala
            else:
                bgd = sald; bgh = sala
            tot['sd']  += sd;   tot['sh']  += sh
            tot['sald']+= sald; tot['sala']+= sala
            tot['bgd'] += bgd;  tot['bgh'] += bgh
            tot['erd'] += erd;  tot['erh'] += erh

        if sd or sh:
            filas.append({'cuenta': c, 'sd': sd, 'sh': sh,
                          'sald': sald, 'sala': sala,
                          'bgd': bgd, 'bgh': bgh, 'erd': erd, 'erh': erh})

    resultado_er = tot['erh'] - tot['erd']

    from datetime import datetime
    return render_template('reportes/balance_print.html',
                           empresa=empresa, filas=filas,
                           desde=desde, hasta=hasta,
                           tot=tot, resultado_er=resultado_er,
                           now=datetime.now())


@bp.route('/empresa/<int:eid>/reportes/diario')
def diario(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    asientos = (Asiento.query
                .filter_by(empresa_id=eid, estado='CONFIRMADO')
                .filter(Asiento.fecha >= desde, Asiento.fecha <= hasta)
                .order_by(Asiento.numero)
                .all())
    return render_template('reportes/diario.html', empresa=empresa,
                           asientos=asientos, desde=desde, hasta=hasta)


@bp.route('/empresa/<int:eid>/reportes/diario/imprimir')
def diario_imprimir(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    asientos = (Asiento.query
                .filter_by(empresa_id=eid, estado='CONFIRMADO')
                .filter(Asiento.fecha >= desde, Asiento.fecha <= hasta)
                .order_by(Asiento.numero)
                .all())
    from datetime import datetime
    return render_template('reportes/diario_print.html', empresa=empresa,
                           asientos=asientos, desde=desde, hasta=hasta,
                           now=datetime.now())


@bp.route('/empresa/<int:eid>/reportes/mayor/imprimir')
def mayor_imprimir(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    cuenta_id = request.args.get('cuenta_id', type=int)
    cuenta_sel = Cuenta.query.get(cuenta_id) if cuenta_id else None
    movimientos = []
    saldo_inicial = 0.0
    if cuenta_sel:
        movimientos = (LineaAsiento.query
                       .join(Asiento)
                       .filter(
                           LineaAsiento.cuenta_id == cuenta_id,
                           Asiento.estado == 'CONFIRMADO',
                           Asiento.fecha >= desde,
                           Asiento.fecha <= hasta,
                       )
                       .order_by(Asiento.fecha, Asiento.numero)
                       .all())
        si = (db.session.query(
                  func.sum(LineaAsiento.debe).label('debe'),
                  func.sum(LineaAsiento.haber).label('haber'),
              )
              .join(Asiento)
              .filter(LineaAsiento.cuenta_id == cuenta_id,
                      Asiento.estado == 'CONFIRMADO',
                      Asiento.fecha < desde)
              .first())
        sd, sh = (si.debe or 0), (si.haber or 0)
        saldo_inicial = sd - sh if cuenta_sel.naturaleza == 'DEUDORA' else sh - sd
    from datetime import datetime
    return render_template('reportes/mayor_print.html', empresa=empresa,
                           cuenta_sel=cuenta_sel, movimientos=movimientos,
                           saldo_inicial=saldo_inicial, desde=desde, hasta=hasta,
                           now=datetime.now())
