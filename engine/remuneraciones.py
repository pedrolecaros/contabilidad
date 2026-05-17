"""
Motor de cálculo de liquidaciones de sueldo Chile.
Normas vigentes 2024-2025 compatibles con Previred.
"""

# Tasas fijas legales
TASA_AFP_OBLIGATORIO   = 0.10       # 10% obligatorio (igual en todas las AFP)
TASA_CESANTIA_TRAB     = 0.006      # 0.6% trabajador
TASA_CESANTIA_EMP      = 0.024      # 2.4% empleador (contrato indefinido)
TASA_SIS               = 0.0149     # 1.49% SIS (seguro invalidez y sobrevivencia)
TASA_SALUD_FONASA      = 0.07       # 7% salud

# Tramos impuesto único 2da categoría 2024 (en UTM)
# (desde, hasta, tasa, factor_rebaja_en_utm)
TRAMOS_IMPUESTO = [
    (0,     13.5,  0.000, 0.000),
    (13.5,  30.0,  0.040, 0.540),
    (30.0,  50.0,  0.080, 1.740),
    (50.0,  70.0,  0.135, 4.490),
    (70.0,  90.0,  0.230, 11.140),
    (90.0,  120.0, 0.304, 17.800),
    (120.0, 150.0, 0.350, 23.320),
    (150.0, float('inf'), 0.400, 30.820),
]

AFP_COMISIONES = {
    'Capital':   0.0144,
    'Cuprum':    0.0144,
    'Habitat':   0.0127,
    'Modelo':    0.0058,
    'PlanVital': 0.0116,
    'ProVida':   0.0145,
    'Uno':       0.0046,
}

TOPE_GRATIFICACION_DEFAULT = 209395.0


def calcular(emp, utm: float, uf: float = None,
             tope_gratificacion: float = None,
             tope_imponible: float = None,
             horas_extra: float = 0.0,
             otros: float = 0.0,
             tasa_sis: float = None) -> dict:
    """
    Calcula liquidación. Si tipo_sueldo=='LIQUIDO', emp.sueldo_base es el objetivo líquido.
    Gratificación se auto-calcula: min(25% bruto, tope_gratificacion).
    Si isapre > 7% imponible, el exceso descuenta del líquido.
    """
    tope_grat = tope_gratificacion or TOPE_GRATIFICACION_DEFAULT
    sis = tasa_sis if tasa_sis is not None else TASA_SIS

    uf_val = uf or 0.0
    monto_isapre_uf = getattr(emp, 'monto_isapre_uf', 0.0) or 0.0
    if uf_val and monto_isapre_uf:
        monto_isapre = round(monto_isapre_uf * uf_val)
    else:
        monto_isapre = round(getattr(emp, 'monto_isapre', 0.0) or 0.0)

    tipo_sueldo = getattr(emp, 'tipo_sueldo', 'BRUTO') or 'BRUTO'
    if tipo_sueldo == 'LIQUIDO':
        sueldo_bruto = _encontrar_bruto(emp, utm, tope_grat, monto_isapre, horas_extra, otros, sis)
    else:
        sueldo_bruto = round(emp.sueldo_base)

    grat = round(min(sueldo_bruto * 0.25, tope_grat))
    return _calcular_con_bruto(emp, utm, sueldo_bruto, grat, monto_isapre, horas_extra, otros, sis, tope_imponible)


