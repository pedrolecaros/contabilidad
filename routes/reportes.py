from flask import Blueprint, render_template, request, flash, redirect, url_for, Response
from datetime import date
import calendar
import csv
import io
from models import db, Empresa, Cuenta, LineaAsiento, Asiento, DocumentoSII, Contraparte
from sqlalchemy import func, case
from collections import defaultdict
from engine.contabilidad import sumas_balance as _sumas_balance, clasificar_cuenta_efe as _clasificar_cuenta_efe

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

    # Optional comparison period
    comparar_desde_str = request.args.get('comparar_desde', '')
    comparar_hasta_str = request.args.get('comparar_hasta', '')
    comparar_desde = comparar_hasta = None
    if comparar_desde_str and comparar_hasta_str:
        try:
            comparar_desde = date.fromisoformat(comparar_desde_str)
            comparar_hasta = date.fromisoformat(comparar_hasta_str)
        except ValueError:
            pass

    sumas = _sumas_balance(eid, desde, hasta)
    sumas_cmp = _sumas_balance(eid, comparar_desde, comparar_hasta) if comparar_desde else {}

    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .order_by(Cuenta.codigo).all())

    def _build_filas(sumas_dict):
        filas = []
        tot = {k: 0.0 for k in ['sd','sh','sald','sala','bgd','bgh','erd','erh']}
        for c in cuentas:
            sd, sh = sumas_dict.get(c.id, (0.0, 0.0))
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
            if sd or sh or c.es_titulo:
                filas.append({'cuenta': c, 'sd': sd, 'sh': sh,
                               'sald': sald, 'sala': sala,
                               'bgd': bgd, 'bgh': bgh,
                               'erd': erd, 'erh': erh})
        return filas, tot

    filas, tot = _build_filas(sumas)
    filas_cmp, tot_cmp = _build_filas(sumas_cmp) if comparar_desde else ([], {})

    # Build comparison dict keyed by cuenta.id for easy template lookup
    cmp_por_cuenta = {f['cuenta'].id: f for f in filas_cmp} if filas_cmp else {}

    resultado_er = tot['erh'] - tot['erd']
    resultado_er_cmp = tot_cmp.get('erh', 0) - tot_cmp.get('erd', 0) if tot_cmp else None

    return render_template('reportes/balance.html', empresa=empresa,
                           filas=filas, desde=desde, hasta=hasta,
                           tot=tot, resultado_er=resultado_er,
                           comparar_desde=comparar_desde, comparar_hasta=comparar_hasta,
                           cmp_por_cuenta=cmp_por_cuenta, tot_cmp=tot_cmp,
                           resultado_er_cmp=resultado_er_cmp)


