import json
import calendar
from datetime import date as date_
from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, DocumentoSII, MovimientoBanco, Cuenta, Conciliacion
from engine import asientos as motor
from engine.asientos import confirmar_asiento

bp = Blueprint('pendientes', __name__)

# Cuentas que disparan selector de documento al contabilizar banco
CUENTA_CODIGO_TIPO = {
    '2.1.01': ['COMPRAS', 'HONORARIOS'],  # Proveedores
    '1.1.03': ['VENTAS'],                  # Clientes
    '2.1.04': ['HONORARIOS'],              # Retenciones honorarios
}


def _default_rango():
    """Devuelve (desde_mes, hasta_mes) como strings YYYY-MM: 3 meses atrás → hoy."""
    hoy = date_.today()
    hasta = f'{hoy.year}-{hoy.month:02d}'
    y, m = hoy.year, hoy.month - 3
    while m <= 0:
        m += 12
        y -= 1
    return f'{y}-{m:02d}', hasta


def _mes_a_fecha_inicio(mes_str):
    return date_.fromisoformat(mes_str + '-01')


def _mes_a_fecha_fin(mes_str):
    d = date_.fromisoformat(mes_str + '-01')
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


@bp.route('/empresa/<int:eid>/pendientes')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)

    default_desde, default_hasta = _default_rango()
    desde_mes = request.args.get('desde', default_desde)
    hasta_mes = request.args.get('hasta', default_hasta)
    # soporte param legacy 'mes' (rango de un mes)
    if 'mes' in request.args and 'desde' not in request.args:
        mes = request.args.get('mes', '')
        if mes and len(mes) == 7:
            desde_mes = hasta_mes = mes
        else:
            desde_mes, hasta_mes = '', ''

    q_docs = (DocumentoSII.query
              .filter_by(empresa_id=eid, procesado=False)
              .filter(DocumentoSII.conciliacion_id == None))
    q_movs = (MovimientoBanco.query
              .filter_by(empresa_id=eid, procesado=False)
              .filter(MovimientoBanco.conciliacion_id == None))
    if desde_mes and hasta_mes:
        try:
            d_ini = _mes_a_fecha_inicio(desde_mes)
            d_fin = _mes_a_fecha_fin(hasta_mes)
            q_docs = q_docs.filter(DocumentoSII.fecha >= d_ini, DocumentoSII.fecha <= d_fin)
            q_movs = q_movs.filter(MovimientoBanco.fecha >= d_ini, MovimientoBanco.fecha <= d_fin)
        except ValueError:
            desde_mes = hasta_mes = ''

    docs = q_docs.order_by(DocumentoSII.tipo_libro, DocumentoSII.fecha).all()
    movs = q_movs.order_by(MovimientoBanco.fecha).all()
    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid, es_titulo=False, activa=True)
               .order_by(Cuenta.codigo)
               .all())

    # Documentos sin conciliar — para el selector que aparece al elegir cuenta
    docs_nc = (DocumentoSII.query
               .filter_by(empresa_id=eid)
               .filter(DocumentoSII.conciliacion_id == None)
               .order_by(DocumentoSII.fecha.desc())
               .all())

    def _fmt(d):
        return {
            'id': d.id,
            'folio': d.folio or '',
            'rs': (d.razon_social_contraparte or '')[:35],
            'rut': d.rut_contraparte or '',
            'total': int(d.total or 0),
            'fecha': d.fecha.strftime('%d/%m/%Y') if d.fecha else '',
            'tipo': d.tipo_libro,
        }

    docs_por_tipo = {
        'COMPRAS':    [_fmt(d) for d in docs_nc if d.tipo_libro == 'COMPRAS'],
        'VENTAS':     [_fmt(d) for d in docs_nc if d.tipo_libro == 'VENTAS'],
        'HONORARIOS': [_fmt(d) for d in docs_nc if d.tipo_libro == 'HONORARIOS'],
    }
    cuentas_map = {str(c.id): c.codigo for c in cuentas}

    return render_template('pendientes/index.html', empresa=empresa,
                           docs=docs, movs=movs, cuentas=cuentas,
                           desde_mes=desde_mes, hasta_mes=hasta_mes,
                           docs_por_tipo_json=json.dumps(docs_por_tipo),
                           cuentas_map_json=json.dumps(cuentas_map),
                           cuenta_codigo_tipo_json=json.dumps(CUENTA_CODIGO_TIPO))


