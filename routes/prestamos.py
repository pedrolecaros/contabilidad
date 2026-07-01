from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, Prestamo, Asiento, Contraparte

bp = Blueprint('prestamos', __name__)


# ── Lista ─────────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos')
def lista(eid):
    empresa = Empresa.query.get_or_404(eid)
    prestamos = (Prestamo.query
                 .filter_by(empresa_id=eid)
                 .order_by(Prestamo.activo.desc(), Prestamo.fecha_inicio.desc())
                 .all())

    saldos = {p.id: p.saldo_actual() for p in prestamos}

    total_pagar = sum(saldos[p.id] for p in prestamos if p.activo and p.tipo == 'PAGAR')
    total_cobrar = sum(saldos[p.id] for p in prestamos if p.activo and p.tipo == 'COBRAR')

    return render_template('prestamos/lista.html',
                           empresa=empresa,
                           prestamos=prestamos,
                           saldos=saldos,
                           total_pagar=total_pagar,
                           total_cobrar=total_cobrar)


# ── Nuevo ─────────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/nuevo', methods=['GET', 'POST'])
def nuevo(eid):
    empresa = Empresa.query.get_or_404(eid)

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio', 'danger')
            contrapartes = Contraparte.query.order_by(Contraparte.razon_social).all()
            return render_template('prestamos/form.html',
                                   empresa=empresa, prestamo=None,
                                   contrapartes=contrapartes, titulo='Nuevo Préstamo')

        try:
            monto_original = float(request.form.get('monto_original', 0))
        except (ValueError, TypeError):
            monto_original = 0.0

        try:
            fecha_inicio = date.fromisoformat(request.form.get('fecha_inicio', ''))
        except (ValueError, TypeError):
            flash('Fecha de inicio inválida', 'danger')
            contrapartes = Contraparte.query.order_by(Contraparte.razon_social).all()
            return render_template('prestamos/form.html',
                                   empresa=empresa, prestamo=None,
                                   contrapartes=contrapartes, titulo='Nuevo Préstamo')

        acreedor_rut = request.form.get('acreedor_rut', '').strip() or None
        acreedor_deudor = request.form.get('acreedor_deudor', '').strip() or None
        tipo = request.form.get('tipo', 'PAGAR')

        # Auto-detect empresa relacionada from RUT
        emp_rel_id = None
        if acreedor_rut:
            emp = Empresa.query.filter_by(rut=acreedor_rut).first()
            if emp and emp.id != eid:
                emp_rel_id = emp.id

        prestamo = Prestamo(
            empresa_id=eid,
            nombre=nombre,
            tipo=tipo,
            moneda=request.form.get('moneda', 'PESOS'),
            subtipo=request.form.get('subtipo', 'BANCARIO'),
            monto_original=monto_original,
            fecha_inicio=fecha_inicio,
            acreedor_deudor=acreedor_deudor,
            acreedor_rut=acreedor_rut,
            empresa_relacionada_id=emp_rel_id,
            activo=True,
            notas=request.form.get('notas', '').strip() or None,
        )
        db.session.add(prestamo)
        db.session.commit()
        flash(f'Préstamo "{nombre}" creado', 'success')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=prestamo.id))

    contrapartes = Contraparte.query.order_by(Contraparte.razon_social).all()
    return render_template('prestamos/form.html',
                           empresa=empresa, prestamo=None,
                           contrapartes=contrapartes, titulo='Nuevo Préstamo')


