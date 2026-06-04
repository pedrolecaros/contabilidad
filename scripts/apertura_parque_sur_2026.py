"""
Rearma el asiento de apertura 2026 de Parque Sur SpA (empresa_id=1) en base al
balance ContaLive al 31-12-2025 + desgloses por auxiliar (PDFs en carpeta 03).

- Elimina las líneas del asiento #1 (apertura actual) sin borrar el asiento.
- Crea cuenta 1.1.04 Anticipos a Proveedores si no existe.
- Crea contrapartes faltantes (clientes, proveedores, acreedores) si no existen.
- Inserta las líneas de apertura nuevas con aux por contraparte donde aplica.

Cuadre: Debe = Haber = 623.526.123.

Convención (confirmada con Pedro):
- Todos los créditos / préstamos por pagar van a 2.1.11 con auxiliar
  (terceros, socios, relacionadas — nunca 2.1.12).
- Fondos por rendir se suman a 1.1.01 Caja.
- Anticipo a Proveedores 1.492.409 va a nueva cuenta 1.1.04 sin aux.
"""

import sqlite3
from contextlib import closing
from datetime import date

DB = "contabilidad.db"
EMPRESA_ID = 1
# Buscamos el asiento de apertura de Parque Sur (empresa_id=1, fecha 2026-01-01) dinámicamente

# Auxiliares - CLIENTES (saldo deudor = saldo positivo; acreedor en cliente = anticipo)
CLIENTES = [
    ("76098820-0", "Bodegas San Francisco Limitada",       849_166),
    ("77057527-3", "Servicios Integrales LS SpA",          402_056),
    ("77499240-5", "Inversiones Fenix Servicios Limitada", 9_928_014),
    ("78017988-0", "Flex Rack SpA",                        59_500),
    ("78924100-7", "Inversiones Integrales Ltda",          2_867_350),
    ("81675600-6", "Hites S.A.",                           943_518),
    ("86881400-4", "Envases CMF Sociedad Anonima",         5_429_845),
    ("96990610-4", "Montajes y Servicios Industriales D", -189_068),  # anticipo cliente
]
# Total CLIENTES: 20.290.381

# Auxiliares - PROVEEDORES (saldo acreedor = positivo en este dict, deudor = negativo)
# Esos dos con saldo deudor son anticipos a proveedor dentro de 2.1.01.
PROVEEDORES = [
    ("76052927-3", "Soc. Conc. Autopista Nueva Vespucio",      8_237),
    ("76106956-K", "General Alquiler de Maquinarias Chile",    4_145_367),
    ("76217392-1", "Horizon High Reach Chile SpA",             1_533_354),
    ("76496130-7", "Sociedad Concesionaria Costanera Norte",   17_633),
    ("76524465-K", "Ahern Chile SpA",                          17_500_938),
    ("76540394-4", "Servicios de Grua Cristian Manuel Silva",  606_900),
    ("76622050-9", "CASS Inversiones S.A.",                    49_734),
    ("76686138-5", "SKC Red SpA",                              129_867),
    ("76800655-5", "Comercial y Mecanica MBC Limitada",        740_180),
    ("77008670-1", "Portillo S.A.",                           -368_418),  # anticipo a prov
    ("77398220-1", "MercadoLibre Chile Ltda",                  13_502),
    ("77615904-2", "Comercial Agurto SpA",                     111_736),
    ("78112170-3", "Net Now",                                  11_000),
    ("78793360-2", "Sociedad de Alimentacion Casino EXP",      86_909),
    ("79771890-4", "Metalmecanica Metalbert Limitada",        -62_204),  # anticipo a prov
    ("83162400-0", "Emaresa Ingenieros y Representacion",      195_043),
    ("96806980-2", "Entel PCS Telecomunicaciones S.A.",        12_490),
    ("96945440-8", "Soc Concesionaria Autopista Central",      9_705),
    ("96992030-1", "Sociedad Concesionaria Vespucio Norte",    75_745),
    ("97004000-5", "Banco de Chile",                           4_791),
]
# Total PROVEEDORES: 24.822.509 acreedor neto

# 2.1.11 Préstamos de Terceros — incluye créditos privados (212110) + socios (212109)
# Cada aux suma ambos saldos cuando aplica. Convención Pedro: todo a 2.1.11.
PRESTAMOS_TERCEROS = [
    # RUT, nombre, saldo créditos privados (212110), saldo socios (212109)
    ("15595261-K", "Jose Luis Illanes Guridi",     11_294_262, 31_816_968),
    ("15640430-6", "Jose Miguel Parro Fluxa",      0,          5_639_245),
    ("15640744-5", "Felipe Andres Hiriart Blome",  85_519_996, 550_280),
    ("16100615-7", "Dominique Hiriart",            83_139_894, 0),
    ("16367300-2", "Pedro Lecaros Sotomayor",      12_827_364, 550_280),
    ("76185865-3", "Inversiones Jopa Limitada",    17_217_701, 25_897_465),
    ("77160572-9", "Ecox SpA",                     0,          62_533_376),
    ("77712326-2", "Inversiones Majo SpA",         0,          2_684_058),
    ("77714024-8", "Asesorias Ecox Limitada",      12_661_697, 0),
]
# Total 222.660.914 + 129.671.672 = 352.332.586

