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
    'Uno':       0.0049,
}


def calcular(emp, utm: float, horas_extra: float = 0.0, otros: float = 0.0,
             gratificacion: float = 0.0) -> dict:
    """
    Calcula la liquidación de sueldo para un Empleado dado.
    utm: valor de la UTM del mes.
    Retorna dict con todos los campos necesarios para Liquidacion y la liquidación impresa.
    """
    # ── Haberes ───────────────────────────────────────────────────────────────
    sueldo_base      = round(emp.sueldo_base)
    bono_colacion    = round(emp.bono_colacion)
    bono_movil       = round(emp.bono_movilizacion)
    otros_haberes    = round(emp.otros_haberes + otros)
    he_monto         = round(horas_extra)
    grat             = round(gratificacion)
    total_haberes    = sueldo_base + he_monto + bono_colacion + bono_movil + otros_haberes + grat

    # Renta imponible: excluye colación y movilización (no son imponibles si son razonables)
    renta_imponible  = sueldo_base + he_monto + otros_haberes + grat

    # ── Descuentos previsionales ──────────────────────────────────────────────
    tasa_afp_total   = TASA_AFP_OBLIGATORIO + AFP_COMISIONES.get(emp.afp, emp.tasa_afp_comision)
    afp_desc         = round(renta_imponible * tasa_afp_total)

    if emp.tipo_salud == 'FONASA':
        salud_desc   = round(renta_imponible * TASA_SALUD_FONASA)
    else:
        salud_base   = round(renta_imponible * TASA_SALUD_FONASA)
        salud_desc   = round(max(salud_base, emp.monto_isapre or 0))

    cesantia_trab    = round(renta_imponible * TASA_CESANTIA_TRAB)

    # ── Base imponible renta ──────────────────────────────────────────────────
    base_renta       = renta_imponible - afp_desc - salud_desc - cesantia_trab
    impuesto         = _calcular_impuesto(base_renta, utm)

    # ── Sueldo líquido ────────────────────────────────────────────────────────
    total_descuentos = afp_desc + salud_desc + cesantia_trab + impuesto
    liquido          = total_haberes - total_descuentos

    # ── Aportes empleador ─────────────────────────────────────────────────────
    sis_emp          = round(renta_imponible * TASA_SIS)
    cesantia_emp     = round(renta_imponible * TASA_CESANTIA_EMP)
    mutual_emp       = round(renta_imponible * (emp.tasa_mutual or 0))
    costo_empresa    = total_haberes + sis_emp + cesantia_emp + mutual_emp

    return {
        'sueldo_base':       sueldo_base,
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
        # Detalle para previred
        'afp_nombre':        emp.afp,
        'tasa_afp':          round(tasa_afp_total * 100, 4),
        'tipo_salud':        emp.tipo_salud,
        'isapre':            emp.isapre or '',
    }


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
