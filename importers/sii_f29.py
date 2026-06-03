"""
Descarga del F29 mensual desde el portal SII.

NOTA: el portal de declaraciones del SII cambia con frecuencia. Este módulo
funciona en modo "mejor esfuerzo": navega al detalle del F29 enviado y parsea
los códigos clave del HTML. Si la página cambia, levantamos F29NotFoundError
con un mensaje claro y el usuario puede subir el PDF manual mientras se
adapta el parser.

Codigos relevantes del F29:
  - 538  IVA Débito Fiscal
  - 547  IVA Crédito Fiscal
  - 89   PPM neto del mes
  - 39   Retención honorarios 10% (Ley antigua)
  - 151  Retención honorarios escalada (Ley 21.133)
  - 91   Total a pagar (después de reajustes)
  - 92   Reajustes / intereses / multas
"""
import re
import subprocess
import tempfile
import time
from typing import Dict, Tuple

from .sii_scraper import _launch, _login, _logout, SIILoginError


# El portal SII de consulta de declaraciones cambia con frecuencia.
# Probamos varias URLs candidatas — la primera que cargue una página VÁLIDA se usa.
URLS_DECLARACIONES = [
    'https://misiir.sii.cl/cgi_misii/siihome.cgi',
    'https://www4.sii.cl/iccmsiimi/',
    'https://zeus.sii.cl/dii_doc/dii_decl/dii_decl.htm',
    'https://www4.sii.cl/iadmindecweb/dichweb/Consultas/declaracionMensual',
    'https://www4.sii.cl/eluuui/eluu/consultaIVA.html',
]
URL_F29_PUBLICAR = 'https://www4.sii.cl/iadmindecweb/dichweb/ConsultaPublicarF29Servlet'


def _pagina_invalida(html: str) -> bool:
    """Detecta páginas de error / not found del SII."""
    h = html.lower()
    # Páginas muy cortas suelen ser errores
    if len(h) < 1000 and ('error 404' in h or '404' in h or 'not found' in h):
        return True
    return ('no se encontró la página' in h or
            'no se encontro la pagina' in h or
            'página no encontrada' in h or
            'pagina no encontrada' in h or
            'error 404' in h[:5000])


class F29NotFoundError(Exception):
    """No se pudo localizar el F29 enviado para ese período."""


class F29DownloadError(Exception):
    """Error general al descargar/parsear el F29."""


class F29ParseError(Exception):
    """No se pudieron parsear códigos válidos del F29."""


# Códigos que extraemos del HTML del F29.
_CODIGOS_CLAVE = ('89', '39', '151', '538', '547', '91', '92', '142', '563',
                  '062', '077', '111', '115', '595', '763')


def _parsear_numero_es(s: str) -> float:
    """Parsea un número chileno/español tolerando puntos miles y coma decimal.

    Heurística:
      - "1.234.567"  → 1234567  (miles)
      - "1.234,56"   → 1234.56  (miles + decimal)
      - "0,125"      → 0.125    (decimal)
      - "0.125"      → 0.125    (decimal, porque parte entera es "0")
      - "100.000"    → 100000   (miles)
      - "1234"       → 1234
    """
    s = s.strip()
    if not s:
        raise ValueError('vacío')
    # Si tiene coma: la coma es decimal y los puntos son miles
    if ',' in s:
        return float(s.replace('.', '').replace(',', '.'))
    # Solo puntos: heurística — si la parte entera es "0", es decimal
    if '.' in s:
        partes = s.split('.')
        if partes[0] == '0' and len(partes) == 2:
            return float(s)  # "0.125" → 0.125
        # Si hay un solo punto y la parte derecha no tiene 3 dígitos, es decimal
        if len(partes) == 2 and len(partes[1]) != 3:
            return float(s)
        # Sino, todos los puntos son miles
        return float(s.replace('.', ''))
    return float(s)


