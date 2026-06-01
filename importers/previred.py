"""
Scraper de indicadores previsionales desde previred.com.

Interfaz pública:
  scrape(periodo: str) -> dict

Retorna un dict con claves: ok, periodo, uf, utm, imm, tope_imponible,
tope_gratificacion, tasa_sis, tasas_afp.

No depende de Flask ni de la base de datos.
"""
import re


def scrape(periodo: str) -> dict:
    import requests
    from bs4 import BeautifulSoup

    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
    r = requests.get('https://www.previred.com/indicadores-previsionales/',
                     timeout=15, headers=headers)
    if r.status_code != 200:
        return {'ok': False, 'error': f'HTTP {r.status_code}'}

    soup = BeautifulSoup(r.text, 'html.parser')
    lines = [l.strip() for l in soup.get_text().split('\n') if l.strip()]

    def clp(s):
        return float(s.replace('$', '').replace('\xa0', '').replace('.', '').replace(',', '.').strip())

    def pct(s):
        return float(s.replace('%', '').replace(',', '.').strip())

    AFP_NAMES = ['Capital', 'Cuprum', 'Habitat', 'PlanVital', 'ProVida', 'Modelo', 'Uno']
    result = {'ok': True, 'periodo': periodo, 'tasas_afp': {}}

    for i, l in enumerate(lines):
        if not result.get('uf'):
            uf_match = re.match(r'Al \d+ de \w+ del \d{4}:', l)
            if uf_match and i + 1 < len(lines):
                try:
                    result['uf'] = clp(lines[i + 1])
                except Exception:
                    pass

        if l in AFP_NAMES and i + 1 < len(lines):
            try:
                total_pct = pct(lines[i + 1])
                result['tasas_afp'][l] = round(total_pct - 10.0, 2)
            except Exception:
                pass

        if l == 'UTM' and i + 3 < len(lines):
            try:
                result['utm'] = clp(lines[i + 3])
            except Exception:
                pass

        if 'Dependientes e Independientes' in l and i + 1 < len(lines):
            try:
                result['imm'] = clp(lines[i + 1])
            except Exception:
                pass

        if l == 'Tasa SIS' and i + 1 < len(lines):
            try:
                result['tasa_sis'] = round(pct(lines[i + 1]) / 100, 6)
            except Exception:
                pass

    if result.get('uf'):
        result['tope_imponible'] = round(result['uf'] * 90)
    if result.get('imm'):
        result['tope_gratificacion'] = round(result['imm'] * 4.75 / 12)

    return result
