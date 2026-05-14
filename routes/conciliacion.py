import calendar
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, DocumentoSII, MovimientoBanco, Conciliacion, Contraparte, Cuenta
from engine import asientos as motor
from engine.asientos import (confirmar_asiento, generar_asiento_pago_proveedor,
                             generar_asiento_cobro_cliente,
                             generar_asiento_banco_compuesto)

bp = Blueprint('conciliacion', __name__)


def _default_rango():
    hoy = date.today()
    hasta = f'{hoy.year}-{hoy.month:02d}'
    y, m = hoy.year, hoy.month - 3
    while m <= 0:
        m += 12
        y -= 1
    return f'{y}-{m:02d}', hasta


def _mes_a_rango(mes_str):
    d = date.fromisoformat(mes_str + '-01')
    return d, d.replace(day=calendar.monthrange(d.year, d.month)[1])


@bp.route('/empresa/<int:eid>/conciliacion')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)

    default_desde, default_hasta = _default_rango()
    # soporte param legacy 'mes'
    if 'mes' in request.args and 'desde' not in request.args:
        mes = request.args.get('mes', '')
        desde_mes = hasta_mes = mes if mes else default_desde
    else:
        desde_mes = request.args.get('desde', default_desde)
        hasta_mes  = request.args.get('hasta', default_hasta)

    try:
        d_ini, _ = _mes_a_rango(desde_mes)
        _, d_fin  = _mes_a_rango(hasta_mes)
    except ValueError:
        desde_mes, hasta_mes = default_desde, default_hasta
        d_ini, _ = _mes_a_rango(desde_mes)
        _, d_fin  = _mes_a_rango(hasta_mes)

    docs_sin = (DocumentoSII.query
                .filter_by(empresa_id=eid)
                .filter(DocumentoSII.conciliacion_id == None)
                .filter(DocumentoSII.fecha >= d_ini, DocumentoSII.fecha <= d_fin)
                .order_by(DocumentoSII.tipo_libro, DocumentoSII.fecha)
                .all())

    movs_sin = (MovimientoBanco.query
                .filter_by(empresa_id=eid)
                .filter(MovimientoBanco.conciliacion_id == None)
                .filter(MovimientoBanco.fecha >= d_ini, MovimientoBanco.fecha <= d_fin)
                .order_by(MovimientoBanco.fecha)
                .all())

    conciliaciones = (Conciliacion.query
                      .filter_by(empresa_id=eid)
                      .filter(Conciliacion.fecha >= d_ini, Conciliacion.fecha <= d_fin)
                      .order_by(Conciliacion.fecha.desc())
                      .all())

    total_movs_sin = (MovimientoBanco.query
                      .filter_by(empresa_id=eid)
                      .filter(MovimientoBanco.conciliacion_id == None)
                      .count())
    total_docs_sin = (DocumentoSII.query
                      .filter_by(empresa_id=eid)
                      .filter(DocumentoSII.conciliacion_id == None,
                              DocumentoSII.procesado == False)
                      .count())

    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, es_titulo=False, activa=True)
               .order_by(Cuenta.codigo)
               .all())

    return render_template('conciliacion/index.html',
                           empresa=empresa,
                           desde_mes=desde_mes, hasta_mes=hasta_mes,
                           docs_sin=docs_sin, movs_sin=movs_sin,
                           conciliaciones=conciliaciones,
                           total_movs_sin=total_movs_sin,
                           total_docs_sin=total_docs_sin,
                           cuentas=cuentas,
                           tipos_label=TIPOS_LABEL)


TIPOS_LABEL = {
    'SII':       'Documento SII',
    'SUELDO':    'Sueldo / Remuneraciones',
    'RETIRO':    'Retiro socio',
    'IMPUESTO':  'Impuesto (IVA / PPM / Renta)',
    'F29':       'Pago F29 (IVA + Retenciones + PPM)',
    'BANCO':     'Gasto bancario / comisión',
    'PRESTAMO':  'Préstamo / amortización',
    'INTERNO':   'Transferencia interna',
    'OTRO':      'Otro',
}