def _parsear_codigos(html: str) -> Dict[str, float]:
    """Extrae pares (codigo, valor) del HTML del F29.

    Estrategias soportadas:
      1) Pares 'C89 ... $ 123.456' en celdas o atributos
      2) Inputs <input id="cod_89" value="123456">
      3) Texto plano 'Código 89 ... 123.456'
    """
    valores: Dict[str, float] = {}

    # Estrategia 4 (PDF compacto SII): la más confiable, corre primero.
    # Estrategia 4: F29/F22 compacto (PDF SII).
    # Filas con 1+ columnas tipo "<código> <descripción> <valor>".
    # Encuentro todos los códigos por línea (los que van seguidos de descripción
    # en letras) y para cada uno el valor es el último número del segmento que
    # va hasta el siguiente código.
    VALOR_OK = r'(0|-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d{4,}(?:[\.,]\d+)?)'
    NUM_REGEX = re.compile(
        r'(-?(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[\.,]\d+)?))'
    )
    for linea in html.split('\n'):
        # códigos en la línea: número 2-4 dígitos seguido de descripción
        codigos_pos = list(re.finditer(
            r'(?<![\d.])(\d{2,4})(?=\s{2,}[A-ZÁÉÍÓÚÑa-záéíóúñ])', linea))
        for i, m in enumerate(codigos_pos):
            cod = m.group(1).lstrip('0') or '0'
            if cod in valores:
                continue
            start = m.end()
            end = codigos_pos[i+1].start() if i+1 < len(codigos_pos) else len(linea)
            segmento = linea[start:end]
            # último número "limpio" del segmento (formato miles, decimal o entero)
            nums = list(NUM_REGEX.finditer(segmento))
            if not nums:
                continue
            txt = nums[-1].group(1)
            # rechazar fechas tipo "04/2026" o similares: si el último número
            # está adyacente a un slash/fecha, lo dejamos pasar pero igual va
            # como número entero. Heurística: descartar si hay '/' inmediatamente
            # antes o después.
            s, e = nums[-1].start(), nums[-1].end()
            ctx_l = segmento[max(0, s-2):s]
            ctx_r = segmento[e:e+2]
            if '/' in ctx_l or '/' in ctx_r:
                continue
            try:
                neg = txt.startswith('-')
                valor = _parsear_numero_es(txt.lstrip('-'))
                if neg:
                    valor = -valor
                valores[cod] = valor
            except ValueError:
                continue

    # Estrategia 5: línea de totales tipo "85    448.942    +"
    # (código junto al valor sin descripción intermedia, terminada en +/-/=)
    for linea in html.split('\n'):
        for m in re.finditer(
                r'(?<![\d.])(\d{2,4})\s{2,}' + VALOR_OK + r'\s+[\+\-=](?:\s|$)',
                linea):
            cod = m.group(1).lstrip('0') or '0'
            if cod in valores:
                continue
            try:
                txt = m.group(2)
                neg = txt.startswith('-')
                valor = _parsear_numero_es(txt.lstrip('-'))
                if neg:
                    valor = -valor
                valores[cod] = valor
            except ValueError:
                continue

    # Estrategia 6: "<TEXTO> <cod 2-3 dig> <valor> [+/-/=]"
    # Para "Impuesto Adeudado   90   123.456 +" o "TOTAL A PAGAR ... 91 ... 150.997"
    for m in re.finditer(
            r'(?:[A-ZÁÉÍÓÚÑa-záéíóúñ]\S*\s){1,}(\d{2,3})\s+' + VALOR_OK + r'\s*[\+\-=]?',
            html):
        cod = m.group(1).lstrip('0') or '0'
        if cod in valores:
            continue
        try:
            txt = m.group(2)
            neg = txt.startswith('-')
            valor = _parsear_numero_es(txt.lstrip('-'))
            if neg:
                valor = -valor
            valores[cod] = valor
        except ValueError:
            continue

    # ── Legacy strategies (HTML / texto plano sin formato compacto) ─────────
    # Solo capturan lo que las 4/5/6 no encontraron.

    # Estrategia legacy 1: <input ... id="cod_<NN>" value="<num>">
    for m in re.finditer(
            r'(?:id|name)\s*=\s*["\']?(?:cod_?|c_)?(\d{2,4})["\']?\s+[^>]*value\s*=\s*["\']?([\-\d\.,]+)',
            html, flags=re.IGNORECASE):
        cod = m.group(1).lstrip('0') or '0'
        if cod in valores:
            continue
        try:
            valores[cod] = float(m.group(2).replace('.', '').replace(',', '.'))
        except ValueError:
            continue

    # Estrategia legacy 2: "Código 89" ... número
    for m in re.finditer(
            r'(?:C[óo]digo|C\.?|Cod\.?)\s*(\d{2,4})[^\d\$]{1,80}\$?\s*([\-\d\.,]+)',
            html, flags=re.IGNORECASE):
        cod = m.group(1).lstrip('0') or '0'
        if cod in valores:
            continue
        try:
            valores[cod] = float(m.group(2).replace('.', '').replace(',', '.'))
        except ValueError:
            continue

    # Estrategia legacy 3: tabla con <td>89</td><td>123.456</td>
    for m in re.finditer(
            r'<td[^>]*>\s*(\d{2,4})\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*\$?\s*([\-\d\.,]+)',
            html, flags=re.IGNORECASE):
        cod = m.group(1).lstrip('0') or '0'
        if cod in valores:
            continue
        try:
            valores[cod] = float(m.group(2).replace('.', '').replace(',', '.'))
        except ValueError:
            continue

    # Filtrar códigos de etiqueta del encabezado (01-09, 55, 60, etc. son
    # números de campo del PDF, no códigos del F29 con monto real).
    LABELS_DESCARTAR = {
        '1', '2', '3', '5', '6', '7', '8', '9',           # Apellido, RUT, Folio, Calle, Comuna, Tel
        '15',                                              # Periodo (es etiqueta, el período va aparte)
        '55', '60', '314', '915', '922', '610',            # Correo, %Condonación, Rep, Fecha, N°Resolución, Número
        '93', '94', '795',                                 # Campos en blanco / total con recargo
    }
    return {k: v for k, v in valores.items()
            if k.isdigit() and len(k) <= 4 and k not in LABELS_DESCARTAR}


