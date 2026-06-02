"""Servicio de bitácora — registra eventos en la tabla historial.

Diseñado para llamar manualmente desde rutas (más explícito que listeners SQLAlchemy
y evita ruido por cada commit interno)."""
import json
from datetime import date, datetime
from models import db, Historial


def _serializable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _snapshot(obj, fields):
    return {f: _serializable(getattr(obj, f, None)) for f in fields}


def log(accion: str, tipo_objeto: str, *, empresa_id=None, objeto_id=None,
        descripcion: str = '', datos: dict | None = None, revertible: bool = False) -> Historial:
    h = Historial(
        empresa_id=empresa_id,
        accion=accion,
        tipo_objeto=tipo_objeto,
        objeto_id=objeto_id,
        descripcion=(descripcion or '')[:480],
        datos_json=json.dumps(datos, default=str, ensure_ascii=False) if datos else None,
        revertible=revertible,
    )
    db.session.add(h)
    return h


def log_asiento(accion: str, asiento, descripcion: str = '', revertible: bool = False):
    snap = _snapshot(asiento, ['id', 'empresa_id', 'fecha', 'numero', 'descripcion',
                                'origen', 'estado'])
    snap['lineas'] = [
        {'cuenta_id': l.cuenta_id, 'contraparte_id': l.contraparte_id,
         'debe': l.debe, 'haber': l.haber, 'descripcion': l.descripcion, 'orden': l.orden}
        for l in (asiento.lineas or [])
    ]
    return log(accion, 'ASIENTO',
               empresa_id=asiento.empresa_id, objeto_id=asiento.id,
               descripcion=descripcion or (asiento.descripcion or '')[:200],
               datos=snap, revertible=revertible)


def log_importacion(archivo, descripcion: str = ''):
    snap = _snapshot(archivo, ['id', 'empresa_id', 'tipo', 'nombre_archivo',
                                'periodo', 'ndocs', 'sha256', 'fecha_importacion'])
    return log('IMPORTAR', 'ARCHIVO_IMPORTADO',
               empresa_id=archivo.empresa_id, objeto_id=archivo.id,
               descripcion=descripcion or f"{archivo.tipo} {archivo.periodo or ''} — {archivo.ndocs} doc(s)",
               datos=snap, revertible=True)


def log_conciliacion(conc, descripcion: str = ''):
    snap = _snapshot(conc, ['id', 'empresa_id', 'fecha', 'descripcion', 'tipo',
                            'contraparte_id', 'respaldo_url'])
    return log('CONCILIAR', 'CONCILIACION',
               empresa_id=conc.empresa_id, objeto_id=conc.id,
               descripcion=descripcion or (conc.descripcion or '')[:200],
               datos=snap, revertible=True)