@bp.route('/empresa/<int:eid>/reportes/resultado')
def resultado(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()

    # Optional comparison period
    comparar_desde_str = request.args.get('comparar_desde', '')
    comparar_hasta_str = request.args.get('comparar_hasta', '')
    comparar_desde = comparar_hasta = None
    if comparar_desde_str and comparar_hasta_str:
        try:
            comparar_desde = date.fromisoformat(comparar_desde_str)
            comparar_hasta = date.fromisoformat(comparar_hasta_str)
        except ValueError:
            pass

    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .filter(Cuenta.tipo.in_(['INGRESO', 'GASTO']))
               .order_by(Cuenta.codigo).all())

    def _calcular_filas(d_desde, d_hasta):
        tot_ing = 0.0
        tot_gas = 0.0
        fs = []
        for c in cuentas:
            saldo = c.saldo(desde=d_desde, hasta=d_hasta)
            fs.append({'cuenta': c, 'saldo': saldo})
            if not c.es_titulo:
                if c.tipo == 'INGRESO':
                    tot_ing += saldo
                else:
                    tot_gas += saldo
        return fs, tot_ing, tot_gas

    filas, total_ingresos, total_gastos = _calcular_filas(desde, hasta)
    resultado_neto = total_ingresos - total_gastos

    filas_cmp = []
    total_ingresos_cmp = total_gastos_cmp = resultado_neto_cmp = None
    if comparar_desde and comparar_hasta:
        filas_cmp, total_ingresos_cmp, total_gastos_cmp = _calcular_filas(comparar_desde, comparar_hasta)
        resultado_neto_cmp = total_ingresos_cmp - total_gastos_cmp

    # Build comparison dict keyed by cuenta.id
    cmp_por_cuenta = {f['cuenta'].id: f['saldo'] for f in filas_cmp} if filas_cmp else {}

    return render_template('reportes/resultado.html', empresa=empresa,
                           filas=filas, total_ingresos=total_ingresos,
                           total_gastos=total_gastos, resultado_neto=resultado_neto,
                           desde=desde, hasta=hasta,
                           comparar_desde=comparar_desde, comparar_hasta=comparar_hasta,
                           cmp_por_cuenta=cmp_por_cuenta,
                           total_ingresos_cmp=total_ingresos_cmp,
                           total_gastos_cmp=total_gastos_cmp,
                           resultado_neto_cmp=resultado_neto_cmp)


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

    cp_por_rut = {
        cp.rut.replace('.', '').replace('-', '').upper(): cp.id
        for cp in Contraparte.query.filter_by(empresa_id=eid).all()
        if cp.rut
    }

    return render_template('reportes/ap_ar.html',
        empresa=empresa, desde=desde, hasta=hasta,
        proveedores=proveedores, clientes=clientes, honorarios=honorarios,
        saldo_proveedores=saldo_proveedores,
        saldo_clientes=saldo_clientes,
        saldo_honorarios=saldo_honorarios,
        docs_detalle=docs_detalle,
        rut_detalle=rut_detalle,
        tipo_detalle=tipo_detalle,
        cp_por_rut=cp_por_rut,
    )


@bp.route('/empresa/<int:eid>/reportes/cxc-cxp')
def cxc_cxp(eid):
    empresa = Empresa.query.get_or_404(eid)

    # Todas las lineas de cuentas CxC/CxP (con o sin contraparte)
    lineas = (LineaAsiento.query
              .join(Asiento)
              .join(Cuenta, LineaAsiento.cuenta_id == Cuenta.id)
              .filter(
                  Asiento.empresa_id == eid,
                  Asiento.estado == 'CONFIRMADO',
                  Cuenta.tipo.in_(['ACTIVO', 'PASIVO']),
                  Cuenta.es_titulo == False,
                  db.or_(
                      Cuenta.codigo.like('1.1.%'),
                      Cuenta.codigo.like('2.%'),
                  ),
              )
              .all())

    # Acumula por (cuenta_id, contraparte_id) — None = sin aux
    totales_cuenta = defaultdict(lambda: {'debe': 0.0, 'haber': 0.0, 'cuenta': None})
    por_cp         = defaultdict(lambda: {'debe': 0.0, 'haber': 0.0, 'cuenta': None, 'cp': None})

    for l in lineas:
        totales_cuenta[l.cuenta_id]['debe']   += l.debe
        totales_cuenta[l.cuenta_id]['haber']  += l.haber
        totales_cuenta[l.cuenta_id]['cuenta']  = l.cuenta
        if l.contraparte_id:
            por_cp[(l.cuenta_id, l.contraparte_id)]['debe']   += l.debe
            por_cp[(l.cuenta_id, l.contraparte_id)]['haber']  += l.haber
            por_cp[(l.cuenta_id, l.contraparte_id)]['cuenta']  = l.cuenta
            por_cp[(l.cuenta_id, l.contraparte_id)]['cp']      = l.contraparte

    def _saldo(g, naturaleza):
        return g['debe'] - g['haber'] if naturaleza == 'DEUDORA' else g['haber'] - g['debe']

    cxc = []
    cxp = []

    for cid, gt in totales_cuenta.items():
        c = gt['cuenta']
        saldo_total = _saldo(gt, c.naturaleza)
        if saldo_total == 0:
            continue

        # Desglose por contraparte para esta cuenta
        filas_cp = []
        saldo_cp_sum = 0.0
        for (cid2, cpid), gc in por_cp.items():
            if cid2 != cid:
                continue
            s = _saldo(gc, c.naturaleza)
            if s == 0:
                continue
            filas_cp.append({'cp': gc['cp'], 'saldo': s})
            saldo_cp_sum += s

        # Residuo sin contraparte identificada
        residuo = round(saldo_total - saldo_cp_sum, 2)
        if abs(residuo) >= 1:
            filas_cp.append({'cp': None, 'saldo': residuo})

        filas_cp.sort(key=lambda r: r['cp'].razon_social if r['cp'] else 'zz')
        entry = {'cuenta': c, 'saldo_total': saldo_total, 'filas': filas_cp}

        if c.tipo == 'ACTIVO':
            cxc.append(entry)
        else:
            cxp.append(entry)

    cxc.sort(key=lambda r: r['cuenta'].codigo)
    cxp.sort(key=lambda r: r['cuenta'].codigo)

    tot_cxc = sum(r['saldo_total'] for r in cxc)
    tot_cxp = sum(r['saldo_total'] for r in cxp)

    return render_template('reportes/cxc_cxp.html',
        empresa=empresa,
        cxc=cxc, cxp=cxp,
        tot_cxc=tot_cxc, tot_cxp=tot_cxp)