def _navegar_a_listado(page, periodo_yyyymm: str, debug_dir: str | None = None):
    """Navega a la consulta de declaraciones mensuales y filtra por el período.
    Retorna (html_resultado, lista_pasos_log)."""
    pasos = []
    anio = periodo_yyyymm[:4]
    mes  = periodo_yyyymm[4:6]

    # Arrancamos en misiir (panel personal del SII) y de ahí seguimos al servicio F29
    try:
        page.goto('https://misiir.sii.cl/cgi_misii/siihome.cgi',
                  wait_until='domcontentloaded', timeout=20000)
        time.sleep(2)
        pasos.append(f'  ✓ misiir cargado ({page.url[:80]})')
    except Exception as e:
        pasos.append(f'  · misiir falló: {e}')

    # Intentamos navegar al servicio F29 desde misiir.
    # En el HTML del SII vemos links del tipo `<a href="javascript:linkexterno(15)">Consulta Integral F29</a>`.
    # linkexterno() abre nueva ventana o navega — manejamos ambos casos.
    nueva_pagina = page
    # Estrategia 1: ejecutar linkexterno() directamente (más confiable)
    for cod, label in [(15, 'linkexterno(15) [Consulta Integral F29]'),
                        (16, 'linkexterno(16) [Consulta seguimiento F29/F50]')]:
        try:
            with page.context.expect_page(timeout=10000) as nuevo:
                page.evaluate(f'linkexterno({cod})')
            nueva_pagina = nuevo.value
            nueva_pagina.wait_for_load_state('domcontentloaded', timeout=15000)
            time.sleep(2)
            pasos.append(f'  ✓ JS {label} → popup {nueva_pagina.url[:80]}')
            break
        except Exception as e:
            # Sin popup — quizás navega en la misma ventana
            try:
                page.evaluate(f'linkexterno({cod})')
                page.wait_for_load_state('domcontentloaded', timeout=10000)
                time.sleep(2)
                if 'misiir' not in page.url:
                    pasos.append(f'  ✓ JS {label} → mismo tab {page.url[:80]}')
                    nueva_pagina = page
                    break
            except Exception:
                pass
            pasos.append(f'  · JS {label} no funcionó: {type(e).__name__}')

    # Estrategia 2: si JS falló, intentar click directo con selectores corregidos
    if nueva_pagina is page and 'misiir' in page.url:
        for selector, label in [
            ('a[href*="linkexterno(15)"]', 'href linkexterno(15)'),
            ('a[href*="linkexterno(16)"]', 'href linkexterno(16)'),
            (':text-is("Consulta Integral F29")', 'texto exacto "Consulta Integral F29"'),
            ('a:has-text("Consulta y seguimiento")', 'link "Consulta y seguimiento"'),
            ('a[href*="3266"]', 'href servicios_online/...3266'),
        ]:
            try:
                with page.context.expect_page(timeout=8000) as nuevo:
                    page.click(selector, timeout=3000)
                nueva_pagina = nuevo.value
                nueva_pagina.wait_for_load_state('domcontentloaded', timeout=15000)
                time.sleep(2)
                pasos.append(f'  ✓ click {label} → popup {nueva_pagina.url[:80]}')
                break
            except Exception:
                try:
                    page.click(selector, timeout=2000)
                    page.wait_for_load_state('domcontentloaded', timeout=10000)
                    time.sleep(2)
                    if 'misiir' not in page.url:
                        pasos.append(f'  ✓ click {label} → mismo tab {page.url[:80]}')
                        nueva_pagina = page
                        break
                except Exception:
                    pasos.append(f'  · selector {label} no disponible')

    page = nueva_pagina
    url_funcionando = page.url
    if 'misiir' in url_funcionando:
        # No pudimos salir de misiir. Probamos URLs directas como fallback.
        pasos.append('  ⚠ No se pudo navegar desde misiir, probando URLs directas…')
        url_funcionando = None
        for url in URLS_DECLARACIONES[1:]:  # saltamos misiir que ya probamos
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=20000)
                time.sleep(2)
                cur = page.url
                html = page.content()
                if 'InicioAutenticacion' in cur or 'login' in cur.lower():
                    pasos.append(f'  · {url} → redirige a login ({cur[:80]})')
                    continue
                if _pagina_invalida(html):
                    pasos.append(f'  · {url} → "página no encontrada"')
                    continue
                pasos.append(f'  ✓ {url} → {cur[:80]}')
                url_funcionando = url
                break
            except Exception as e:
                pasos.append(f'  · {url} → error {type(e).__name__}: {str(e)[:60]}')

    if not url_funcionando:
        if debug_dir:
            try:
                page.screenshot(path=f'{debug_dir}/f29_no_url.png', full_page=True)
                with open(f'{debug_dir}/f29_no_url.html', 'w') as f:
                    f.write(page.content())
            except Exception:
                pass
        raise F29NotFoundError(
            'Ninguna URL de consulta de declaraciones del SII funcionó. '
            'Usá "Subir PDF" mientras adapto el scraper.\n\n'
            'URLs probadas:\n' + '\n'.join(pasos)
        )

    # La página sifmConsultaInternet tiene los menús en la página principal y el
    # contenido en un iframe `sifmConsulta`. Click en "Buscar Formulario" lleva
    # al filtro de búsqueda por período.
    if 'sifmConsulta' in page.url:
        try:
            page.click('button:has-text("Buscar Formulario")', timeout=5000)
            time.sleep(3)
            pasos.append(f'  ✓ Click "Buscar Formulario" → {page.url[:80]}')
        except Exception as e:
            pasos.append(f'  · Click "Buscar Formulario" falló: {type(e).__name__}')

    # Guardar siempre el iframe principal por si los selectores están ahí
    if debug_dir:
        try:
            for i, fr in enumerate(page.frames):
                if fr == page.main_frame:
                    continue
                with open(f'{debug_dir}/iframe_{i}_{fr.name or "noname"}.html', 'w') as f:
                    f.write(fr.content())
                pasos.append(f'  · iframe[{i}] name={fr.name!r} url={fr.url[:80]}')
        except Exception:
            pass

    # Intentar seleccionar año/mes en página principal Y en cada iframe
    posibles_selects_anio = ['select[name="ANO"]', 'select[name="anio"]',
                              'select[name="anioPeriodo"]', 'select#ddYear',
                              'select[name="ANOTRIB"]', 'select[name="cboAno"]',
                              'select[name="ano"]', 'select[name="aaaa"]']
    posibles_selects_mes  = ['select[name="MES"]', 'select[name="mes"]',
                              'select[name="mesPeriodo"]', 'select#ddMonth',
                              'select[name="MESTRIB"]', 'select[name="cboMes"]',
                              'select[name="mm"]']

    # Contextos a probar: main + iframes
    contextos = [page] + [fr for fr in page.frames if fr != page.main_frame]

    sel_anio_ok = False
    ctx_anio = None
    for ctx in contextos:
        for sel in posibles_selects_anio:
            try:
                ctx.select_option(sel, anio, timeout=1500)
                ctx_label = 'main' if ctx is page else f'iframe[{ctx.name or "noname"}]'
                pasos.append(f'  ✓ Año en {ctx_label}/{sel}')
                sel_anio_ok = True
                ctx_anio = ctx
                break
            except Exception:
                continue
        if sel_anio_ok:
            break
    sel_mes_ok = False
    if sel_anio_ok:
        for sel in posibles_selects_mes:
            try:
                ctx_anio.select_option(sel, mes, timeout=2000)
                pasos.append(f'  ✓ Mes seleccionado en {sel}')
                sel_mes_ok = True
                break
            except Exception:
                continue

    if sel_anio_ok and sel_mes_ok:
        for sel in ['button:has-text("Consultar")', 'input[type="submit"]',
                     'a:has-text("Consultar")', 'button[type="submit"]',
                     'button:has-text("Buscar")', 'input[value="Consultar"]',
                     'input[value="Buscar"]']:
            try:
                ctx_anio.click(sel, timeout=2000)
                pasos.append(f'  ✓ Click consultar en {sel}')
                time.sleep(3)
                break
            except Exception:
                continue
    else:
        pasos.append('  ⚠ No se pudo seleccionar año/mes — devolviendo página tal cual')

    # HTML final: concatenamos contenido de main + iframes para que el parser
    # encuentre los códigos vivan donde vivan.
    partes = [page.content()]
    for fr in page.frames:
        if fr != page.main_frame:
            try:
                partes.append(fr.content())
            except Exception:
                pass
    html_final = '\n'.join(partes)
    if debug_dir:
        try:
            page.screenshot(path=f'{debug_dir}/f29_listado.png', full_page=True)
            with open(f'{debug_dir}/f29_listado.html', 'w') as f:
                f.write(html_final)
        except Exception:
            pass

    return html_final, pasos


