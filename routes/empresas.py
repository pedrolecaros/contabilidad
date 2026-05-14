from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa
from database import sembrar_plan_cuentas

bp = Blueprint('empresas', __name__)


@bp.route('/empresas')
def lista():
    empresas = Empresa.query.order_by(Empresa.razon_social).all()
    return render_template('empresas/lista.html', empresas=empresas)


@bp.route('/empresas/nueva', methods=['GET', 'POST'])
def nueva():
    if request.method == 'POST':
        rut = request.form['rut'].strip()
        if Empresa.query.filter_by(rut=rut).first():
            flash(f'Ya existe una empresa con RUT {rut}', 'danger')
            return render_template('empresas/form.html', empresa=None)

        empresa = Empresa(
            rut=rut,
            razon_social=request.form['razon_social'].strip(),
            nombre_fantasia=request.form.get('nombre_fantasia', '').strip(),
            giro=request.form.get('giro', '').strip(),
            clave_sii=request.form.get('clave_sii', '').strip() or None,
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
        empresa.rut = request.form['rut'].strip()
        empresa.razon_social = request.form['razon_social'].strip()
        empresa.nombre_fantasia = request.form.get('nombre_fantasia', '').strip()
        empresa.giro = request.form.get('giro', '').strip()
        clave = request.form.get('clave_sii', '').strip()
        if clave:
            empresa.clave_sii = clave
        db.session.commit()
        flash('Empresa actualizada', 'success')
        return redirect(url_for('main.index'))
    return render_template('empresas/form.html', empresa=empresa)