@bp.route('/empresa/<int:eid>/reportes/balance-gaap')
def balance_gaap(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()

    sumas = _sumas_balance(eid, desde, hasta)
    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .order_by(Cuenta.codigo).all())

    activos, pasivos, patrimonio = [], [], []
    tot_activo = tot_pasivo = tot_patrimonio = 0.0
    tot_sd = tot_sh = 0.0

    for c in cuentas:
        sd, sh = sumas.get(c.id, (0.0, 0.0))
        if not c.es_titulo:
            tot_sd += sd
            tot_sh += sh
        if c.tipo == 'ACTIVO':
            valor = sd - sh  # DEUDORA: positivo = activo
            if not c.es_titulo:
                tot_activo += valor
            activos.append({'cuenta': c, 'valor': valor})
        elif c.tipo == 'PASIVO':
            valor = sh - sd  # ACREEDORA: positivo = pasivo
            if not c.es_titulo:
                tot_pasivo += valor
            pasivos.append({'cuenta': c, 'valor': valor})
        elif c.tipo == 'PATRIMONIO':
            valor = sh - sd  # ACREEDORA: positivo = patrimonio
            if not c.es_titulo:
                tot_patrimonio += valor
            patrimonio.append({'cuenta': c, 'valor': valor})

    # Resultado del ejercicio (ingresos - gastos)
    resultado_er = 0.0
    for c in cuentas:
        if c.es_titulo or c.tipo not in ('INGRESO', 'GASTO'):
            continue
        sd, sh = sumas.get(c.id, (0.0, 0.0))
        if c.tipo == 'INGRESO':
            resultado_er += sh - sd
        else:
            resultado_er -= sd - sh

    return render_template('reportes/balance_gaap.html',
        empresa=empresa, desde=desde, hasta=hasta,
        activos=activos, pasivos=pasivos, patrimonio=patrimonio,
        tot_activo=tot_activo, tot_pasivo=tot_pasivo,
        tot_patrimonio=tot_patrimonio, resultado_er=resultado_er)


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


