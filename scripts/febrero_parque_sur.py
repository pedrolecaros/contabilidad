"""
Procesa febrero 2026 de Parque Sur SpA (empresa_id=1).

- Corrige error de enero: asiento Casino 28/01 $183.173 ahora paga 349377+349883
  (no 349883+350516). Reconoce fact 349377 que faltaba.
- Procesa los 61 movs banco feb con sus respectivas facturas SII.
- Mismo patrón que enero (leasing, créditos privados, TC).
"""

import sqlite3
from contextlib import closing

DB = "contabilidad.db"
EMPRESA_ID = 1


def main(dry_run=False):
    with closing(sqlite3.connect(DB)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT razon_social, rut FROM empresas WHERE id=?", (EMPRESA_ID,))
        row = cur.fetchone()
        if not row: raise SystemExit("empresa no existe")
        print(f"=== Trabajando sobre empresa_id={EMPRESA_ID}: {row[0]} ({row[1]}) ===")

        cur.execute("SELECT codigo, id FROM cuentas WHERE empresa_id=?", (EMPRESA_ID,))
        cta = dict(cur.fetchall())
        cur.execute("SELECT rut, id, razon_social FROM contrapartes WHERE empresa_id=?", (EMPRESA_ID,))
        cps = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        cur.execute(
            "SELECT id, fecha, descripcion, cargo, abono, procesado FROM movimientos_banco "
            "WHERE empresa_id=? AND fecha>='2026-02-01' AND fecha<'2026-03-01' ORDER BY fecha, id",
            (EMPRESA_ID,))
        movs = list(cur.fetchall())
        print(f"Movs feb: {len(movs)}")

        cur.execute(
            "SELECT id, tipo_libro, tipo_dte, folio, fecha, rut_contraparte, razon_social_contraparte, monto_neto, iva, total, procesado "
            "FROM documentos_sii WHERE empresa_id=?", (EMPRESA_ID,))
        sii_docs = list(cur.fetchall())

        def cp_id(rut):
            if rut not in cps: raise SystemExit(f"Falta cp {rut}")
            return cps[rut][0]

        def get_or_create_cp(rut, nombre, tipo="PROVEEDOR"):
            if rut in cps: return cps[rut][0]
            cur.execute(
                "INSERT INTO contrapartes (empresa_id, rut, razon_social, tipo, activo) VALUES (?,?,?,?,1)",
                (EMPRESA_ID, rut, nombre, tipo))
            new_id = cur.lastrowid
            cps[rut] = (new_id, nombre)
            return new_id

        def sii_by_folio(libro, folio):
            return next((d for d in sii_docs if d[1]==libro and d[3]==str(folio)), None)

        def find_mov(fecha, desc_substr, monto=None, mov_id_exclude=()):
            for m in movs:
                if str(m[1]).startswith(fecha) and desc_substr.upper() in m[2].upper():
                    if m[0] in mov_id_exclude: continue
                    if monto is None: return m
                    if abs((m[3] or 0)-monto) < 1 or abs((m[4] or 0)-monto) < 1:
                        return m
            return None

        asientos_creados = []
        def crear_asiento(fecha, descripcion, lineas, mov_ids=(), sii_ids=()):
            td = sum(l[1] for l in lineas); th = sum(l[2] for l in lineas)
            if abs(td-th) > 1:
                raise SystemExit(f"DESCUADRE '{descripcion}': D={td:,} H={th:,}")
            cur.execute(
                "INSERT INTO asientos (empresa_id, fecha, descripcion, estado, origen) VALUES (?,?,?,?,?)",
                (EMPRESA_ID, fecha, descripcion, "BORRADOR",
                 "BANCO" if mov_ids and not sii_ids else ("SII" if sii_ids else "MANUAL")))
            aid = cur.lastrowid
            for orden, (cod, d, h, ldesc, lcp) in enumerate(lineas, start=1):
                if cod not in cta: raise SystemExit(f"Cta {cod} no existe")
                cur.execute(
                    "INSERT INTO lineas_asiento (asiento_id, cuenta_id, debe, haber, descripcion, orden, contraparte_id) VALUES (?,?,?,?,?,?,?)",
                    (aid, cta[cod], d, h, ldesc, orden, lcp))
            for mid in mov_ids:
                cur.execute("UPDATE movimientos_banco SET asiento_id=?, procesado=1 WHERE id=?", (aid, mid))
            for sid in sii_ids:
                cur.execute("UPDATE documentos_sii SET asiento_id=?, procesado=1 WHERE id=?", (aid, sid))
            asientos_creados.append((aid, fecha, descripcion, td))
            return aid

        # ===== CORRECCIÓN ENERO: asiento #1504 Casino =====
        # Ene #1504 decía "pago parcial 349883+350516". En realidad pagó 349377 + 349883 exactos.
        # 1) Reconocer fact 349377 (estaba pendiente)
        # 2) Reescribir descripción asiento #1504 a "Pago facts 349377+349883 Casino"
        # 3) Desconectar fact 350516 de ese asiento (queda lista para pago feb)
        sii_349377 = sii_by_folio("COMPRAS", "349377")
        sii_350516 = sii_by_folio("COMPRAS", "350516")
        casino = cp_id("78793360-2")
        if sii_349377 and (sii_349377[10] is None or sii_349377[10]==0):
            # Crear asiento para fact 349377 (compra dic 25)
            crear_asiento("2025-12-29",
                "Factura 349377 Sociedad de Alimentación Casino (alimentación dic-25)",
                [
                    ("5.2.17", sii_349377[7], 0, "Gasto alimentación — fact 349377", None),
                    ("1.1.05", sii_349377[8], 0, "IVA CF fact 349377", None),
                    ("2.1.01", 0, sii_349377[9], "Factura 349377 Casino dic-25", casino),
                ],
                sii_ids=[sii_349377[0]])
        # Ajustar descripción y desconectar 350516 del asiento #1504
        cur.execute(
            "UPDATE asientos SET descripcion=? WHERE empresa_id=? AND descripcion LIKE '%349883+350516%'",
            ("Pago facts 349377+349883 Casino ($183.173 = $86.909 + $96.264 exactos)", EMPRESA_ID))
        if sii_350516:
            # Desvincular 350516 del asiento erróneo y dejarla pendiente para pago feb
            cur.execute(
                "UPDATE documentos_sii SET procesado=0, asiento_id=NULL WHERE id=?",
                (sii_350516[0],))
            # Buscar asiento (fact 350516 reconocimiento) — sigue válido
            # Está como #1503; no se borra, solo se mantiene como reconocido
        print("Enero corregido: asiento Casino 28/01 ajustado, fact 349377 reconocida")

        # ===================== FEBRERO =====================

        # ===== 1. SUELDOS FEB (02/02) =====
        m_ruth = find_mov("2026-02-02", "Ruth Noemi", 1_804_522)
        m_mauricio = find_mov("2026-02-02", "Mauricio Vicencio", 1_203_765)
        crear_asiento("2026-02-02",
            "Pago remuneraciones enero 2026 (Ruth Salas + Mauricio Vicencio)",
            [
                ("5.2.01", 1_804_522, 0, "Sueldo Ruth Salas enero 26", None),
                ("1.1.02", 0, 1_804_522, "TRASPASO A: Ruth Noemi Salas Anabalon", None),
                ("5.2.01", 1_203_765, 0, "Sueldo Mauricio Vicencio enero 26", None),
                ("1.1.02", 0, 1_203_765, "TRASPASO A: Mauricio Vicencio", None),
            ],
            mov_ids=[m_ruth[0], m_mauricio[0]])

        # ===== 2. PRÉSTAMO ASESORÍAS ECOX (entra 02/02, sale 06/02) =====
        m_in_aec = next(m for m in movs if str(m[1])=="2026-02-02" and "ASESOR" in m[2].upper() and (m[4] or 0)==260_000)
        m_out_aec = next(m for m in movs if str(m[1])=="2026-02-06" and "ASESOR" in m[2].upper() and (m[3] or 0)==260_000)
        aec = cp_id("77714024-8")
        crear_asiento("2026-02-02",
            "Préstamo recibido de Asesorías Ecox (entra 260.000)",
            [
                ("1.1.02", 260_000, 0, "TRASPASO DE: Asesorías Ecox — préstamo privado", None),
                ("2.1.11", 0, 260_000, "Préstamo recibido de Asesorías Ecox", aec),
            ],
            mov_ids=[m_in_aec[0]])
        crear_asiento("2026-02-06",
            "Devolución préstamo Asesorías Ecox (sale 260.000)",
            [
                ("2.1.11", 260_000, 0, "Devuelve préstamo Asesorías Ecox", aec),
                ("1.1.02", 0, 260_000, "TRASPASO A: Asesorías Ecox — devolución préstamo", None),
            ],
            mov_ids=[m_out_aec[0]])

        # ===== 3. COTIZACIONES (06/02) =====
        m_cot = find_mov("2026-02-06", "Instituciones Previsionales", 859_335)
        crear_asiento("2026-02-06",
            "Pago cotizaciones previsionales enero 2026 (Previred)",
            [
                ("5.2.01", 859_335, 0, "Cotizaciones previsionales enero 26", None),
                ("1.1.02", 0, 859_335, "Pago Instituciones Previsionales", None),
            ],
            mov_ids=[m_cot[0]])

        # ===== 4. F29 ENE (09/02) =====
        m_f29 = find_mov("2026-02-09", "SII.CL", 73_509)
        crear_asiento("2026-02-09",
            "Pago F29 enero 2026 (SII)",
            [
                # F29 ene incluye típicamente: IVA cód 91, retenciones cód 151, PPM cód 62.
                # Sin detalle del F29 — agrupo todo como gasto/Impuestos por Pagar.
                # Lo correcto sería descomponer. Por ahora dejo como gasto.
                ("5.2.16", 73_509, 0, "Pago F29 ene-26 (sin descomposición — ver F29 detalle)", None),
                ("1.1.02", 0, 73_509, "Pago en SII.cl F29 ene-26", None),
            ],
            mov_ids=[m_f29[0]])

        # ===== 5. LEASING — 5 cuotas feb =====
        bch = cp_id("97004000-5")
        leasing_feb = [
            # (fecha_pago, folio, n_cuota, contrato, neto, iva, total, interes, capital)
            ("2026-02-23", "46549444", 31, "9995069",   415_516,  78_948,   494_464,   98_960,   316_556),
            ("2026-02-24", "46554491", 23, "10000582", 1_659_803, 315_362, 1_975_165,  468_956, 1_190_847),
            ("2026-02-25", "46559613",  6, "10014750", 1_295_143, 246_077, 1_541_220,  515_809,  779_334),
            ("2026-02-27", "46575527", 35, "9992667", 1_348_669, 256_247, 1_604_916,  278_410, 1_070_259),
            ("2026-02-27", "46575528", 27, "9998305",   397_707,  75_564,   473_271,  109_692,  288_015),
        ]
        for f, folio, n, contrato, neto, iva, total, intt, cap in leasing_feb:
            sii_d = sii_by_folio("COMPRAS", folio)
            mov = find_mov(f, "Leasing", total)
            crear_asiento(f,
                f"Factura Banco de Chile {folio} — Cuota {n} leasing contrato {contrato}",
                [
                    ("5.2.12", intt, 0, f"Interés leasing cuota {n} contrato {contrato}", None),
                    ("1.1.05", iva, 0, f"IVA CF factura leasing {folio}", None),
                    ("2.1.10", cap, 0, f"Capital leasing cuota {n} contrato {contrato}", bch),
                    ("2.1.01", 0, total, f"Factura {folio} leasing Banco de Chile", bch),
                ],
                sii_ids=[sii_d[0]] if sii_d else [])
            crear_asiento(f,
                f"Pago factura Banco de Chile {folio} — Cuota {n} leasing",
                [
                    ("2.1.01", total, 0, f"Pago factura leasing {folio}", bch),
                    ("1.1.02", 0, total, f"Pago Cuota Leasing Cuota {n} fact {folio}", None),
                ],
                mov_ids=[mov[0]] if mov else [])

        # ===== 6. CRÉDITOS PRIVADOS FEB (9 cuotas día 20) =====
        creditos_feb = [
            # (rut, nombre, cuota_n, monto_real, int_excel, cap_excel, total_excel)
            ("15640744-5", "Felipe Andres Hiriart Blome",  11, 2_498_575,   557_527, 1_941_048, 2_498_575),
            ("16367300-2", "Pedro Lecaros Sotomayor",      11,   374_770,    83_597,   291_173,   374_770),
            ("16100615-7", "Dominique Hiriart",             8, 2_277_110,   538_011, 1_739_099, 2_277_110),
            ("76185865-3", "Inversiones Jopa Limitada",     6,   313_966,    77_140,   236_826,   313_966),
            ("15595261-K", "Jose Luis Illanes Guridi",      6,   297_151,    73_122,   224_029,   297_151),
            ("76185865-3", "Inversiones Jopa Limitada",     5, 2_669_669,    18_005, 2_651_664, 2_669_669),
            ("77714024-8", "Asesorias Ecox Limitada",       4,   147_289,    37_660,   109_629,   147_289),
            ("77714024-8", "Asesorias Ecox Limitada",       3,   174_423,    10_910,   163_513,   174_423),
            ("77714024-8", "Asesorias Ecox Limitada",       2,   122_177,    30_273,    91_904,   122_177),
        ]
        for rut, nombre, cn, total_real, intt, cap, _ in creditos_feb:
            cp = cp_id(rut)
            primer = nombre.split()[0]
            mov = None
            for m in movs:
                # solo movs día 20 sin procesar aún
                already = m[5] == 1 or m[0] in [a[0] for a in []]
                if str(m[1])=="2026-02-20" and primer.upper() in m[2].upper() and abs((m[3] or 0)-total_real) < 2:
                    # check movement isn't reused (procesado after our updates)
                    cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
                    if cur.fetchone()[0] == 1: continue
                    mov = m; break
            if not mov and rut == "15595261-K":
                for m in movs:
                    if str(m[1])=="2026-02-20" and "luisill" in m[2].lower() and abs((m[3] or 0)-total_real) < 2:
                        cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
                        if cur.fetchone()[0] == 1: continue
                        mov = m; break
            if not mov:
                print(f"WARN: no encuentro mov crédito {nombre} cuota {cn} ${total_real}")
                continue
            crear_asiento("2026-02-20",
                f"Pago cuota {cn} crédito privado {nombre}",
                [
                    ("1.1.02", 0, total_real, f"TRASPASO A: {nombre} — cuota {cn} crédito privado", None),
                    ("2.1.11", cap, 0, f"Capital cuota {cn} crédito {nombre}", cp),
                    ("5.2.12", intt, 0, f"Interés cuota {cn} crédito {nombre}", cp),
                ],
                mov_ids=[mov[0]])

        # ===== 7. PAGOS DE FACTURAS SII (compra + pago) =====
        # Casino fact 350516 — pago feb cancela (originada ene)
        m_cas = find_mov("2026-02-06", "Alimentacio", 112_682)
        crear_asiento("2026-02-06",
            "Pago factura 350516 Sociedad de Alimentación Casino",
            [
                ("2.1.01", 112_682, 0, "Pago fact 350516 Casino", casino),
                ("1.1.02", 0, 112_682, "TRASPASO A: Casino — fact 350516", None),
            ],
            mov_ids=[m_cas[0]])

        # Asesorias Ecox Fact 203 (exenta, $100.000) — admin mensual
        sii_203 = sii_by_folio("COMPRAS", "203")
        m_aec_fact = find_mov("2026-02-06", "Asesor", 100_000)
        crear_asiento("2026-02-05",
            "Factura 203 Asesorías Ecox (administración exenta)",
            [
                ("5.2.11", 100_000, 0, "Asesoría administración — fact 203 exenta", None),
                ("2.1.01", 0, 100_000, "Factura 203 Asesorías Ecox", aec),
            ],
            sii_ids=[sii_203[0]] if sii_203 else [])
        crear_asiento("2026-02-06",
            "Pago factura 203 Asesorías Ecox",
            [
                ("2.1.01", 100_000, 0, "Pago fact 203 Asesorías Ecox", aec),
                ("1.1.02", 0, 100_000, "TRASPASO A: Asesorías Ecox fact 203", None),
            ],
            mov_ids=[m_aec_fact[0]])

        # Comercial Agurto $106.540 — paga facts ene pendientes 154605 + 156360
        sii_154605 = sii_by_folio("COMPRAS", "154605")
        sii_156360 = sii_by_folio("COMPRAS", "156360")
        agurto = cp_id("77615904-2")
        m_agurto = find_mov("2026-02-06", "Agurto", 106_540)
        # Reconocer ambas facts (estaban pendientes ene). Combustible: diferencia neto+iva vs total
        # corresponde a impuesto específico combustible → suma al mismo gasto.
        def reconocer_fact_combustible(sii_d, fecha, folio):
            if not sii_d or (sii_d[10] not in (None, 0)): return
            gasto = sii_d[7] + (sii_d[9] - sii_d[7] - sii_d[8])  # neto + diff (impuesto específico)
            crear_asiento(fecha,
                f"Factura {folio} Comercial Agurto (combustible)",
                [
                    ("5.2.06", gasto, 0, f"Combustible (incl. imp. específico) — fact {folio}", None),
                    ("1.1.05", sii_d[8], 0, f"IVA CF fact {folio}", None),
                    ("2.1.01", 0, sii_d[9], f"Factura {folio} Agurto", agurto),
                ],
                sii_ids=[sii_d[0]])
        reconocer_fact_combustible(sii_154605, "2026-01-08", "154605")
        reconocer_fact_combustible(sii_156360, "2026-01-20", "156360")
        crear_asiento("2026-02-06",
            "Pago Comercial Agurto facts 154605 + 156360 (combustible ene)",
            [
                ("2.1.01", 106_540, 0, "Pago facts Agurto 154605+156360", agurto),
                ("1.1.02", 0, 106_540, "TRASPASO A: Comercial Agurto", None),
            ],
            mov_ids=[m_agurto[0]])

        # Emaresa $195.043 — cierra apertura Emaresa
        emaresa = cp_id("83162400-0")
        m_em = find_mov("2026-02-09", "Emaresa", 195_043)
        crear_asiento("2026-02-09",
            "Pago Emaresa fact 2993591 — repuestos (apertura)",
            [
                ("2.1.01", 195_043, 0, "Cancela saldo apertura Emaresa fact 2993591", emaresa),
                ("1.1.02", 0, 195_043, "TRASPASO A: Emaresa", None),
            ],
            mov_ids=[m_em[0]])

        # MBC $740.180 — cierra apertura
        mbc = cp_id("76800655-5")
        m_mbc = find_mov("2026-02-09", "Mecanica", 740_180)
        crear_asiento("2026-02-09",
            "Pago Comercial y Mecánica MBC fact 5320 — baterías (apertura)",
            [
                ("2.1.01", 740_180, 0, "Cancela saldo apertura MBC fact 5320", mbc),
                ("1.1.02", 0, 740_180, "TRASPASO A: Comercial y Mecánica MBC", None),
            ],
            mov_ids=[m_mbc[0]])

        # Luis Letelier $142.800 — fact 6421 feb
        sii_6421 = sii_by_folio("COMPRAS", "6421")
        letelier = cp_id("5544861-2")
        m_let = find_mov("2026-02-10", "Letelier", 142_800)
        crear_asiento("2026-02-10",
            "Factura 6421 Luis Letelier (maestranza taller)",
            [
                ("5.2.07", sii_6421[7], 0, "Mantención — fact 6421", None),
                ("1.1.05", sii_6421[8], 0, "IVA CF fact 6421", None),
                ("2.1.01", 0, sii_6421[9], "Factura 6421 Luis Letelier", letelier),
            ],
            sii_ids=[sii_6421[0]])
        crear_asiento("2026-02-10",
            "Pago factura 6421 Luis Letelier",
            [
                ("2.1.01", 142_800, 0, "Pago fact 6421 Luis Letelier", letelier),
                ("1.1.02", 0, 142_800, "TRASPASO A: Luis Letelier", None),
            ],
            mov_ids=[m_let[0]])

        # Mundo Lockers $80.908 — fact 6017 feb
        sii_6017 = sii_by_folio("COMPRAS", "6017")
        lockers = get_or_create_cp("76580205-9", "Mundo Lockers SpA")
        m_lock = find_mov("2026-02-12", "Lockers", 80_908)
        crear_asiento("2026-02-16",
            "Factura 6017 Mundo Lockers (lockers taller)",
            [
                ("5.2.06", sii_6017[7], 0, "Lockers taller — fact 6017", None),
                ("1.1.05", sii_6017[8], 0, "IVA CF fact 6017", None),
                ("2.1.01", 0, sii_6017[9], "Factura 6017 Mundo Lockers", lockers),
            ],
            sii_ids=[sii_6017[0]])
        crear_asiento("2026-02-12",
            "Pago factura 6017 Mundo Lockers",
            [
                ("2.1.01", 80_908, 0, "Pago fact 6017 Mundo Lockers", lockers),
                ("1.1.02", 0, 80_908, "TRASPASO A: Mundo Lockers", None),
            ],
            mov_ids=[m_lock[0]])

        # Cristian Silva $714.000 — facts 5568 a 5704 (apertura + ene-dic pendientes)
        silva = cp_id("76540394-4")
        # Cancela apertura $606.900 + fact 5704 dic-25 $107.100 = $714.000
        sii_5704 = sii_by_folio("COMPRAS", "5704")
        if sii_5704 and (sii_5704[10] is None or sii_5704[10]==0):
            crear_asiento("2025-12-31",
                "Factura 5704 Servicios de Grúa Cristian Silva (transportes dic-25)",
                [
                    ("5.2.09", sii_5704[7], 0, "Transportes — fact 5704", None),
                    ("1.1.05", sii_5704[8], 0, "IVA CF fact 5704", None),
                    ("2.1.01", 0, sii_5704[9], "Factura 5704 Cristian Silva", silva),
                ],
                sii_ids=[sii_5704[0]])
        m_silva1 = find_mov("2026-02-13", "Servicios De Grua", 714_000)
        crear_asiento("2026-02-13",
            "Pago Cristian Silva facts 5568-5704 (apertura + dic-25)",
            [
                ("2.1.01", 714_000, 0, "Cancela apertura + fact 5704 Cristian Silva", silva),
                ("1.1.02", 0, 714_000, "TRASPASO A: Cristian Silva — facts varias", None),
            ],
            mov_ids=[m_silva1[0]])

        # Enduro Motor $714.000 — fact 3479 feb
        sii_3479 = sii_by_folio("COMPRAS", "3479")
        enduro = cp_id("76124823-5")
        m_end = find_mov("2026-02-13", "Enduro", 714_000)
        crear_asiento("2026-02-09",
            "Factura 3479 Enduro Motor / Hernan Alvayay (arriendo taller)",
            [
                ("5.2.03", sii_3479[7], 0, "Arriendo taller — fact 3479", None),
                ("1.1.05", sii_3479[8], 0, "IVA CF fact 3479", None),
                ("2.1.01", 0, sii_3479[9], "Factura 3479 Enduro Motor", enduro),
            ],
            sii_ids=[sii_3479[0]])
        crear_asiento("2026-02-13",
            "Pago factura 3479 Enduro Motor",
            [
                ("2.1.01", 714_000, 0, "Pago fact 3479 Enduro Motor", enduro),
                ("1.1.02", 0, 714_000, "TRASPASO A: Enduro Motor", None),
            ],
            mov_ids=[m_end[0]])

        # GAM $3.427.200 — facts 93575+93576+93577 pendientes dic-25
        gam = cp_id("76106956-K")
        for folio in ["93575","93576","93577"]:
            sii_d = sii_by_folio("COMPRAS", folio)
            if sii_d and (sii_d[10] is None or sii_d[10]==0):
                crear_asiento("2025-12-31",
                    f"Factura {folio} GAM — subarriendo dic-25",
                    [
                        ("5.2.03", sii_d[7], 0, f"Subarriendo maquinaria — fact {folio}", None),
                        ("1.1.05", sii_d[8], 0, f"IVA CF fact {folio}", None),
                        ("2.1.01", 0, sii_d[9], f"Factura {folio} GAM", gam),
                    ],
                    sii_ids=[sii_d[0]])
        m_gam = find_mov("2026-02-16", "General", 3_427_200)
        crear_asiento("2026-02-16",
            "Pago GAM facts 93575+93576+93577 (subarriendo dic-25)",
            [
                ("2.1.01", 3_427_200, 0, "Pago GAM facts 93575+76+77", gam),
                ("1.1.02", 0, 3_427_200, "TRASPASO A: GAM", None),
            ],
            mov_ids=[m_gam[0]])

        # Horizon $1.755.012 — fact 24772 pendiente dic-25
        sii_24772 = sii_by_folio("COMPRAS", "24772")
        horizon = cp_id("76217392-1")
        if sii_24772 and (sii_24772[10] is None or sii_24772[10]==0):
            crear_asiento("2025-12-30",
                "Factura 24772 Horizon (subarriendo dic-25)",
                [
                    ("5.2.03", sii_24772[7], 0, "Subarriendo — fact 24772", None),
                    ("1.1.05", sii_24772[8], 0, "IVA CF fact 24772", None),
                    ("2.1.01", 0, sii_24772[9], "Factura 24772 Horizon", horizon),
                ],
                sii_ids=[sii_24772[0]])
        m_hor = find_mov("2026-02-16", "Horizon", 1_755_012)
        crear_asiento("2026-02-16",
            "Pago Horizon fact 24772 (subarriendo)",
            [
                ("2.1.01", 1_755_012, 0, "Pago fact 24772 Horizon", horizon),
                ("1.1.02", 0, 1_755_012, "TRASPASO A: Horizon", None),
            ],
            mov_ids=[m_hor[0]])

        # Comercial BVS $278.460 — fact 6063 feb
        sii_6063 = sii_by_folio("COMPRAS", "6063")
        bvs = get_or_create_cp("76577972-3", "Comercial BVS SpA")
        m_bvs = find_mov("2026-02-16", "Bvs", 278_460)
        crear_asiento("2026-02-12",
            "Factura 6063 Comercial BVS (cambio neumáticos)",
            [
                ("5.2.07", sii_6063[7], 0, "Mantención neumáticos — fact 6063", None),
                ("1.1.05", sii_6063[8], 0, "IVA CF fact 6063", None),
                ("2.1.01", 0, sii_6063[9], "Factura 6063 BVS", bvs),
            ],
            sii_ids=[sii_6063[0]])
        crear_asiento("2026-02-16",
            "Pago factura 6063 Comercial BVS",
            [
                ("2.1.01", 278_460, 0, "Pago fact 6063 BVS", bvs),
                ("1.1.02", 0, 278_460, "TRASPASO A: Comercial BVS", None),
            ],
            mov_ids=[m_bvs[0]])

        # Wurth $337.358 — fact 2314289 feb
        sii_wurth = sii_by_folio("COMPRAS", "2314289")
        wurth = get_or_create_cp("78701740-1", "Wurth Chile Ltda")
        m_wurth = find_mov("2026-02-19", "Wurth", 337_358)
        crear_asiento("2026-02-19",
            "Factura 2314289 Wurth (insumos)",
            [
                ("5.2.06", sii_wurth[7], 0, "Insumos — fact 2314289", None),
                ("1.1.05", sii_wurth[8], 0, "IVA CF fact 2314289", None),
                ("2.1.01", 0, sii_wurth[9], "Factura 2314289 Wurth", wurth),
            ],
            sii_ids=[sii_wurth[0]])
        crear_asiento("2026-02-19",
            "Pago factura 2314289 Wurth",
            [
                ("2.1.01", 337_358, 0, "Pago fact 2314289 Wurth", wurth),
                ("1.1.02", 0, 337_358, "TRASPASO A: Wurth", None),
            ],
            mov_ids=[m_wurth[0]])

        # Los Navegantes $10.864 (20/02 y 27/02) — fact 235108 feb $10.864 + ¿otra?
        sii_navegantes = sii_by_folio("COMPRAS", "235108")
        navegantes = cp_id("96868900-2")
        if sii_navegantes:
            crear_asiento("2026-02-05",
                "Factura 235108 Los Navegantes",
                [
                    ("5.2.09", sii_navegantes[7], 0, "Transporte — fact 235108", None),
                    ("1.1.05", sii_navegantes[8], 0, "IVA CF fact 235108", None),
                    ("2.1.01", 0, sii_navegantes[9], "Factura 235108 Los Navegantes", navegantes),
                ],
                sii_ids=[sii_navegantes[0]])
        for fecha in ["2026-02-20", "2026-02-27"]:
            m_nav = find_mov(fecha, "Navegantes", 10_864)
            if not m_nav:
                # check exact
                for m in movs:
                    if str(m[1])==fecha and "Navegante" in m[2] and (m[3] or 0)==10_864:
                        cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
                        if cur.fetchone()[0] == 1: continue
                        m_nav = m; break
            if m_nav:
                crear_asiento(fecha,
                    f"Pago Los Navegantes ({fecha})",
                    [
                        ("2.1.01", 10_864, 0, "Pago Los Navegantes", navegantes),
                        ("1.1.02", 0, 10_864, "TRASPASO A: Los Navegantes", None),
                    ],
                    mov_ids=[m_nav[0]])

        # Casino fact 351085 $99.796 (27/02)
        sii_351085 = sii_by_folio("COMPRAS", "351085")
        m_cas2 = find_mov("2026-02-27", "Alimentacio", 99_796)
        if sii_351085:
            crear_asiento("2026-02-16",
                "Factura 351085 Casino (alimentación)",
                [
                    ("5.2.17", sii_351085[7], 0, "Gasto alimentación — fact 351085", None),
                    ("1.1.05", sii_351085[8], 0, "IVA CF fact 351085", None),
                    ("2.1.01", 0, sii_351085[9], "Factura 351085 Casino", casino),
                ],
                sii_ids=[sii_351085[0]])
        if m_cas2:
            crear_asiento("2026-02-27",
                "Pago factura 351085 Casino",
                [
                    ("2.1.01", 99_796, 0, "Pago fact 351085 Casino", casino),
                    ("1.1.02", 0, 99_796, "TRASPASO A: Casino fact 351085", None),
                ],
                mov_ids=[m_cas2[0]])

        # Cristian Silva $678.300 (27/02) — facts 5721-5855 ene-feb
        # 5721 (ene pendiente) + 5761 5766 (ene pendientes) + 5811 5826 5835 5847 5855 (feb)
        for folio in ["5721","5761","5766","5811","5826","5835","5847","5855"]:
            sii_d = sii_by_folio("COMPRAS", folio)
            if sii_d and (sii_d[10] is None or sii_d[10]==0):
                crear_asiento(sii_d[4],
                    f"Factura {folio} Cristian Silva (transportes)",
                    [
                        ("5.2.09", sii_d[7], 0, f"Transporte — fact {folio}", None),
                        ("1.1.05", sii_d[8], 0, f"IVA CF fact {folio}", None),
                        ("2.1.01", 0, sii_d[9], f"Factura {folio} Cristian Silva", silva),
                    ],
                    sii_ids=[sii_d[0]])
        m_silva2 = find_mov("2026-02-27", "Servicios De Grua", 678_300)
        if m_silva2:
            crear_asiento("2026-02-27",
                "Pago Cristian Silva facts 5721+5761+5766+5811+5826+5835+5847+5855",
                [
                    ("2.1.01", 678_300, 0, "Pago Cristian Silva 8 facts ene-feb", silva),
                    ("1.1.02", 0, 678_300, "TRASPASO A: Cristian Silva", None),
                ],
                mov_ids=[m_silva2[0]])

        # Ferreteria El Metro $440.000 — fact 454439 feb (hidrolavadora)
        sii_454439 = sii_by_folio("COMPRAS", "454439")
        ferret = get_or_create_cp("83364400-9", "Ferretería El Metro Ltda")
        m_fer = find_mov("2026-02-27", "Metro", 440_000)
        if sii_454439:
            # Hidrolavadora — la trato como gasto materiales por monto chico ($440K), avisar Pedro si quiere activo
            crear_asiento("2026-02-27",
                "Factura 454439 Ferretería El Metro (hidrolavadora — revisar si va a 1.2.03)",
                [
                    ("5.2.06", sii_454439[7], 0, "Hidrolavadora — fact 454439", None),
                    ("1.1.05", sii_454439[8], 0, "IVA CF fact 454439", None),
                    ("2.1.01", 0, sii_454439[9], "Factura 454439 Ferretería El Metro", ferret),
                ],
                sii_ids=[sii_454439[0]])
        if m_fer:
            crear_asiento("2026-02-27",
                "Pago factura 454439 Ferretería El Metro",
                [
                    ("2.1.01", 440_000, 0, "Pago fact 454439", ferret),
                    ("1.1.02", 0, 440_000, "TRASPASO A: Ferretería El Metro", None),
                ],
                mov_ids=[m_fer[0]])

        # Sueldos feb (27/02)
        m_ruth2 = find_mov("2026-02-27", "Ruth Noemi", 2_045_662)
        m_mau2 = find_mov("2026-02-27", "Mauricio Vicencio", 1_203_689)
        crear_asiento("2026-02-27",
            "Pago remuneraciones febrero 2026 (Ruth Salas + Mauricio Vicencio)",
            [
                ("5.2.01", 2_045_662, 0, "Sueldo Ruth Salas febrero 26", None),
                ("1.1.02", 0, 2_045_662, "TRASPASO A: Ruth Salas", None),
                ("5.2.01", 1_203_689, 0, "Sueldo Mauricio Vicencio febrero 26", None),
                ("1.1.02", 0, 1_203_689, "TRASPASO A: Mauricio Vicencio", None),
            ],
            mov_ids=[m_ruth2[0], m_mau2[0]])

        # ===== 8. TC FEB =====
        # 12/02 Pago automático
        m_tc = find_mov("2026-02-12", "TARJETA", 321_512)
        a_tc_pago = crear_asiento("2026-02-12",
            "Pago automático Tarjeta de Crédito (salda gastos TC ene)",
            [
                ("2.1.14", 321_512, 0, "Salda saldo TC ciclo ene-26", None),
                ("1.1.02", 0, 321_512, "PAGO AUTOMATICO TARJETA DE CREDITO", None),
            ],
            mov_ids=[m_tc[0]])
        # Espejo abono [ref] $321.512 — solo linkear al mismo asiento
        m_tc_esp = next((m for m in movs if str(m[1])=="2026-02-12" and "[ref 120200000000]" in m[2] and (m[4] or 0)==321_512), None)
        if m_tc_esp:
            cur.execute("UPDATE movimientos_banco SET asiento_id=?, procesado=1 WHERE id=?", (a_tc_pago, m_tc_esp[0]))

        # Cargos TC feb (espejo)
        cargos_tc_feb = [
            ("2026-02-09", "LINKEDIN", 87_480, "5.2.10", "LinkedIn — publicidad digital TC"),
            ("2026-02-12", "TRASPASO DEUDA INTERNACIONAL", 83_160, "5.2.10", "Publicidad digital internacional TC"),
            ("2026-02-25", "IMPUESTO DECRETO LEY 3475", 55, "5.2.12", "Impuesto DL 3475 TC"),
            ("2026-02-25", "COMISION MENSUAL", 1_591, "5.2.12", "Comisión mensual mantención TC"),
            ("2026-02-25", "INTERESES ROTATIVOS", 1_922, "5.2.12", "Intereses rotativos TC"),
        ]
        for fecha, ds, monto, cta_g, glosa in cargos_tc_feb:
            mov = None
            for m in movs:
                if str(m[1]).startswith(fecha) and ds.upper() in m[2].upper() and abs((m[3] or 0)-monto) < 1:
                    mov = m; break
            if not mov:
                print(f"WARN TC feb no encontrado: {fecha} {ds} ${monto}")
                continue
            crear_asiento(fecha, glosa,
                [
                    (cta_g, monto, 0, glosa, None),
                    ("2.1.14", 0, monto, f"Cargo TC {ds}", None),
                ],
                mov_ids=[mov[0]])

        # ===== 9. TAG (peajes) =====
        m_tag = find_mov("2026-02-25", "SERVIPAG", 38_645)
        crear_asiento("2026-02-25",
            "TAG autopista (Servipag)",
            [
                ("5.2.09", 38_645, 0, "TAG peajes autopista", None),
                ("1.1.02", 0, 38_645, "PAGO EN SERVIPAG.COM*", None),
            ],
            mov_ids=[m_tag[0]])

        # ===== 10. VENTAS SII FEB =====
        ventas_feb = [d for d in sii_docs if d[1]=="VENTAS" and d[4] and d[4].startswith("2026-02")]
        ventas_agrupadas = {}
        for d in ventas_feb:
            ventas_agrupadas.setdefault((d[5], d[4]), []).append(d)
        for (rut, fecha), docs in sorted(ventas_agrupadas.items(), key=lambda x: x[0][1]):
            cp = get_or_create_cp(rut, docs[0][6] if docs[0][6] else rut, tipo="CLIENTE")
            lineas = []
            for d in docs:
                _, _, dte, fol, _, _, nom_cp, neto, iva, total, _ = d
                if dte == "33":
                    lineas.append(("1.1.03", total, 0, f"Fact vta {fol}", cp))
                    lineas.append(("4.1.04", 0, neto, f"Servicios afectos fact {fol}", None))
                    lineas.append(("2.1.03", 0, iva, f"IVA DF fact {fol}", None))
                elif dte == "61":
                    # Nota de crédito: revierte una venta
                    lineas.append(("1.1.03", 0, total, f"NC {fol}", cp))
                    lineas.append(("4.1.04", neto, 0, f"NC servicios fact {fol}", None))
                    lineas.append(("2.1.03", iva, 0, f"NC IVA fact {fol}", None))
                else:
                    lineas.append(("1.1.03", total, 0, f"Fact vta {fol} exenta", cp))
                    lineas.append(("4.1.05", 0, total, f"Servicios exentos fact {fol}", None))
            sii_ids_list = [d[0] for d in docs]
            crear_asiento(fecha,
                f"Ventas a {docs[0][6]} — fact(s) {'+'.join(d[3] for d in docs)}",
                lineas, sii_ids=sii_ids_list)

        # ===== 11. COBRANZAS FEB (clientes) =====
        cobros = [
            ("2026-02-06", "Fenix", 10_639_862, "77499240-5", "Cobranza facts vta 1018-1021 Fenix"),
            ("2026-02-06", "0760988200", 3_495_135, "76098820-0", "Cobranza fact vta 1001 Bodegas SF"),
            ("2026-02-06", "0789241007", 1_832_113, "78924100-7", "Cobranza fact vta 1011 Inv Integrales"),
            ("2026-02-06", "0789241007", 3_905_020, "78924100-7", "Cobranza facts vta 1012+1013 Inv Integrales"),
            ("2026-02-09", "Fenix", 1_327_144, "77499240-5", "Cobranza fact vta 1027 Fenix"),
            ("2026-02-11", "0765281105", 3_687_354, "76528110-5", "Cobranza fact vta 1009 ALD Logística"),
            ("2026-02-13", "Flex Rack", 59_500, "78017988-0", "Cobranza fact vta 978 Flex Rack (apertura)"),
            ("2026-02-13", "0760988200", 195_250, "76098820-0", "Cobranza facts vta 1015+1017 Bodegas SF"),
            ("2026-02-13", "0789241007", 107_100, "78924100-7", "Cobranza fact vta 1014 Inv Integrales"),
            ("2026-02-13", "0764496213", 1_642_640, "76449621-3", "Cobranza fact vta 1022 Constructora Lo Aguirre"),
            ("2026-02-16", "0816756006", 945_525, "81675600-6", "Cobranza fact vta 994 Hites"),
            ("2026-02-20", "0789241007", 4_198_728, "78924100-7", "Cobranza facts vta 1035-1038 Inv Integrales"),
            ("2026-02-20", "0789241007", 2_156_481, "78924100-7", "Cobranza fact vta 1043 Inv Integrales"),
        ]
        for fecha, ds, monto, rut, glosa in cobros:
            m = find_mov(fecha, ds, monto)
            if not m:
                print(f"WARN: cobro no encontrado {fecha} {ds} ${monto}")
                continue
            cp = get_or_create_cp(rut, glosa, tipo="CLIENTE")
            crear_asiento(fecha, glosa,
                [
                    ("1.1.02", monto, 0, f"{glosa} — abono banco", None),
                    ("1.1.03", 0, monto, glosa, cp),
                ],
                mov_ids=[m[0]])

        # ===== RESUMEN =====
        print(f"\n=== ASIENTOS CREADOS: {len(asientos_creados)} ===")
        for aid, f, desc, t in asientos_creados[-30:]:
            print(f"  #{aid} {f} ${t:>14,.0f} {desc[:70]}")

        cur.execute(
            "SELECT id, fecha, descripcion, cargo, abono FROM movimientos_banco "
            "WHERE empresa_id=? AND fecha>='2026-02-01' AND fecha<'2026-03-01' AND (procesado IS NULL OR procesado=0) ORDER BY fecha, id",
            (EMPRESA_ID,))
        np = cur.fetchall()
        print(f"\n=== MOVS BANCO FEB NO PROCESADOS: {len(np)} ===")
        for r in np:
            print(f"  mov#{r[0]} {r[1]} ${(r[3] or 0):>10,} ${(r[4] or 0):>10,} {r[2][:60]}")

        if dry_run:
            print("\n[DRY-RUN] No commit.")
        else:
            conn.commit()
            print("\nCommit OK.")


if __name__ == "__main__":
    import sys
    main(dry_run=("--dry-run" in sys.argv))
