from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, Empleado, Liquidacion
from engine import remuneraciones as motor

bp = Blueprint('remuneraciones', __name__)

AFP_OPCIONES = ['Capital', 'Cuprum', 'Habitat', 'Modelo', 'PlanVital', 'ProVida', 'Uno']


@bp.route('/empresa/<int:eid>/remuneraciones')
def index(eid):
    empresa = Empresa.query.get_or_404(eid)
    empleados = (Empleado.query
                 .filter_by(empresa_id=eid)
                 .order_by(Empleado.nombre)
                 .all())
    return render_template('remuneraciones/index.html', empresa=empresa, empleados=empleados)


@bp.route('/empresa/<int:eid>/remuneraciones/nuevo', methods=['GET', 'POST'])
def nuevo(eid):
    empresa = Empresa.query.get_or_404(eid)
    if request.method == 'POST':
        emp = Empleado(empresa_id=eid)
        _poblar(emp, request.form)
        db.session.add(emp)
        db.session.commit()
        flash('Empleado creado correctamente.', 'success')
        return redirect(url_for('remuneraciones.index', eid=eid))
    return render_template('remuneraciones/form.html', empresa=empresa,
                           emp=None, afp_opciones=AFP_OPCIONES)


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/editar', methods=['GET', 'POST'])
def editar(eid, emp_id):
    empresa = Empresa.query.get_or_404(eid)
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    if request.method == 'POST':
        _poblar(emp, request.form)
        db.session.commit()
        flash('Empleado actualizado.', 'success')
        return redirect(url_for('remuneraciones.index', eid=eid))
    return render_template('remuneraciones/form.html', empresa=empresa,
                           emp=emp, afp_opciones=AFP_OPCIONES)


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/eliminar', methods=['POST'])
def eliminar(eid, emp_id):
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    db.session.delete(emp)
    db.session.commit()
    flash('Empleado eliminado.', 'success')
    return redirect(url_for('remuneraciones.index', eid=eid))


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/liquidar', methods=['GET', 'POST'])
def liquidar(eid, emp_id):
    empresa = Empresa.query.get_or_404(eid)
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    if request.method == 'POST':
        periodo = request.form.get('periodo', '').strip()
        utm = float(request.form.get('utm', 0) or 0)
        horas_extra = float(request.form.get('horas_extra', 0) or 0)
        otros = float(request.form.get('otros', 0) or 0)
        gratificacion = float(request.form.get('gratificacion', 0) or 0)

        if not periodo or utm <= 0:
            flash('Período y UTM son obligatorios.', 'danger')
            return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp)

        # Evitar duplicados
        existe = Liquidacion.query.filter_by(empleado_id=emp_id, periodo=periodo).first()
        if existe:
            flash(f'Ya existe una liquidación para {periodo}. Edítela o elimínela primero.', 'warning')
            return redirect(url_for('remuneraciones.detalle', eid=eid, liq_id=existe.id))

        resultado = motor.calcular(emp, utm, horas_extra, otros, gratificacion)

        liq = Liquidacion(empresa_id=eid, empleado_id=emp_id, periodo=periodo)
        for campo, valor in resultado.items():
            if hasattr(liq, campo):
                setattr(liq, campo, valor)
        db.session.add(liq)
        db.session.commit()
        flash(f'Liquidación {periodo} generada.', 'success')
        return redirect(url_for('remuneraciones.detalle', eid=eid, liq_id=liq.id))

    return render_template('remuneraciones/liquidar.html', empresa=empresa, emp=emp)


@bp.route('/empresa/<int:eid>/remuneraciones/liquidacion/<int:liq_id>')
def detalle(eid, liq_id):
    empresa = Empresa.query.get_or_404(eid)
    liq = Liquidacion.query.filter_by(id=liq_id, empresa_id=eid).first_or_404()
    return render_template('remuneraciones/detalle.html', empresa=empresa, liq=liq)


@bp.route('/empresa/<int:eid>/remuneraciones/liquidacion/<int:liq_id>/imprimir')
def imprimir(eid, liq_id):
    empresa = Empresa.query.get_or_404(eid)
    liq = Liquidacion.query.filter_by(id=liq_id, empresa_id=eid).first_or_404()
    return render_template('remuneraciones/imprimir.html', empresa=empresa, liq=liq)


@bp.route('/empresa/<int:eid>/remuneraciones/liquidacion/<int:liq_id>/eliminar', methods=['POST'])
def eliminar_liq(eid, liq_id):
    liq = Liquidacion.query.filter_by(id=liq_id, empresa_id=eid).first_or_404()
    emp_id = liq.empleado_id
    db.session.delete(liq)
    db.session.commit()
    flash('Liquidación eliminada.', 'success')
    return redirect(url_for('remuneraciones.historial', eid=eid, emp_id=emp_id))


@bp.route('/empresa/<int:eid>/remuneraciones/<int:emp_id>/historial')
def historial(eid, emp_id):
    empresa = Empresa.query.get_or_404(eid)
    emp = Empleado.query.filter_by(id=emp_id, empresa_id=eid).first_or_404()
    liqs = (Liquidacion.query
            .filter_by(empleado_id=emp_id)
            .order_by(Liquidacion.periodo.desc())
            .all())
    return render_template('remuneraciones/historial.html', empresa=empresa, emp=emp, liqs=liqs)


def _poblar(emp, form):
    emp.rut = form.get('rut', '').strip()
    emp.nombre = form.get('nombre', '').strip()
    emp.cargo = form.get('cargo', '').strip()
    emp.tipo_contrato = form.get('tipo_contrato', 'INDEFINIDO')
    emp.sueldo_base = float(form.get('sueldo_base', 0) or 0)
    emp.afp = form.get('afp', 'Habitat')
    raw_tasa = form.get('tasa_afp_comision', '').strip()
    emp.tasa_afp_comision = float(raw_tasa) / 100 if raw_tasa else motor.AFP_COMISIONES.get(emp.afp, 0.0127)
    emp.tipo_salud = form.get('tipo_salud', 'FONASA')
    emp.isapre = form.get('isapre', '').strip() or None
    emp.monto_isapre = float(form.get('monto_isapre', 0) or 0)
    emp.bono_colacion = float(form.get('bono_colacion', 0) or 0)
    emp.bono_movilizacion = float(form.get('bono_movilizacion', 0) or 0)
    emp.otros_haberes = float(form.get('otros_haberes', 0) or 0)
    raw_mutual = form.get('tasa_mutual', '').strip()
    emp.tasa_mutual = float(raw_mutual) / 100 if raw_mutual else 0.0093
    emp.activo = form.get('activo') == 'on'
    fi = form.get('fecha_ingreso', '').strip()
    if fi:
        from datetime import date
        try:
            emp.fecha_ingreso = date.fromisoformat(fi)
        except ValueError:
            pass
