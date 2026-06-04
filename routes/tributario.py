import json
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from models import db, Empresa, Asiento, LineaAsiento, Cuenta, DeclaracionF29, DeclaracionF22
from sqlalchemy import func

bp = Blueprint('tributario', __name__)


def _mes_anterior(hoy=None):
    hoy = hoy or date.today()
    if hoy.month == 1:
        return f'{hoy.year - 1}-12'
    return f'{hoy.year}-{hoy.month - 1:02d}'


# ── F29 mensual ────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/f29')
def f29_lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    declaraciones = (DeclaracionF29.query
                     .filter_by(empresa_id=eid)
                     .order_by(DeclaracionF29.periodo.desc())
                     .all())
    return render_template('tributario/f29_lista.html',
                           empresa=empresa,
                           declaraciones=declaraciones,
                           mes_anterior=_mes_anterior())


@bp.route('/empresa/<int:eid>/f29/descargar', methods=['POST'])
def f29_descargar(eid):
    empresa = Empresa.query.get_or_404(eid)
    periodo = request.form.get('periodo', _mes_anterior()).strip()

    if not empresa.clave_sii:
        flash('La empresa no tiene clave SII configurada (editar empresa)', 'danger')
        return redirect(url_for('tributario.f29_lista', eid=eid))

    DeclaracionF29.query.filter_by(empresa_id=eid, periodo=periodo).delete()
    db.session.commit()

    from importers import sii_f29
    try:
        codigos, folio, html = sii_f29.descargar_f29(empresa.rut, empresa.clave_sii, periodo)
    except sii_f29.F29NotFoundError as e:
        flash(str(e), 'warning')
        return redirect(url_for('tributario.f29_lista', eid=eid))
    except sii_f29.F29DownloadError as e:
        flash(f'Error descargando F29: {e}', 'danger')
        return redirect(url_for('tributario.f29_lista', eid=eid))
    except Exception as e:
        flash(f'Error inesperado: {e}', 'danger')
        return redirect(url_for('tributario.f29_lista', eid=eid))

    f29 = DeclaracionF29(
        empresa_id=eid,
        periodo=periodo,
        folio=folio or None,
        fecha_descarga=datetime.now(),
        codigo_89  = codigos.get('89',  0.0),
        codigo_39  = codigos.get('39',  0.0),
        codigo_151 = codigos.get('151', 0.0),
        codigo_538 = codigos.get('538', 0.0),
        codigo_547 = codigos.get('547', 0.0),
        codigo_91  = codigos.get('91',  0.0),
        codigo_92  = codigos.get('92',  0.0),
        codigos_json=json.dumps(codigos),
    )
    db.session.add(f29)
    db.session.commit()
    flash(f'F29 {periodo} descargado: {len(codigos)} códigos parseados', 'success')
    return redirect(url_for('tributario.f29_lista', eid=eid))


