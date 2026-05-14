"""Utilidades compartidas por todos los importadores."""
import io
import pandas as pd
from dateutil import parser as dateparser


ENCODINGS = ['utf-8-sig', 'latin-1', 'utf-8', 'cp1252']
SEPARADORES = [';', ',', '\t', '|']


def leer_archivo(file_storage):
    """
    Recibe un FileStorage de Flask y retorna un DataFrame.
    Soporta CSV (cualquier separador), XLS y XLSX.
    """
    nombre = file_storage.filename.lower()
    contenido = file_storage.read()

    if nombre.endswith('.xlsx'):
        return pd.read_excel(io.BytesIO(contenido), engine='openpyxl', dtype=str)
    if nombre.endswith('.xls'):
        return pd.read_excel(io.BytesIO(contenido), engine='xlrd', dtype=str)

    # CSV: probar encodings y separadores
    for enc in ENCODINGS:
        for sep in SEPARADORES:
            try:
                df = pd.read_csv(io.BytesIO(contenido), sep=sep, encoding=enc,
                                 dtype=str, skipinitialspace=True)
                if len(df.columns) > 1:
                    return df
            except Exception:
                continue
    raise ValueError("No se pudo leer el archivo. Verifique el formato.")


def normalizar_columnas(df):
    """Normaliza nombres de columnas: minúsculas, sin acentos, sin espacios extra."""
    import unicodedata
    def limpiar(s):
        s = str(s).strip().lower()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        s = s.replace(' ', '_').replace('°', '').replace('.', '').replace('/', '_')
        return s
    df.columns = [limpiar(c) for c in df.columns]
    return df


def parsear_fecha(valor):
    if pd.isna(valor) or str(valor).strip() in ('', 'nan'):
        return None
    try:
        return dateparser.parse(str(valor).strip(), dayfirst=True).date()
    except Exception:
        return None


def parsear_monto(valor):
    if pd.isna(valor) or str(valor).strip() in ('', 'nan', '-'):
        return 0.0
    s = str(valor).strip()
    # Banco de Chile usa +0002040542 (signo + y ceros leading)
    s = s.lstrip('+')
    # Eliminar puntos de miles y reemplazar coma decimal
    s = s.replace('.', '').replace(',', '.').replace('$', '').replace(' ', '')
    try:
        return float(s)
    except Exception:
        return 0.0


def primera_col(df, *candidatos):
    """Retorna el nombre de la primera columna que existe en el DataFrame."""
    for c in candidatos:
        if c in df.columns:
            return c
    return None
