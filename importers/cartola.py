"""
Importador de cartolas bancarias.
Detecta automáticamente el formato:
  - Banco de Chile CSV: fila 0 = metadata empresa/cuenta, fila 1 = headers reales
    Columnas: Fecha; Detalle Movimiento; Cheque o Cargo; Deposito o Abono; Saldo
    Montos con prefijo '+' y ceros leading.
  - Santander XLSX: filas 0-14 metadata, fila 15 = headers
    Columnas: MONTO; DESCRIPCIÓN MOVIMIENTO; ...; FECHA; ...; CARGO/ABONO (A/C)
  - Fallback genérico para otros bancos.
"""
import io
import pandas as pd
from models import db, MovimientoBanco
from .base import parsear_fecha, parsear_monto, ENCODINGS


# ── Banco de Chile ──────────────────────────────────────────────────────────

def _es_banco_chile_csv(contenido):
    """Detecta si el CSV es de Banco de Chile (primera línea tiene 'cta:')."""
    try:
        primera = contenido[:200].decode('latin-1', errors='ignore').lower()
        return 'cta:' in primera
    except Exception:
        return False


def _importar_banco_chile_csv(contenido, empresa_id, banco, cuenta_bancaria, filename):
    """
    Banco de Chile CSV:
    - Fila 0: "Parque Sur SpA (077465483-6) cta:008870371109"
    - Fila 1: headers reales
    - Montos: +0002040542 (cargo) o 00000000000 (cero)
    """
    df = None
    for enc in ENCODINGS:
        try:
            df = pd.read_csv(io.BytesIO(contenido), sep=';', encoding=enc,
                             dtype=str, skiprows=1, skipinitialspace=True)
            if len(df.columns) >= 4:
                break
        except Exception:
            continue
    if df is None:
        raise ValueError("No se pudo leer la cartola Banco de Chile")

    # Extraer cuenta de la primera línea si no se proporcionó
    if not cuenta_bancaria:
        try:
            primera = contenido.decode('latin-1', errors='ignore').split('\n')[0]
            if 'cta:' in primera.lower():
                cuenta_bancaria = primera.lower().split('cta:')[1].strip().split(';')[0].strip()
        except Exception:
            pass

    # Normalizar columnas
    import unicodedata
    def limpiar(s):
        s = str(s).strip().lower()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.replace(' ', '_').replace('.', '').replace('/', '_')
    df.columns = [limpiar(c) for c in df.columns]

    # Columnas Banco de Chile
    col_fecha = next((c for c in df.columns if c == 'fecha'), None)
    col_desc  = next((c for c in df.columns if 'detalle' in c or 'descripcion' in c or 'glosa' in c), None)
    col_cargo = next((c for c in df.columns if 'cargo' in c or 'cheque' in c or 'debito' in c), None)
    col_abono = next((c for c in df.columns if 'abono' in c or 'deposito' in c or 'credito' in c), None)
    col_saldo = next((c for c in df.columns if 'saldo' in c), None)

    importados, errores = 0, []

    for i, row in df.iterrows():
        try:
            fecha = parsear_fecha(row[col_fecha]) if col_fecha else None
            if not fecha:
                continue

            desc  = str(row[col_desc]).strip() if col_desc else ''
            cargo = parsear_monto(row[col_cargo]) if col_cargo else 0.0
            abono = parsear_monto(row[col_abono]) if col_abono else 0.0
            saldo = parsear_monto(row[col_saldo]) if col_saldo else None

            if cargo == 0 and abono == 0:
                continue

            mov = MovimientoBanco(
                empresa_id=empresa_id,
                banco=banco or 'Banco de Chile',
                cuenta_bancaria=cuenta_bancaria,
                fecha=fecha,
                descripcion=desc,
                cargo=cargo,
                abono=abono,
                saldo=saldo,
                archivo_origen=filename,
            )
            db.session.add(mov)
            importados += 1
        except Exception as e:
            errores.append(f"Fila {i+2}: {e}")

    db.session.commit()
    return {'importados': importados, 'errores': errores}


# ── Santander ───────────────────────────────────────────────────────────────

def _es_santander_xlsx(df_raw):
    """Detecta si el XLSX es de Santander buscando 'Cartolas históricas' en fila 0."""
    try:
        return 'cartola' in str(df_raw.iloc[0, 0]).lower()
    except Exception:
        return False