@bp.route('/empresa/<int:eid>/f29/subir', methods=['POST'])
def f29_subir(eid):
    empresa = Empresa.query.get_or_404(eid)
    archivos = request.files.getlist('archivos') or request.files.getlist('archivo')
    archivos = [a for a in archivos if a and a.filename]
    if not archivos:
        flash('Seleccioná uno o varios PDFs del F29', 'warning')
        return redirect(url_for('tributario.f29_lista', eid=eid))

    from importers import sii_f29
    from importers.sii_f29 import _normalizar_rut
    from storage import save_import_backup
    ok, errores = [], []
    ultimo_periodo = None
    rut_empresa = _normalizar_rut(empresa.rut)

    for archivo in archivos:
        nombre = archivo.filename
        if not nombre.lower().endswith('.pdf'):
            errores.append(f'{nombre}: no es PDF')
            continue
        bytes_pdf = archivo.read()
        try:
            codigos, periodo_detectado, folio, rut_pdf = sii_f29.parsear_pdf(bytes_pdf)
        except sii_f29.F29ParseError as e:
            errores.append(f'{nombre}: {e}')
            continue

        # Validar que el RUT del PDF coincida con el de la empresa
        if rut_empresa and rut_pdf:
            if _normalizar_rut(rut_pdf) != rut_empresa:
                errores.append(
                    f'{nombre}: RUT del PDF ({rut_pdf}) no coincide con el de '
                    f'{empresa.razon_social} ({empresa.rut}) — no se importó'
                )
                continue
        elif rut_empresa and not rut_pdf:
            errores.append(
                f'{nombre}: no se pudo leer el RUT del PDF — '
                f'verificá que sea de {empresa.razon_social} ({empresa.rut})'
            )
            continue

        periodo = (request.form.get('periodo') or periodo_detectado or '').strip()
        if not periodo:
            errores.append(f'{nombre}: no se detectó el período (renombrá el PDF con YYYY-MM)')
            continue

        # Guardar PDF como respaldo en backups_importacion/<rut>/F29/<periodo>/
        respaldo_url = None
        try:
            rel = save_import_backup(
                bytes_pdf, nombre,
                current_app.config['UPLOAD_FOLDER'],
                empresa.rut, 'F29', periodo,
            )
            respaldo_url = f'local:{rel}'
        except Exception as e:
            errores.append(f'{nombre}: aviso — no se pudo guardar respaldo ({e})')

        DeclaracionF29.query.filter_by(empresa_id=eid, periodo=periodo).delete()
        f29 = DeclaracionF29(
            empresa_id=eid,
            periodo=periodo,
            folio=folio or None,
            fecha_descarga=datetime.now(),
            codigo_62  = codigos.get('62',  0.0),
            codigo_48  = codigos.get('48',  0.0),
            codigo_39  = codigos.get('39',  0.0),
            codigo_151 = codigos.get('151', 0.0),
            codigo_89  = codigos.get('89',  0.0),
            codigo_538 = codigos.get('538', 0.0),
            codigo_537 = codigos.get('537', 0.0),
            codigo_547 = codigos.get('547', 0.0),
            codigo_91  = codigos.get('91',  0.0),
            codigo_92  = codigos.get('92',  0.0),
            codigos_json=json.dumps(codigos),
            respaldo_url=respaldo_url,
        )
        db.session.add(f29)
        ok.append(f'{periodo} ({len(codigos)} códigos)')
        ultimo_periodo = periodo

    db.session.commit()

    if ok:
        flash(f'F29 importados: {", ".join(ok)}', 'success')
    for e in errores:
        flash(e, 'danger')

    if len(ok) == 1 and ultimo_periodo:
        return redirect(url_for('tributario.f29_detalle', eid=eid, periodo=ultimo_periodo))
    return redirect(url_for('tributario.f29_lista', eid=eid))


@bp.route('/empresa/<int:eid>/f29/<periodo>')
def f29_detalle(eid, periodo):
    empresa = Empresa.query.get_or_404(eid)
    f29 = DeclaracionF29.query.filter_by(empresa_id=eid, periodo=periodo).first_or_404()
    try:
        todos = json.loads(f29.codigos_json or '{}')
    except json.JSONDecodeError:
        todos = {}
    return render_template('tributario/f29_detalle.html',
                           empresa=empresa, f29=f29, todos=todos)


# ── F22 anual ──────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/f22')
def f22_lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    declaraciones = (DeclaracionF22.query
                     .filter_by(empresa_id=eid)
                     .order_by(DeclaracionF22.anio.desc())
                     .all())
    return render_template('tributario/f22_lista.html',
                           empresa=empresa,
                           declaraciones=declaraciones,
                           anio_default=date.today().year)