@bp.route('/empresa/<int:eid>/reportes/mayor/csv')
def mayor_csv(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    cuenta_id = request.args.get('cuenta_id', type=int)
    cuenta_sel = Cuenta.query.get(cuenta_id) if cuenta_id else None

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Empresa', empresa.razon_social])
    writer.writerow(['Período', f'{desde.isoformat()} al {hasta.isoformat()}'])
    writer.writerow([])

    if cuenta_sel:
        writer.writerow(['Cuenta', f'{cuenta_sel.codigo} – {cuenta_sel.nombre}'])
        writer.writerow([])
        writer.writerow(['Fecha', 'N° Asiento', 'Descripción', 'Debe', 'Haber', 'Saldo'])

        # Saldo inicial
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
        si_debe = si.debe or 0.0
        si_haber = si.haber or 0.0
        if cuenta_sel.naturaleza == 'DEUDORA':
            saldo = si_debe - si_haber
        else:
            saldo = si_haber - si_debe
        writer.writerow([desde.isoformat(), '', 'Saldo inicial', '', '', round(saldo)])

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
        for l in movimientos:
            if cuenta_sel.naturaleza == 'DEUDORA':
                saldo += (l.debe or 0) - (l.haber or 0)
            else:
                saldo += (l.haber or 0) - (l.debe or 0)
            writer.writerow([
                l.asiento.fecha.isoformat(),
                l.asiento.numero,
                l.descripcion or l.asiento.descripcion or '',
                round(l.debe or 0),
                round(l.haber or 0),
                round(saldo),
            ])
    else:
        writer.writerow(['Sin cuenta seleccionada'])

    output.seek(0)
    nombre = f'mayor_{cuenta_sel.codigo if cuenta_sel else "sin_cuenta"}_{desde}_{hasta}.csv'
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{nombre}"'},
    )


@bp.route('/empresa/<int:eid>/reportes/diario/csv')
def diario_csv(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    asientos = (Asiento.query
                .filter_by(empresa_id=eid, estado='CONFIRMADO')
                .filter(Asiento.fecha >= desde, Asiento.fecha <= hasta)
                .order_by(Asiento.numero)
                .all())

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Empresa', empresa.razon_social])
    writer.writerow(['Período', f'{desde.isoformat()} al {hasta.isoformat()}'])
    writer.writerow([])
    writer.writerow(['N° Asiento', 'Fecha', 'Descripción Asiento', 'Código Cuenta', 'Cuenta', 'Descripción Línea', 'Debe', 'Haber'])

    for a in asientos:
        for l in a.lineas:
            writer.writerow([
                a.numero,
                a.fecha.isoformat(),
                a.descripcion or '',
                l.cuenta.codigo,
                l.cuenta.nombre,
                l.descripcion or '',
                round(l.debe or 0),
                round(l.haber or 0),
            ])

    output.seek(0)
    nombre = f'diario_{desde}_{hasta}.csv'
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{nombre}"'},
    )


@bp.route('/empresa/<int:eid>/reportes/balance/csv')
def balance_csv(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    sumas = _sumas_balance(eid, desde, hasta)
    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, activa=True)
               .order_by(Cuenta.codigo).all())

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Empresa', empresa.razon_social])
    writer.writerow(['Período', f'{desde.isoformat()} al {hasta.isoformat()}'])
    writer.writerow([])
    writer.writerow(['Código', 'Cuenta', 'Tipo', 'Sumas Débito', 'Sumas Crédito',
                     'Saldo Deudor', 'Saldo Acreedor',
                     'Balance Activo', 'Balance Pasivo/Patr.',
                     'ER Pérdidas', 'ER Ganancias'])

    for c in cuentas:
        if c.es_titulo:
            continue
        sd, sh = sumas.get(c.id, (0.0, 0.0))
        sald = max(sd - sh, 0)
        sala = max(sh - sd, 0)
        if c.tipo in ('GASTO', 'INGRESO'):
            erd, erh, bgd, bgh = sald, sala, 0.0, 0.0
        else:
            erd, erh, bgd, bgh = 0.0, 0.0, sald, sala
        if sd or sh:
            writer.writerow([
                c.codigo, c.nombre, c.tipo,
                round(sd), round(sh),
                round(sald), round(sala),
                round(bgd), round(bgh),
                round(erd), round(erh),
            ])

    output.seek(0)
    nombre = f'balance_{desde}_{hasta}.csv'
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{nombre}"'},
    )


