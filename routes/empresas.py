from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from models import db, Empresa
from database import sembrar_plan_cuentas, copiar_plan_cuentas
from utils.rut import normalizar_rut as _normalizar_rut, validar_rut_dv as _validar_rut_dv

bp = Blueprint('empresas', __name__)


def _guardar_logo(empresa_rut: str) -> str | None:
    """Si se subió logo_file, lo guarda y retorna el storage key. Si no, retorna None."""
    f = request.files.get('logo_file')
    if not f or not f.filename:
        return None
    from storage import save_attachment
    rut_limpio = empresa_rut.replace('.', '').replace('-', '')
    return save_attachment(f, f.filename,
                           current_app.config['UPLOAD_FOLDER'],
                           subfolder=f'{rut_limpio}/logo')


@bp.route('/empresas')
def lista():
    empresas = Empresa.query.order_by(Empresa.razon_social).all()
    return render_template('empresas/lista.html', empresas=empresas)


@bp.route('/empresas/nueva', methods=['GET', 'POST'])
def nueva():
    otras_empresas = Empresa.query.order_by(Empresa.razon_social).all()
    if request.method == 'POST':
        rut = _normalizar_rut(request.form['rut'])
        if not _validar_rut_dv(rut):
            flash(f'Advertencia: RUT {rut} tiene dígito verificador inválido', 'warning')
        if Empresa.query.filter_by(rut=rut).first():
            flash(f'Ya existe una empresa con RUT {rut}', 'danger')
            return render_template('empresas/form.html', empresa=None, otras_empresas=otras_empresas)

        part_str = request.form.get('participacion_ecox', '').strip()
        tasa_ppm_str = request.form.get('tasa_ppm', '1.0').strip()
        empresa = Empresa(
            rut=rut,
            razon_social=request.form['razon_social'].strip(),
            nombre_fantasia=request.form.get('nombre_fantasia', '').strip(),
            giro=request.form.get('giro', '').strip(),
            clave_sii=request.form.get('clave_sii', '').strip() or None,
            participacion_ecox=float(part_str) if part_str else None,
            contribuyente_iva='contribuyente_iva' in request.form,
            tasa_ppm=float(tasa_ppm_str) if tasa_ppm_str else 1.0,
            regimen=request.form.get('regimen', 'GENERAL'),
        )
        db.session.add(empresa)
        db.session.commit()
        logo_key = _guardar_logo(empresa.rut)
        if logo_key:
            empresa.logo_url = logo_key
            db.session.commit()
        origen_plan = request.form.get('origen_plan', '').strip()
        if origen_plan:
            copiar_plan_cuentas(int(origen_plan), empresa.id)
            flash(f'Empresa {empresa.razon_social} creada copiando plan de cuentas', 'success')
        else:
            sembrar_plan_cuentas(empresa.id)
            flash(f'Empresa {empresa.razon_social} creada con plan de cuentas PCGA', 'success')
        return redirect(url_for('main.index'))

    return render_template('empresas/form.html', empresa=None, otras_empresas=otras_empresas)


@bp.route('/empresa/<int:eid>/editar', methods=['GET', 'POST'])
def editar(eid):
    empresa = Empresa.query.get_or_404(eid)
    if request.method == 'POST':
        rut = _normalizar_rut(request.form['rut'])
        if not _validar_rut_dv(rut):
            flash(f'Advertencia: RUT {rut} tiene dígito verificador inválido', 'warning')
        empresa.rut = rut
        empresa.razon_social = request.form['razon_social'].strip()
        empresa.nombre_fantasia = request.form.get('nombre_fantasia', '').strip()
        empresa.giro = request.form.get('giro', '').strip()
        clave = request.form.get('clave_sii', '').strip()
        if clave:
            empresa.clave_sii = clave
        part_str = request.form.get('participacion_ecox', '').strip()
        empresa.participacion_ecox = float(part_str) if part_str else None
        empresa.contribuyente_iva = 'contribuyente_iva' in request.form
        empresa.activa = 'activa' in request.form
        tasa_ppm_str = request.form.get('tasa_ppm', '1.0').strip()
        empresa.tasa_ppm = float(tasa_ppm_str) if tasa_ppm_str else 1.0
        empresa.regimen = request.form.get('regimen', 'GENERAL')
        logo_key = _guardar_logo(empresa.rut)
        if logo_key:
            empresa.logo_url = logo_key
        db.session.commit()
        flash('Empresa actualizada', 'success')
        return redirect(url_for('main.index'))
    return render_template('empresas/form.html', empresa=empresa, otras_empresas=[])