# Cuentas usadas — todas ya existen en el plan de Parque Sur
CUENTAS_REQ = [
    "1.1.01", "1.1.02", "1.1.03", "1.1.07", "1.1.05", "1.1.06",
    "1.2.03", "1.2.08", "1.2.12",
    "2.1.01", "2.1.04", "2.1.05", "2.1.06", "2.1.07",
    "2.1.10", "2.1.11",
    "3.1.01", "3.1.04",
]
# Mapping local (codigo no debe cambiar — solo apertura)
COD_ANTICIPO_PROV = "1.1.07"
COD_IMPUESTOS_POR_PAGAR = "2.1.07"  # se usa "Impuesto a la Renta por Pagar" como impuestos generales


def get_cuenta_id(cur, codigo):
    cur.execute("SELECT id FROM cuentas WHERE empresa_id=? AND codigo=?", (EMPRESA_ID, codigo))
    r = cur.fetchone()
    if not r:
        raise SystemExit(f"Cuenta {codigo} no existe en plan de Parque Sur")
    return r[0]


def get_or_create_contraparte(cur, rut, razon_social, tipo="PROVEEDOR"):
    cur.execute(
        "SELECT id FROM contrapartes WHERE empresa_id=? AND rut=?",
        (EMPRESA_ID, rut),
    )
    r = cur.fetchone()
    if r:
        return r[0]
    cur.execute(
        "INSERT INTO contrapartes (empresa_id, rut, razon_social, tipo, activo) VALUES (?,?,?,?,1)",
        (EMPRESA_ID, rut, razon_social, tipo),
    )
    return cur.lastrowid


