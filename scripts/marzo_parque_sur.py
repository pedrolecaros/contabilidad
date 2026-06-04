"""
Procesa marzo 2026 de Parque Sur SpA (empresa_id=1).
Patrón idéntico a febrero. Cubre: leasing x7 (incl cuota 9 pendiente feb), créditos privados x8 (JOPA-6 terminó),
TC pago auto + cargos, sueldos no aparecen (Ruth/Mauricio reciben "rendiciones" en marzo),
préstamos lavados Asesorías Ecox (entran $2.5M en 02-03/03, salen $2.5M el 06/03),
rescate FFMM $10M (es aporte, va contra 1.1.09).
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
        print(f"=== Empresa_id={EMPRESA_ID}: {row[0]} ({row[1]}) ===")

        cur.execute("SELECT codigo, id FROM cuentas WHERE empresa_id=?", (EMPRESA_ID,))
        cta = dict(cur.fetchall())
        cur.execute("SELECT rut, id FROM contrapartes WHERE empresa_id=?", (EMPRESA_ID,))
        cps = dict(cur.fetchall())

        cur.execute(
            "SELECT id, fecha, descripcion, cargo, abono, procesado FROM movimientos_banco "
            "WHERE empresa_id=? AND fecha>='2026-03-01' AND fecha<'2026-04-01' ORDER BY fecha, id",
            (EMPRESA_ID,))
        movs = list(cur.fetchall())
        print(f"Movs mar: {len(movs)}")

        cur.execute("SELECT id, tipo_libro, tipo_dte, folio, fecha, rut_contraparte, razon_social_contraparte, "
                    "monto_neto, iva, total, procesado FROM documentos_sii WHERE empresa_id=?", (EMPRESA_ID,))
        sii_docs = list(cur.fetchall())

        def cp_id(rut):
            if rut not in cps: raise SystemExit(f"Falta cp {rut}")
            return cps[rut]
        def get_or_create_cp(rut, nombre, tipo="PROVEEDOR"):
            if rut in cps: return cps[rut]
            cur.execute("INSERT INTO contrapartes (empresa_id, rut, razon_social, tipo, activo) VALUES (?,?,?,?,1)",
                        (EMPRESA_ID, rut, nombre, tipo))
            cps[rut] = cur.lastrowid
            return cps[rut]
        def sii_by_folio(libro, folio):
            return next((d for d in sii_docs if d[1]==libro and d[3]==str(folio)), None)
        def find_mov(fecha, sub, monto=None):
            for m in movs:
                if str(m[1]).startswith(fecha) and sub.upper() in m[2].upper():
                    if monto is None: return m
                    if abs((m[3] or 0)-monto)<2 or abs((m[4] or 0)-monto)<2:
                        cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
                        if cur.fetchone()[0] == 1: continue
                        return m
            return None

        asientos = []
        def C(fecha, desc, lineas, mov_ids=(), sii_ids=()):
            td = sum(l[1] for l in lineas); th = sum(l[2] for l in lineas)
            if abs(td-th) > 1:
                raise SystemExit(f"DESCUADRE '{desc}': D={td:,} H={th:,}")
            cur.execute("INSERT INTO asientos (empresa_id, fecha, descripcion, estado, origen) VALUES (?,?,?,?,?)",
                        (EMPRESA_ID, fecha, desc, "BORRADOR",
                         "BANCO" if mov_ids and not sii_ids else ("SII" if sii_ids else "MANUAL")))
            aid = cur.lastrowid
            for orden, (cod, d, h, ldesc, lcp) in enumerate(lineas, start=1):
                if cod not in cta: raise SystemExit(f"Cta {cod} no existe")
                cur.execute("INSERT INTO lineas_asiento (asiento_id, cuenta_id, debe, haber, descripcion, orden, contraparte_id) VALUES (?,?,?,?,?,?,?)",
                            (aid, cta[cod], d, h, ldesc, orden, lcp))
            for mid in mov_ids:
                cur.execute("UPDATE movimientos_banco SET asiento_id=?, procesado=1 WHERE id=?", (aid, mid))
            for sid in sii_ids:
                cur.execute("UPDATE documentos_sii SET asiento_id=?, procesado=1 WHERE id=?", (aid, sid))
            asientos.append((aid, fecha, desc, td))
            return aid

        bch = cp_id("97004000-5")
        casino = cp_id("78793360-2")
        agurto = cp_id("77615904-2")
        aec = cp_id("77714024-8")
        gam = cp_id("76106956-K")
        ahern = cp_id("76524465-K")
        enduro = cp_id("76124823-5")
        navegantes = cp_id("96868900-2")
        wurth = cp_id("78701740-1")
        mbc = cp_id("76800655-5")

        # ===== PRÉSTAMOS ASESORÍAS ECOX (lavado mar) =====
        # 02/03 entra $1.500.000, 03/03 entra $1.000.000, 06/03 sale $1.000.000 + $1.500.000
        for fecha, monto in [("2026-03-02", 1_500_000), ("2026-03-03", 1_000_000)]:
            m = next((m for m in movs if str(m[1])==fecha and "ASESOR" in m[2].upper() and (m[4] or 0)==monto), None)
            if m:
                C(fecha, f"Préstamo recibido de Asesorías Ecox ${monto:,}",
                  [("1.1.02", monto, 0, "TRASPASO DE: Asesorías Ecox", None),
                   ("2.1.11", 0, monto, "Préstamo recibido Asesorías Ecox", aec)],
                  mov_ids=[m[0]])
        for monto in [1_000_000, 1_500_000]:
            m = next((m for m in movs if str(m[1])=="2026-03-06" and "ASESOR" in m[2].upper() and (m[3] or 0)==monto and "prestamo" in m[2].lower()), None)
            if not m:
                m = next((m for m in movs if str(m[1])=="2026-03-06" and "ASESOR" in m[2].upper() and (m[3] or 0)==monto), None)
            if m:
                cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
                if cur.fetchone()[0] == 1: continue
                C("2026-03-06", f"Devolución préstamo Asesorías Ecox ${monto:,}",
                  [("2.1.11", monto, 0, "Devuelve préstamo Asesorías Ecox", aec),
                   ("1.1.02", 0, monto, "TRASPASO A: Asesorías Ecox", None)],
                  mov_ids=[m[0]])

        # ===== COTIZACIONES feb (06/03 $942.893) =====
        m_cot = find_mov("2026-03-06", "Instituciones", 942_893)
        if m_cot:
            C("2026-03-06", "Pago cotizaciones previsionales feb 2026",
              [("5.2.01", 942_893, 0, "Cotizaciones feb 26", None),
               ("1.1.02", 0, 942_893, "Pago Previred", None)],
              mov_ids=[m_cot[0]])

        # ===== F29 feb (09/03 $81.466) — usar declaraciones_f29 =====
        cur.execute("SELECT codigo_62, codigo_48, codigo_151, codigo_89, codigo_91 FROM declaraciones_f29 WHERE empresa_id=1 AND periodo='2026-02'")
        f29 = cur.fetchone()
        m_f29 = find_mov("2026-03-09", "SII.CL", 81_466)
        if f29 and m_f29:
            c62, c48, c151, c89, c91 = f29
            lineas = []
            if c62: lineas.append(("1.1.06", c62, 0, "Cód 62 PPM feb 26", None))
            if c48: lineas.append(("5.2.01", c48, 0, "Cód 48 Imp 2ª cat retenido", None))
            if c151: lineas.append(("2.1.04", c151, 0, "Cód 151 Retención honorarios", None))
            if c89: lineas.append(("2.1.03", c89, 0, "Cód 89 IVA por pagar", None))
            # Diferencia → recargo
            suma = sum(l[1] for l in lineas)
            diff = c91 - suma
            if diff > 0:
                lineas.append(("5.2.12", diff, 0, "Recargo/interés F29", None))
            lineas.append(("1.1.02", 0, c91, f"Pago F29 feb 26 cód 91 ${c91:,.0f}", None))
            C("2026-03-09",
              f"Pago F29 febrero 2026 — cód 62 PPM ${c62:,.0f} + cód 48 ${c48:,.0f} = cód 91 ${c91:,.0f}",
              lineas, mov_ids=[m_f29[0]])

        # ===== LEASING — 7 cuotas (incl cuota 9 pendiente feb pagada 02/03) =====
        leasing = [
            ("2026-03-02", "46646539",  9, "10011732",  776_256, 147_489,   923_745, 211_104,  565_152),
            ("2026-03-23", "46855794", 32, "9995069",   415_516,  78_948,   494_464,  96_447,  319_069),
            ("2026-03-24", "46861928", 24, "10000582", 1_659_803, 315_362, 1_975_165, 452_004, 1_207_799),
            ("2026-03-25", "46867076",  7, "10014750", 1_295_143, 246_077, 1_541_220, 507_267,  787_876),
            ("2026-03-27", "46880236", 28, "9998305",   397_707,  75_564,   473_271, 108_168,  289_539),
            ("2026-03-27", "46880562", 36, "9992667", 1_348_669, 256_247, 1_604_916, 268_879, 1_079_790),
            ("2026-03-30", "46887133", 10, "10011732",  776_256, 147_489,   923_745, 206_801,  569_455),
        ]
        for f, folio, n, contrato, neto, iva, total, intt, cap in leasing:
            sii_d = sii_by_folio("COMPRAS", folio)
            mov = find_mov(f, "Leasing", total)
            C(f, f"Factura Banco de Chile {folio} — Cuota {n} leasing contrato {contrato}",
              [("5.2.12", intt, 0, f"Interés leasing cuota {n} contrato {contrato}", None),
               ("1.1.05", iva, 0, f"IVA CF fact {folio}", None),
               ("2.1.10", cap, 0, f"Capital leasing cuota {n} contrato {contrato}", bch),
               ("2.1.01", 0, total, f"Factura {folio} leasing", bch)],
              sii_ids=[sii_d[0]] if sii_d else [])
            if mov:
                C(f, f"Pago factura BCH {folio} — Cuota {n} leasing",
                  [("2.1.01", total, 0, f"Pago fact leasing {folio}", bch),
                   ("1.1.02", 0, total, f"Pago Cuota Leasing Cuota {n} fact {folio}", None)],
                  mov_ids=[mov[0]])

        # ===== CRÉDITOS PRIVADOS MAR (8 cuotas 20/03) =====
        creditos = [
            ("15640744-5", "Felipe Hiriart",                12, 2_504_638, 545_908, 1_958_730),
            ("16367300-2", "Pedro Lecaros Sotomayor",       12,   375_680,  81_883,   293_797),
            ("16100615-7", "Dominique Hiriart",              9, 2_282_635, 532_752, 1_749_883),
            ("76185865-3", "Inversiones Jopa Limitada",      7,   314_728,  76_641,   238_087),
            ("15595261-K", "Jose Luis Illanes Guridi",       7,   297_872,  72_536,   225_336),
            ("77714024-8", "Asesorias Ecox-7",               5,   147_646,  37_428,   110_218),
            ("77714024-8", "Asesorias Ecox-8",               4,   174_846,  10_149,   164_697),
            ("77714024-8", "Asesorias Ecox-9",               3,   122_473,  32_254,    90_219),
        ]
        for rut, nombre, cn, total_real, intt, cap in creditos:
            cp = cp_id(rut)
            short = nombre.split()[0] if not nombre.startswith("Asesorias") else "ASESOR"
            mov = None
            for m in movs:
                if str(m[1])=="2026-03-20" and short.upper() in m[2].upper() and abs((m[3] or 0)-total_real)<2:
                    cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
                    if cur.fetchone()[0]==1: continue
                    mov = m; break
            if not mov and rut == "15595261-K":
                for m in movs:
                    if str(m[1])=="2026-03-20" and "luisill" in m[2].lower() and abs((m[3] or 0)-total_real)<2:
                        cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
                        if cur.fetchone()[0]==1: continue
                        mov = m; break
            if not mov:
                print(f"WARN crédito {nombre} cuota {cn} ${total_real} no encontrado")
                continue
            # Ajustar cap = total - int para que cuadre exacto
            cap_adj = total_real - intt
            C("2026-03-20", f"Pago cuota {cn} crédito privado {nombre}",
              [("1.1.02", 0, total_real, f"TRASPASO A: {nombre} cuota {cn} crédito", None),
               ("2.1.11", cap_adj, 0, f"Capital cuota {cn} crédito {nombre}", cp),
               ("5.2.12", intt, 0, f"Interés cuota {cn} crédito {nombre}", cp)],
              mov_ids=[mov[0]])

        # ===== RESCATE/APORTE FFMM 20/03 $10M =====
        m_fmu = find_mov("2026-03-20", "Fmu", 10_000_000) or find_mov("2026-03-20", "96571220", 10_000_000)
        if m_fmu:
            C("2026-03-20", "Aporte a Fondos Mutuos Banco Chile (FMU $10.000.000)",
              [("1.1.09", 10_000_000, 0, "Aporte FFMM Banco Chile FMU", None),
               ("1.1.02", 0, 10_000_000, "TRASPASO A 96571220-8 FMU — aporte FFMM", None)],
              mov_ids=[m_fmu[0]])

        # ===== PAGOS DE FACTURAS / CIERRES =====
        # Ahern $5.719.378 (04/03) — cuota 5/5 final fact 4432
        m_ah = find_mov("2026-03-04", "CHEQUE", 5_719_378)
        if m_ah:
            C("2026-03-04", "Pago Ahern fact 4432 cuota 5 de 5 (última) — compra 2 tijeras",
              [("2.1.01", 5_719_378, 0, "Pago Ahern fact 4432 cuota 5/5 (última)", ahern),
               ("1.1.02", 0, 5_719_378, "CHEQUE COBRADO — Ahern fact 4432", None)],
              mov_ids=[m_ah[0]])

        # GAM $2.195.999 06/03 — facts 94124+94125+94126 pendientes ene
        for folio in ["94124","94125","94126"]:
            sii_d = sii_by_folio("COMPRAS", folio)
            if sii_d and (sii_d[10] in (None, 0)):
                C(sii_d[4], f"Factura {folio} GAM — subarriendo ene-26",
                  [("5.2.03", sii_d[7], 0, f"Subarriendo — fact {folio}", None),
                   ("1.1.05", sii_d[8], 0, f"IVA CF fact {folio}", None),
                   ("2.1.01", 0, sii_d[9], f"Factura {folio} GAM", gam)],
                  sii_ids=[sii_d[0]])
        m_gam = find_mov("2026-03-06", "General", 2_195_999)
        if m_gam:
            C("2026-03-06", "Pago GAM facts 94124+94125+94126 (subarriendo ene)",
              [("2.1.01", 2_195_999, 0, "Pago GAM 3 facts ene", gam),
               ("1.1.02", 0, 2_195_999, "TRASPASO A: GAM", None)],
              mov_ids=[m_gam[0]])

        # Casino 06/03 $111.117 — fact 351824 feb
        sii_c1 = sii_by_folio("COMPRAS", "351824")
        m_c1 = find_mov("2026-03-06", "Alimentacio", 111_117)
        if m_c1 and sii_c1:
            C("2026-03-06", "Pago Casino fact 351824 (feb)",
              [("2.1.01", 111_117, 0, "Pago fact 351824", casino),
               ("1.1.02", 0, 111_117, "TRASPASO A: Casino fact 351824", None)],
              mov_ids=[m_c1[0]])
        # Casino 27/03 $148.348 — fact 352195 mar
        sii_c2 = sii_by_folio("COMPRAS", "352195")
        if sii_c2:
            C("2026-03-16", "Factura 352195 Casino (alimentación)",
              [("5.2.17", sii_c2[7], 0, "Gasto alimentación fact 352195", None),
               ("1.1.05", sii_c2[8], 0, "IVA CF fact 352195", None),
               ("2.1.01", 0, sii_c2[9], "Factura 352195 Casino", casino)],
              sii_ids=[sii_c2[0]])
        m_c2 = find_mov("2026-03-27", "Alimentacio", 148_348)
        if m_c2:
            C("2026-03-27", "Pago Casino fact 352195",
              [("2.1.01", 148_348, 0, "Pago fact 352195", casino),
               ("1.1.02", 0, 148_348, "TRASPASO A: Casino", None)],
              mov_ids=[m_c2[0]])

        # Asesorías Ecox fact 210 admin 06/03 $100.000
        sii_210 = sii_by_folio("COMPRAS", "210")
        m_aec_f = find_mov("2026-03-06", "ASESOR", 100_000)
        if sii_210:
            C("2026-03-06", "Factura 210 Asesorías Ecox (administración)",
              [("5.2.11", 100_000, 0, "Asesoría administración fact 210", None),
               ("2.1.01", 0, 100_000, "Factura 210 Asesorías Ecox", aec)],
              sii_ids=[sii_210[0]])
        if m_aec_f:
            C("2026-03-06", "Pago fact 210 Asesorías Ecox",
              [("2.1.01", 100_000, 0, "Pago fact 210", aec),
               ("1.1.02", 0, 100_000, "TRASPASO A: Asesorías Ecox fact 210", None)],
              mov_ids=[m_aec_f[0]])

        # Enduro $714.000 — fact 3504 mar
        sii_3504 = sii_by_folio("COMPRAS", "3504")
        m_en = find_mov("2026-03-06", "Enduro", 714_000)
        if sii_3504:
            C("2026-03-04", "Factura 3504 Enduro Motor (arriendo taller)",
              [("5.2.03", sii_3504[7], 0, "Arriendo taller fact 3504", None),
               ("1.1.05", sii_3504[8], 0, "IVA CF fact 3504", None),
               ("2.1.01", 0, sii_3504[9], "Factura 3504 Enduro", enduro)],
              sii_ids=[sii_3504[0]])
        if m_en:
            C("2026-03-06", "Pago fact 3504 Enduro",
              [("2.1.01", 714_000, 0, "Pago fact 3504", enduro),
               ("1.1.02", 0, 714_000, "TRASPASO A: Enduro", None)],
              mov_ids=[m_en[0]])

        # Agurto $3.195 06/03 — facts 158240+159064 (ya facts cargadas en SII feb)
        sii_158240 = sii_by_folio("COMPRAS", "158240")
        sii_159064 = sii_by_folio("COMPRAS", "159064")
        for sii_d, folio in [(sii_158240, "158240"), (sii_159064, "159064")]:
            if sii_d and (sii_d[10] in (None, 0)):
                # Combustible: usar mismo patrón ene
                gasto = sii_d[7] + (sii_d[9] - sii_d[7] - sii_d[8])
                C(sii_d[4], f"Factura {folio} Comercial Agurto (combustible)",
                  [("5.2.06", gasto, 0, f"Combustible fact {folio}", None),
                   ("1.1.05", sii_d[8], 0, f"IVA CF fact {folio}", None),
                   ("2.1.01", 0, sii_d[9], f"Factura {folio} Agurto", agurto)],
                  sii_ids=[sii_d[0]])
        m_agurto = find_mov("2026-03-06", "Agurto", 3_195)
        if m_agurto:
            C("2026-03-06", "Pago Comercial Agurto facts 158240+159064 (combustible feb)",
              [("2.1.01", 3_195, 0, "Pago facts Agurto 158240+159064", agurto),
               ("1.1.02", 0, 3_195, "TRASPASO A: Agurto", None)],
              mov_ids=[m_agurto[0]])

        # Sociedad Jorklift $442.435 — fact 7348 mar (insumos)
        sii_7348 = sii_by_folio("COMPRAS", "7348")
        jork = get_or_create_cp("78028928-7", "Sociedad Jorklift SpA")
        m_jor = find_mov("2026-03-06", "Jorklift", 442_435)
        if sii_7348:
            C("2026-03-06", "Factura 7348 Sociedad Jorklift (insumos taller)",
              [("5.2.06", sii_7348[7], 0, "Insumos taller fact 7348", None),
               ("1.1.05", sii_7348[8], 0, "IVA CF fact 7348", None),
               ("2.1.01", 0, sii_7348[9], "Factura 7348 Jorklift", jork)],
              sii_ids=[sii_7348[0]])
        if m_jor:
            C("2026-03-06", "Pago fact 7348 Jorklift",
              [("2.1.01", 442_435, 0, "Pago fact 7348", jork),
               ("1.1.02", 0, 442_435, "TRASPASO A: Jorklift", None)],
              mov_ids=[m_jor[0]])

        # Ruth Salas $1.167.963 "rendicion" — rendición de gastos (no remuneración)
        m_ruth_r = find_mov("2026-03-06", "Ruth", 1_167_963)
        if m_ruth_r:
            C("2026-03-06", "Rendición gastos Ruth Salas (no remuneración)",
              [("5.2.17", 1_167_963, 0, "Rendición gastos Ruth Salas — sin desglose por fact", None),
               ("1.1.02", 0, 1_167_963, "TRASPASO A: Ruth Salas rendición", None)],
              mov_ids=[m_ruth_r[0]])

        # Mauricio Vicencio $100.000 — comentario raro "Rendicion N°12 Ruth Salas"
        m_mau_r = find_mov("2026-03-06", "Mauricio", 100_000)
        if m_mau_r:
            C("2026-03-06", "Rendición a Mauricio Vicencio (anotada como rendición Ruth)",
              [("5.2.17", 100_000, 0, "Rendición Mauricio Vicencio", None),
               ("1.1.02", 0, 100_000, "TRASPASO A: Mauricio Vicencio rendición", None)],
              mov_ids=[m_mau_r[0]])

        # Servipag TAGs (10/03, 17/03, 27/03)
        for fecha, monto in [("2026-03-10", 10_428), ("2026-03-17", 12_490), ("2026-03-27", 61_317)]:
            m = find_mov(fecha, "SERVIPAG", monto)
            if m:
                C(fecha, "TAG autopista (Servipag)",
                  [("5.2.09", monto, 0, "TAG peajes", None),
                   ("1.1.02", 0, monto, "PAGO SERVIPAG.COM TAG", None)],
                  mov_ids=[m[0]])

        # Wurth $737.788 — fact 2327814 mar
        sii_wurth = sii_by_folio("COMPRAS", "2327814")
        m_wurth = find_mov("2026-03-11", "Wurth", 737_788)
        if sii_wurth:
            C("2026-03-11", "Factura 2327814 Wurth (taller)",
              [("5.2.06", sii_wurth[7], 0, "Insumos taller fact 2327814", None),
               ("1.1.05", sii_wurth[8], 0, "IVA CF fact 2327814", None),
               ("2.1.01", 0, sii_wurth[9], "Factura 2327814 Wurth", wurth)],
              sii_ids=[sii_wurth[0]])
        if m_wurth:
            C("2026-03-11", "Pago fact 2327814 Wurth",
              [("2.1.01", 737_788, 0, "Pago fact 2327814", wurth),
               ("1.1.02", 0, 737_788, "TRASPASO A: Wurth", None)],
              mov_ids=[m_wurth[0]])

        # Nuevamerica Impresores $458.931 — fact 24128
        sii_24128 = sii_by_folio("COMPRAS", "24128")
        nuevam = None
        if sii_24128:
            nuevam = get_or_create_cp(sii_24128[5], sii_24128[6])
            C("2026-03-17", "Factura 24128 Nuevamérica Impresores (material marketing)",
              [("5.2.10", sii_24128[7], 0, "Material marketing fact 24128", None),
               ("1.1.05", sii_24128[8], 0, "IVA CF fact 24128", None),
               ("2.1.01", 0, sii_24128[9], "Factura 24128 Nuevamérica", nuevam)],
              sii_ids=[sii_24128[0]])
        m_nv = find_mov("2026-03-17", "Nuevamerica", 458_931)
        if m_nv and nuevam:
            C("2026-03-17", "Pago fact 24128 Nuevamérica Impresores",
              [("2.1.01", 458_931, 0, "Pago fact 24128", nuevam),
               ("1.1.02", 0, 458_931, "TRASPASO A: Nuevamérica", None)],
              mov_ids=[m_nv[0]])

        # Aseguradora Porvenir $302.916 19/03 — fact 975751 mar
        sii_porv = sii_by_folio("COMPRAS", "975751")
        porv = None
        if sii_porv:
            porv = get_or_create_cp(sii_porv[5], sii_porv[6])
            C("2026-03-19", "Factura 975751 Aseguradora Porvenir",
              [("5.2.08", sii_porv[7], 0, "Seguro fact 975751", None),
               ("1.1.05", sii_porv[8], 0, "IVA CF fact 975751", None),
               ("2.1.01", 0, sii_porv[9], "Factura 975751 Porvenir", porv)],
              sii_ids=[sii_porv[0]])
        m_porv = find_mov("2026-03-19", "Porveni", 302_916)
        if m_porv and porv:
            C("2026-03-19", "Pago fact 975751 Aseguradora Porvenir",
              [("2.1.01", 302_916, 0, "Pago fact 975751", porv),
               ("1.1.02", 0, 302_916, "TRASPASO A: Porvenir", None)],
              mov_ids=[m_porv[0]])

        # Navegantes $10.898 19/03 — fact 237147 mar
        sii_nav = sii_by_folio("COMPRAS", "237147")
        if sii_nav:
            C("2026-03-05", "Factura 237147 Los Navegantes",
              [("5.2.09", sii_nav[7], 0, "Transporte fact 237147", None),
               ("1.1.05", sii_nav[8], 0, "IVA CF fact 237147", None),
               ("2.1.01", 0, sii_nav[9], "Factura 237147 Los Navegantes", navegantes)],
              sii_ids=[sii_nav[0]])
        m_nav = find_mov("2026-03-19", "Navegante", 10_898)
        if m_nav:
            C("2026-03-19", "Pago Los Navegantes fact 237147",
              [("2.1.01", 10_898, 0, "Pago fact 237147", navegantes),
               ("1.1.02", 0, 10_898, "TRASPASO A: Los Navegantes", None)],
              mov_ids=[m_nav[0]])

        # MBC $521.220 27/03 — fact 5554 feb
        sii_5554 = sii_by_folio("COMPRAS", "5554")
        if sii_5554 and (sii_5554[10] in (None, 0)):
            C("2026-02-12", "Factura 5554 Comercial y Mecánica MBC",
              [("5.2.07", sii_5554[7], 0, "Mantención fact 5554", None),
               ("1.1.05", sii_5554[8], 0, "IVA CF fact 5554", None),
               ("2.1.01", 0, sii_5554[9], "Factura 5554 MBC", mbc)],
              sii_ids=[sii_5554[0]])
        m_mbc = find_mov("2026-03-27", "Mecanica", 521_220)
        if m_mbc:
            C("2026-03-27", "Pago fact 5554 MBC",
              [("2.1.01", 521_220, 0, "Pago fact 5554 MBC", mbc),
               ("1.1.02", 0, 521_220, "TRASPASO A: MBC", None)],
              mov_ids=[m_mbc[0]])

        # ===== TC MAR =====
        # 12/03 pago automático $174.208 (= saldo TC fin feb)
        m_tc = find_mov("2026-03-12", "TARJETA", 174_208)
        if m_tc:
            a_tc = C("2026-03-12", "Pago automático Tarjeta de Crédito (salda gastos TC feb)",
                [("2.1.14", 174_208, 0, "Salda saldo TC ciclo feb-26", None),
                 ("1.1.02", 0, 174_208, "PAGO AUTOMATICO TARJETA DE CREDITO", None)],
                mov_ids=[m_tc[0]])
            # espejo abono
            m_tc_esp = next((m for m in movs if str(m[1])=="2026-03-12" and "[ref" in m[2].lower() and (m[4] or 0)==174_208), None)
            if m_tc_esp:
                cur.execute("UPDATE movimientos_banco SET asiento_id=?, procesado=1 WHERE id=?", (a_tc, m_tc_esp[0]))

        # Cargos TC mar
        cargos_tc = []
        for m in movs:
            if "[REF" in m[2].upper() and m[2] != "" and (m[3] or 0) > 0:
                cargos_tc.append(m)
        for m in cargos_tc:
            d = m[2]
            monto = m[3]
            # cuenta gasto
            if "LINKEDIN" in d.upper() or "FACEBOOK" in d.upper() or "TRASPASO DEUDA" in d.upper():
                cta_g = "5.2.10"; glosa = f"Publicidad digital TC ({d[:30]})"
            elif "IMPUESTO" in d.upper() or "COMISION" in d.upper() or "INTERES" in d.upper():
                cta_g = "5.2.12"; glosa = f"Gasto bancario TC ({d[:30]})"
            elif "PORVENI" in d.upper():
                cta_g = "5.2.08"; glosa = "Seguro Porvenir TC"
            else:
                cta_g = "5.2.17"; glosa = f"Cargo TC ({d[:30]})"
            cur.execute("SELECT procesado FROM movimientos_banco WHERE id=?", (m[0],))
            if cur.fetchone()[0] == 1: continue
            C(str(m[1]), glosa,
              [(cta_g, monto, 0, glosa, None),
               ("2.1.14", 0, monto, f"Cargo TC {d}", None)],
              mov_ids=[m[0]])

        # ===== VENTAS SII MAR =====
        ventas = [d for d in sii_docs if d[1]=="VENTAS" and d[4] and d[4].startswith("2026-03")]
        agr = {}
        for d in ventas:
            agr.setdefault((d[5], d[4]), []).append(d)
        for (rut, fecha), docs in sorted(agr.items(), key=lambda x: x[0][1]):
            cp = get_or_create_cp(rut, docs[0][6] if docs[0][6] else rut, tipo="CLIENTE")
            lineas = []
            for d in docs:
                _, _, dte, fol, _, _, nom, neto, iva, total, _ = d
                if dte == "33":
                    lineas.append(("1.1.03", total, 0, f"Fact vta {fol}", cp))
                    lineas.append(("4.1.04", 0, neto, f"Servicios afectos {fol}", None))
                    lineas.append(("2.1.03", 0, iva, f"IVA DF {fol}", None))
                elif dte == "61":
                    lineas.append(("1.1.03", 0, total, f"NC {fol}", cp))
                    lineas.append(("4.1.04", neto, 0, f"NC {fol}", None))
                    lineas.append(("2.1.03", iva, 0, f"NC IVA {fol}", None))
                else:
                    lineas.append(("1.1.03", total, 0, f"Fact vta {fol} exenta", cp))
                    lineas.append(("4.1.05", 0, total, f"Servicios exentos {fol}", None))
            C(fecha, f"Ventas a {docs[0][6]} — fact(s) {'+'.join(d[3] for d in docs)}",
              lineas, sii_ids=[d[0] for d in docs])

        # ===== COBRANZAS MAR =====
        cobros = [
            ("2026-03-06", "0760988200", 5_654_796, "76098820-0", "Cobranza Bodegas SF facts 1030+1031+1032+1044"),
            ("2026-03-06", "0789241007", 9_044_411, "78924100-7", "Cobranza Inv Integrales facts 1023-1026+1034+1054-1059"),
            ("2026-03-09", "Fenix",      10_772_318, "77499240-5", "Cobranza Inv Fenix facts 1050-1053"),
            ("2026-03-12", "Fenix",       1_559_242, "77499240-5", "Cobranza Inv Fenix fact 1071"),
            ("2026-03-12", "Flex Rack",   1_658_848, "78017988-0", "Cobranza Flex Rack fact 1073"),
            ("2026-03-12", "Flex Rack",     578_628, "78017988-0", "Cobranza Flex Rack fact 1072"),
            ("2026-03-12", "Cheque",        107_100, "76786295-4", "Cobranza Agrícola El Descanso fact 1045 (cheque)"),
            ("2026-03-13", "0764496213",  2_058_107, "76449621-3", "Cobranza Constructora Lo Aguirre facts 1062+1070"),
            ("2026-03-16", "Integrales",  1_004_199, "77057527-3", "Cobranza Servicios Integrales LS fact 1040"),
            ("2026-03-16", "0816756006",    945_066, "81675600-6", "Cobranza Hites fact 1028"),
            ("2026-03-20", "0789241007",  1_704_630, "78924100-7", "Cobranza Inv Integrales fact 1068"),
            ("2026-03-20", "0760988200",  5_676_355, "76098820-0", "Cobranza Bodegas SF facts 1064-1067+1069"),
            ("2026-03-20", "0789241007",  1_955_590, "78924100-7", "Cobranza Inv Integrales fact 1074"),
            ("2026-03-26", "Exit Flow",     213_822, "77096081-9", "Cobranza Exit Flow fact 1091"),
        ]
        for fecha, ds, monto, rut, glosa in cobros:
            m = find_mov(fecha, ds, monto)
            if not m:
                print(f"WARN cobro {fecha} {ds} ${monto} no encontrado")
                continue
            cp = get_or_create_cp(rut, glosa, tipo="CLIENTE")
            C(fecha, glosa,
              [("1.1.02", monto, 0, f"{glosa} — abono banco", None),
               ("1.1.03", 0, monto, glosa, cp)],
              mov_ids=[m[0]])

        # ===== RESUMEN =====
        print(f"\nAsientos creados: {len(asientos)}")
        cur.execute("SELECT id, fecha, descripcion, cargo, abono FROM movimientos_banco "
                    "WHERE empresa_id=? AND fecha>='2026-03-01' AND fecha<'2026-04-01' AND (procesado IS NULL OR procesado=0)",
                    (EMPRESA_ID,))
        np = cur.fetchall()
        print(f"Movs no procesados: {len(np)}")
        for r in np: print(f"  mov#{r[0]} {r[1]} ${(r[3] or 0):>10,} ${(r[4] or 0):>10,} {r[2][:50]}")

        if dry_run:
            print("[DRY-RUN] No commit.")
        else:
            conn.commit()
            print("Commit OK.")


if __name__ == "__main__":
    import sys
    main(dry_run=("--dry-run" in sys.argv))
