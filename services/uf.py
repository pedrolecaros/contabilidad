"""
Servicio UF — consulta y persiste valores UF desde mindicador.cl.

Interfaz pública:
  fetch_year(anio)  -> int   (cantidad de valores actualizados)

No importa Flask. Requiere contexto de aplicación SQLAlchemy activo.
Si la red no está disponible, retorna 0 silenciosamente.
"""
from datetime import date


_API_URL = 'https://mindicador.cl/api/uf/{anio}'
_HEADERS  = {'User-Agent': 'Mozilla/5.0 (compatible; contabilidad-app)'}


def fetch_year(anio: int) -> int:
    """Descarga todos los valores UF del año `anio` y los persiste.

    Retorna la cantidad de registros insertados/actualizados.
    Lanza excepción si la red falla — el caller decide si capturar o propagar.
    """
    import requests
    from models import db, ValorUF

    r = requests.get(_API_URL.format(anio=anio), timeout=20, headers=_HEADERS)
    if r.status_code != 200:
        raise ConnectionError(f'mindicador.cl respondió HTTP {r.status_code}')

    actualizados = 0
    for item in r.json().get('serie', []):
        raw = item.get('fecha', '')[:10]
        try:
            fecha = date.fromisoformat(raw)
            if fecha.year != anio:
                continue
            valor = float(item['valor'])
            existing = ValorUF.query.filter_by(fecha=fecha).first()
            if existing:
                existing.valor = valor
            else:
                db.session.add(ValorUF(fecha=fecha, valor=valor))
            actualizados += 1
        except Exception:
            pass

    if actualizados:
        db.session.commit()
    return actualizados


def fetch_today_if_missing(app) -> None:
    """Llama fetch_year para el año actual si falta el valor de hoy.

    Diseñado para correr en un thread de background al arrancar la app.
    Silencia cualquier error de red.
    """
    with app.app_context():
        from models import ValorUF
        hoy = date.today()
        try:
            if ValorUF.query.filter_by(fecha=hoy).first():
                return
            fetch_year(hoy.year)
        except Exception:
            pass