def main(dry_run=False):
    with closing(sqlite3.connect(DB)) as conn:
        cur = conn.cursor()

        # Buscar el asiento de apertura de Parque Sur dinámicamente
        cur.execute(
            "SELECT id FROM asientos WHERE empresa_id=? AND fecha='2026-01-01' "
            "AND descripcion LIKE 'Asiento de Apertura%' ORDER BY id LIMIT 1",
            (EMPRESA_ID,),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"No encuentro asiento de apertura para empresa_id={EMPRESA_ID}")
        asiento_id = row[0]
        print(f"Trabajando sobre asiento_id={asiento_id} (empresa_id={EMPRESA_ID})")

        # 1. Cuentas (todas deben existir)
        cta_id = {cod: get_cuenta_id(cur, cod) for cod in CUENTAS_REQ}

        # 2. Borrar líneas del asiento de apertura existente
        cur.execute("DELETE FROM lineas_asiento WHERE asiento_id=?", (asiento_id,))
        cur.execute(
            "UPDATE asientos SET descripcion=?, fecha=?, estado='CONFIRMADO' WHERE id=?",
            ("Asiento de Apertura 2026 — Saldos al 31-12-2025 (balance ContaLive + aux PDFs)",
             "2026-01-01", asiento_id),
        )

        # 3. Construir lista de líneas
        lineas = []  # (cuenta_id, debe, haber, descripcion, contraparte_id)
        orden = 0

        def add(cod, debe, haber, desc, cp_id=None):
            nonlocal orden
            orden += 1
            lineas.append((cta_id[cod], debe, haber, desc, orden, cp_id))

        # --- ACTIVOS ---
        # Caja + fondos por rendir
        add("1.1.01", 1_645_387 + 264_770, 0, "Saldo Caja al 31-12-2025 (incl. Fondos por Rendir)")
        # Banco
        add("1.1.02", 5_580_311, 0, "Saldo Banco Chile cta 00-887-03711-09 al 31-12-2025")
        # Clientes con aux (los saldos negativos = anticipos = van al haber con aux)
        for rut, nom, saldo in CLIENTES:
            cp = get_or_create_contraparte(cur, rut, nom, tipo="CLIENTE")
            if saldo >= 0:
                add("1.1.03", saldo, 0, f"Saldo cliente {nom}", cp)
            else:
                add("1.1.03", 0, -saldo, f"Anticipo cliente {nom}", cp)
        # Anticipo a Proveedores (cuenta separada 1.1.07; sin aux por ahora — balance no desglosa)
        add("1.1.07", 1_492_409, 0, "Anticipo a proveedores al 31-12-2025 (sin desglose en balance)")
        # IVA CF Remanente
        add("1.1.05", 32_609_015, 0, "IVA Remanente Crédito Fiscal al 31-12-2025")
        # PPM
        add("1.1.06", 550_614, 0, "PPM acumulado al 31-12-2025")
        # Maquinarias y Equipos
        add("1.2.03", 422_504_430, 0, "Maquinarias y Equipos a valor de adquisición")
        # Dep. Acum. Maquinarias y Equipos (saldo acreedor)
        add("1.2.08", 0, 220_057_558, "Depreciación acumulada Maquinarias y Equipos al 31-12-2025")
        # Activos en Leasing
        add("1.2.12", 272_415_486, 0, "Activos en Leasing — precio neto total 6 contratos vigentes")

        # --- PASIVOS ---
        # Proveedores con aux
        for rut, nom, saldo in PROVEEDORES:
            cp = get_or_create_contraparte(cur, rut, nom, tipo="PROVEEDOR")
            if saldo >= 0:
                add("2.1.01", 0, saldo, f"Saldo proveedor {nom}", cp)
            else:
                add("2.1.01", -saldo, 0, f"Anticipo a proveedor {nom}", cp)
        # Retención Honorarios
        add("2.1.04", 0, 2_300, "Saldo Retención Honorarios al 31-12-2025")
        # Remuneraciones por Pagar (se salda con primeros pagos enero)
        add("2.1.05", 0, 3_037_115, "Remuneraciones por pagar (Ruth Salas + Mauricio Vicencio dic-25)")
        # Cotizaciones (se paga 06/01)
        add("2.1.06", 0, 853_720, "Cotizaciones previsionales dic-25 (se pagan 06-01-26)")
        # Impuestos F29 (se paga 12/01)
        add("2.1.07", 0, 85_632, "F29 diciembre 2025 por pagar (se paga 12-01-26 SII)")
        # Obligaciones Leasing
        cp_bch = get_or_create_contraparte(cur, "97004000-5", "Banco de Chile", tipo="ACREEDOR")
        add("2.1.10", 0, 241_392_261, "Obligaciones por Leasing Banco Chile — 6 contratos vigentes", cp_bch)
        # Préstamos de Terceros (incluye socios)
        for rut, nom, cred, soc in PRESTAMOS_TERCEROS:
            total = cred + soc
            if total == 0:
                continue
            cp = get_or_create_contraparte(cur, rut, nom, tipo="ACREEDOR")
            desc_parts = []
            if cred:
                desc_parts.append(f"crédito privado ${cred:,.0f}")
            if soc:
                desc_parts.append(f"préstamo socio ${soc:,.0f}")
            desc = f"Préstamo de {nom} — {' + '.join(desc_parts)}"
            add("2.1.11", 0, total, desc, cp)

        # --- PATRIMONIO ---
        add("3.1.01", 0, 1_000_000, "Capital pagado")
        # Pérdidas acumuladas = -135.785.147 + ganancia 2025 49.554.269 = -86.230.878
        add("3.1.04", 86_230_878, 0,
            "Resultados acumulados al 31-12-2025 (saldo cuenta 221101 -135.785.147 + ganancia 2025 +49.554.269)")

        # 4. Verificar cuadre
        td = sum(l[1] for l in lineas)
        th = sum(l[2] for l in lineas)
        print(f"Total Debe : {td:>16,}")
        print(f"Total Haber: {th:>16,}")
        if td != th:
            print(f"DESCUADRE: D-H = {td-th:,}")
            raise SystemExit("Apertura no cuadra — abortando")

        if dry_run:
            print(f"[dry-run] asiento_id={asiento_id} ← {len(lineas)} líneas, cuadrado en {td:,}.")
            # Mostrar resumen
            print(f"\n{'Cta':<8}{'Desc':<55}{'Aux':<35}{'Debe':>16}{'Haber':>16}")
            cur.execute("SELECT id, codigo FROM cuentas WHERE empresa_id=?", (EMPRESA_ID,))
            cod_by_id = {i: c for i, c in cur.fetchall()}
            cur.execute("SELECT id, razon_social FROM contrapartes WHERE empresa_id=?", (EMPRESA_ID,))
            cp_by_id = {i: n for i, n in cur.fetchall()}
            for cta, d, h, desc, ord_, cp in lineas:
                cpn = (cp_by_id.get(cp, "") or "")[:32] if cp else ""
                print(f"{cod_by_id[cta]:<8}{desc[:53]:<55}{cpn:<35}{d:>16,}{h:>16,}")
            return
        # 5. Insertar (sólo si NO dry-run)
        for cta, d, h, desc, ord_, cp in lineas:
            cur.execute(
                "INSERT INTO lineas_asiento (asiento_id, cuenta_id, debe, haber, descripcion, orden, contraparte_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (asiento_id, cta, d, h, desc, ord_, cp),
            )

        conn.commit()
        print(f"Apertura insertada: {len(lineas)} líneas, asiento_id={asiento_id}, cuadrado en {td:,}.")


if __name__ == "__main__":
    import sys
    main(dry_run=("--dry-run" in sys.argv))
