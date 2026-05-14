"""
Importador Libro de Honorarios SII.
Formato XLSX: filas 0-4 son metadata, fila 5 es el header real.
Columnas: N°, Fecha, Estado, Fecha Anulación, Rut, Nombre o Razón Social,
          Soc. Prof., Brutos, Retenido, Pagado
"""
import io
import pandas as pd
from models import db, DocumentoSII
from .base import parsear_fecha, parsear_monto, ENCODINGS


def _leer_html_sii(contenido):
    """Lee el formato HTML-as-XLS que exporta el SII para honorarios."""
    tables = pd.read_html(io.BytesIO(contenido))
    if not tables:
        raise ValueError("No se encontró tabla en el archivo HTML de Honorarios")
    df = tables[0]
    # Fila 2 contiene los nombres reales de columnas (0=empresa, 1=grupos, 2=headers)
    df.columns = [str(v).strip() for v in df.iloc[2]]
    df = df.iloc[3:].reset_index(drop=True)  # datos desde fila 3
    return df.astype(str)


def _leer_honorarios(file_storage):
    nombre = file_storage.filename.lower()
    contenido = file_storage.read()

    if nombre.endswith('.xlsx'):
        # Header real está en la fila 5 (índice 5), datos desde fila 6
        df = pd.read_excel(io.BytesIO(contenido), engine='openpyxl',
                           dtype=str, header=5)
        return df

    if nombre.endswith('.xls'):
        # El SII a veces exporta HTML con extensión .xls — detectar y parsear
        if contenido.lstrip()[:6] in (b'<table', b'\n<tabl', b'<Table'):
            return _leer_html_sii(contenido)
        try:
            df = pd.read_excel(io.BytesIO(contenido), engine='xlrd',
                               dtype=str, header=5)
            return df
        except Exception:
            # Segundo intento: puede ser HTML igual
            return _leer_html_sii(contenido)

    # CSV: buscar fila con "folio" o "n°" para encontrar header
    for enc in ENCODINGS:
        for sep in [';', ',', '\t']:
            try:
                df_raw = pd.read_csv(io.BytesIO(contenido), sep=sep,
                                     encoding=enc, dtype=str, header=None)
                for idx, row in df_raw.iterrows():
                    vals = [str(v).lower().strip() for v in row.values]
                    if any('folio' in v or 'n°' in v or 'bruto' in v for v in vals):
                        df = pd.read_csv(io.BytesIO(contenido), sep=sep,
                                         encoding=enc, dtype=str, skiprows=idx)
                        return df
            except Exception:
                continue
    raise ValueError("No se pudo leer el archivo de Honorarios")


def importar(file_storage, empresa_id) -> dict:
    df = _leer_honorarios(file_storage)

    # Normalizar nombres de columna
    import unicodedata
    def limpiar(s):
        s = str(s).strip().lower()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.replace(' ', '_').replace('°', '').replace('.', '').replace('/', '_')
    df.columns = [limpiar(c) for c in df.columns]

    # Columnas esperadas del SII Honorarios
    # n, fecha, estado, fecha_anulacion, rut, nombre_o_razon_social,
    # soc_prof, brutos, retenido, pagado
    col_folio  = next((c for c in df.columns if c in ('n', 'n_boleta', 'folio', 'n_folio')), None)
    col_fecha  = next((c for c in df.columns if 'fecha' in c and 'anulac' not in c), None)
    col_estado = next((c for c in df.columns if 'estado' in c), None)
    col_rut    = next((c for c in df.columns if c == 'rut' or c.startswith('rut')), None)
    col_rs     = next((c for c in df.columns if 'nombre' in c or 'razon' in c), None)
    col_bruto  = next((c for c in df.columns if 'bruto' in c or 'honorario' in c), None)
    col_reten  = next((c for c in df.columns if 'retenido' in c or 'retencion' in c), None)
    col_pagado = next((c for c in df.columns if 'pagado' in c or 'liquido' in c or c == 'neto'), None)

    importados = 0
    errores = []

    for i, row in df.iterrows():
        try:
            folio = str(row[col_folio]).strip() if col_folio else ''
            rut   = str(row[col_rut]).strip()   if col_rut   else ''
            rs    = str(row[col_rs]).strip()     if col_rs    else ''

            # Saltar filas vacías y fila de totales
            if folio in ('', 'nan') or rut in ('', 'nan'):
                continue
            # Saltar si parece ser la fila de totales
            if 'total' in folio.lower() or 'total' in rut.lower():
                continue

            # Saltar boletas anuladas
            if col_estado:
                estado = str(row[col_estado]).strip().upper()
                if estado == 'ANULADO':
                    continue

            fecha    = parsear_fecha(row[col_fecha]) if col_fecha else None
            bruto    = parsear_monto(row[col_bruto])  if col_bruto  else 0.0
            retenido = parsear_monto(row[col_reten])  if col_reten  else round(bruto * 0.1075)
            pagado   = parsear_monto(row[col_pagado]) if col_pagado else bruto - retenido

            if bruto == 0:
                continue

            if DocumentoSII.query.filter_by(
                empresa_id=empresa_id, tipo_libro='HONORARIOS',
                folio=folio, rut_contraparte=rut
            ).first():
                continue

            doc = DocumentoSII(
                empresa_id=empresa_id,
                tipo_libro='HONORARIOS',
                tipo_dte='39',
                folio=folio,
                fecha=fecha,
                rut_contraparte=rut,
                razon_social_contraparte=rs,
                monto_exento=0.0,
                monto_neto=pagado,    # líquido a pagar
                iva=retenido,         # retención (guardada en campo iva)
                total=bruto,          # monto bruto
                archivo_origen=file_storage.filename,
            )
            db.session.add(doc)
            importados += 1
        except Exception as e:
            errores.append(f"Fila {i+2}: {e}")

    db.session.commit()
    return {'importados': importados, 'errores': errores}
