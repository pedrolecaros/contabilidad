import json
import calendar
from datetime import date as date_
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from models import db, Empresa, DocumentoSII, MovimientoBanco, Cuenta, Conciliacion, Asiento, LineaAsiento
from engine import asientos as motor
from engine.asientos import confirmar_asiento
from engine.plan_cuentas_default import CUENTAS_SISTEMA as _C

bp = Blueprint('pendientes', __name__)

# Cuentas que disparan selector de documento al contabilizar banco
CUENTA_CODIGO_TIPO = {
    _C['PROVEEDORES']:    ['COMPRAS', 'HONORARIOS'],
    _C['CLIENTES']:       ['VENTAS'],
    _C['RET_HONORARIOS']: ['HONORARIOS'],
}


def _default_rango():
    """Sin restricción por defecto: muestra todos los pendientes."""
    return '', ''


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
    cuentas_nat = {str(c.id): c.naturaleza for c in cuentas}  # DEUDORA | ACREEDORA

    # Contactos para el aux toggle: todos los globales, marcados los que ya tienen
    # actividad en esta empresa.
    from models import Contraparte
    ids_lineas_cp = {r[0] for r in db.session.query(LineaAsiento.contraparte_id)
                     .join(Asiento)
                     .filter(Asiento.empresa_id == eid,
                             LineaAsiento.contraparte_id != None).distinct().all()}
    ruts_docs_cp = {r[0] for r in db.session.query(DocumentoSII.rut_contraparte)
                    .filter(DocumentoSII.empresa_id == eid,
                            DocumentoSII.rut_contraparte != None).distinct().all()}
    ids_cp_docs = ({r[0] for r in db.session.query(Contraparte.id)
                    .filter(Contraparte.rut.in_(ruts_docs_cp)).all()}
                   if ruts_docs_cp else set())
    en_emp_cp = ids_lineas_cp | ids_cp_docs
    cps_all = Contraparte.query.filter_by(activo=True).order_by(Contraparte.razon_social).all()
    contrapartes_json = json.dumps([
        {'id': c.id, 'nombre': c.razon_social, 'en_empresa': c.id in en_emp_cp}
        for c in cps_all
    ], ensure_ascii=False)

    return render_template('pendientes/index.html', empresa=empresa,
                           docs=docs, movs=movs, cuentas=cuentas,
                           desde_mes=desde_mes, hasta_mes=hasta_mes,
                           docs_por_tipo_json=json.dumps(docs_por_tipo),
                           cuentas_map_json=json.dumps(cuentas_map),
                           cuentas_nat_json=json.dumps(cuentas_nat),
                           cuenta_codigo_tipo_json=json.dumps(CUENTA_CODIGO_TIPO),
                           contrapartes_json=contrapartes_json)


def _is_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _err(msg, eid):
    if _is_ajax():
        return jsonify({'ok': False, 'error': msg}), 400
    flash(msg, 'danger')
    return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/contabilizar-doc/<int:did>', methods=['POST'])
def contabilizar_doc(eid, did):
    # Atomic check-and-lock: only proceed if currently unprocessed
    rows = DocumentoSII.query.filter_by(id=did, procesado=False).update({'procesado': True})
    db.session.flush()
    if rows == 0:
        return _err('Este documento ya fue procesado.', eid)
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
            return _err('Tipo de libro desconocido.', eid)

        if confirmar:
            try:
                confirmar_asiento(asiento)
            except ValueError as e:
                flash(f'Asiento N°{asiento.numero} creado pero no cuadra: {e}', 'warning')
                confirmar = False

        doc.asiento_id = asiento.id
        db.session.commit()
        msg = f'Asiento N°{asiento.numero} generado y confirmado' if confirmar else f'Asiento N°{asiento.numero} generado en borrador'
        flash(msg, 'success')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            lineas = [{'cuenta': f"{l.cuenta.codigo} {l.cuenta.nombre}",
                       'debe': int(l.debe or 0), 'haber': int(l.haber or 0),
                       'descripcion': l.descripcion or ''}
                      for l in asiento.lineas]
            return jsonify({'ok': True, 'numero': asiento.numero, 'msg': msg,
                            'url': url_for('asientos.detalle', eid=eid, aid=asiento.id),
                            'edit_url': url_for('asientos.editar', eid=eid, aid=asiento.id),
                            'respaldo_post_url': url_for('asientos.subir_respaldo', eid=eid, aid=asiento.id),
                            'lineas': lineas})
        return redirect(url_for('asientos.detalle', eid=eid, aid=asiento.id))
    except Exception as e:
        db.session.rollback()
        # Undo the atomic lock so the document can be retried
        DocumentoSII.query.filter_by(id=did).update({'procesado': False})
        db.session.commit()
        return _err(str(e), eid)