def _importar_santander_xlsx(contenido, empresa_id, banco, cuenta_bancaria, filename):
    """
    Santander XLSX:
    - Filas 0-14: metadata
    - Fila 15: headers: MONTO | DESCRIPCIÓN MOVIMIENTO | (vacío) | FECHA | N° DOCUMENTO | SUCURSAL | (vacío) | CARGO/ABONO
    - CARGO/ABONO: 'A' = abono, 'C' = cargo
    - MONTO: entero, positivo o negativo
    """
    df = pd.read_excel(io.BytesIO(contenido), engine='openpyxl', dtype=str, header=15)

    # Extraer cuenta de las filas de metadata si no se proporcionó
    if not cuenta_bancaria:
        try:
            meta = pd.read_excel(io.BytesIO(contenido), engine='openpyxl',
                                 dtype=str, header=None)
            for idx in range(15):
                fila = str(meta.iloc[idx, 0])
                if 'cuenta corriente' in fila.lower() or 'n°:' in fila.lower():
                    cuenta_bancaria = fila.split('N°:')[-1].strip().split(' ')[0] if 'N°:' in fila else ''
                    break
        except Exception:
            pass

    import unicodedata
    def limpiar(s):
        s = str(s).strip().lower()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.replace(' ', '_').replace('.', '').replace('/', '_').replace('°', '')
    df.columns = [limpiar(c) for c in df.columns]

    col_monto   = next((c for c in df.columns if c == 'monto'), None)
    col_desc    = next((c for c in df.columns if 'descripcion' in c or 'movimiento' in c), None)
    col_fecha   = next((c for c in df.columns if c == 'fecha'), None)
    col_tipo    = next((c for c in df.columns if 'cargo' in c and 'abono' in c), None)

    importados, errores = 0, []

    for i, row in df.iterrows():
        try:
            # Stop when MONTO contains a non-numeric section header ("Resumen comisiones", etc.)
            if col_monto:
                monto_raw = str(row[col_monto]).strip()
                if monto_raw.lower() not in ('nan', ''):
                    try:
                        float(monto_raw.replace('.', '').replace(',', '.'))
                    except ValueError:
                        break

            fecha = parsear_fecha(row[col_fecha]) if col_fecha else None
            if not fecha:
                continue

            desc  = str(row[col_desc]).strip() if col_desc else ''
            monto = parsear_monto(row[col_monto]) if col_monto else 0.0
            tipo  = str(row[col_tipo]).strip().upper() if col_tipo else ''

            if monto == 0:
                continue

            # 'A' = abono (dinero que entra), 'C' = cargo (dinero que sale)
            if tipo == 'A':
                cargo, abono = 0.0, abs(monto)
            elif tipo == 'C':
                cargo, abono = abs(monto), 0.0
            else:
                # Sin columna CARGO/ABONO: usar signo del monto
                cargo = abs(monto) if monto < 0 else 0.0
                abono = monto if monto > 0 else 0.0

            mov = MovimientoBanco(
                empresa_id=empresa_id,
                banco=banco or 'Santander',
                cuenta_bancaria=cuenta_bancaria,
                fecha=fecha,
                descripcion=desc,
                cargo=cargo,
                abono=abono,
                saldo=None,
                archivo_origen=filename,
            )
            db.session.add(mov)
            importados += 1
        except Exception as e:
            errores.append(f"Fila {i+2}: {e}")

    db.session.commit()
    return {'importados': importados, 'errores': errores}


# ── Genérico (fallback) ─────────────────────────────────────────────────────

def _importar_generico(file_storage, empresa_id, banco, cuenta_bancaria):
    from .base import leer_archivo, normalizar_columnas, primera_col

    file_storage.seek(0)
    df = leer_archivo(file_storage)

    # Buscar fila con 'fecha' y usarla como header
    for idx in range(min(20, len(df))):
        row_vals = [str(v).lower().strip() for v in df.iloc[idx].values]
        if any('fecha' in v or 'date' in v for v in row_vals):
            df.columns = df.iloc[idx].values
            df = df.iloc[idx+1:].copy()
            break

    df = normalizar_columnas(df)

    col_fecha = primera_col(df, 'fecha', 'fecha_operacion', 'date')
    col_desc  = primera_col(df, 'descripcion', 'glosa', 'detalle', 'descripcion_movimiento')
    col_cargo = primera_col(df, 'cargo', 'debito', 'cheque_o_cargo', 'retiro')
    col_abono = primera_col(df, 'abono', 'credito', 'deposito_o_abono', 'deposito')
    col_saldo = primera_col(df, 'saldo')

    importados, errores = 0, []
    for i, row in df.iterrows():
        try:
            fecha = parsear_fecha(row[col_fecha]) if col_fecha else None
            if not fecha:
                continue
            desc  = str(row[col_desc]).strip() if col_desc else ''
            cargo = parsear_monto(row[col_cargo]) if col_cargo else 0.0
            abono = parsear_monto(row[col_abono]) if col_abono else 0.0
            saldo = parsear_monto(row[col_saldo]) if col_saldo else None
            if cargo == 0 and abono == 0:
                continue
            mov = MovimientoBanco(
                empresa_id=empresa_id, banco=banco,
                cuenta_bancaria=cuenta_bancaria,
                fecha=fecha, descripcion=desc,
                cargo=cargo, abono=abono, saldo=saldo,
                archivo_origen=file_storage.filename,
            )
            db.session.add(mov)
            importados += 1
        except Exception as e:
            errores.append(f"Fila {i+2}: {e}")
    db.session.commit()
    return {'importados': importados, 'errores': errores}


# ── Entrada principal ───────────────────────────────────────────────────────

def importar(file_storage, empresa_id, banco='', cuenta_bancaria='') -> dict:
    nombre = file_storage.filename.lower()
    contenido = file_storage.read()

    if nombre.endswith('.xlsx'):
        df_raw = pd.read_excel(io.BytesIO(contenido), engine='openpyxl',
                               dtype=str, header=None)
        if _es_santander_xlsx(df_raw):
            return _importar_santander_xlsx(contenido, empresa_id, banco, cuenta_bancaria,
                                            file_storage.filename)
        # Otro XLSX: fallback genérico
        import tempfile, os
        from werkzeug.datastructures import FileStorage
        file_storage.seek(0)
        return _importar_generico(file_storage, empresa_id, banco, cuenta_bancaria)

    if nombre.endswith('.xls'):
        if _es_banco_chile_csv(contenido):
            return _importar_banco_chile_csv(contenido, empresa_id, banco, cuenta_bancaria,
                                             file_storage.filename)
        file_storage.seek(0)
        return _importar_generico(file_storage, empresa_id, banco, cuenta_bancaria)

    # CSV
    if _es_banco_chile_csv(contenido):
        return _importar_banco_chile_csv(contenido, empresa_id, banco, cuenta_bancaria,
                                         file_storage.filename)

    # CSV genérico
    import io as _io
    file_storage.stream = _io.BytesIO(contenido)
    file_storage.seek = file_storage.stream.seek
    return _importar_generico(file_storage, empresa_id, banco, cuenta_bancaria)
