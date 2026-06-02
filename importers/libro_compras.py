"""Importador Libro de Compras SII (CSV separado por punto y coma)."""
from .base import importar_libro_sii


_ALIAS = {
    'tipo':   ('tipo_doc', 'tipo_de_doc', 'tipo_dte'),
    'rut':    ('rut_proveedor', 'rut_emisor', 'rut'),
    'rs':     ('razon_social', 'razon_social_proveedor', 'razon_social_emisor'),
    'folio':  ('folio', 'n_folio', 'numero_folio'),
    'fecha':  ('fecha_docto', 'fecha_doc', 'fecha_documento',
               'fecha_emision', 'fecha_de_emision', 'fecha'),
    'exento': ('monto_exento', 'exento'),
    'neto':   ('monto_neto', 'neto'),
    # Solo IVA recuperable; el no-recuperable se absorbe en el gasto (total - iva).
    'iva':    ('monto_iva_recuperable', 'iva_recuperable',
               'monto_iva', 'iva', 'credito_fiscal'),
    'total':  ('monto_total', 'total'),
}


def importar(file_storage, empresa_id) -> dict:
    return importar_libro_sii(file_storage, empresa_id, 'COMPRAS', _ALIAS)