def _calcular_con_bruto(emp, utm, sueldo_bruto, grat, monto_isapre, horas_extra, otros, tasa_sis=TASA_SIS, tope_imponible=None):
    bono_colacion   = round(getattr(emp, 'bono_colacion', 0) or 0)
    bono_movil      = round(getattr(emp, 'bono_movilizacion', 0) or 0)
    otros_haberes   = round((getattr(emp, 'otros_haberes', 0) or 0) + otros)
    he_monto        = round(horas_extra)

    total_haberes   = sueldo_bruto + grat + he_monto + bono_colacion + bono_movil + otros_haberes
    renta_imponible = sueldo_bruto + grat + he_monto + otros_haberes
    # Cotizaciones previsionales se calculan sobre renta acotada al tope imponible (~90 UF)
    renta_cot = min(renta_imponible, tope_imponible) if tope_imponible else renta_imponible

    # Prefer employee's stored commission; fall back to the AFP dict
    emp_comision = getattr(emp, 'tasa_afp_comision', None) or 0
    dict_comision = AFP_COMISIONES.get(getattr(emp, 'afp', ''), None)
    tasa_afp_total = TASA_AFP_OBLIGATORIO + (emp_comision if emp_comision > 0 else (dict_comision or 0.0127))

    afp_desc        = round(renta_cot * tasa_afp_total)
    salud_legal     = round(renta_cot * TASA_SALUD_FONASA)

    if getattr(emp, 'tipo_salud', 'FONASA') == 'FONASA':
        salud_desc    = salud_legal
        extra_isapre  = 0
    else:
        salud_desc    = salud_legal
        extra_isapre  = max(0, monto_isapre - salud_legal)

    cesantia_trab   = round(renta_cot * TASA_CESANTIA_TRAB)

    # APV: Tipo A reduce la base imponible para impuesto 2ª categoría
    apv_monto = round(getattr(emp, 'apv_monto', 0) or 0)
    apv_tipo  = getattr(emp, 'apv_tipo', 'A') or 'A'

    base_renta      = renta_imponible - afp_desc - salud_desc - cesantia_trab
    base_impuesto   = max(0, base_renta - (apv_monto if apv_tipo == 'A' else 0))
    impuesto        = _calcular_impuesto(base_impuesto, utm)

    total_descuentos = afp_desc + salud_desc + cesantia_trab + impuesto + extra_isapre + apv_monto
    liquido          = total_haberes - total_descuentos

    sis_emp         = round(renta_cot * tasa_sis)
    cesantia_emp    = round(renta_cot * TASA_CESANTIA_EMP)
    mutual_emp      = round(renta_cot * (getattr(emp, 'tasa_mutual', 0) or 0))
    costo_empresa   = total_haberes + sis_emp + cesantia_emp + mutual_emp

    return {
        'sueldo_base':       sueldo_bruto,
        'horas_extra':       he_monto,
        'bono_colacion':     bono_colacion,
        'bono_movilizacion': bono_movil,
        'otros_haberes':     otros_haberes,
        'gratificacion':     grat,
        'total_haberes':     total_haberes,
        'renta_imponible':   renta_imponible,
        'afp':               afp_desc,
        'salud':             salud_desc,
        'cesantia_trab':     cesantia_trab,
        'impuesto_renta':    impuesto,
        'total_descuentos':  total_descuentos,
        'liquido':           liquido,
        'sis':               sis_emp,
        'cesantia_emp':      cesantia_emp,
        'mutual':            mutual_emp,
        'costo_empresa':     costo_empresa,
        'utm':               utm,
        'extra_isapre':      extra_isapre,
        'apv':               apv_monto,
        'afp_nombre':        getattr(emp, 'afp', ''),
        'tasa_afp':          round(tasa_afp_total * 100, 4),
        'tipo_salud':        getattr(emp, 'tipo_salud', 'FONASA'),
        'isapre':            getattr(emp, 'isapre', '') or '',
    }


def _encontrar_bruto(emp, utm, tope_grat, monto_isapre, horas_extra, otros, tasa_sis=TASA_SIS):
    """Binary search: find gross salary whose net == emp.sueldo_base."""
    from types import SimpleNamespace
    objetivo = emp.sueldo_base
    emp_tmp = SimpleNamespace(
        afp=getattr(emp, 'afp', 'Habitat'),
        tasa_afp_comision=getattr(emp, 'tasa_afp_comision', 0.0127),
        tipo_salud=getattr(emp, 'tipo_salud', 'FONASA'),
        isapre=getattr(emp, 'isapre', None),
        bono_colacion=getattr(emp, 'bono_colacion', 0.0),
        bono_movilizacion=getattr(emp, 'bono_movilizacion', 0.0),
        otros_haberes=getattr(emp, 'otros_haberes', 0.0),
        tasa_mutual=getattr(emp, 'tasa_mutual', 0.0093),
        apv_monto=getattr(emp, 'apv_monto', 0.0),
        apv_tipo=getattr(emp, 'apv_tipo', 'A'),
        tipo_sueldo='BRUTO',
        monto_isapre=0,
        monto_isapre_uf=0,
        sueldo_base=0,
    )
    lo, hi = max(objetivo * 0.7, 100), objetivo * 2.5
    bruto = (lo + hi) / 2
    for _ in range(80):
        bruto = (lo + hi) / 2
        grat = round(min(bruto * 0.25, tope_grat))
        emp_tmp.sueldo_base = round(bruto)
        res = _calcular_con_bruto(emp_tmp, utm, round(bruto), grat, monto_isapre, horas_extra, otros, tasa_sis)
        diff = res['liquido'] - objetivo
        if diff < -0.5:
            lo = bruto
        else:
            hi = bruto
        if abs(diff) < 1:
            break
    return round(bruto)


def _calcular_impuesto(base_pesos: float, utm: float) -> int:
    """Impuesto único 2da categoría según tramos en UTM."""
    if utm <= 0:
        return 0
    base_utm = base_pesos / utm
    for desde, hasta, tasa, rebaja_utm in TRAMOS_IMPUESTO:
        if base_utm <= hasta:
            imp = base_pesos * tasa - rebaja_utm * utm
            return max(0, round(imp))
    return 0