@bp.route('/empresa/<int:eid>/f22/subir', methods=['POST'])
def f22_subir(eid):
    empresa = Empresa.query.get_or_404(eid)
    archivos = request.files.getlist('archivos') or request.files.getlist('archivo')
    archivos = [a for a in archivos if a and a.filename]
    if not archivos:
        flash('Seleccioná uno o varios PDFs del F22', 'warning')
        return redirect(url_for('tributario.f22_lista', eid=eid))

    from importers import sii_f22
    from importers.sii_f29 import _normalizar_rut
    from storage import save_import_backup
    ok, errores = [], []
    ultimo_anio = None
    rut_empresa = _normalizar_rut(empresa.rut)

    for archivo in archivos:
        nombre = archivo.filename
        if not nombre.lower().endswith('.pdf'):
            errores.append(f'{nombre}: no es PDF')
            continue
        bytes_pdf = archivo.read()
        try:
            codigos, anio_detectado, folio, rut_pdf = sii_f22.parsear_pdf(bytes_pdf)
        except sii_f22.F22ParseError as e:
            errores.append(f'{nombre}: {e}')
            continue

        if rut_empresa and rut_pdf and _normalizar_rut(rut_pdf) != rut_empresa:
            errores.append(
                f'{nombre}: RUT del PDF ({rut_pdf}) no coincide con el de '
                f'{empresa.razon_social} ({empresa.rut}) — no se importó'
            )
            continue
        elif rut_empresa and not rut_pdf:
            errores.append(
                f'{nombre}: no se pudo leer el RUT del PDF — '
                f'verificá que sea de {empresa.razon_social}'
            )
            continue

        anio = anio_detectado or (int(request.form.get('anio') or 0) or None)
        if not anio:
            errores.append(f'{nombre}: no se detectó el año tributario (AT)')
            continue

        respaldo_url = None
        try:
            rel = save_import_backup(
                bytes_pdf, nombre,
                current_app.config['UPLOAD_FOLDER'],
                empresa.rut, 'F22', str(anio),
            )
            respaldo_url = f'local:{rel}'
        except Exception as e:
            errores.append(f'{nombre}: aviso — no se pudo guardar respaldo ({e})')

        DeclaracionF22.query.filter_by(empresa_id=eid, anio=anio).delete()
        f22 = DeclaracionF22(
            empresa_id=eid,
            anio=anio,
            folio=folio or None,
            fecha_descarga=datetime.now(),
            codigo_1440 = codigos.get('1440', 0.0),
            codigo_643 = codigos.get('643', 0.0),
            codigo_1513 = codigos.get('1513', 0.0),
            codigo_90  = codigos.get('90',  0.0),
            codigo_91  = codigos.get('91',  0.0),
            codigo_94  = codigos.get('94',  0.0),
            codigos_json=json.dumps(codigos),
            respaldo_url=respaldo_url,
        )
        db.session.add(f22)
        ok.append(f'AT {anio} ({len(codigos)} códigos)')
        ultimo_anio = anio

    db.session.commit()

    if ok:
        flash(f'F22 importados: {", ".join(ok)}', 'success')
    for e in errores:
        flash(e, 'danger')

    if len(ok) == 1 and ultimo_anio:
        return redirect(url_for('tributario.f22_detalle', eid=eid, anio=ultimo_anio))
    return redirect(url_for('tributario.f22_lista', eid=eid))


@bp.route('/empresa/<int:eid>/f22/<int:anio>')
def f22_detalle(eid, anio):
    empresa = Empresa.query.get_or_404(eid)
    f22 = DeclaracionF22.query.filter_by(empresa_id=eid, anio=anio).first_or_404()
    try:
        todos = json.loads(f22.codigos_json or '{}')
    except json.JSONDecodeError:
        todos = {}
    return render_template('tributario/f22_detalle.html',
                           empresa=empresa, f22=f22, todos=todos)


@bp.route('/empresa/<int:eid>/f29/<periodo>/debug')
def f29_debug(eid, periodo):
    """Muestra texto crudo del PDF junto con el match exacto que generó cada código."""
    empresa = Empresa.query.get_or_404(eid)
    f29 = DeclaracionF29.query.filter_by(empresa_id=eid, periodo=periodo).first_or_404()
    if not f29.respaldo_url or not f29.respaldo_url.startswith('local:'):
        flash('Este F29 no tiene PDF de respaldo guardado', 'warning')
        return redirect(url_for('tributario.f29_detalle', eid=eid, periodo=periodo))

    import os
    rel = f29.respaldo_url[6:]
    full = os.path.join(current_app.config['UPLOAD_FOLDER'], rel)
    if not os.path.exists(full):
        flash('No se encuentra el PDF en disco', 'danger')
        return redirect(url_for('tributario.f29_detalle', eid=eid, periodo=periodo))

    with open(full, 'rb') as f:
        bs = f.read()
    from importers.sii_f29 import _extraer_texto_pdf
    texto = _extraer_texto_pdf(bs)
    try:
        todos = json.loads(f29.codigos_json or '{}')
    except json.JSONDecodeError:
        todos = {}

    # Para cada código, buscar la línea del texto donde aparece
    contexto = {}
    for cod in todos.keys():
        for linea in texto.split('\n'):
            stripped = linea.strip()
            if stripped.startswith(cod + ' ') or stripped.startswith('0' + cod + ' ') or (' ' + cod + ' ') in linea:
                contexto[cod] = linea.rstrip()
                break

    return render_template('tributario/f29_debug.html',
                           empresa=empresa, f29=f29,
                           todos=todos, contexto=contexto, texto=texto)


