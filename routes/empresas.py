from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa
from database import sembrar_plan_cuentas

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


@bp.route('/empresas')
def lista():
    empresas = Empresa.query.order_by(Empresa.razon_social).all()
    return render_template('empresas/lista.html', empresas=empresas)


@bp.route('/empresas/nueva', methods=['GET', 'POST'])
def nueva():
    if request.method == 'POST':
        rut = _normalizar_rut(request.form['rut'])
        if Empresa.query.filter_by(rut=rut).first():
            flash(f'Ya existe una empresa con RUT {rut}', 'danger')
            return render_template('empresas/form.html', empresa=None)

        part_str = request.form.get('participacion_ecox', '').strip()
        empresa = Empresa(
            rut=rut,
            razon_social=request.form['razon_social'].strip(),
            nombre_fantasia=request.form.get('nombre_fantasia', '').strip(),
            giro=request.form.get('giro', '').strip(),
            clave_sii=request.form.get('clave_sii', '').strip() or None,
            participacion_ecox=float(part_str) if part_str else None,
            tipo_participacion=request.form.get('tipo_participacion', '').strip() or None,
        )
        db.session.add(empresa)
        db.session.commit()
        sembrar_plan_cuentas(empresa.id)
        flash(f'Empresa {empresa.razon_social} creada con plan de cuentas PCGA', 'success')
        return redirect(url_for('main.index'))

    return render_template('empresas/form.html', empresa=None)


@bp.route('/empresa/<int:eid>/editar', methods=['GET', 'POST'])
def editar(eid):
    empresa = Empresa.query.get_or_404(eid)
    if request.method == 'POST':
        empresa.rut = _normalizar_rut(request.form['rut'])
        empresa.razon_social = request.form['razon_social'].strip()
        empresa.nombre_fantasia = request.form.get('nombre_fantasia', '').strip()
        empresa.giro = request.form.get('giro', '').strip()
        clave = request.form.get('clave_sii', '').strip()
        if clave:
            empresa.clave_sii = clave
        part_str = request.form.get('participacion_ecox', '').strip()
        empresa.participacion_ecox = float(part_str) if part_str else None
        empresa.tipo_participacion = request.form.get('tipo_participacion', '').strip() or None
        db.session.commit()
        flash('Empresa actualizada', 'success')
        return redirect(url_for('main.index'))
    return render_template('empresas/form.html', empresa=empresa)