def _extraer_folio_f29(html_listado: str) -> str | None:
    """De la página de listado de declaraciones, intenta encontrar el folio del F29."""
    # Buscamos patrones tipo: F29 ... folio 1234567
    m = re.search(r'F\s*29[^0-9]{0,40}(\d{6,12})', html_listado)
    if m:
        return m.group(1)
    # Otro patrón: input hidden con folio
    m = re.search(r'folio\s*=\s*["\']?(\d{6,12})', html_listado, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def descargar_f29(rut: str, clave: str, periodo: str,
                  progress_cb=None) -> Tuple[Dict[str, float], str, str]:
    """Descarga el F29 enviado del período dado.

    Args:
        rut, clave: credenciales SII
        periodo: 'YYYY-MM'
        progress_cb: callback opcional (pct, mensaje)

    Returns:
        (codigos_dict, folio, html_respaldo)

    Raises:
        SIILoginError, F29NotFoundError, F29DownloadError
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    import os

    periodo_sii = periodo.replace('-', '')
    debug_dir = f'/tmp/sii_f29_debug_{periodo}'
    os.makedirs(debug_dir, exist_ok=True)

    with sync_playwright() as pw:
        browser, ctx = _launch(pw)
        page = ctx.new_page()
        try:
            if progress_cb:
                progress_cb(10, 'Conectando al portal SII…')
            _login(page, rut, clave)

            if progress_cb:
                progress_cb(40, f'Buscando F29 del período {periodo}…')
            html_listado, pasos = _navegar_a_listado(page, periodo_sii, debug_dir=debug_dir)

            folio = _extraer_folio_f29(html_listado)
            if not folio:
                # Intentamos también el detalle directo sin folio
                # (algunas vistas muestran un solo F29 al filtrar)
                codigos = _parsear_codigos(html_listado)
                if not codigos or '89' not in codigos:
                    raise F29NotFoundError(
                        f'No se encontró el F29 enviado para el período {periodo}.\n\n'
                        f'Pasos del scraper:\n' + '\n'.join(pasos) + '\n\n'
                        f'Diagnóstico: screenshot + HTML en {debug_dir}/\n'
                        f'(esto sirve para adaptar el parser; mientras tanto usá "Subir PDF")'
                    )
                return codigos, '', html_listado

            if progress_cb:
                progress_cb(70, f'Descargando detalle F29 folio {folio}…')

            # Intentamos abrir el detalle. URL típica con folio en query.
            for url in (f'{URL_F29_PUBLICAR}?folio={folio}',
                         f'{URL_F29_PUBLICAR}?FOLIO={folio}'):
                try:
                    page.goto(url, wait_until='domcontentloaded', timeout=20000)
                    time.sleep(1.5)
                    if 'F29' in page.content() or 'PPM' in page.content():
                        break
                except Exception:
                    continue

            html_detalle = page.content()
            codigos = _parsear_codigos(html_detalle)

            if not codigos:
                raise F29DownloadError(
                    'No se pudo parsear el F29. Subí el PDF manual mientras adapto el parser.'
                )

            if progress_cb:
                progress_cb(95, f'F29 leído: {len(codigos)} códigos detectados')

            return codigos, folio, html_detalle

        except SIILoginError:
            raise
        except (F29NotFoundError, F29DownloadError):
            raise
        except PWTimeout as e:
            raise F29DownloadError(f'Timeout al conectar con SII: {e}')
        except Exception as e:
            raise F29DownloadError(f'Error inesperado: {e}')
        finally:
            _logout(page)
            browser.close()


# ── Subida manual de PDF / HTML ────────────────────────────────────────────────

def _extraer_texto_pdf(contenido_bytes: bytes) -> str:
    """Usa pdftotext (poppler) para extraer texto del F29 PDF."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as f:
        f.write(contenido_bytes)
        f.flush()
        proc = subprocess.run(
            ['pdftotext', '-layout', f.name, '-'],
            capture_output=True, check=True, timeout=30,
        )
    return proc.stdout.decode('utf-8', errors='ignore')


def _extraer_periodo_f29(texto: str) -> str | None:
    """Detecta el período (YYYY-MM) declarado en el F29."""
    # F29 compacto SII: "PERIODO [15] 202601" (formato YYYYMM)
    m = re.search(r'PERIODO\s*\[?15\]?\s+(\d{4})(\d{2})\b', texto, re.IGNORECASE)
    if m:
        return f'{m.group(1)}-{m.group(2)}'
    # Formato típico: "Período Tributario MM/YYYY" o "Periodo MM-YYYY"
    m = re.search(r'Per[íi]odo\s*(?:Tributario)?\s*[:\-]?\s*(\d{2})[/\-](\d{4})', texto, re.IGNORECASE)
    if m:
        return f'{m.group(2)}-{m.group(1)}'
    # YYYYMM suelto cerca de la palabra "PERIODO"
    m = re.search(r'PERIODO[^\d]{0,40}(\d{4})(\d{2})\b', texto, re.IGNORECASE)
    if m:
        return f'{m.group(1)}-{m.group(2)}'
    # Mes en texto: "ENERO 2026"
    meses = {'ENERO':'01','FEBRERO':'02','MARZO':'03','ABRIL':'04','MAYO':'05','JUNIO':'06',
             'JULIO':'07','AGOSTO':'08','SEPTIEMBRE':'09','OCTUBRE':'10','NOVIEMBRE':'11','DICIEMBRE':'12'}
    for nombre, num in meses.items():
        m = re.search(rf'{nombre}\s+(\d{{4}})', texto, re.IGNORECASE)
        if m:
            return f'{m.group(1)}-{num}'
    return None


def _extraer_folio_pdf(texto: str) -> str | None:
    """Folio del F29 en el PDF."""
    # F29 compacto: "FOLIO [07] 8783025646"
    m = re.search(r'FOLIO\s*\[?07\]?\s+(\d{6,15})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'Folio\s*[:\-]?\s*(\d{6,15})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _extraer_rut_pdf(texto: str) -> str | None:
    """Extrae el RUT del contribuyente del PDF F29."""
    # F29 compacto: "RUT [03] 76.703.937-9"
    m = re.search(r'RUT\s*\[?03\]?\s+([\d\.]+\-[\dkK])', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    # Patrón general: RUT chileno xx.xxx.xxx-y
    m = re.search(r'\b(\d{1,2}\.\d{3}\.\d{3}\-[\dkK])\b', texto)
    if m:
        return m.group(1)
    return None


def _normalizar_rut(rut: str | None) -> str:
    """Quita puntos, guiones y espacios; devuelve dígitos+DV en mayúscula."""
    if not rut:
        return ''
    return re.sub(r'[\.\-\s]', '', rut).upper()


def parsear_pdf(contenido_bytes: bytes) -> Tuple[Dict[str, float], str | None, str | None, str | None]:
    """Parsea un PDF del F29 enviado.

    Returns:
        (codigos_dict, periodo_YYYY-MM, folio, rut)

    Raises:
        F29ParseError si no detecta códigos.
    """
    try:
        texto = _extraer_texto_pdf(contenido_bytes)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise F29ParseError(f'No se pudo leer el PDF: {e}')

    codigos = _parsear_codigos(texto)
    if not codigos:
        raise F29ParseError(
            'No se reconocieron códigos del F29 en el PDF. '
            '¿Está el PDF correcto? (Estado de declaración / Comprobante de envío)'
        )

    periodo = _extraer_periodo_f29(texto)
    folio = _extraer_folio_pdf(texto)
    rut = _extraer_rut_pdf(texto)
    return codigos, periodo, folio, rut