@bp.route('/empresa/<int:eid>/pendientes/contabilizar-banco/<int:mid>', methods=['POST'])
def contabilizar_banco(eid, mid):
    # Atomic check-and-lock: only proceed if currently unprocessed
    rows = MovimientoBanco.query.filter_by(id=mid, procesado=False).update({'procesado': True})
    db.session.flush()
    if rows == 0:
        return _err('Este movimiento ya fue procesado.', eid)
    mov = MovimientoBanco.query.get_or_404(mid)
    cuenta_id = request.form.get('cuenta_id', type=int)
    contraparte_id = request.form.get('contraparte_id', type=int)
    confirmar = request.form.get('confirmar') == '1'
    doc_conciliar_id = request.form.get('doc_conciliar_id', type=int)
    if not cuenta_id:
        MovimientoBanco.query.filter_by(id=mid).update({'procesado': False})
        db.session.commit()
        return _err('Seleccione una cuenta contraparte.', eid)
    try:
        asiento = motor.generar_asiento_banco(mov, cuenta_id, contraparte_id)
        if confirmar:
            try:
                confirmar_asiento(asiento)
            except ValueError as e:
                flash(f'Asiento N°{asiento.numero} creado pero no cuadra: {e}', 'warning')
                confirmar = False
        mov.asiento_id = asiento.id

        # Vincular documento SII si se seleccionó
        doc_vinculado = None
        if doc_conciliar_id:
            doc_vinculado = DocumentoSII.query.get(doc_conciliar_id)
            if doc_vinculado and doc_vinculado.empresa_id == eid:
                # Generar asiento SII del doc si aún no lo tiene
                if not doc_vinculado.procesado:
                    try:
                        if doc_vinculado.tipo_libro == 'COMPRAS':
                            asiento_doc = motor.generar_asiento_compra(doc_vinculado)
                        elif doc_vinculado.tipo_libro == 'VENTAS':
                            asiento_doc = motor.generar_asiento_venta(doc_vinculado)
                        elif doc_vinculado.tipo_libro == 'HONORARIOS':
                            asiento_doc = motor.generar_asiento_honorario(doc_vinculado)
                        else:
                            asiento_doc = None
                        if asiento_doc:
                            if confirmar:
                                try:
                                    confirmar_asiento(asiento_doc)
                                except ValueError:
                                    pass
                            doc_vinculado.procesado = True
                            doc_vinculado.asiento_id = asiento_doc.id
                    except Exception:
                        pass  # si falla el asiento SII no bloqueamos el banco

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
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            lineas = [{'cuenta': f"{l.cuenta.codigo} {l.cuenta.nombre}",
                       'debe': int(l.debe or 0), 'haber': int(l.haber or 0),
                       'descripcion': l.descripcion or ''}
                      for l in asiento.lineas]
            return jsonify({'ok': True, 'numero': asiento.numero, 'msg': msg,
                            'url': url_for('asientos.detalle', eid=eid, aid=asiento.id),
                            'edit_url': url_for('asientos.editar', eid=eid, aid=asiento.id),
                            'respaldo_post_url': url_for('asientos.subir_respaldo', eid=eid, aid=asiento.id),
                            'lineas': lineas})
        return redirect(url_for('asientos.detalle', eid=eid, aid=asiento.id))
    except Exception as e:
        db.session.rollback()
        # Undo the atomic lock so the movement can be retried
        MovimientoBanco.query.filter_by(id=mid).update({'procesado': False})
        db.session.commit()
        return _err(str(e), eid)


