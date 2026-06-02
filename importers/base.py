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
        try:
            return pd.read_excel(io.BytesIO(contenido), engine='xlrd', dtype=str)
        except Exception:
            # Many Chilean banks export HTML files with .xls extension
            try:
                tables = pd.read_html(io.BytesIO(contenido), dtype=str)
                if tables:
                    return tables[0]
            except Exception:
                pass
            raise ValueError("No se pudo leer el archivo XLS. Intente exportarlo como CSV o XLSX.")

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


def _leer_csv_sii(contenido):
    """Lee CSV típico del SII (separador ';', encoding latin-1/utf-8)."""
    for enc in ENCODINGS:
        try:
            df = pd.read_csv(io.BytesIO(contenido), sep=';', encoding=enc,
                             dtype=str, skipinitialspace=True, index_col=False)
            if len(df.columns) > 3:
                return df
        except Exception:
            continue
    raise ValueError("No se pudo leer el CSV del SII")


def importar_libro_sii(file_storage, empresa_id, tipo_libro, alias):
    """Importa libro SII de Compras o Ventas.

    `alias` es un dict con tuplas de nombres alternativos para columnas:
    {'tipo': (...), 'rut': (...), 'rs': (...), 'folio': (...),
     'fecha': (...), 'exento': (...), 'neto': (...), 'iva': (...), 'total': (...)}
    """
    from models import db, DocumentoSII

    contenido = file_storage.read()
    df = _leer_csv_sii(contenido)
    df = normalizar_columnas(df)

    cols = {k: primera_col(df, *names) for k, names in alias.items()}

    importados = 0
    errores = []

    for i, row in df.iterrows():
        try:
            tipo  = str(row[cols['tipo']]).strip()  if cols.get('tipo')  else ''
            folio = str(row[cols['folio']]).strip() if cols.get('folio') else ''
            rut   = str(row[cols['rut']]).strip()   if cols.get('rut')   else ''
            rs    = str(row[cols['rs']]).strip()    if cols.get('rs')    else ''

            if rut in ('', 'nan') or folio in ('', 'nan'):
                continue

            fecha  = parsear_fecha(row[cols['fecha']])  if cols.get('fecha')  else None
            exento = parsear_monto(row[cols['exento']]) if cols.get('exento') else 0.0
            neto   = parsear_monto(row[cols['neto']])   if cols.get('neto')   else 0.0
            iva    = parsear_monto(row[cols['iva']])    if cols.get('iva')    else 0.0
            total  = parsear_monto(row[cols['total']])  if cols.get('total')  else 0.0

            if DocumentoSII.query.filter_by(
                empresa_id=empresa_id, tipo_libro=tipo_libro,
                tipo_dte=tipo, folio=folio, rut_contraparte=rut
            ).first():
                continue

            doc = DocumentoSII(
                empresa_id=empresa_id,
                tipo_libro=tipo_libro,
                tipo_dte=tipo,
                folio=folio,
                fecha=fecha,
                rut_contraparte=rut,
                razon_social_contraparte=rs,
                monto_exento=exento,
                monto_neto=neto,
                iva=iva,
                total=total,
                archivo_origen=file_storage.filename,
            )
            db.session.add(doc)
            importados += 1
        except Exception as e:
            errores.append(f"Fila {i+2}: {e}")

    db.session.commit()
    return {'importados': importados, 'errores': errores}