# ── Renta Líquida Imponible ────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/tributario/rli-pyme')
def rli_pyme(eid):
    """RLI PYME (régimen 14D / Pro-PYME): base caja real, no devengado.
    Recorre cargos/abonos de Banco+Caja del año y los clasifica:
    - Egresos PYME: pagos a proveedores, sueldos, leasing capital+interés, compra activo, impuestos, TC.
      Excluye: capital crédito directo (devolución pasivo), aportes a FFMM, transferencias entre cuentas propias.
    - Ingresos PYME: cobranzas a clientes, otros ingresos reales.
      Excluye: préstamos recibidos, rescates FFMM, aportes capital.
    Considera pérdida tributaria de arrastre del F22 año anterior (cód 1440).
    """
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    anio = int(request.args.get('anio', hoy.year))
    desde = date(anio, 1, 1)
    hasta = date(anio, 12, 31)

    # Cuentas relevantes
    cuentas_banco_caja = ['1.1.01', '1.1.02']  # caja + banco corriente
    cuenta_ids = {c.codigo: c.id for c in Cuenta.query.filter_by(empresa_id=eid).all()}

    # Recorrer todas las líneas de Banco+Caja del año en asientos confirmados.
    # Para cada línea, traer todas las otras líneas del mismo asiento para clasificar.
    movs = (db.session.query(LineaAsiento, Asiento)
            .join(Asiento, Asiento.id == LineaAsiento.asiento_id)
            .join(Cuenta, Cuenta.id == LineaAsiento.cuenta_id)
            .filter(
                Asiento.empresa_id == eid,
                Asiento.estado == 'CONFIRMADO',
                Asiento.fecha >= desde,
                Asiento.fecha <= hasta,
                Cuenta.codigo.in_(cuentas_banco_caja),
            ).all())

    # Cache de líneas por asiento
    asiento_ids = list({m[1].id for m in movs})
    todas_lineas = (db.session.query(LineaAsiento, Cuenta)
                    .join(Cuenta, Cuenta.id == LineaAsiento.cuenta_id)
                    .filter(LineaAsiento.asiento_id.in_(asiento_ids)).all()) if asiento_ids else []
    lineas_x_asiento = {}
    for la, c in todas_lineas:
        lineas_x_asiento.setdefault(la.asiento_id, []).append((la, c))

    egresos_total = 0.0
    ingresos_total = 0.0
    egresos_detalle = []  # {fecha, asiento, monto, categoria, glosa}
    ingresos_detalle = []
    excluidos = []  # movs que NO son ingreso/egreso PYME (informativo)

    for la_bk, asiento in movs:
        haber = float(la_bk.haber or 0)  # salida del banco
        debe  = float(la_bk.debe or 0)   # entrada al banco
        otras = [(l, c) for l, c in lineas_x_asiento.get(asiento.id, []) if l.id != la_bk.id]

        # Helper: detectar si una línea es restitución de pasivo (cod 2.1.11 con débito = capital crédito directo)
        # o aporte/rescate FFMM (1.1.09) o transferencia caja↔banco (1.1.01 o 1.1.02)
        if haber > 0:
            # SALIDA de banco/caja → candidato a EGRESO
            cat_excluida = None
            for la_o, c_o in otras:
                if c_o.codigo == '2.1.11' and (la_o.debe or 0) > 0:
                    cat_excluida = 'Devolución capital crédito/préstamo (no es egreso PYME)'
                    break
                if c_o.codigo == '1.1.09' and (la_o.debe or 0) > 0:
                    cat_excluida = 'Aporte a Fondos Mutuos (traslado, no egreso)'
                    break
                if c_o.codigo in cuentas_banco_caja and (la_o.debe or 0) > 0:
                    cat_excluida = 'Transferencia entre cuentas propias'
                    break
            if cat_excluida:
                excluidos.append({'fecha': asiento.fecha, 'asiento_num': asiento.numero,
                                  'monto': haber, 'categoria': cat_excluida,
                                  'glosa': asiento.descripcion[:80]})
                continue
            # Categorizar el egreso por la cuenta destino principal
            principal = max((l for l, c in otras if (l.debe or 0) > 0),
                            key=lambda l: l.debe or 0, default=None)
            if principal:
                cta_p = next((c for l, c in otras if l.id == principal.id), None)
                cat = f'{cta_p.codigo} {cta_p.nombre}' if cta_p else 'Otros'
            else:
                cat = 'Otros'
            egresos_total += haber
            egresos_detalle.append({'fecha': asiento.fecha, 'asiento_num': asiento.numero,
                                    'monto': haber, 'categoria': cat,
                                    'glosa': asiento.descripcion[:80]})
        elif debe > 0:
            # ENTRADA al banco/caja → candidato a INGRESO
            cat_excluida = None
            for la_o, c_o in otras:
                if c_o.codigo == '2.1.11' and (la_o.haber or 0) > 0:
                    cat_excluida = 'Préstamo recibido (no es ingreso PYME)'
                    break
                if c_o.codigo == '1.1.09' and (la_o.haber or 0) > 0:
                    cat_excluida = 'Rescate Fondos Mutuos (traslado, no ingreso)'
                    break
                if c_o.codigo in ('3.1.01',) and (la_o.haber or 0) > 0:
                    cat_excluida = 'Aporte de capital (no es ingreso operacional)'
                    break
                if c_o.codigo in cuentas_banco_caja and (la_o.haber or 0) > 0:
                    cat_excluida = 'Transferencia entre cuentas propias'
                    break
            if cat_excluida:
                excluidos.append({'fecha': asiento.fecha, 'asiento_num': asiento.numero,
                                  'monto': debe, 'categoria': cat_excluida,
                                  'glosa': asiento.descripcion[:80]})
                continue
            principal = max((l for l, c in otras if (l.haber or 0) > 0),
                            key=lambda l: l.haber or 0, default=None)
            if principal:
                cta_p = next((c for l, c in otras if l.id == principal.id), None)
                cat = f'{cta_p.codigo} {cta_p.nombre}' if cta_p else 'Otros'
            else:
                cat = 'Otros'
            ingresos_total += debe
            ingresos_detalle.append({'fecha': asiento.fecha, 'asiento_num': asiento.numero,
                                     'monto': debe, 'categoria': cat,
                                     'glosa': asiento.descripcion[:80]})

    rli_anio = ingresos_total - egresos_total

    # Pérdida arrastre del F22 año anterior (cód 1440 negativo = pérdida disponible)
    f22_ant = DeclaracionF22.query.filter_by(empresa_id=eid, anio=anio).first()
    perdida_arrastre = 0.0
    f22_origen = None
    if f22_ant and f22_ant.codigo_1440:
        # En F22 cód 1440 negativo = pérdida arrastrada disponible
        if f22_ant.codigo_1440 < 0:
            perdida_arrastre = abs(f22_ant.codigo_1440)
            f22_origen = f'F22 AT {anio} cód 1440'

    rli_imponible = max(0.0, rli_anio - perdida_arrastre)
    perdida_consumida = min(perdida_arrastre, max(0.0, rli_anio))
    perdida_remanente = perdida_arrastre - perdida_consumida
    if rli_anio < 0:
        perdida_remanente += abs(rli_anio)

    # Resumen por categoría
    from collections import defaultdict
    cat_egresos = defaultdict(float)
    for e in egresos_detalle:
        cat_egresos[e['categoria']] += e['monto']
    cat_ingresos = defaultdict(float)
    for e in ingresos_detalle:
        cat_ingresos[e['categoria']] += e['monto']

    return render_template('tributario/rli_pyme.html',
        empresa=empresa, anio=anio,
        ingresos_total=ingresos_total, egresos_total=egresos_total,
        rli_anio=rli_anio,
        perdida_arrastre=perdida_arrastre, f22_origen=f22_origen,
        perdida_consumida=perdida_consumida, perdida_remanente=perdida_remanente,
        rli_imponible=rli_imponible,
        cat_egresos=sorted(cat_egresos.items(), key=lambda x: -x[1]),
        cat_ingresos=sorted(cat_ingresos.items(), key=lambda x: -x[1]),
        egresos_detalle=sorted(egresos_detalle, key=lambda x: x['fecha']),
        ingresos_detalle=sorted(ingresos_detalle, key=lambda x: x['fecha']),
        excluidos=sorted(excluidos, key=lambda x: x['fecha']),
    )


@bp.route('/empresa/<int:eid>/tributario/rli')
def rli(eid):
    """RLI — redirige al cálculo correcto según régimen tributario de la empresa.
    PYME → base caja (rli_pyme). General → cálculo contable (rli_contable interno).
    """
    empresa = Empresa.query.get_or_404(eid)
    if empresa.regimen == 'PYME':
        return redirect(url_for('tributario.rli_pyme', eid=eid, **request.args))
    return _rli_contable(eid)


def _rli_contable(eid):
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

