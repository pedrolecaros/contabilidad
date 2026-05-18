from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa
from database import sembrar_plan_cuentas, copiar_plan_cuentas

bp = Blueprint('empresas', __name__)


def _normalizar_rut(rut: str) -> str:
    """Convierte cualquier formato de RUT chileno a XX.XXX.XXX-X."""
    rut = rut.strip().upper().replace('.', '').replace(' ', '')
    if not rut:
        return rut
    if '-' in rut:
        body, dv = rut.rsplit('-', 1)
    else:
        body, dv = rut[:-1], rut[-1]
    body = body.lstrip('0') or '0'
    # Insertar puntos cada 3 dígitos desde la derecha
    formatted = ''
    for i, c in enumerate(reversed(body)):
        if i > 0 and i % 3 == 0:
            formatted = '.' + formatted
        formatted = c + formatted
    return f'{formatted}-{dv}'


def _validar_rut_dv(rut: str) -> bool:
    """Valida el dígito verificador de un RUT chileno usando módulo 11.
    Retorna True si es válido, False si no lo es."""
    try:
        rut_clean = rut.strip().upper().replace('.', '').replace(' ', '')
        if not rut_clean:
            return True  # vacío, no validar
        if '-' in rut_clean:
            body, dv = rut_clean.rsplit('-', 1)
        else:
            body, dv = rut_clean[:-1], rut_clean[-1]
        body = body.lstrip('0') or '0'
        if not body.isdigit():
            return False
        digits = [int(c) for c in body]
        factors = [2, 3, 4, 5, 6, 7]
        total = 0
        for i, d in enumerate(reversed(digits)):
            total += d * factors[i % 6]
        remainder = 11 - (total % 11)
        if remainder == 11:
            expected = '0'
        elif remainder == 10:
            expected = 'K'
        else:
            expected = str(remainder)
        return dv == expected
    except Exception:
        return True  # en caso de error, no bloquear


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
            logo_url=request.form.get('logo_url', '').strip() or None,
        )
        db.session.add(empresa)
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
        empresa.logo_url = request.form.get('logo_url', '').strip() or None
        db.session.commit()
        flash('Empresa actualizada', 'success')
        return redirect(url_for('main.index'))
    return render_template('empresas/form.html', empresa=empresa, otras_empresas=[])
