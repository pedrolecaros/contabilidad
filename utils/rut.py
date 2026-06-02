def normalizar_rut(rut: str) -> str:
    """Convierte cualquier formato de RUT chileno a XX.XXX.XXX-X."""
    rut = rut.strip().upper().replace('.', '').replace(' ', '')
    if not rut:
        return rut
    if '-' in rut:
        body, dv = rut.rsplit('-', 1)
    else:
        body, dv = rut[:-1], rut[-1]
    body = body.lstrip('0') or '0'
    formatted = ''
    for i, c in enumerate(reversed(body)):
        if i > 0 and i % 3 == 0:
            formatted = '.' + formatted
        formatted = c + formatted
    return f'{formatted}-{dv}'


def validar_rut_dv(rut: str) -> bool:
    """Valida el dígito verificador de un RUT chileno usando módulo 11."""
    try:
        rut_clean = rut.strip().upper().replace('.', '').replace(' ', '')
        if not rut_clean:
            return True
        if '-' in rut_clean:
            body, dv = rut_clean.rsplit('-', 1)
        else:
            body, dv = rut_clean[:-1], rut_clean[-1]
        body = body.lstrip('0') or '0'
        if not body.isdigit():
            return False
        digits = [int(c) for c in body]
        factors = [2, 3, 4, 5, 6, 7]
        total = 0
        for i, d in enumerate(reversed(digits)):
            total += d * factors[i % 6]
        remainder = 11 - (total % 11)
        if remainder == 11:
            expected = '0'
        elif remainder == 10:
            expected = 'K'
        else:
            expected = str(remainder)
        return dv == expected
    except Exception:
        return True