@bp.route('/empresa/<int:eid>/reportes/flujo-iva')
def flujo_iva(eid):
    empresa = Empresa.query.get_or_404(eid)
    if not empresa.contribuyente_iva:
        flash('Esta empresa no está configurada como contribuyente de IVA.', 'warning')
        return redirect(url_for('reportes.balance', eid=eid))

    desde, hasta = _rango_fechas()

    # Group by month: year-month
    rows = (db.session.query(
                func.strftime('%Y-%m', DocumentoSII.fecha).label('mes'),
                DocumentoSII.tipo_libro,
                func.sum(DocumentoSII.iva).label('total_iva'),
            )
            .filter(
                DocumentoSII.empresa_id == eid,
                DocumentoSII.tipo_libro.in_(['VENTAS', 'COMPRAS']),
                DocumentoSII.fecha >= desde,
                DocumentoSII.fecha <= hasta,
            )
            .group_by(func.strftime('%Y-%m', DocumentoSII.fecha), DocumentoSII.tipo_libro)
            .order_by(func.strftime('%Y-%m', DocumentoSII.fecha))
            .all())

    # Build month-by-month breakdown
    meses = {}
    for r in rows:
        mes = r.mes or 'Sin fecha'
        if mes not in meses:
            meses[mes] = {'debito': 0.0, 'credito': 0.0}
        if r.tipo_libro == 'VENTAS':
            meses[mes]['debito'] += r.total_iva or 0.0
        elif r.tipo_libro == 'COMPRAS':
            meses[mes]['credito'] += r.total_iva or 0.0

    filas = []
    for mes, vals in sorted(meses.items()):
        saldo = vals['debito'] - vals['credito']
        filas.append({
            'mes': mes,
            'debito': vals['debito'],
            'credito': vals['credito'],
            'saldo': saldo,
        })

    total_debito = sum(f['debito'] for f in filas)
    total_credito = sum(f['credito'] for f in filas)
    total_saldo = total_debito - total_credito

    return render_template('reportes/flujo_iva.html',
                           empresa=empresa,
                           desde=desde, hasta=hasta,
                           filas=filas,
                           total_debito=total_debito,
                           total_credito=total_credito,
                           total_saldo=total_saldo)


def _clasificar_cuenta_efe(cuenta):
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


