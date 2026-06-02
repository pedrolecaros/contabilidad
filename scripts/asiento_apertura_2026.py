"""Inserta el Asiento de Apertura 2026 para Tregualemu (3), Futrono (4),
Los Robles (5) y Asesorías Ecox (6) usando los saldos al 31/12/2025 de los
balances ContaLive y mapeándolos al plan de cuentas actual.

Mantiene la convención de los asientos existentes (Ecox=1, Parque Sur=2,
Los Chilcos=14): fecha 2026-01-01, numero 1, origen MANUAL, estado CONFIRMADO,
descripción "desde <código original>".
"""

import sqlite3
from datetime import datetime

DB = "/home/pedro/contabilidad/contabilidad.db"

ASIENTOS = {
    3: {  # Tregualemu SpA
        "desc": "Asiento de Apertura 2026 – Saldos iniciales al 31-12-2025",
        "lineas": [
            ("1.1.01", 581_727,       0,           "Caja – desde 100101"),
            ("1.1.06",   9_583,       0,           "PPM – desde 105101"),
            ("3.1.03", 32_063_004,    0,           "Retiro / Dividendo Socio Jorge Alberto Hiriart Blome – desde 106102"),
            ("3.1.03", 64_126_008,    0,           "Retiro / Dividendo Socio Felipe Andrés Hiriart Blome – desde 106103"),
            ("3.1.03", 64_126_008,    0,           "Retiro / Dividendo Socio Pedro José Lecaros Sotomayor – desde 106104"),
            ("1.1.13",  1_000_000,    0,           "Cta. Cte. Asesorías Ecox Ltda. – desde 111112"),
            ("2.1.10",        0,      7_876,       "Línea de Crédito – desde 200103"),
            ("2.1.12",        0,    792_820,       "Cta. Cte. Inversiones Aysen SpA – desde 111107"),
            ("3.1.01",        0,    100_000,       "Capital Pagado – desde 220101"),
            ("3.1.03",        0, 156_767_904,      "Resultados Acumulados al 31-12-2024 – desde 221101"),
            ("3.1.03",        0,   4_237_730,      "Incorporación Resultado Ejercicio 2025 (Utilidad)"),
        ],
    },
    4: {  # Futrono SpA
        "desc": "Asiento de Apertura 2026 – Saldos iniciales al 31-12-2025",
        "lineas": [
            ("1.1.01",        338_328,           0, "Caja – desde 100101"),
            ("1.1.02",      1_113_261,           0, "Banco Santander – desde 100102"),
            ("1.1.06",        174_300,           0, "PPM – desde 105101"),
            ("3.1.03",     42_646_500,           0, "Retiro / Dividendo Socio Inversiones Cerro Pan de Azúcar Ltda. – desde 106102"),
            ("3.1.03",     42_646_500,           0, "Retiro / Dividendo Socio Inversiones El Volcán Ltda. – desde 106103"),
            ("3.1.03",    124_464_600,           0, "Retiro / Dividendo Socio Inversiones Aysen SpA – desde 106105"),
            ("3.1.03",    106_142_400,           0, "Retiro / Dividendo Socio Ecox SpA – desde 106106"),
            ("1.1.13",     80_185_604,           0, "Cuentas por Cobrar Inversiones Cerro Pan de Azúcar Ltda. – desde 111106"),
            ("1.1.13",     46_099_014,           0, "Cuentas por Cobrar Inversiones El Volcán Ltda. – desde 111107"),
            ("1.1.13",    382_112_329,           0, "Préstamos por Cobrar Los Robles SpA – desde 111109"),
            ("2.1.07",              0,       3_590, "Impuestos por Pagar – desde 202104"),
            ("3.1.01",              0,   1_000_000, "Capital Pagado – desde 220101"),
            ("3.1.03",              0, 555_436_478, "Resultados Acumulados al 31-12-2024 – desde 221101"),
            ("3.1.03",              0, 269_482_768, "Incorporación Resultado Ejercicio 2025 (Utilidad)"),
        ],
    },
    5: {  # Los Robles SpA
        "desc": "Asiento de Apertura 2026 – Saldos iniciales al 31-12-2025",
        "lineas": [
            ("1.1.01",       522_886,           0, "Caja – desde 100101"),
            ("1.1.02",     2_346_606,           0, "Banco de Chile – desde 100102"),
            ("1.1.09",    11_990_000,           0, "Fondos Mutuos – desde 100204"),
            ("1.1.06",       444_447,           0, "PPM – desde 105101"),
            ("1.1.14",       361_601,           0, "Impuesto por Recuperar – desde 105108"),
            ("1.2.01",   288_933_390,           0, "Terrenos – desde 115101"),
            ("3.1.04",    79_499_119,           0, "Resultados acumulados netos: utilidades 202.984.428 al 31-12-2024 (221101) menos pérdida del ejercicio 2025 282.483.547"),
            ("2.1.05",             0,     750_000, "Remuneraciones por Pagar – desde 202102"),
            ("2.1.07",             0,      17_385, "Impuestos por Pagar – desde 202104"),
            ("2.1.06",             0,     218_335, "Cotizaciones Previsionales por Pagar – desde 202105"),
            ("2.1.12",             0, 382_112_329, "Cuenta por Pagar Empresa Futrono – desde 204101"),
            ("3.1.01",             0,   1_000_000, "Capital Pagado – desde 220101"),
        ],
    },
    10: {  # Santa Delfina SpA
        "desc": "Asiento de Apertura 2026 – Saldos iniciales al 31-12-2025",
        "lineas": [
            ("1.1.01",         100_000,             0, "Caja – desde 100101"),
            ("1.1.02",       1_178_682,             0, "Banco BICE – desde 100102"),
            ("1.1.10",     186_344_381,             0, "Depósito a Plazo – desde 100201"),
            ("1.2.01",   1_191_838_800,             0, "Terrenos – desde 115101"),
            ("2.1.11",               0,   180_175_000, "Préstamos de Terceros – desde 204102"),
            ("2.2.04",               0, 1_191_838_800, "Préstamos por Pagar por Compra de Terreno – desde 204104"),
            ("3.1.01",               0,       100_000, "Capital Pagado – desde 220101"),
            ("3.1.03",               0,     7_348_063, "Incorporación Resultado Ejercicio 2025 (Utilidad)"),
        ],
    },
    7: {  # Cerro Colorado SpA
        "desc": "Asiento de Apertura 2026 – Saldos iniciales al 31-12-2025",
        "lineas": [
            ("1.1.01",     462_948,           0, "Caja – desde 100101"),
            ("1.1.02",   4_261_958,           0, "Banco Chile – desde 100103"),
            ("1.1.14", 188_288_135,           0, "Transferencia por Mandato – desde 111111"),
            ("2.1.13",           0, 192_113_041, "Ingresos Recibidos para Terceros EREF – desde 212101"),
            ("3.1.01",           0,     900_000, "Capital Pagado – desde 220101"),
        ],
    },
    6: {  # Asesorías Ecox Limitada
        "desc": "Asiento de Apertura 2026 – Saldos iniciales al 31-12-2025",
        "lineas": [
            ("1.1.01",       143_770,           0, "Caja – desde 100101"),
            ("1.1.02",    30_166_859,           0, "Banco Santander – desde 100103"),
            ("1.1.09",    25_578_831,           0, "Fondos Mutuos – desde 100204"),
            ("1.1.03",     1_200_000,           0, "Clientes – desde 102101"),
            ("1.1.13",    65_737_355,           0, "Cta. Cte. Mercantil Ecox SpA – desde 102201"),
            ("1.1.13",   143_320_466,           0, "Préstamos Inversiones Aysen SpA – desde 102203"),
            ("1.1.13",    12_629_691,           0, "Préstamo por Cobrar Parque Sur SpA – desde 102204"),
            ("1.1.06",       481_132,           0, "PPM – desde 105101"),
            ("3.1.03",     7_769_750,           0, "Retiro Socio Pedro Lecaros – desde 106102"),
            ("3.1.03",     7_769_750,           0, "Retiro Socio Felipe Hiriart – desde 106103"),
            ("1.1.12",    16_010_000,           0, "Préstamo por Cobrar Felipe Hiriart – desde 107107"),
            ("1.1.12",    16_010_000,           0, "Préstamo por Cobrar Pedro Lecaros – desde 107109"),
            ("1.1.14",     1_035_295,           0, "Anticipo Clientes (saldo deudor) – desde 211104"),
            ("2.1.12",             0,   2_200_000, "Fondos por Rendir Proyecto Puerto Octay – desde 107112"),
            ("2.1.04",             0,     749_600, "Honorarios por Pagar – desde 202103"),
            ("2.1.07",             0,     392_248, "Impuestos por Pagar – desde 202104"),
            ("2.1.13",             0,   1_000_000, "Provisión Tregualemu – desde 213102"),
            ("3.1.01",             0,     200_000, "Capital Pagado – desde 220101"),
            ("3.1.03",             0, 197_394_850, "Resultados Acumulados al 31-12-2024 – desde 221101"),
            ("3.1.03",             0, 125_916_201, "Incorporación Resultado Ejercicio 2025 (Utilidad)"),
        ],
    },
}


