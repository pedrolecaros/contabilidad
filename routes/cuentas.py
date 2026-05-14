from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, Cuenta

bp = Blueprint('cuentas', __name__)


@bp.route('/empresa/<int:eid>/cuentas')
def lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    cuentas = (Cuenta.query
               .filter_by(empresa_id=eid)
               .order_by(Cuenta.codigo)
               .all())
    return render_template('cuentas/lista.html', empresa=empresa, cuentas=cuentas)


@bp.route('/empresa/<int:eid>/cuentas/nueva', methods=['GET', 'POST'])
def nueva(eid):
    empresa = Empresa.query.get_or_404(eid)
    if request.method == 'POST':
        codigo = request.form['codigo'].strip()
        if Cuenta.query.filter_by(empresa_id=eid, codigo=codigo).first():
            flash(f'Ya existe la cuenta {codigo}', 'danger')
        else:
            cuenta = Cuenta(
                empresa_id=eid,
                codigo=codigo,
                nombre=request.form['nombre'].strip(),
                tipo=request.form['tipo'],
                naturaleza=request.form['naturaleza'],
                es_titulo='es_titulo' in request.form,
            )
            db.session.add(cuenta)
            db.session.commit()
            flash('Cuenta creada', 'success')
            return redirect(url_for('cuentas.lista', eid=eid))
    return render_template('cuentas/form.html', empresa=empresa, cuenta=None)


@bp.route('/empresa/<int:eid>/cuentas/<int:cid>/editar', methods=['GET', 'POST'])
def editar(eid, cid):
    empresa = Empresa.query.get_or_404(eid)
    cuenta = Cuenta.query.get_or_404(cid)
    if request.method == 'POST':
        cuenta.codigo = request.form['codigo'].strip()
        cuenta.nombre = request.form['nombre'].strip()
        cuenta.tipo = request.form['tipo']
        cuenta.naturaleza = request.form['naturaleza']
        cuenta.es_titulo = 'es_titulo' in request.form
        cuenta.activa = 'activa' in request.form
        db.session.commit()
        flash('Cuenta actualizada', 'success')
        return redirect(url_for('cuentas.lista', eid=eid))
    return render_template('cuentas/form.html', empresa=empresa, cuenta=cuenta)
