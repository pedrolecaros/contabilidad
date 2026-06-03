"""
Parser de PDF del F22 (Declaración Anual de Renta) enviado al SII.

Reutiliza las heurísticas del F29 (mismas estrategias de parseo de códigos,
mismo `_parsear_numero_es`). El F22 tiene su propio formato pero conserva
estructura tipo `<código 3 dig> <descripción> <valor>`.

Códigos típicos del F22 (régimen Pyme / 14D8 / atribuida):
  - 628: Renta Líquida Imponible (RLI)
  - 643: Impuesto Primera Categoría
  - 91 : Total a pagar
  - 94 : Total con recargo
"""
import re
import subprocess
import tempfile
from typing import Dict, Tuple

from .sii_f29 import (
    _parsear_codigos,
    _parsear_numero_es,
    _normalizar_rut,
    _extraer_rut_pdf,
)


class F22ParseError(Exception):
    """No se pudieron parsear códigos válidos del F22."""


def _extraer_texto_pdf(contenido_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as f:
        f.write(contenido_bytes)
        f.flush()
        proc = subprocess.run(
            ['pdftotext', '-layout', f.name, '-'],
            capture_output=True, check=True, timeout=30,
        )
    return proc.stdout.decode('utf-8', errors='ignore')


def _extraer_anio_f22(texto: str) -> int | None:
    """El F22 declara el AT (Año Tributario), que se presenta en abril del mismo
    año y corresponde a las rentas del año comercial anterior."""
    # "AÑO TRIBUTARIO 2026" o "Año Tributario [09] 2026"
    m = re.search(r'A[ÑN]O\s*TRIBUTARIO\s*\[?09\]?\s*(\d{4})', texto, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'A[ÑN]O\s*TRIBUTARIO[^\d]{0,30}(\d{4})', texto, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # "AT 2026" suelto
    m = re.search(r'\bAT\s*(20\d{2})\b', texto)
    if m:
        return int(m.group(1))
    return None


def _extraer_folio_f22(texto: str) -> str | None:
    """Folio del F22."""
    # F22 compacto: "07 N° 314439486" (al inicio del PDF, parte superior)
    m = re.search(r'07\s*N\.?\s*°?\s*(\d{6,15})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    # "Folio N° 314439486"
    m = re.search(r'Folio\s*N\.?\s*°?\s*(\d{6,15})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    # F29-style: "FOLIO [07] xxx"
    m = re.search(r'FOLIO\s*\[?07\]?\s+(\d{6,15})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'Folio\s*[:\-]?\s*(\d{6,15})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _extraer_rut_pdf_f22(texto: str) -> str | None:
    """RUT del F22 (puede venir sin puntos: 76703937-9)."""
    # Intentar primero el extractor genérico del F29
    rut = _extraer_rut_pdf(texto)
    if rut:
        return rut
    # F22 compacto: RUT sin puntos típicamente en línea propia tras "ROL UNICO"
    m = re.search(r'(?:ROL\s*UNICO|R\.?U\.?T\.?)[^\n]*\n\s*(\d{7,9}\-[\dkK])', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    # Cualquier RUT sin puntos del estilo 76703937-9
    m = re.search(r'\b(\d{7,9}\-[\dkK])\b', texto)
    if m:
        return m.group(1)
    return None


def parsear_pdf(contenido_bytes: bytes) -> Tuple[Dict[str, float], int | None, str | None, str | None]:
    """Parsea un PDF del F22 enviado.

    Returns:
        (codigos_dict, anio_tributario, folio, rut)

    Raises:
        F22ParseError si no detecta códigos.
    """
    try:
        texto = _extraer_texto_pdf(contenido_bytes)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise F22ParseError(f'No se pudo leer el PDF: {e}')

    codigos = _parsear_codigos(texto)
    if not codigos:
        raise F22ParseError(
            'No se reconocieron códigos del F22 en el PDF. '
            '¿Está el PDF correcto? (Estado de declaración / Comprobante de envío del F22)'
        )

    anio = _extraer_anio_f22(texto)
    folio = _extraer_folio_f22(texto)
    rut = _extraer_rut_pdf_f22(texto)
    return codigos, anio, folio, rut