@bp.route('/empresa/<int:eid>/conciliacion/crear', methods=['POST'])
def crear(eid):
    doc_ids      = request.form.getlist('doc_ids', type=int)
    mov_ids      = request.form.getlist('mov_ids', type=int)
    desde_mes    = request.form.get('desde', '')
    hasta_mes    = request.form.get('hasta', '')
    # Multi-line: lista de cuentas + montos (banco-solo compuesto)
    cuenta_ids   = request.form.getlist('cuenta_ids', type=int)
    cuenta_montos = request.form.getlist('cuenta_montos', type=float)
    # Legacy single-account fallback
    cuenta_id_legacy = request.form.get('cuenta_id', type=int)
    if not cuenta_ids and cuenta_id_legacy:
        cuenta_ids = [cuenta_id_legacy]
        cuenta_montos = []
    # soporte legacy
    if not desde_mes:
        desde_mes = hasta_mes = request.form.get('mes', date.today().strftime('%Y-%m'))
    tiene_cuentas = bool(cuenta_ids and not doc_ids)
    tipo = 'BANCO' if tiene_cuentas else request.form.get('tipo', 'SII')
    nota = request.form.get('nota', '').strip()
    respaldo_url = request.form.get('respaldo_url', '').strip() or None

    if not doc_ids and not mov_ids:
        flash('Seleccione al menos un documento o movimiento', 'warning')
        return redirect(url_for('conciliacion.index', eid=eid, desde=desde_mes, hasta=hasta_mes))

    docs = [DocumentoSII.query.get(i) for i in doc_ids]
    docs = [d for d in docs if d and d.empresa_id == eid]
    movs = [MovimientoBanco.query.get(i) for i in mov_ids]
    movs = [m for m in movs if m and m.empresa_id == eid]

    fechas = [d.fecha for d in docs if d.fecha] + [m.fecha for m in movs if m.fecha]
    if not fechas:
        flash('No se encontraron registros válidos', 'warning')
        return redirect(url_for('conciliacion.index', eid=eid, desde=desde_mes, hasta=hasta_mes))

    # Descripción automática según tipo
    if tipo == 'SII' and docs:
        desc = ', '.join(
            f"{d.tipo_libro} {d.folio} {(d.razon_social_contraparte or '')[:15]}"
            for d in docs[:3]
        )
        if movs:
            desc += ' | ' + ', '.join((m.descripcion or '')[:25] for m in movs[:2])
    else:
        label = TIPOS_LABEL.get(tipo, tipo)
        mov_desc = ', '.join((m.descripcion or '')[:30] for m in movs[:2])
        desc = f"{label}: {nota or mov_desc}"

    # Detectar contraparte desde documentos SII
    contraparte_id = None
    if docs:
        rut = next((d.rut_contraparte for d in docs if d.rut_contraparte), None)
        if rut:
            cp = Contraparte.query.filter_by(empresa_id=eid, rut=rut).first()
            if cp:
                contraparte_id = cp.id

    conc = Conciliacion(
        empresa_id=eid,
        fecha=max(fechas),
        descripcion=desc[:280],
        tipo=tipo,
        respaldo_url=respaldo_url,
        contraparte_id=contraparte_id,
    )
    db.session.add(conc)
    db.session.flush()

    for d in docs:
        d.conciliacion_id = conc.id
    for m in movs:
        m.conciliacion_id = conc.id

    asientos_creados = 0
    errores_asiento = []

    # Banco sin doc SII + cuentas seleccionadas → asiento compuesto
    if not docs and movs and cuenta_ids:
        for m in movs:
            try:
                asiento = motor.generar_asiento_banco_compuesto(m, cuenta_ids, cuenta_montos)
                try:
                    confirmar_asiento(asiento)
                except ValueError:
                    pass
                m.procesado = True
                m.asiento_id = asiento.id
                asientos_creados += 1
            except Exception as e:
                errores_asiento.append(f"Mov {m.fecha}: {e}")

    # Contabilizar automáticamente los docs SII no procesados
    tipos_doc = set()
    for d in docs:
        if d.procesado:
            tipos_doc.add(d.tipo_libro)
            continue
        try:
            if d.tipo_libro == 'COMPRAS':
                asiento = motor.generar_asiento_compra(d)
            elif d.tipo_libro == 'VENTAS':
                asiento = motor.generar_asiento_venta(d)
            elif d.tipo_libro == 'HONORARIOS':
                asiento = motor.generar_asiento_honorario(d)
            else:
                continue
            try:
                confirmar_asiento(asiento)
            except ValueError:
                pass  # queda en borrador si no cuadra
            d.procesado = True
            d.asiento_id = asiento.id
            asientos_creados += 1
            tipos_doc.add(d.tipo_libro)
        except Exception as e:
            errores_asiento.append(f"Folio {d.folio}: {e}")

    # Cuando hay docs + movimientos, generar asiento de pago/cobro para cada mov
    # Esto cierra el saldo de Proveedores/Clientes y registra el movimiento de banco
    if docs and movs:
        es_cobro = 'VENTAS' in tipos_doc and 'COMPRAS' not in tipos_doc and 'HONORARIOS' not in tipos_doc
        for m in movs:
            if m.procesado:
                continue
            try:
                if es_cobro:
                    asiento = generar_asiento_cobro_cliente(m)
                else:
                    asiento = generar_asiento_pago_proveedor(m)
                try:
                    confirmar_asiento(asiento)
                except ValueError:
                    pass
                m.procesado = True
                m.asiento_id = asiento.id
                asientos_creados += 1
            except Exception as e:
                errores_asiento.append(f"Mov banco {m.fecha}: {e}")

    db.session.commit()

    if docs:
        msg = f'Conciliado: {len(docs)} doc(s) ↔ {len(movs)} mov(s)'
        if asientos_creados:
            msg += f' · {asientos_creados} asiento(s) generado(s)'
        flash(msg, 'success')
    elif asientos_creados:
        cnames = []
        for cid in cuenta_ids:
            c = Cuenta.query.get(cid)
            if c: cnames.append(f'{c.codigo} {c.nombre}')
        flash(f'{asientos_creados} asiento(s) generado(s) → {" + ".join(cnames)}', 'success')
    else:
        flash(f'Marcado como "{TIPOS_LABEL.get(tipo, tipo)}": {len(movs)} movimiento(s)', 'success')
    for err in errores_asiento:
        flash(err, 'warning')
    return redirect(url_for('conciliacion.index', eid=eid, desde=desde_mes, hasta=hasta_mes))


@bp.route('/empresa/<int:eid>/conciliacion/<int:cid>/deshacer', methods=['POST'])
def deshacer(eid, cid):
    conc = Conciliacion.query.get_or_404(cid)
    if conc.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('conciliacion.index', eid=eid))
    desde_mes = request.form.get('desde', '')
    hasta_mes  = request.form.get('hasta', request.form.get('mes', date.today().strftime('%Y-%m')))
    if not desde_mes:
        desde_mes = hasta_mes
    DocumentoSII.query.filter_by(conciliacion_id=cid).update({'conciliacion_id': None, 'procesado': False, 'asiento_id': None})
    MovimientoBanco.query.filter_by(conciliacion_id=cid).update({'conciliacion_id': None, 'procesado': False, 'asiento_id': None})
    db.session.delete(conc)
    db.session.commit()
    flash('Conciliación deshecha', 'warning')
    return redirect(url_for('conciliacion.index', eid=eid, desde=desde_mes, hasta=hasta_mes))