# ── Detalle ───────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>')
def detalle(eid, pid):
    empresa = Empresa.query.get_or_404(eid)
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))

    # All linked asientos ordered by date (confirmed ones drive balance)
    todos_asientos = (prestamo.asientos_vinculados
                      .filter(Asiento.estado != 'ANULADO')
                      .order_by(Asiento.fecha, Asiento.numero)
                      .all())

    saldo = float(prestamo.monto_original or 0)
    movimientos = []
    for a in todos_asientos:
        monto = max(a.total_debe or 0, a.total_haber or 0)
        sentido = a.prestamo_sentido or '-'
        if a.estado == 'CONFIRMADO':
            if sentido == '+':
                saldo += monto
            else:
                saldo -= monto
        movimientos.append({
            'asiento': a,
            'monto': monto,
            'sentido': sentido,
            'saldo': saldo if a.estado == 'CONFIRMADO' else None,
        })

    saldo_actual = saldo

    return render_template('prestamos/detalle.html',
                           empresa=empresa,
                           prestamo=prestamo,
                           movimientos=movimientos,
                           saldo_actual=saldo_actual,
                           back_url=request.args.get('back', url_for('prestamos.lista', eid=eid)))


# ── Editar ────────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/editar', methods=['GET', 'POST'])
def editar(eid, pid):
    empresa = Empresa.query.get_or_404(eid)
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio', 'danger')
            contrapartes = Contraparte.query.order_by(Contraparte.razon_social).all()
            return render_template('prestamos/form.html',
                                   empresa=empresa, prestamo=prestamo,
                                   contrapartes=contrapartes, titulo='Editar Préstamo')

        prestamo.nombre = nombre
        prestamo.tipo = request.form.get('tipo', 'PAGAR')
        prestamo.moneda = request.form.get('moneda', 'PESOS')
        prestamo.subtipo = request.form.get('subtipo', 'BANCARIO')
        prestamo.activo = bool(request.form.get('activo'))
        prestamo.acreedor_deudor = request.form.get('acreedor_deudor', '').strip() or None
        prestamo.acreedor_rut = request.form.get('acreedor_rut', '').strip() or None
        prestamo.notas = request.form.get('notas', '').strip() or None

        try:
            prestamo.monto_original = float(request.form.get('monto_original', 0))
        except (ValueError, TypeError):
            pass

        try:
            prestamo.fecha_inicio = date.fromisoformat(request.form.get('fecha_inicio', ''))
        except (ValueError, TypeError):
            flash('Fecha de inicio inválida', 'danger')
            contrapartes = Contraparte.query.order_by(Contraparte.razon_social).all()
            return render_template('prestamos/form.html',
                                   empresa=empresa, prestamo=prestamo,
                                   contrapartes=contrapartes, titulo='Editar Préstamo')

        # Auto-detect empresa relacionada from RUT
        if prestamo.acreedor_rut:
            emp = Empresa.query.filter_by(rut=prestamo.acreedor_rut).first()
            if emp and emp.id != eid:
                prestamo.empresa_relacionada_id = emp.id

        db.session.commit()
        flash('Préstamo actualizado', 'success')
        return redirect(url_for('prestamos.detalle', eid=eid, pid=pid))

    contrapartes = Contraparte.query.order_by(Contraparte.razon_social).all()
    return render_template('prestamos/form.html',
                           empresa=empresa, prestamo=prestamo,
                           contrapartes=contrapartes, titulo='Editar Préstamo')


# ── Eliminar ──────────────────────────────────────────────────────────────────

@bp.route('/empresa/<int:eid>/prestamos/<int:pid>/eliminar', methods=['POST'])
def eliminar(eid, pid):
    prestamo = Prestamo.query.get_or_404(pid)
    if prestamo.empresa_id != eid:
        flash('No autorizado', 'danger')
        return redirect(url_for('prestamos.lista', eid=eid))
    nombre = prestamo.nombre
    # Desvincular asientos que apunten a este préstamo (el asiento sigue válido).
    Asiento.query.filter_by(prestamo_id=pid).update(
        {'prestamo_id': None, 'prestamo_sentido': '-'})
    db.session.delete(prestamo)
    db.session.commit()
    flash(f'Préstamo "{nombre}" eliminado', 'warning')
    return redirect(url_for('prestamos.lista', eid=eid))