def main(commit: bool = False) -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    for empresa_id, payload in ASIENTOS.items():
        exists = cur.execute(
            "SELECT id FROM asientos WHERE empresa_id=? AND fecha='2026-01-01' "
            "AND (LOWER(descripcion) LIKE '%apertura%' OR LOWER(descripcion) LIKE '%inicial%')",
            (empresa_id,),
        ).fetchone()
        if exists:
            print(f"empresa {empresa_id}: ya existe asiento {exists['id']} — skip")
            continue

        suma_debe = sum(l[1] for l in payload["lineas"])
        suma_haber = sum(l[2] for l in payload["lineas"])
        if suma_debe != suma_haber:
            raise SystemExit(
                f"empresa {empresa_id}: descuadre Debe={suma_debe:,} Haber={suma_haber:,}"
            )

        cur.execute(
            """INSERT INTO asientos
               (empresa_id, fecha, numero, descripcion, respaldo_url, origen, estado, creado_en)
               VALUES (?, '2026-01-01', 1, ?, NULL, 'MANUAL', 'CONFIRMADO', ?)""",
            (empresa_id, payload["desc"], datetime.now().isoformat()),
        )
        asiento_id = cur.lastrowid

        for orden, (codigo, debe, haber, descripcion) in enumerate(payload["lineas"]):
            row = cur.execute(
                "SELECT id FROM cuentas WHERE empresa_id=? AND codigo=?",
                (empresa_id, codigo),
            ).fetchone()
            if not row:
                raise SystemExit(f"empresa {empresa_id}: falta cuenta {codigo}")
            cur.execute(
                """INSERT INTO lineas_asiento
                   (asiento_id, cuenta_id, debe, haber, descripcion, orden)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (asiento_id, row["id"], debe, haber, descripcion, orden),
            )

        print(
            f"empresa {empresa_id}: asiento {asiento_id} creado — "
            f"{len(payload['lineas'])} líneas — Debe={suma_debe:,} Haber={suma_haber:,}"
        )

    if commit:
        con.commit()
        print("COMMIT aplicado.")
    else:
        con.rollback()
        print("DRY RUN — rollback. Pasa commit=True para persistir.")
    con.close()


if __name__ == "__main__":
    import sys
    main(commit="--commit" in sys.argv)
