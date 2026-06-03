from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from models import db, Empresa, NotaContable, NotaGlobal

bp = Blueprint('notas', __name__)


def _get_or_create(empresa_id: int) -> NotaContable:
    nota = NotaContable.query.get(empresa_id)
    if nota is None:
        nota = NotaContable(empresa_id=empresa_id, contenido='')
        db.session.add(nota)
        db.session.commit()
    return nota


def _get_global() -> NotaGlobal:
    nota = NotaGlobal.query.get(1)
    if nota is None:
        nota = NotaGlobal(id=1, contenido='')
        db.session.add(nota)
        db.session.commit()
    return nota


@bp.route('/empresa/<int:eid>/notas', methods=['GET', 'POST'])
def empresa(eid):
    empresa = Empresa.query.get_or_404(eid)
    nota = _get_or_create(eid)
    if request.method == 'POST':
        nota.contenido = request.form.get('contenido', '').strip()
        nota.actualizado_en = datetime.now()
        db.session.commit()
        flash('Notas guardadas', 'success')
        return redirect(url_for('notas.empresa', eid=eid))
    return render_template('notas/empresa.html', empresa=empresa, nota=nota)


@bp.route('/notas')
def consolidado():
    empresas = Empresa.query.filter_by(activa=True).order_by(Empresa.razon_social).all()
    notas_map = {n.empresa_id: n for n in NotaContable.query.all()}
    items = []
    for e in empresas:
        n = notas_map.get(e.id)
        items.append({
            'empresa': e,
            'contenido': (n.contenido if n else '') or '',
            'actualizado_en': n.actualizado_en if n else None,
        })
    nota_global = _get_global()
    return render_template('notas/consolidado.html', items=items, nota_global=nota_global)


@bp.route('/notas/general', methods=['GET', 'POST'])
def general():
    nota = _get_global()
    if request.method == 'POST':
        nota.contenido = request.form.get('contenido', '').strip()
        nota.actualizado_en = datetime.now()
        db.session.commit()
        flash('Nota general guardada', 'success')
        return redirect(url_for('notas.general'))
    return render_template('notas/general.html', nota=nota)


@bp.route('/notas/general/guardar', methods=['POST'])
def guardar_general_inline():
    nota = _get_global()
    nota.contenido = request.form.get('contenido', '').strip()
    nota.actualizado_en = datetime.now()
    db.session.commit()
    flash('Nota general guardada', 'success')
    return redirect(url_for('notas.consolidado') + '#nota-general')


@bp.route('/notas/<int:eid>/guardar', methods=['POST'])
def guardar_inline(eid):
    Empresa.query.get_or_404(eid)
    nota = _get_or_create(eid)
    nota.contenido = request.form.get('contenido', '').strip()
    nota.actualizado_en = datetime.now()
    db.session.commit()
    flash(f'Notas guardadas', 'success')
    return redirect(url_for('notas.consolidado') + f'#empresa-{eid}')
