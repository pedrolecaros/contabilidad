"""
Descarga automática de libros SII usando Playwright.

Flujo RCV (Compras + Ventas):
  Login en www4.sii.cl/consdcvinternetui/ → ya estamos en el RCV Angular app.
  Seleccionar período → Consultar → Descargar detalles (Compras).
  Click tab Ventas → Descargar detalles (Ventas).

Flujo Honorarios (BHE recibidas):
  Navegar a BHE → Consultar boletas recibidas → seleccionar período mensual
  → Consultar → Ver informe como planilla electrónica (XLS).
"""
import time
import tempfile
import os
from pathlib import Path

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ImportError:
    class PlaywrightTimeoutError(Exception):  # fallback si playwright no está cargado
        pass


URL_RCV      = 'https://www4.sii.cl/consdcvinternetui/'
URL_BHE_MENU = 'https://loa.sii.cl/cgi_IMT/TMBCOC_MenuConsultasContribRec.cgi'
URL_LOGOUT   = 'https://zeusr.sii.cl/cgi_AUT2000/autTermino.cgi'


class SIILoginError(Exception):
    pass

class SIIDownloadError(Exception):
    pass

class SIIEmptyPeriodError(Exception):
    """Período válido pero sin movimientos — no es un error, solo 0 registros."""
    pass


def _rut_partes(rut: str):
    """'12.345.678-9' → ('12345678', '9')"""
    clean = rut.replace('.', '').replace(' ', '')
    if '-' in clean:
        body, dv = clean.split('-', 1)
    else:
        body, dv = clean[:-1], clean[-1]
    return body.strip(), dv.strip().upper()


def _rut_sin_puntos(rut: str) -> str:
    """'12.345.678-9' → '123456789'  (para el campo #rutcntr del login)"""
    return rut.replace('.', '').replace('-', '').replace(' ', '')


def _screenshot(page, nombre):
    try:
        page.screenshot(path=f'/tmp/sii_{nombre}.png')
    except Exception:
        pass


