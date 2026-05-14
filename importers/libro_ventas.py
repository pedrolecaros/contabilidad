"""
Importador Libro de Ventas SII.
Formato: CSV separado por punto y coma, encoding Latin-1 o UTF-8.
Columnas relevantes: Tipo Doc, Rut cliente, Razon Social, Folio,
  Fecha Docto, Monto Exento, Monto Neto, Monto IVA, Monto total.
"""
import io
import pandas as pd
from models import db, DocumentoSII
from .base import parsear_fecha, parsear_monto, primera_col, normalizar_columnas, ENCODINGS


def _leer_csv_sii(contenido):
    for enc in ENCODINGS:
        try:
            df = pd.read_csv(io.BytesIO(contenido), sep=';', encoding=enc,
                             dtype=str, skipinitialspace=True, index_col=False)
            if len(df.columns) > 3:
                return df
        except Exception:
            continue
    raise ValueError("No se pudo leer el CSV de Ventas")


def importar(file_storage, empresa_id) -> dict:
    contenido = file_storage.read()
    df = _leer_csv_sii(contenido)
    df = normalizar_columnas(df)

    col_tipo  = primera_col(df, 'tipo_doc', 'tipo_de_doc', 'tipo_dte')
    col_rut   = primera_col(df, 'rut_cliente', 'rut_receptor', 'rut')
    col_rs    = primera_col(df, 'razon_social', 'razon_social_receptor', 'razon_social_cliente')
    col_folio = primera_col(df, 'folio', 'n_folio', 'numero_folio')
    col_fecha = primera_col(df, 'fecha_docto', 'fecha_doc', 'fecha_documento',
                            'fecha_emision', 'fecha_de_emision', 'fecha')
    col_exento = primera_col(df, 'monto_exento', 'exento')
    col_neto   = primera_col(df, 'monto_neto', 'neto')
    col_iva    = primera_col(df, 'monto_iva', 'iva', 'monto_i_v_a', 'debito_fiscal')
    col_total  = primera_col(df, 'monto_total', 'total')

    importados = 0
    errores = []

    for i, row in df.iterrows():
        try:
            tipo  = str(row[col_tipo]).strip()  if col_tipo  else ''
            folio = str(row[col_folio]).strip() if col_folio else ''
            rut   = str(row[col_rut]).strip()   if col_rut   else ''
            rs    = str(row[col_rs]).strip()    if col_rs    else ''

            if rut in ('', 'nan') or folio in ('', 'nan'):
                continue

            fecha  = parsear_fecha(row[col_fecha]) if col_fecha else None
            exento = parsear_monto(row[col_exento]) if col_exento else 0.0
            neto   = parsear_monto(row[col_neto])   if col_neto   else 0.0
            iva    = parsear_monto(row[col_iva])     if col_iva    else 0.0
            total  = parsear_monto(row[col_total])   if col_total  else 0.0

            if DocumentoSII.query.filter_by(
                empresa_id=empresa_id, tipo_libro='VENTAS',
                tipo_dte=tipo, folio=folio, rut_contraparte=rut
            ).first():
                continue

            doc = DocumentoSII(
                empresa_id=empresa_id,
                tipo_libro='VENTAS',
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
