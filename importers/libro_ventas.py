"""Importador Libro de Ventas SII (CSV separado por punto y coma)."""
from .base import importar_libro_sii


_ALIAS = {
    'tipo':   ('tipo_doc', 'tipo_de_doc', 'tipo_dte'),
    'rut':    ('rut_cliente', 'rut_receptor', 'rut'),
    'rs':     ('razon_social', 'razon_social_receptor', 'razon_social_cliente'),
    'folio':  ('folio', 'n_folio', 'numero_folio'),
    'fecha':  ('fecha_docto', 'fecha_doc', 'fecha_documento',
               'fecha_emision', 'fecha_de_emision', 'fecha'),
    'exento': ('monto_exento', 'exento'),
    'neto':   ('monto_neto', 'neto'),
    'iva':    ('monto_iva', 'iva', 'monto_i_v_a', 'debito_fiscal'),
    'total':  ('monto_total', 'total'),
}


def importar(file_storage, empresa_id) -> dict:
    return importar_libro_sii(file_storage, empresa_id, 'VENTAS', _ALIAS)