def _launch(pw):
    browser = pw.chromium.launch(
        headless=True,
        args=['--disable-blink-features=AutomationControlled',
              '--no-sandbox', '--disable-dev-shm-usage'],
    )
    ctx = browser.new_context(
        user_agent=(
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ),
        viewport={'width': 1280, 'height': 900},
        accept_downloads=True,
        java_script_enabled=True,
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, ctx


def _logout(page):
    try:
        page.goto(URL_LOGOUT, wait_until='domcontentloaded', timeout=10000)
    except Exception:
        pass


# ─── Login ────────────────────────────────────────────────────────────────────

def _login(page, rut: str, clave: str):
    """
    Navega al portal RCV (consdcvinternetui) que redirige al formulario real.
    Después del login queda posicionado en el RCV Angular app.
    """
    page.goto(URL_RCV, wait_until='domcontentloaded', timeout=30000)
    time.sleep(1)

    try:
        page.wait_for_selector('#rutcntr', timeout=15000)
    except Exception:
        raise SIILoginError(
            f"No apareció el formulario de login. URL: {page.url}"
        )

    page.fill('#rutcntr', _rut_sin_puntos(rut))
    page.fill('#clave', clave)
    page.click('#bt_ingresar')

    # Esperar que salga de la pantalla de autenticación
    try:
        page.wait_for_url(lambda u: 'InicioAutenticacion' not in u, timeout=25000)
    except Exception:
        pass

    time.sleep(2)

    # Si ya estamos en www4.sii.cl como HOST, el login fue exitoso
    if page.url.startswith('https://www4.sii.cl'):
        return

    page_text = page.content().lower()

    if 'máximo de sesiones' in page_text or 'maximo de sesiones' in page_text \
            or '01.01.131.500' in page_text:
        raise SIILoginError(
            "SII bloqueó el acceso: demasiadas sesiones activas. "
            "Espera unos minutos o cierra sesión en el portal SII."
        )
    if 'demasiados intentos' in page_text or 'bloqueado temporalmente' in page_text:
        raise SIILoginError(
            "SII bloqueó temporalmente (demasiados intentos). "
            "Espera 5-10 minutos."
        )
    if any(x in page_text for x in ['clave incorrecta', 'rut o clave',
                                     'no válido', 'autenticación fallida']):
        raise SIILoginError("RUT o clave incorrectos")
    if 'InicioAutenticacion' in page.url:
        raise SIILoginError("Login fallido — verificar RUT y clave SII")

    # Esperar a estar de vuelta en www4.sii.cl
    try:
        page.wait_for_url(lambda u: 'www4.sii.cl' in u, timeout=15000)
    except Exception:
        pass
    time.sleep(2)


# ─── RCV (Compras y Ventas) ───────────────────────────────────────────────────

def _rcv_asegurar_posicion(page):
    """Navegar al RCV si no estamos ahí, y esperar que Angular cargue."""
    if 'consdcvinternetui' not in page.url:
        page.goto(URL_RCV, wait_until='domcontentloaded', timeout=20000)
    # Esperar que Angular inicialice el select de período
    try:
        page.wait_for_selector('select[ng-model="periodoMes"]', timeout=15000)
    except Exception:
        time.sleep(4)


def _rcv_seleccionar_periodo(page, anio: str, mes_zz: str):
    """
    Selecciona año y mes en el RCV Angular.
    Selectores confirmados: ng-model="periodoAnho" (año), ng-model="periodoMes" (mes).
    """
    # Año: select con ng-model="periodoAnho" (sin name ni id)
    for sel in ['select[ng-model="periodoAnho"]', 'select[ng-model*="anho" i]',
                'select[ng-model*="anno" i]', 'select[ng-model*="anio" i]']:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.select_option(anio)
                break
        except Exception:
            continue

    # Mes: id="periodoMes", ng-model="periodoMes"
    for sel in ['select[id="periodoMes"]', 'select[ng-model="periodoMes"]',
                'select[ng-model*="mes" i]']:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.select_option(mes_zz)
                break
        except Exception:
            continue


def _rcv_consultar(page):
    for sel in ['button:has-text("Consultar")', 'input[value*="Consultar" i]',
                'a:has-text("Consultar")', 'button[type="submit"]']:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click()
                time.sleep(5)
                return
        except Exception:
            continue


_RCV_FRASES_VACIAS = [
    'no existen registros', 'no hay registros', 'sin registros',
    'no se encontraron registros', 'no existen documentos',
    'no hay documentos', 'sin documentos', '0 documentos',
    'sin movimientos', 'no hay movimientos',
    'no existen datos', 'no hay datos', 'sin datos',
    'no hay información de registro',
]


def _rcv_descargar_detalles(page, tipo_label: str, periodo_yyyymm: str) -> bytes:
    """Click en 'Descargar Detalles' y retorna el contenido del archivo."""
    _screenshot(page, f'rcv_{tipo_label}_{periodo_yyyymm}_antes_descarga')

    def _vacio_ahora() -> bool:
        return any(f in page.content().lower() for f in _RCV_FRASES_VACIAS)

    # 1. Pre-check rápido de período vacío.
    if _vacio_ahora():
        raise SIIEmptyPeriodError(
            f"Sin movimientos de {tipo_label} para el período {periodo_yyyymm[:4]}-{periodo_yyyymm[4:6]}."
        )

    # 2. Esperar a que aparezca el botón "Descargar Detalles" (hasta 10s).
    BTN_SELECTORS = (
        'button:has-text("Descargar Detalles"), '
        'a:has-text("Descargar Detalles"), '
        'button[ng-click*="limiteDoc"], '
        'button[ng-click*="descarga"]'
    )
    try:
        page.wait_for_selector(BTN_SELECTORS, timeout=10000, state='visible')
    except PlaywrightTimeoutError:
        # Si no apareció en 10s, verificar si la página declaró vacío.
        if _vacio_ahora():
            raise SIIEmptyPeriodError(
                f"Sin movimientos de {tipo_label} para el período {periodo_yyyymm[:4]}-{periodo_yyyymm[4:6]}."
            )
        raise SIIDownloadError(
            f"No apareció el botón 'Descargar Detalles' en el RCV ({tipo_label}) tras 10s. "
            f"URL: {page.url} — ver /tmp/sii_rcv_{tipo_label}_{periodo_yyyymm}_antes_descarga.png"
        )

    # 3. Click + esperar descarga. Si el botón existe pero el click no dispara
    # descarga en 20s, asumir período vacío (el SII renderiza la tabla resumen
    # con totales en 0 y el botón habilitado pero sin nada que entregar).
    DL_TIMEOUT_MS = 20000
    click_intentado = False
    with tempfile.TemporaryDirectory() as tmpdir:
        for sel in [
            'button:has-text("Descargar Detalles")',
            'button:has-text("Descargar detalles")',
            'a:has-text("Descargar Detalles")',
            'a:has-text("Descargar detalles")',
            'button[ng-click*="limiteDoc"]',
            'button[ng-click*="descarga"]',
        ]:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            try:
                with page.expect_download(timeout=DL_TIMEOUT_MS) as dl_info:
                    loc.first.click()
                    click_intentado = True
                download = dl_info.value
                dest = os.path.join(
                    tmpdir,
                    download.suggested_filename or f'rcv_{tipo_label}_{periodo_yyyymm}.csv'
                )
                download.save_as(dest)
                return Path(dest).read_bytes()
            except PlaywrightTimeoutError:
                # El click pasó pero no hubo descarga; probable período vacío.
                click_intentado = True
                break
            except Exception:
                continue

        # Si llegamos acá, el botón existía pero no produjo descarga.
        if click_intentado or _vacio_ahora():
            raise SIIEmptyPeriodError(
                f"Sin movimientos de {tipo_label} para el período {periodo_yyyymm[:4]}-{periodo_yyyymm[4:6]}."
            )
        raise SIIDownloadError(
            f"No se pudo descargar el detalle RCV ({tipo_label}). "
            f"URL: {page.url} — ver /tmp/sii_rcv_{tipo_label}_{periodo_yyyymm}_antes_descarga.png"
        )


def _rcv_click_tab(page, text: str) -> bool:
    """Click the first VISIBLE anchor whose text matches. Returns True if clicked."""
    loc = page.locator(f'a:has-text("{text}")')
    for i in range(loc.count()):
        el = loc.nth(i)
        try:
            if el.is_visible():
                el.click()
                time.sleep(3)
                return True
        except Exception:
            continue
    return False


def _descargar_compras_y_ventas(page, rut: str, periodo_yyyymm: str,
                                 hacer_compras: bool, hacer_ventas: bool,
                                 progress_cb=None, pct_compras=20, pct_ventas=45) -> dict:
    """
    Descarga compras y/o ventas en una sola visita al RCV.

    Flujo confirmado:
      - Seleccionar período → Consultar (carga tab COMPRA por defecto).
      - Los tabs COMPRA/VENTA tienen múltiples <a> en el DOM; sólo el último es visible.
        Usar _rcv_click_tab que itera todos y hace click en el primero visible.
      - Después de hacer click en VENTA tab, NO volver a hacer Consultar — el segundo
        Consultar resetea el tab a COMPRA. Los datos de VENTA ya están cargados.
      - Botón de descarga: button:has-text("Descargar Detalles")
    """
    anio   = periodo_yyyymm[:4]
    mes_zz = periodo_yyyymm[4:6]
    resultado = {}

    _rcv_asegurar_posicion(page)
    _rcv_seleccionar_periodo(page, anio, mes_zz)
    _rcv_consultar(page)

    # --- COMPRAS (tab activo por defecto tras Consultar) ---
    if hacer_compras:
        if progress_cb:
            progress_cb(pct_compras, 'Descargando Compras…')
        try:
            resultado['compras'] = _rcv_descargar_detalles(page, 'compras', periodo_yyyymm)
        except (SIIDownloadError, SIIEmptyPeriodError) as e:
            resultado['compras'] = e

    # --- VENTAS (click tab visible; NO hacer segundo Consultar) ---
    if hacer_ventas:
        if progress_cb:
            progress_cb(pct_ventas, 'Descargando Ventas…')
        _rcv_click_tab(page, 'VENTA')
        try:
            resultado['ventas'] = _rcv_descargar_detalles(page, 'ventas', periodo_yyyymm)
        except (SIIDownloadError, SIIEmptyPeriodError) as e:
            resultado['ventas'] = e

    return resultado


# ─── Honorarios (BHE Recibidas) ───────────────────────────────────────────────

def _descargar_honorarios(page, rut: str, periodo_yyyymm: str) -> bytes:
    """
    Descarga el informe mensual de BHE recibidas.

    Flujo confirmado:
      1. Navegar a loa.sii.cl/cgi_IMT/TMBCOC_MenuConsultasContribRec.cgi
      2. Seleccionar año (cbanoinformemensual) y mes (cbmesinformemensual)
      3. Llamar presionaBoton('validar_mensual_rec') via JS
      4. Esperar página INFORME MENSUAL DE BOLETAS RECIBIDAS
      5. Hacer click en input[name="planilla"] con expect_download
    """
    anio   = periodo_yyyymm[:4]
    mes_zz = periodo_yyyymm[4:6]

    page.goto(URL_BHE_MENU, wait_until='domcontentloaded', timeout=30000)
    time.sleep(2)

    if 'error' in page.url.lower():
        raise SIIDownloadError(
            f"No se pudo cargar el portal BHE. URL: {page.url}"
        )

    # Seleccionar año y mes en el formulario mensual
    page.select_option('select[name="cbanoinformemensual"]', anio)
    page.select_option('select[name="cbmesinformemensual"]', mes_zz)

    # Disparar la consulta mensual via JS (equivale a click en Consultar del formulario mensual)
    page.evaluate("presionaBoton('validar_mensual_rec')")
    time.sleep(5)

    _screenshot(page, f'bhe_{periodo_yyyymm}_informe')

    # Detectar período vacío ANTES de intentar la descarga (evita cuelgue de 30s)
    _BHE_FRASES_VACIAS = [
        'no existen boletas', 'sin boletas', 'no hay boletas',
        'no existen registros', 'sin registros', 'no hay registros',
        'sin datos', 'no hay datos', 'no existen datos',
        'no hay información', 'totales* :', '0 documentos',
    ]
    content_early = page.content().lower()
    # "sin datos" aparece en la celda de la tabla; Totales en 0 también indica vacío
    if any(f in content_early for f in _BHE_FRASES_VACIAS):
        # Confirmar que los totales son cero (la frase "sin datos" puede aparecer en tablas vacías)
        if 'sin datos' in content_early or 'no existen boletas' in content_early or 'no hay boletas' in content_early:
            raise SIIEmptyPeriodError(
                f"Sin boletas de honorarios para el período {periodo_yyyymm[:4]}-{mes_zz}."
            )

    # El botón de planilla electrónica llama a VerPlanillaMensualRec()
    planilla = page.locator('input[name="planilla"]')
    if planilla.count() == 0:
        content = page.content().lower()
        if any(f in content for f in _BHE_FRASES_VACIAS):
            raise SIIEmptyPeriodError(
                f"Sin boletas de honorarios para el período {periodo_yyyymm[:4]}-{mes_zz}."
            )
        raise SIIDownloadError(
            f"No se encontró el botón de descarga en la página de BHE. "
            f"URL: {page.url} — ver /tmp/sii_bhe_{periodo_yyyymm}_informe.png"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with page.expect_download(timeout=12000) as dl_info:
                planilla.first.click()
            download = dl_info.value
        except PlaywrightTimeoutError:
            content_after = page.content().lower()
            if any(f in content_after for f in _BHE_FRASES_VACIAS):
                raise SIIEmptyPeriodError(
                    f"Sin boletas de honorarios para el período {periodo_yyyymm[:4]}-{mes_zz}."
                )
            raise SIIEmptyPeriodError(
                f"Sin boletas de honorarios para el período {periodo_yyyymm[:4]}-{mes_zz} (timeout 12s sin descarga)."
            )
        dest = os.path.join(
            tmpdir,
            download.suggested_filename or f'hon_{periodo_yyyymm}.xls'
        )
        download.save_as(dest)
        data = Path(dest).read_bytes()
        if len(data) < 100:
            raise SIIDownloadError(
                f"Descarga de honorarios vacía ({len(data)} bytes). "
                "Puede que no haya boletas para el período."
            )
        return data


# ─── Descarga lote (una sola sesión Playwright) ───────────────────────────────

def descargar_lote(rut: str, clave: str, periodo: str, tipos: list,
                   progress_cb=None) -> dict:
    """
    Descarga compras, ventas y/o honorarios en una sola sesión Playwright.
    Retorna {tipo: bytes} o {tipo: Exception}.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    periodo_sii = periodo.replace('-', '')
    resultados: dict = {}

    with sync_playwright() as pw:
        browser, ctx = _launch(pw)
        page = ctx.new_page()
        try:
            if progress_cb:
                progress_cb(10, 'Conectando al portal SII…')
            _login(page, rut, clave)

            # ── RCV (compras + ventas juntas para no repetir login) ──
            tipos_rcv = [t for t in tipos if t in ('compras', 'ventas')]
            if tipos_rcv:
                hacer_compras = 'compras' in tipos_rcv
                hacer_ventas  = 'ventas'  in tipos_rcv
                # Assign percentages based on which books are being downloaded
                if hacer_compras and hacer_ventas:
                    pct_c, pct_v = 20, 45
                elif hacer_compras:
                    pct_c, pct_v = 20, 20
                else:
                    pct_c, pct_v = 20, 20
                try:
                    rcv = _descargar_compras_y_ventas(
                        page, rut, periodo_sii,
                        hacer_compras=hacer_compras,
                        hacer_ventas=hacer_ventas,
                        progress_cb=progress_cb,
                        pct_compras=pct_c,
                        pct_ventas=pct_v,
                    )
                    resultados.update(rcv)
                except Exception as e:
                    for t in tipos_rcv:
                        if t not in resultados:
                            resultados[t] = e

            # ── Honorarios ──
            if 'honorarios' in tipos:
                if progress_cb:
                    progress_cb(70, 'Descargando Honorarios (BHE recibidas)…')
                try:
                    resultados['honorarios'] = _descargar_honorarios(
                        page, rut, periodo_sii
                    )
                except Exception as e:
                    resultados['honorarios'] = e

        except (SIILoginError, SIIDownloadError, SIIEmptyPeriodError):
            raise
        except PWTimeout as e:
            raise SIIDownloadError(f'Timeout al conectar con SII: {e}')
        except Exception as e:
            raise SIIDownloadError(f'Error inesperado: {e}')
        finally:
            _logout(page)
            browser.close()

    return resultados


# ─── Descarga individual ──────────────────────────────────────────────────────

def descargar(rut: str, clave: str, periodo: str, tipo: str) -> bytes:
    """Descarga un solo libro. tipo: 'compras'|'ventas'|'honorarios'"""
    res = descargar_lote(rut, clave, periodo, [tipo])
    val = res.get(tipo)
    if isinstance(val, Exception):
        raise val
    if val is None:
        raise SIIDownloadError(f'Sin resultado para {tipo}')
    return val