@bp.route('/empresa/<int:eid>/pendientes/contabilizar-doc/<int:did>', methods=['POST'])
def contabilizar_doc(eid, did):
    doc = DocumentoSII.query.get_or_404(did)
    confirmar = request.form.get('confirmar') == '1'
    try:
        if doc.tipo_libro == 'COMPRAS':
            asiento = motor.generar_asiento_compra(doc)
        elif doc.tipo_libro == 'VENTAS':
            asiento = motor.generar_asiento_venta(doc)
        elif doc.tipo_libro == 'HONORARIOS':
            asiento = motor.generar_asiento_honorario(doc)
        else:
            flash('Tipo de libro desconocido', 'danger')
            return redirect(url_for('pendientes.index', eid=eid))

        if confirmar:
            try:
                confirmar_asiento(asiento)
            except ValueError as e:
                flash(f'Asiento N°{asiento.numero} creado pero no cuadra: {e}', 'warning')
                confirmar = False

        doc.procesado = True
        doc.asiento_id = asiento.id
        db.session.commit()
        msg = f'Asiento N°{asiento.numero} generado y confirmado' if confirmar else f'Asiento N°{asiento.numero} generado en borrador'
        flash(msg, 'success')
        return redirect(url_for('asientos.detalle', eid=eid, aid=asiento.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
        return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/contabilizar-banco/<int:mid>', methods=['POST'])
def contabilizar_banco(eid, mid):
    mov = MovimientoBanco.query.get_or_404(mid)
    cuenta_id = request.form.get('cuenta_id', type=int)
    confirmar = request.form.get('confirmar') == '1'
    doc_conciliar_id = request.form.get('doc_conciliar_id', type=int)
    if not cuenta_id:
        flash('Seleccione una cuenta contraparte', 'danger')
        return redirect(url_for('pendientes.index', eid=eid))
    try:
        asiento = motor.generar_asiento_banco(mov, cuenta_id)
        if confirmar:
            try:
                confirmar_asiento(asiento)
            except ValueError as e:
                flash(f'Asiento N°{asiento.numero} creado pero no cuadra: {e}', 'warning')
                confirmar = False
        mov.procesado = True
        mov.asiento_id = asiento.id

        # Vincular documento SII si se seleccionó
        doc_vinculado = None
        if doc_conciliar_id:
            doc_vinculado = DocumentoSII.query.get(doc_conciliar_id)
            if doc_vinculado and doc_vinculado.empresa_id == eid:
                conc = Conciliacion(
                    empresa_id=eid,
                    fecha=mov.fecha or date_.today(),
                    descripcion=f"{doc_vinculado.tipo_libro} {doc_vinculado.folio} "
                                f"{(doc_vinculado.razon_social_contraparte or '')[:40]}",
                    tipo='SII',
                )
                db.session.add(conc)
                db.session.flush()
                doc_vinculado.conciliacion_id = conc.id
                mov.conciliacion_id = conc.id

        db.session.commit()
        msg = f'Asiento N°{asiento.numero} {"confirmado" if confirmar else "en borrador"}'
        if doc_vinculado:
            msg += f' · conciliado con {doc_vinculado.tipo_libro} folio {doc_vinculado.folio}'
        flash(msg, 'success')
        return redirect(url_for('asientos.detalle', eid=eid, aid=asiento.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
        return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/eliminar-doc/<int:did>', methods=['POST'])
def eliminar_doc(eid, did):
    doc = DocumentoSII.query.get_or_404(did)
    db.session.delete(doc)
    db.session.commit()
    flash(f'Documento {doc.tipo_dte} folio {doc.folio} eliminado', 'success')
    return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/eliminar-banco/<int:mid>', methods=['POST'])
def eliminar_banco(eid, mid):
    mov = MovimientoBanco.query.get_or_404(mid)
    desc = (mov.descripcion or '')[:40]
    db.session.delete(mov)
    db.session.commit()
    flash(f'Movimiento "{desc}" eliminado', 'success')
    return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/contabilizar-lote', methods=['POST'])
def contabilizar_lote(eid):
    """Contabiliza los documentos SII pendientes visibles (respeta rango desde/hasta)."""
    desde_mes = request.form.get('desde', '')
    hasta_mes = request.form.get('hasta', '')
    q = (DocumentoSII.query
         .filter_by(empresa_id=eid, procesado=False)
         .filter(DocumentoSII.conciliacion_id == None))
    if desde_mes and hasta_mes:
        try:
            d_ini = _mes_a_fecha_inicio(desde_mes)
            d_fin = _mes_a_fecha_fin(hasta_mes)
            q = q.filter(DocumentoSII.fecha >= d_ini, DocumentoSII.fecha <= d_fin)
        except ValueError:
            pass
    docs = q.all()
    confirmar = request.form.get('confirmar') == '1'
    ok = 0
    errores = []
    for doc in docs:
        try:
            if doc.tipo_libro == 'COMPRAS':
                asiento = motor.generar_asiento_compra(doc)
            elif doc.tipo_libro == 'VENTAS':
                asiento = motor.generar_asiento_venta(doc)
            elif doc.tipo_libro == 'HONORARIOS':
                asiento = motor.generar_asiento_honorario(doc)
            else:
                continue
            if confirmar:
                try:
                    confirmar_asiento(asiento)
                except ValueError:
                    pass  # queda en borrador si no cuadra
            doc.procesado = True
            doc.asiento_id = asiento.id
            db.session.flush()
            ok += 1
        except Exception as e:
            db.session.rollback()
            errores.append(f"Doc {doc.folio}: {e}")

    if ok:
        db.session.commit()
    if ok:
        accion = 'generados y confirmados' if confirmar else 'generados en borrador'
        flash(f'{ok} asientos {accion}', 'success')
    for e in errores:
        flash(e, 'warning')
    return redirect(url_for('pendientes.index', eid=eid, desde=desde_mes, hasta=hasta_mes))
