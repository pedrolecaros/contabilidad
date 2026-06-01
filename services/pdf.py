"""Generación de PDF desde templates Jinja2 usando WeasyPrint."""
import os
import re


def _sanitize(text: str) -> str:
    return re.sub(r'[^\w\-]', '_', str(text))


def generar_liquidacion_pdf(app, liq) -> bytes:
    """Renderiza imprimir.html y lo convierte a PDF. Devuelve bytes del PDF."""
    from weasyprint import HTML, CSS
    from flask import render_template

    with app.app_context():
        html_str = render_template(
            'remuneraciones/imprimir.html',
            empresa=liq.empresa,
            liq=liq,
        )

    base_url = f'file://{app.root_path}/static/'
    pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()
    return pdf_bytes


def guardar_liquidacion_pdf(app, liq, upload_folder: str) -> str:
    """Genera PDF de la liquidación y lo guarda en uploads. Devuelve storage key."""
    from storage import save_bytes

    emp = liq.empleado
    empresa = liq.empresa
    rut_limpio = _sanitize(empresa.rut)
    nombre_limpio = _sanitize(emp.nombre_completo)
    subfolder = os.path.join(rut_limpio, 'liquidaciones', liq.periodo)
    filename = f"liq_{liq.periodo}_{nombre_limpio}.pdf"

    pdf_bytes = generar_liquidacion_pdf(app, liq)
    storage_key = save_bytes(pdf_bytes, filename, upload_folder, subfolder)
    return storage_key