@bp.route('/empresa/<int:eid>/pendientes/eliminar-doc/<int:did>', methods=['POST'])
def eliminar_doc(eid, did):
    doc = DocumentoSII.query.get_or_404(did)
    tipo_dte = doc.tipo_dte
    folio = doc.folio
    from routes.papelera import enviar_papelera, _ser_documento_sii
    enviar_papelera(
        'DOCUMENTO_SII', doc.id, doc.empresa_id,
        f'{doc.tipo_libro} – {doc.razon_social_contraparte or ""} – {doc.fecha or ""}',
        _ser_documento_sii(doc)
    )
    db.session.delete(doc)
    db.session.commit()
    flash(f'Documento {tipo_dte} folio {folio} eliminado', 'success')
    return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/eliminar-banco/<int:mid>', methods=['POST'])
def eliminar_banco(eid, mid):
    mov = MovimientoBanco.query.get_or_404(mid)
    desc = (mov.descripcion or '')[:40]
    from routes.papelera import enviar_papelera, _ser_movimiento_banco
    enviar_papelera(
        'MOVIMIENTO_BANCO', mov.id, mov.empresa_id,
        f'Banco – {mov.descripcion or ""} – {mov.fecha or ""}',
        _ser_movimiento_banco(mov)
    )
    db.session.delete(mov)
    db.session.commit()
    flash(f'Movimiento "{desc}" eliminado', 'success')
    return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/contabilizar-banco-lote', methods=['POST'])
def contabilizar_banco_lote(eid):
    empresa = Empresa.query.get_or_404(eid)
    mov_ids = request.form.getlist('mov_ids[]')
    cuenta_id = request.form.get('cuenta_id_lote', '').strip()
    if not mov_ids or not cuenta_id:
        flash('Selecciona movimientos y una cuenta contable.', 'warning')
        return redirect(url_for('pendientes.index', eid=eid))
    cuenta = Cuenta.query.filter_by(id=int(cuenta_id), empresa_id=eid, activa=True).first()
    if not cuenta:
        flash('Cuenta no encontrada.', 'danger')
        return redirect(url_for('pendientes.index', eid=eid))
    procesados = 0
    for mid in mov_ids:
        mov = MovimientoBanco.query.get(int(mid))
        if not mov or mov.empresa_id != eid or mov.procesado:
            continue
        try:
            asiento = motor.generar_asiento_banco(mov, cuenta.id)
            confirmar_asiento(asiento)
            mov.procesado = True
            mov.asiento_id = asiento.id
            db.session.flush()
            procesados += 1
        except Exception:
            db.session.rollback()
    db.session.commit()
    flash(f'{procesados} movimiento(s) contabilizados.', 'success')
    return redirect(url_for('pendientes.index', eid=eid))


@bp.route('/empresa/<int:eid>/pendientes/contabilizar-lote', methods=['POST'])
def contabilizar_lote(eid):
    """Contabiliza documentos SII pendientes. Si vienen `doc_ids[]` solo procesa esos,
    de lo contrario procesa todos los visibles del rango desde/hasta."""
    desde_mes = request.form.get('desde', '')
    hasta_mes = request.form.get('hasta', '')
    doc_ids = request.form.getlist('doc_ids', type=int)
    q = (DocumentoSII.query
         .filter_by(empresa_id=eid, procesado=False)
         .filter(DocumentoSII.conciliacion_id == None))
    if doc_ids:
        q = q.filter(DocumentoSII.id.in_(doc_ids))
    elif desde_mes and hasta_mes:
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
