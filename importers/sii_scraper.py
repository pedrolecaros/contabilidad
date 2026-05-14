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


URL_RCV      = 'https://www4.sii.cl/consdcvinternetui/'
URL_BHE_MENU = 'https://loa.sii.cl/cgi_IMT/TMBCOC_MenuConsultasContribRec.cgi'
URL_LOGOUT   = 'https://zeusr.sii.cl/cgi_AUT2000/autTermino.cgi'


class SIILoginError(Exception):
    pass

class SIIDownloadError(Exception):
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
    page_text = page.content().lower()

    if 'máximo de sesiones' in page_text or 'maximo de sesiones' in page_text \
            or '01.01.131.500' in page_text:
        raise SIILoginError(
            "SII bloqueó el acceso: demasiadas sesiones activas. "
            "Espera unos minutos o cierra sesión en el portal SII."
        )
    if '429' in page_text or 'demasiados intentos' in page_text \
            or 'bloqueado temporalmente' in page_text:
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


def _rcv_descargar_detalles(page, tipo_label: str, periodo_yyyymm: str) -> bytes:
    """Click en 'Descargar Detalles' y retorna el contenido del archivo."""
    _screenshot(page, f'rcv_{tipo_label}_{periodo_yyyymm}_antes_descarga')

    with tempfile.TemporaryDirectory() as tmpdir:
        # El botón se llama "Descargar Detalles" en el RCV Angular
        for sel in [
            'button:has-text("Descargar Detalles")',
            'button:has-text("Descargar detalles")',
            'a:has-text("Descargar Detalles")',
            'a:has-text("Descargar detalles")',
            'button[ng-click*="limiteDoc"]',
            'button[ng-click*="descarga"]',
            'button:has-text("Descargar")',
            'a:has-text("Descargar")',
        ]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    with page.expect_download(timeout=60000) as dl_info:
                        loc.first.click()
                    download = dl_info.value
                    dest = os.path.join(
                        tmpdir,
                        download.suggested_filename or f'rcv_{tipo_label}_{periodo_yyyymm}.csv'
                    )
                    download.save_as(dest)
                    return Path(dest).read_bytes()
            except Exception:
                continue

        raise SIIDownloadError(
            f"No se encontró el botón 'Descargar Detalles' en el RCV ({tipo_label}). "
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
                                 hacer_compras: bool, hacer_ventas: bool) -> dict:
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
        resultado['compras'] = _rcv_descargar_detalles(page, 'compras', periodo_yyyymm)

    # --- VENTAS (click tab visible; NO hacer segundo Consultar) ---
    if hacer_ventas:
        _rcv_click_tab(page, 'VENTA')
        resultado['ventas'] = _rcv_descargar_detalles(page, 'ventas', periodo_yyyymm)

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

    # El botón de planilla electrónica llama a VerPlanillaMensualRec()
    planilla = page.locator('input[name="planilla"]')
    if planilla.count() == 0:
        # Puede ser que no hay boletas para este período
        content = page.content().lower()
        if 'no existen boletas' in content or 'sin boletas' in content or 'no hay boletas' in content:
            raise SIIDownloadError(
                f"No hay boletas de honorarios recibidas para {periodo_yyyymm[:4]}-{mes_zz}."
            )
        raise SIIDownloadError(
            f"No se encontró el botón de descarga en la página de BHE. "
            f"URL: {page.url} — ver /tmp/sii_bhe_{periodo_yyyymm}_informe.png"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        with page.expect_download(timeout=30000) as dl_info:
            planilla.first.click()
        download = dl_info.value
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
                if progress_cb:
                    progress_cb(20, 'Descargando Registro de Compras y Ventas…')
                try:
                    rcv = _descargar_compras_y_ventas(
                        page, rut, periodo_sii,
                        hacer_compras='compras' in tipos_rcv,
                        hacer_ventas='ventas' in tipos_rcv,
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

        except (SIILoginError, SIIDownloadError):
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