@bp.route('/empresa/<int:eid>/reportes/efe')
def efe(eid):
    from datetime import timedelta
    from sqlalchemy import or_
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()

    cuentas_efectivo = Cuenta.query.filter(
        Cuenta.empresa_id == eid,
        Cuenta.es_titulo == False,
        Cuenta.activa == True,
        or_(Cuenta.codigo.like('1.1.01%'), Cuenta.codigo.like('1.1.02%'))
    ).all()
    efectivo_ids = {c.id for c in cuentas_efectivo}

    if not efectivo_ids:
        return render_template('reportes/efe.html', empresa=empresa,
                               desde=desde, hasta=hasta,
                               saldo_inicial=0, saldo_final=0,
                               seccion_op={}, seccion_inv={}, seccion_fin={},
                               total_op=0, total_inv=0, total_fin=0, variacion=0)

    desde_m1 = desde - timedelta(days=1)
    saldo_inicial = sum(c.saldo(hasta=desde_m1) for c in cuentas_efectivo)
    saldo_final = sum(c.saldo(hasta=hasta) for c in cuentas_efectivo)

    asiento_ids = (db.session.query(LineaAsiento.asiento_id)
                   .join(Asiento)
                   .filter(
                       Asiento.empresa_id == eid,
                       Asiento.estado == 'CONFIRMADO',
                       Asiento.fecha >= desde,
                       Asiento.fecha <= hasta,
                       LineaAsiento.cuenta_id.in_(efectivo_ids)
                   ).distinct().all())
    asiento_ids = [r[0] for r in asiento_ids]

    seccion_op = defaultdict(float)
    seccion_inv = defaultdict(float)
    seccion_fin = defaultdict(float)

    for aid in asiento_ids:
        asiento = Asiento.query.get(aid)
        lineas_ef = [l for l in asiento.lineas if l.cuenta_id in efectivo_ids]
        lineas_otras = [l for l in asiento.lineas if l.cuenta_id not in efectivo_ids]
        net_banco = sum(l.debe - l.haber for l in lineas_ef)
        if not lineas_otras or net_banco == 0:
            continue
        total_otras = sum(l.debe + l.haber for l in lineas_otras) or 1.0
        for line in lineas_otras:
            peso = (line.debe + line.haber) / total_otras
            flujo = net_banco * peso
            seccion, subcat = _clasificar_cuenta_efe(line.cuenta)
            if seccion == 'OPERACIONAL':
                seccion_op[subcat] += flujo
            elif seccion == 'INVERSION':
                seccion_inv[subcat] += flujo
            else:
                seccion_fin[subcat] += flujo

    total_op = sum(seccion_op.values())
    total_inv = sum(seccion_inv.values())
    total_fin = sum(seccion_fin.values())
    variacion = total_op + total_inv + total_fin

    return render_template('reportes/efe.html', empresa=empresa,
                           desde=desde, hasta=hasta,
                           saldo_inicial=saldo_inicial, saldo_final=saldo_final,
                           seccion_op=dict(seccion_op), seccion_inv=dict(seccion_inv),
                           seccion_fin=dict(seccion_fin),
                           total_op=total_op, total_inv=total_inv, total_fin=total_fin,
                           variacion=variacion)


@bp.route('/empresa/<int:eid>/reportes/auxiliar')
def auxiliar(eid):
    empresa = Empresa.query.get_or_404(eid)
    desde, hasta = _rango_fechas()
    cuenta_id = request.args.get('cuenta_id', type=int)

    cuentas_detalle = (Cuenta.query
                       .filter_by(empresa_id=eid, es_titulo=False, activa=True)
                       .order_by(Cuenta.codigo).all())
    cuenta_sel = Cuenta.query.get(cuenta_id) if cuenta_id else None

    grupos = {}  # contraparte_id (o None) → {'cp': Contraparte|None, 'lineas': [...], 'saldo': float}
    if cuenta_sel:
        lineas = (LineaAsiento.query
                  .join(Asiento)
                  .filter(
                      LineaAsiento.cuenta_id == cuenta_id,
                      Asiento.estado == 'CONFIRMADO',
                      Asiento.fecha >= desde,
                      Asiento.fecha <= hasta,
                  )
                  .order_by(Asiento.fecha, Asiento.numero)
                  .all())
        for l in lineas:
            key = l.contraparte_id
            if key not in grupos:
                grupos[key] = {'cp': l.contraparte, 'lineas': [], 'debe': 0.0, 'haber': 0.0}
            grupos[key]['lineas'].append(l)
            grupos[key]['debe']  += l.debe
            grupos[key]['haber'] += l.haber

        # Calcular saldo neto por grupo según naturaleza de la cuenta
        for g in grupos.values():
            if cuenta_sel.naturaleza == 'DEUDORA':
                g['saldo'] = g['debe'] - g['haber']
            else:
                g['saldo'] = g['haber'] - g['debe']

    # Ordenar: primero grupos con contraparte (por nombre), luego sin contraparte
    grupos_ordenados = sorted(
        grupos.items(),
        key=lambda kv: (kv[0] is None, kv[1]['cp'].razon_social if kv[1]['cp'] else '')
    )

    return render_template('reportes/auxiliar.html', empresa=empresa,
                           cuentas=cuentas_detalle, cuenta_sel=cuenta_sel,
                           grupos=grupos_ordenados, desde=desde, hasta=hasta)
