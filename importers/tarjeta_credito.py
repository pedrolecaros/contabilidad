"""
Importador de estados de cuenta de tarjeta de crédito (PDF).

Por ahora soporta el formato VISA Banco de Chile (Estado_Cuenta.pdf).
Cada cargo se importa como MovimientoBanco con banco='Banco de Chile (TC)'
para que después la conciliación lo asocie a la cuenta de pasivo TC.

Convención:
  - Monto positivo en el PDF (compras, comisiones, intereses, traspasos) → cargo
  - Monto negativo (pagos automáticos, devoluciones)                     → abono
"""
import re
import subprocess
import tempfile
from datetime import date

from models import db, MovimientoBanco


_RE_FECHA = re.compile(r'\b(\d{2}/\d{2}/\d{2})\b')
# Línea típica de movimiento. Dos variantes:
#  A) "<space>DD/MM/YY  CODREF  DESCRIPCION  $ <monto1> $ <monto2>  NN/NN  $ <cuota>"
#  B) "LUGAR_OPERACION    DD/MM/YY  CODREF  DESCRIPCION  $ <monto1> ..."  (compras presenciales)
# Usamos `search` con prefijo opcional para capturar ambas.
_RE_LINEA = re.compile(
    r'(?:^|\s)'
    r'(\d{2}/\d{2}/\d{2})\s+'           # fecha DD/MM/YY
    r'(\d{6,14})\s+'                     # código referencia (suele tener 12 dígitos)
    r'(.+?)\s+'                          # descripción (no greedy)
    r'\$\s*(-?[\d\.,]+)'                 # monto operación (signo opcional)
)


def _extraer_texto(contenido_bytes: bytes) -> str:
    """Usa pdftotext (poppler) para extraer el texto preservando layout."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as f:
        f.write(contenido_bytes)
        f.flush()
        proc = subprocess.run(
            ['pdftotext', '-layout', f.name, '-'],
            capture_output=True, check=True, timeout=30,
        )
    return proc.stdout.decode('utf-8', errors='ignore')


def _parsear_monto(s: str) -> float:
    s = s.replace('.', '').replace(',', '.').strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parsear_fecha(s: str, anio_estado: int) -> date | None:
    """DD/MM/YY → date. Usa el año del estado de cuenta para resolver el siglo."""
    try:
        d, m, y = s.split('/')
        y2 = int(y)
        # Año del estado: usamos el siglo del año del estado (2000+yy si yy<70, sino 1900+yy)
        if y2 < 70:
            anio = 2000 + y2
        else:
            anio = 1900 + y2
        return date(anio, int(m), int(d))
    except Exception:
        return None


# Filas de sumatoria que podrían matchear el regex de movimiento por accidente.
# Se aplican como substring exacto al inicio (sin espacios) de la descripción capturada.
_DESC_IGNORAR_EXACTAS = (
    'Sin Movimientos',
)


def _extraer_metadata(texto: str) -> tuple[str, date | None]:
    """Devuelve (numero_tarjeta_oculto, fecha_estado)."""
    num_tc = ''
    fecha_estado = None
    m = re.search(r'N°\s*DE\s+TARJETA\s+DE\s+CR[ÉE]DITO\s+([X\d\s]{15,25})',
                  texto, re.IGNORECASE)
    if m:
        bruto = re.sub(r'\s+', '', m.group(1))
        # Acepta hasta 16 dígitos/X
        bruto = bruto[:16]
        num_tc = bruto.replace('X', '*')
    m = re.search(r'FECHA\s+ESTADO\s+DE\s+CUENTA\s+(\d{2}/\d{2}/\d{4})', texto, re.IGNORECASE)
    if m:
        d, mm, yyyy = m.group(1).split('/')
        try:
            fecha_estado = date(int(yyyy), int(mm), int(d))
        except ValueError:
            pass
    return num_tc, fecha_estado


def importar(file_storage, empresa_id, banco='', cuenta_bancaria='') -> dict:
    """Importa un estado de cuenta de tarjeta de crédito en PDF.

    Devuelve {'importados': N, 'errores': [...]}.
    """
    contenido = file_storage.read()
    try:
        texto = _extraer_texto(contenido)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return {'importados': 0, 'errores': [f'No se pudo leer el PDF: {e}']}

    num_tc, fecha_estado = _extraer_metadata(texto)
    anio_ref = fecha_estado.year if fecha_estado else date.today().year

    importados, errores = 0, []
    vistos_set = set()  # dedup intra-archivo: (fecha, código, monto)

    for raw in texto.splitlines():
        linea = raw.rstrip()
        m = _RE_LINEA.search(linea)
        if not m:
            continue
        fecha_str, codref, desc, monto_str = m.groups()
        if any(s in desc for s in _DESC_IGNORAR_EXACTAS):
            continue
        fecha = _parsear_fecha(fecha_str, anio_ref)
        if not fecha:
            continue
        monto = _parsear_monto(monto_str)
        if monto == 0:
            continue
        desc = re.sub(r'\s+', ' ', desc).strip()

        clave = (fecha, codref, monto)
        if clave in vistos_set:
            continue
        vistos_set.add(clave)

        # Signo: positivo = cargo (sale del cupo), negativo = abono (entra al cupo / pago)
        if monto > 0:
            cargo, abono = monto, 0.0
        else:
            cargo, abono = 0.0, abs(monto)

        try:
            mov = MovimientoBanco(
                empresa_id=empresa_id,
                banco=banco or 'Banco de Chile (TC)',
                cuenta_bancaria=cuenta_bancaria or num_tc,
                fecha=fecha,
                descripcion=f'{desc} [ref {codref}]',
                cargo=cargo,
                abono=abono,
                saldo=None,
                archivo_origen=file_storage.filename,
            )
            db.session.add(mov)
            importados += 1
        except Exception as e:
            errores.append(f'{fecha_str} {desc}: {e}')

    db.session.commit()
    return {'importados': importados, 'errores': errores}


def es_pdf_tarjeta_credito(contenido_bytes: bytes) -> bool:
    """Heurística rápida para detectar si un PDF es un estado de cuenta de TC."""
    try:
        texto = _extraer_texto(contenido_bytes)[:3000].upper()
    except Exception:
        return False
    return 'ESTADO DE CUENTA' in texto and 'TARJETA DE CR' in texto
