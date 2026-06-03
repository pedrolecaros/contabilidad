"""
Asignador manual de respaldos: las empresas tienen una carpeta local con fotos
de boletas/facturas por mes (PXL_*.jpg típicos de celular). Esta vista lista
esas fotos y permite asignar cada una a un asiento contable existente
(la copia al storage del sistema y setea asiento.respaldo_url).
"""
import os
import shutil
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, current_app
from models import db, Empresa, Asiento
from storage import save_bytes

bp = Blueprint('respaldos_locales', __name__)


# Mapeo empresa_id → directorio raíz donde están las carpetas de respaldos por mes.
# Cada empresa apunta a "<base>/Respaldos <YYYY>" y dentro tiene "<N>. <Mes> <YYYY>".
DIRECTORIOS_RESPALDOS = {
    8: '/home/pedro/contabilidad/ejemplos/Contabilidades/07. Contable Agrícola Los Chilcos SpA',
}

MESES_ES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
            'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


def _dir_mes(empresa_id, periodo):
    """periodo = 'YYYY-MM' → ruta absoluta a la carpeta del mes (o None)."""
    base = DIRECTORIOS_RESPALDOS.get(empresa_id)
    if not base:
        return None
    anio, mes = periodo.split('-')
    mes_num = int(mes)
    return os.path.join(base, f'Respaldos {anio}', f'{mes_num}. {MESES_ES[mes_num]} {anio}')


def _listar_fotos(dir_path):
    """Devuelve lista de (nombre, ruta_absoluta) ordenada."""
    if not dir_path or not os.path.isdir(dir_path):
        return []
    EXTS = {'.jpg', '.jpeg', '.png', '.pdf'}
    fotos = []
    for f in sorted(os.listdir(dir_path)):
        if os.path.splitext(f)[1].lower() in EXTS:
            fotos.append((f, os.path.join(dir_path, f)))
    return fotos


@bp.route('/empresa/<int:eid>/respaldos-locales')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    hoy = date.today()
    periodo = request.args.get('periodo', f'{hoy.year}-{hoy.month-1:02d}' if hoy.month > 1 else f'{hoy.year-1}-12')

    dir_path = _dir_mes(eid, periodo)
    fotos = _listar_fotos(dir_path)
    fotos_no_asignadas = []
    fotos_asignadas = []
    for nombre, ruta in fotos:
        # Verificar si ya está asignada a algún asiento del mes
        marcador = f'local-orig:{nombre}'
        asi = Asiento.query.filter_by(empresa_id=eid).filter(Asiento.respaldo_url.like(f'%{marcador}%')).first()
        if asi:
            fotos_asignadas.append({'nombre': nombre, 'asiento': asi})
        else:
            fotos_no_asignadas.append({'nombre': nombre, 'ruta': ruta})

    # Asientos del mes (todos, no solo confirmados, para que pueda asignar borradores también)
    anio, mes = periodo.split('-')
    desde = date(int(anio), int(mes), 1)
    if int(mes) == 12:
        hasta = date(int(anio)+1, 1, 1)
    else:
        hasta = date(int(anio), int(mes)+1, 1)
    asientos = (Asiento.query
                .filter_by(empresa_id=eid)
                .filter(Asiento.fecha >= desde, Asiento.fecha < hasta)
                .filter(Asiento.estado != 'ANULADO')
                .order_by(Asiento.fecha, Asiento.numero).all())

    # Meses disponibles (carpetas que existen)
    meses = []
    if DIRECTORIOS_RESPALDOS.get(eid):
        base_resp = os.path.join(DIRECTORIOS_RESPALDOS[eid], f'Respaldos {hoy.year}')
        if os.path.isdir(base_resp):
            for d in sorted(os.listdir(base_resp)):
                # "4. Abril 2026"
                parts = d.split('.')
                if len(parts) >= 2 and parts[0].strip().isdigit():
                    n = int(parts[0].strip())
                    if 1 <= n <= 12:
                        meses.append(f'{hoy.year}-{n:02d}')

    return render_template('respaldos_locales.html',
        empresa=empresa, periodo=periodo, meses_disp=meses,
        fotos_no_asignadas=fotos_no_asignadas, fotos_asignadas=fotos_asignadas,
        asientos=asientos, dir_path=dir_path)


@bp.route('/empresa/<int:eid>/respaldos-locales/preview/<periodo>/<nombre>')
def preview(eid, periodo, nombre):
    """Sirve la foto local para preview."""
    dir_path = _dir_mes(eid, periodo)
    if not dir_path:
        return 'No configurado', 404
    full = os.path.join(dir_path, nombre)
    if not os.path.exists(full) or not os.path.commonpath([full, dir_path]) == dir_path:
        return 'No encontrado', 404
    return send_file(full)


@bp.route('/empresa/<int:eid>/respaldos-locales/asignar', methods=['POST'])
def asignar(eid):
    empresa = Empresa.query.get_or_404(eid)
    periodo = request.form.get('periodo', '').strip()
    nombre = request.form.get('nombre', '').strip()
    asiento_id = request.form.get('asiento_id', type=int)

    if not (nombre and asiento_id):
        flash('Faltan datos', 'danger')
        return redirect(url_for('respaldos_locales.index', eid=eid, periodo=periodo))

    asiento = Asiento.query.get_or_404(asiento_id)
    if asiento.empresa_id != eid:
        flash('Asiento no pertenece a la empresa', 'danger')
        return redirect(url_for('respaldos_locales.index', eid=eid, periodo=periodo))

    dir_path = _dir_mes(eid, periodo)
    src = os.path.join(dir_path or '', nombre)
    if not src or not os.path.exists(src):
        flash(f'No se encontró la foto {nombre}', 'danger')
        return redirect(url_for('respaldos_locales.index', eid=eid, periodo=periodo))

    # Copiar la foto al storage del sistema, conservando el nombre original como
    # parte del path para poder identificarla luego (marcador `local-orig:`).
    with open(src, 'rb') as f:
        data = f.read()
    rut_limpio = (empresa.rut or '').replace('.', '').replace('-', '')
    subfolder = f'backups_importacion/{rut_limpio}/RESPALDOS/{periodo}'
    storage_key = save_bytes(data, nombre, current_app.config['UPLOAD_FOLDER'], subfolder)
    # Marcador para poder reconocer cuál foto local generó este respaldo
    asiento.respaldo_url = storage_key + f'#local-orig:{nombre}'
    db.session.commit()
    flash(f'✓ {nombre} asignada al asiento #{asiento.numero}', 'success')
    return redirect(url_for('respaldos_locales.index', eid=eid, periodo=periodo))
