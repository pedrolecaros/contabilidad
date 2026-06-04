"""
Procesa enero 2026 de Parque Sur SpA (empresa_id=1).

Confirmación de empresa explícita al inicio. Asientos en estado BORRADOR.
Cada mov bancario queda linkeado a su asiento. Conciliaciones tipo SII o MANUAL.

Patrones aplicados:
- Cuotas leasing: dos asientos (reconocer factura, pagar). Capital baja 2.1.10.
- Cuotas créditos privados: asiento simple capital+interés. Split proporcional
  para absorber diferencias UF (especialmente Asesorías-9).
- Pagos apertura (cierran saldos al 31-12-25): asiento simple banco/cta apertura.
- Facturas SII enero: match folio↔mov con asiento compra/honorario + asiento pago.
- Cobranzas: asientos ventas + cobranzas matchadas con facturas vta.
- Compra maquinaria Ahern (Fact 4432 cuota 4/5): cancela 2.1.01 Ahern apertura.
- TC: patrón Chilcos (pago auto vs gastos individuales con [ref]).
"""

import sqlite3
from contextlib import closing
from datetime import date

DB = "contabilidad.db"
EMPRESA_ID = 1

# === Catálogo de operaciones por mov_id (id en movimientos_banco) ===
# Generado tras inspeccionar la cartola + SII. Cada entrada describe qué asiento crear.

def main(dry_run=False):
    with closing(sqlite3.connect(DB)) as conn:
        cur = conn.cursor()

        # CONFIRMACIÓN EMPRESA
        cur.execute("SELECT razon_social, rut FROM empresas WHERE id=?", (EMPRESA_ID,))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"empresa_id={EMPRESA_ID} no existe")
        print(f"=== Trabajando sobre empresa_id={EMPRESA_ID}: {row[0]} ({row[1]}) ===")

        # Verificar apertura existe y está confirmada
        cur.execute(
            "SELECT id, estado FROM asientos WHERE empresa_id=? AND fecha='2026-01-01' "
            "AND descripcion LIKE 'Asiento de Apertura%'", (EMPRESA_ID,))
        ap = cur.fetchone()
        if not ap:
            raise SystemExit("No hay apertura 2026 para Parque Sur")
        print(f"Apertura: asiento_id={ap[0]} estado={ap[1]}")

        # === Carga maps de cuentas, contrapartes, movs, sii docs ===
        cur.execute("SELECT codigo, id FROM cuentas WHERE empresa_id=?", (EMPRESA_ID,))
        cta = dict(cur.fetchall())

        cur.execute("SELECT rut, id, razon_social FROM contrapartes WHERE empresa_id=?", (EMPRESA_ID,))
        cps = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        cur.execute(
            "SELECT id, fecha, descripcion, cargo, abono FROM movimientos_banco "
            "WHERE empresa_id=? AND fecha>='2026-01-01' AND fecha<'2026-02-01' "
            "ORDER BY fecha, id", (EMPRESA_ID,))
        movs = list(cur.fetchall())
        print(f"Movs banco enero: {len(movs)}")

        cur.execute(
            "SELECT id, tipo_libro, tipo_dte, folio, fecha, rut_contraparte, monto_neto, iva, total "
            "FROM documentos_sii WHERE empresa_id=? AND fecha>='2025-12-29' AND fecha<'2026-02-01'",
            (EMPRESA_ID,))
        sii_docs = list(cur.fetchall())
        sii_by_folio = {(r[1], r[3]): r for r in sii_docs}

        # === Helpers ===
        def cp_id(rut):
            if rut not in cps:
                raise SystemExit(f"Contraparte RUT {rut} no existe en BD")
            return cps[rut][0]

        def get_or_create_cp(rut, nombre, tipo="PROVEEDOR"):
            if rut in cps:
                return cps[rut][0]
            cur.execute(
                "INSERT INTO contrapartes (empresa_id, rut, razon_social, tipo, activo) VALUES (?,?,?,?,1)",
                (EMPRESA_ID, rut, nombre, tipo))
            new_id = cur.lastrowid
            cps[rut] = (new_id, nombre)
            return new_id

        asientos_creados = []  # (id, fecha, descripcion, lineas, total, mov_ids, sii_ids)

        # Próximo numero de asiento para esta empresa
        cur.execute("SELECT COALESCE(MAX(numero), 0) FROM asientos WHERE empresa_id=?", (EMPRESA_ID,))
        next_num = [cur.fetchone()[0]]

        def crear_asiento(fecha, descripcion, lineas, mov_ids=(), sii_ids=()):
            # lineas: [(cuenta_codigo, debe, haber, descripcion, cp_id_or_none)]
            td = sum(l[1] for l in lineas)
            th = sum(l[2] for l in lineas)
            if abs(td - th) > 1:
                raise SystemExit(
                    f"DESCUADRE en asiento '{descripcion}': D={td:,} H={th:,} diff={td-th:,}")
            next_num[0] += 1
            cur.execute(
                "INSERT INTO asientos (empresa_id, fecha, numero, descripcion, estado, origen) "
                "VALUES (?,?,?,?,?,?)",
                (EMPRESA_ID, fecha, next_num[0], descripcion, "BORRADOR",
                 "BANCO" if mov_ids and not sii_ids else ("SII" if sii_ids else "MANUAL")))
            aid = cur.lastrowid
            for orden, (cod, d, h, ldesc, lcp) in enumerate(lineas, start=1):
                if cod not in cta:
                    raise SystemExit(f"Cta {cod} no existe")
                cur.execute(
                    "INSERT INTO lineas_asiento (asiento_id, cuenta_id, debe, haber, descripcion, orden, contraparte_id) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (aid, cta[cod], d, h, ldesc, orden, lcp))
            for mid in mov_ids:
                cur.execute("UPDATE movimientos_banco SET asiento_id=?, procesado=1 WHERE id=?", (aid, mid))
            for sid in sii_ids:
                cur.execute("UPDATE documentos_sii SET asiento_id=?, procesado=1 WHERE id=?", (aid, sid))
            asientos_creados.append((aid, fecha, descripcion, td))
            return aid

        # === Indexar movs por fecha+descripción para acceso rápido ===
        # Vamos a buscar por descripción dentro de procesamiento manual
        def find_mov(fecha, desc_substr, monto=None):
            for m in movs:
                if str(m[1]).startswith(fecha) and desc_substr.upper() in m[2].upper():
                    if monto is None or abs((m[3] or 0) - monto) < 1 or abs((m[4] or 0) - monto) < 1:
                        return m
            return None

        # ===== 1. SALDA APERTURA REMUNERACIONES (02/01) =====
        m_ruth = find_mov("2026-01-02", "Ruth Noemi", 1_836_540)
        m_mauricio = find_mov("2026-01-02", "Mauricio Vicencio", 1_200_575)
        crear_asiento(
            "2026-01-02", "Pago remuneraciones diciembre 2025 (Ruth Salas + Mauricio Vicencio)",
            [
                ("1.1.02", 0, 1_836_540, "TRASPASO A: Ruth Noemi Salas Anabalon — sueldo dic 25", None),
                ("2.1.05", 1_836_540, 0, "Salda remuneración Ruth Salas dic 25 (apertura)", None),
                ("1.1.02", 0, 1_200_575, "TRASPASO A: Mauricio Vicencio — sueldo dic 25", None),
                ("2.1.05", 1_200_575, 0, "Salda remuneración Mauricio Vicencio dic 25 (apertura)", None),
            ],
            mov_ids=[m_ruth[0], m_mauricio[0]])

        # ===== 2. PAGO COTIZACIONES (06/01) =====
        m_cot = find_mov("2026-01-06", "Instituciones Previsionales", 897_249)
        diff_cot = 897_249 - 853_720
        crear_asiento(
            "2026-01-06", "Pago cotizaciones previsionales dic 2025 (Previred)",
            [
                ("1.1.02", 0, 897_249, "Pago Instituciones Previsionales (Previred dic 25)", None),
                ("2.1.06", 853_720, 0, "Salda cotizaciones dic 25 (apertura)", None),
                ("5.2.12", diff_cot, 0, f"Diferencia/recargo Previred ${diff_cot:,}", None),
            ],
            mov_ids=[m_cot[0]])

        # ===== 3. PAGO F29 DIC 25 (12/01) =====
        m_f29 = find_mov("2026-01-12", "SII.CL", 85_632)
        crear_asiento(
            "2026-01-12", "Pago F29 diciembre 2025 (SII)",
            [
                ("1.1.02", 0, 85_632, "Pago en SII.cl — F29 dic 25", None),
                ("2.1.07", 85_632, 0, "Salda F29 dic 25 (apertura)", None),
            ],
            mov_ids=[m_f29[0]])

        # ===== 4. LEASING — 6 cuotas con factura SII Banco de Chile =====
        bch_rut = "97004000-5"
        bch = cp_id(bch_rut)
        # Valores de facturas SII Banco Chile (autoritativos). Capital = neto - interes (Excel)
        leasing_cuotas = [
            # (fecha_pago, folio_fact, num_cuota, contrato, neto, iva, total, interes, capital)
            ("2026-01-22", "46141006",  30, "9995069",   415_516,  78_948,   494_464,   101_488,   314_028),
            ("2026-01-26", "46266458",  22, "10000582", 1_659_803, 315_362, 1_975_165,  485_908, 1_173_895),
            ("2026-01-26", "46266459",   5, "10014750", 1_295_143, 246_077, 1_541_220,  524_262,   770_881),
            ("2026-01-27", "46273871",  26, "9998305",   397_707,  75_564,   473_271,   111_195,   286_512),
            ("2026-01-28", "46279219",  34, "9992667", 1_349_308, 256_368, 1_605_676,  287_826, 1_061_482),
            ("2026-01-30", "46295108",   8, "10011732",  776_256, 147_489,   923_745,   230_503,   545_753),
        ]
        # Ajustar para que iva neto+iva = total
        for f, folio, n, contrato, neto, iva, total, intt, cap in leasing_cuotas:
            assert neto + iva == total or abs(neto+iva-total) <= 1, f"Leasing {folio} iva no cuadra: {neto+iva} vs {total}"
            assert intt + cap == neto, f"Leasing {folio} int+cap != neto"
            sii_id = None
            for d in sii_docs:
                if d[1] == "COMPRAS" and d[3] == folio:
                    sii_id = d[0]
                    break
            mov = find_mov(f, "Leasing", total)
            if not mov:
                # buscar por folio
                mov = find_mov(f, f"Cuota {n}", total)
            if not mov:
                print(f"WARN: no encuentro mov leasing fact {folio}")
                continue
            # Asiento 1: Reconocer factura
            a1 = crear_asiento(
                f, f"Factura Banco de Chile {folio} — Cuota {n} leasing contrato {contrato}",
                [
                    ("5.2.12", intt, 0, f"Interés leasing cuota {n} contrato {contrato}", None),
                    ("1.1.05", iva, 0, f"IVA CF factura leasing {folio}", None),
                    ("2.1.10", cap, 0, f"Capital leasing cuota {n} contrato {contrato}", bch),
                    ("2.1.01", 0, total, f"Factura {folio} leasing Banco de Chile", bch),
                ],
                sii_ids=[sii_id] if sii_id else [])
            # Asiento 2: Pago
            crear_asiento(
                f, f"Pago factura Banco de Chile {folio} — Cuota {n} leasing",
                [
                    ("2.1.01", total, 0, f"Pago factura leasing {folio}", bch),
                    ("1.1.02", 0, total, f"Pago Cuota Leasing: Cuota {n} de 49 fact {folio}", None),
                ],
                mov_ids=[mov[0]])

        # ===== 5. CRÉDITOS PRIVADOS (9 cuotas día 20) =====
        # Datos del Excel "Tabla de desarrollo creditos privados.xlsx"
        # (rut, nombre, cuota_n, total_calc_clp, interes_clp, capital_clp, total_real_banco)
        creditos = [
            ("15640744-5", "Felipe Andres Hiriart Blome",  10, 2_497_884,   570_224, 1_927_660, 2_497_884),  # FHB
            ("16367300-2", "Pedro Lecaros Sotomayor",      10,   374_667,    85_530,   289_137,   374_667),  # PLS (2)
            ("16100615-7", "Dominique Hiriart",             7, 2_276_480,   554_354, 1_722_126, 2_276_480),  # DHB
            ("76185865-3", "Inversiones Jopa Limitada",     5,   313_879,    79_568,   234_311,   313_879),  # JOPA-4
            ("15595261-K", "Jose Luis Illanes Guridi",      5,   297_069,    75_307,   221_762,   297_069),  # JLI
            ("76185865-3", "Inversiones Jopa Limitada",     4, 2_668_931,    35_233, 2_633_698, 2_668_931),  # JOPA-6
            ("77714024-8", "Asesorias Ecox Limitada",       3,   147_248,    38_778,   108_470,   147_248),  # Asesorias-7
            ("77714024-8", "Asesorias Ecox Limitada",       2,   174_374,    12_290,   162_084,   174_375),  # Asesorias-8 (174.375 banco)
            ("77714024-8", "Asesorias Ecox Limitada",       1,   122_143,    33_355,    88_788,   115_473),  # Asesorias-9 dif UF
        ]
        # Mov banco 20/01 puede tener varios; cada cuota su asiento
        for rut, nombre, cn, total_calc, intt, cap, total_real in creditos:
            # split proporcional si total_real != total_calc
            ratio = total_real / total_calc if total_calc else 1
            intt_r = round(intt * ratio)
            cap_r = total_real - intt_r
            # encontrar mov: filtrar por monto exacto
            mov = None
            primer_nombre = nombre.split()[0]
            for m in movs:
                if str(m[1]).startswith("2026-01-20") and primer_nombre.upper() in m[2].upper() and abs((m[3] or 0)-total_real) < 1:
                    if m[0] in [mid for (_, _, _, _, _, mids, _) in []]:
                        continue
                    # check no procesado
                    mov = m
                    break
            if not mov:
                # caso especial Jose Luisillanes
                if rut == "15595261-K":
                    for m in movs:
                        if str(m[1]).startswith("2026-01-20") and "luisill" in m[2].lower() and abs((m[3] or 0)-total_real) < 1:
                            mov = m; break
                if not mov:
                    print(f"WARN: no encuentro mov crédito {nombre} cuota {cn} ${total_real}")
                    continue
            cp = cp_id(rut)
            crear_asiento(
                "2026-01-20",
                f"Pago cuota {cn} crédito privado {nombre}",
                [
                    ("1.1.02", 0, total_real, f"TRASPASO A: {nombre} — cuota {cn} crédito privado", None),
                    ("2.1.11", cap_r, 0, f"Capital cuota {cn} crédito {nombre}", cp),
                    ("5.2.12", intt_r, 0, f"Interés cuota {cn} crédito {nombre}", cp),
                ],
                mov_ids=[mov[0]])

        # ===== 6. AHERN — pago cuota maquinaria (26/01) =====
        m_ahern = find_mov("2026-01-26", "CHEQUE", 5_719_378)
        ahern = cp_id("76524465-K")
        crear_asiento(
            "2026-01-26", "Pago Ahern fact 4432 cuota 4 de 5 — compra 2 tijeras",
            [
                ("2.1.01", 5_719_378, 0, "Pago Ahern fact 4432 cuota 4/5 (saldo apertura)", ahern),
                ("1.1.02", 0, 5_719_378, "CHEQUE COBRADO POR OTRO BANCO — Ahern fact 4432 cuota 4/5", None),
            ],
            mov_ids=[m_ahern[0]])

        # ===== 7. AHERN — pago Fact 4484 4485 Repuestos (09/01) =====
        # apertura tenía 4484 $90.123 + 4485 $252.681 = $342.804
        m_ah2 = find_mov("2026-01-09", "Ahern", 342_804)
        crear_asiento(
            "2026-01-09", "Pago Ahern facts 4484 4485 (saldo apertura repuestos)",
            [
                ("2.1.01", 342_804, 0, "Pago Ahern fact 4484+4485 repuestos (apertura)", ahern),
                ("1.1.02", 0, 342_804, "TRASPASO A: Ahern Chile Spa — fact 4484 4485", None),
            ],
            mov_ids=[m_ah2[0]])

        # ===== 8. CIERRES APERTURA PROVEEDORES (pagos exactos) =====
        cierres_apertura = [
            # (fecha, descripcion_buscar, monto, rut, nombre_cp, glosa)
            ("2026-01-02", "Comercial Agurto",      3_855,   "77615904-2", "Comercial Agurto SpA",       "Pago Agurto fact 147424 Adblue (apertura)"),
            ("2026-01-09", "Alimentacio",          86_909,  "78793360-2", "Sociedad de Alimentacion Casino EXP", "Pago Casino fact 348572+348573 (apertura)"),
            ("2026-01-09", "Skc Red",               129_867, "76686138-5", "SKC Red SpA",                "Pago SKC fact 110446 mantención grúa Temuco (apertura)"),
            ("2026-01-09", "Comercial Agurto",      54_698,  "77615904-2", "Comercial Agurto SpA",       "Pago Agurto fact 149900 combustible (apertura)"),
            ("2026-01-09", "Comercial Agurto",      53_183,  "77615904-2", "Comercial Agurto SpA",       "Pago Agurto facts 150482+152177 combustible (apertura)"),
            ("2026-01-16", "Horizon",               1_533_354,"76217392-1","Horizon High Reach Chile SpA","Pago Horizon facts 24660+24661+24662 sub-arriendos (apertura)"),
            ("2026-01-16", "General Alquiler",      4_145_367,"76106956-K","General Alquiler de Maquinarias Chile","Pago GAM facts 93128-31 + 93276 subarriendo (apertura)"),
        ]
        for fecha, ds, monto, rut, nom, glosa in cierres_apertura:
            m = find_mov(fecha, ds, monto)
            if not m:
                print(f"WARN: cierre apertura no encontrado {fecha} {ds} ${monto}")
                continue
            cp = cp_id(rut)
            crear_asiento(
                fecha, glosa,
                [
                    ("2.1.01", monto, 0, glosa, cp),
                    ("1.1.02", 0, monto, f"TRASPASO A: {nom}", None),
                ],
                mov_ids=[m[0]])

        # ===== 9. PAGOS CON FACTURA SII ENERO (compra + pago) =====
        # (fecha, mov_substr, monto, folio_fact_sii, nota)
        # Casos con factura SII enero ya emitida y pagada el mismo mes
        pagos_sii_enero = [
            ("2026-01-02", "Enduro Motor",          595_000,  "3436",     "Enduro Motor / Hernan Alvayay arriendo taller"),
            ("2026-01-05", "Asesorias Ecox",        100_000,  "197",      "Asesorías Ecox fact 197 administración (exenta)"),
            ("2026-01-09", "Luis Letelier",         71_400,   "6391",     "Luis Letelier fact 6391 maestranza reparación grúa"),
            ("2026-01-21", "Navegantes",            10_879,   "233060",   "Los Navegantes fact 233060"),
        ]
        for fecha, ds, monto, folio, nota in pagos_sii_enero:
            sii = None
            for d in sii_docs:
                if d[1] == "COMPRAS" and d[3] == folio and abs(d[8]-monto) < 2:
                    sii = d; break
            if not sii:
                print(f"WARN: SII fact {folio} no encontrada para {nota}")
                continue
            mov = find_mov(fecha, ds, monto)
            if not mov:
                print(f"WARN: mov {fecha} ${monto} no encontrado para {nota}")
                continue
            _, _, dte, _, fec_doc, rut_cp, neto, iva, total = sii
            cp = get_or_create_cp(rut_cp, sii[1] or nota)
            # Asiento factura (mismo dia que el pago)
            cuenta_gasto = {
                "3436": "5.2.03",       # arriendo
                "197": "5.2.11",        # asesoría
                "6391": "5.2.07",       # mantención
                "233060": "5.2.09",     # transporte
            }.get(folio, "5.2.17")
            if dte == "34":  # exenta
                lineas_fact = [
                    (cuenta_gasto, total, 0, f"{nota} — fact {folio} exenta", None),
                    ("2.1.01", 0, total, f"Factura {folio} {sii[5]}", cp),
                ]
            else:
                lineas_fact = [
                    (cuenta_gasto, neto, 0, f"{nota} — fact {folio}", None),
                    ("1.1.05", iva, 0, f"IVA CF fact {folio}", None),
                    ("2.1.01", 0, total, f"Factura {folio} {sii[5]}", cp),
                ]
            crear_asiento(fecha, f"Factura {folio} — {nota}", lineas_fact, sii_ids=[sii[0]])
            crear_asiento(
                fecha, f"Pago factura {folio} — {nota}",
                [
                    ("2.1.01", total, 0, f"Pago fact {folio}", cp),
                    ("1.1.02", 0, total, f"TRASPASO/Pago a {sii[5]} fact {folio}", None),
                ],
                mov_ids=[mov[0]])

        # ===== 10. CONSTRUCCIONES JOTA EFE — pago parcial fact 386+387 (05/01) =====
        # SII: fact 386 $2.597.671 + fact 387 $726.490 = $3.324.161. Pago $2.025.325. Pago parcial.
        m_jota = find_mov("2026-01-05", "Construcciones Jota Efe", 2_025_325)
        jota_rut = "77109638-7"
        jota = get_or_create_cp(jota_rut, "Construcciones Jota Efe SpA")
        sii_386 = next(d for d in sii_docs if d[1]=="COMPRAS" and d[3]=="386")
        sii_387 = next(d for d in sii_docs if d[1]=="COMPRAS" and d[3]=="387")
        # Reconocer ambas facturas
        crear_asiento("2026-01-04",
            "Facturas 386 y 387 Construcciones Jota Efe — remodelación vestidor mecánico",
            [
                ("5.2.07", sii_386[6], 0, "Remodelación vestidor — fact 386", None),
                ("1.1.05", sii_386[7], 0, "IVA CF fact 386", None),
                ("2.1.01", 0, sii_386[8], "Factura 386 Construcciones Jota Efe", jota),
                ("5.2.07", sii_387[6], 0, "Remodelación vestidor — fact 387", None),
                ("1.1.05", sii_387[7], 0, "IVA CF fact 387", None),
                ("2.1.01", 0, sii_387[8], "Factura 387 Construcciones Jota Efe", jota),
            ],
            sii_ids=[sii_386[0], sii_387[0]])
        # Pago parcial 05/01 $2.025.325 (queda saldo $1.298.836 pendiente)
        crear_asiento("2026-01-05",
            "Pago parcial facturas 386 y 387 Construcciones Jota Efe ($1.298.836 queda pendiente)",
            [
                ("2.1.01", 2_025_325, 0, "Pago parcial facts 386+387 Jota Efe", jota),
                ("1.1.02", 0, 2_025_325, "TRASPASO A: Construcciones Jota Efe Spa", None),
            ],
            mov_ids=[m_jota[0]])

        # ===== 11. SERVICIOS DIGITALES E IMPRESIÓN — 2 pagos de fact 2174 =====
        # SII: fact 2174 $119.000 total
        sdig_rut = "76839579-9"
        sdig = get_or_create_cp(sdig_rut, "Servicios Digitales e Impresión")
        sii_2174 = next(d for d in sii_docs if d[1]=="COMPRAS" and d[3]=="2174")
        crear_asiento("2026-01-12",
            "Factura 2174 Servicios Digitales — impresión de logos",
            [
                ("5.2.10", sii_2174[6], 0, "Publicidad/impresión logos — fact 2174", None),
                ("1.1.05", sii_2174[7], 0, "IVA CF fact 2174", None),
                ("2.1.01", 0, sii_2174[8], "Factura 2174 Servicios Digitales", sdig),
            ],
            sii_ids=[sii_2174[0]])
        for fecha, monto in [("2026-01-06", 59_500), ("2026-01-13", 59_500)]:
            m = find_mov(fecha, "Servicios Digitales", monto)
            crear_asiento(fecha,
                f"Pago parcial fact 2174 Servicios Digitales ({fecha[8:10]}/01)",
                [
                    ("2.1.01", monto, 0, "Pago parcial fact 2174", sdig),
                    ("1.1.02", 0, monto, "TRASPASO A: Servicios Digitales e Impresión", None),
                ],
                mov_ids=[m[0]] if m else [])

        # ===== 12. PAGOS DE CLIENTES — facturas APERTURA (cobranzas cierran 1.1.03) =====
        cobros_apertura = [
            # (fecha_mov, mov_substr, monto, rut_cliente, nombre, nota)
            ("2026-01-02", "0789241007", 2_867_350, "78924100-7", "Inversiones Integrales Ltda",       "Cobranza fact vta 984 Inv Integrales (apertura)"),
            ("2026-01-09", "0760988200", 849_166,   "76098820-0", "Bodegas San Francisco Limitada",    "Cobranza fact vta 964 Bodegas SF (apertura)"),
            ("2026-01-09", "Fenix",      9_928_014, "77499240-5", "Inversiones Fenix Servicios Limitada","Cobranza facts vta 985-988 Fenix (apertura)"),
            ("2026-01-14", "0868814004", 2_170_091, "86881400-4", "Envases CMF Sociedad Anonima",      "Cobranza fact vta 958 Envases CMF (apertura parcial)"),
            ("2026-01-15", "0816756006", 943_518,   "81675600-6", "Hites S.A.",                        "Cobranza fact vta 976 Hites (apertura)"),
            ("2026-01-16", "Servicios Integrales", 402_056, "77057527-3", "Servicios Integrales LS SpA", "Cobranza fact vta 979 SI LS (apertura)"),
            ("2026-01-27", "0868814004", 3_259_754, "86881400-4", "Envases CMF Sociedad Anonima",      "Cobranza facts vta 989+990 Envases CMF (apertura resto)"),
        ]
        for fecha, ds, monto, rut, nom, glosa in cobros_apertura:
            m = find_mov(fecha, ds, monto)
            if not m:
                print(f"WARN: cobro apertura no encontrado {fecha} {ds} ${monto}")
                continue
            cp = cp_id(rut)
            crear_asiento(fecha, glosa,
                [
                    ("1.1.02", monto, 0, f"{glosa} — abono banco", None),
                    ("1.1.03", 0, monto, f"Salda cuenta cliente {nom} (apertura)", cp),
                ],
                mov_ids=[m[0]])

        # ===== 13. VENTAS SII ENERO + COBRANZAS =====
        # (fecha_cobro, mov_substr, monto, lista de folios vta a matchear)
        # Estructura: para cada cobro, registrar las facts vta (asiento ingreso) si no están, y luego el cobro
        # Para simplificar: asumimos las facts ya emitidas; armamos por cobro

        def asiento_venta(folios_lista, fecha_emision, glosa_extra=""):
            """Crea asiento(s) de venta para una lista de folios SII ventas que pertenecen al mismo cliente"""
            # Agrupar por cliente
            by_cli = {}
            for fol in folios_lista:
                doc = next((d for d in sii_docs if d[1]=="VENTAS" and d[3]==fol), None)
                if not doc: continue
                by_cli.setdefault(doc[5], []).append(doc)
            for rut, docs in by_cli.items():
                cp = get_or_create_cp(rut, docs[0][5] if len(docs[0])>5 else rut, tipo="CLIENTE")
                lineas = []
                tot = 0
                for d in docs:
                    _, _, dte, fol, fec, _, neto, iva, total = d
                    if dte == "33":
                        lineas.append(("1.1.03", total, 0, f"Fact vta {fol} {fec}", cp))
                        lineas.append(("4.1.04", 0, neto, f"Servicios afectos fact {fol}", None))
                        lineas.append(("2.1.03", 0, iva, f"IVA DF fact {fol}", None))
                    else:  # 34 exenta
                        lineas.append(("1.1.03", total, 0, f"Fact vta {fol} exenta", cp))
                        lineas.append(("4.1.05", 0, total, f"Servicios exentos fact {fol}", None))
                    tot += total
                fecha_real = max(d[4] for d in docs)
                # Marcar SII como procesados
                sii_ids_list = [d[0] for d in docs]
                crear_asiento(fecha_real,
                    f"Ventas a {docs[0][5] if len(docs[0])>5 else rut} — facts {'+'.join(d[3] for d in docs)} {glosa_extra}",
                    lineas, sii_ids=sii_ids_list)

        # Ventas SII a procesar (todas) - asiento por venta o por bloque mismo cliente mismo día
        # Para evitar redundancia, agrupamos por (cliente, fecha)
        ventas_agrupadas = {}
        for d in sii_docs:
            if d[1] != "VENTAS": continue
            key = (d[5], d[4])  # rut, fecha
            ventas_agrupadas.setdefault(key, []).append(d)
        for (rut, fecha), docs in sorted(ventas_agrupadas.items(), key=lambda x: x[0][1]):
            cp = get_or_create_cp(rut, docs[0][5] if len(docs[0])>5 else rut, tipo="CLIENTE")
            lineas = []
            for d in docs:
                _, _, dte, fol, fec, _, neto, iva, total = d
                if dte == "33":
                    lineas.append(("1.1.03", total, 0, f"Fact vta {fol}", cp))
                    lineas.append(("4.1.04", 0, neto, f"Servicios afectos fact {fol}", None))
                    lineas.append(("2.1.03", 0, iva, f"IVA DF fact {fol}", None))
                else:
                    lineas.append(("1.1.03", total, 0, f"Fact vta {fol} exenta", cp))
                    lineas.append(("4.1.05", 0, total, f"Servicios exentos fact {fol}", None))
            sii_ids_list = [d[0] for d in docs]
            crear_asiento(fecha,
                f"Ventas a {docs[0][5]} — fact(s) {'+'.join(d[3] for d in docs)}",
                lineas, sii_ids=sii_ids_list)

        # Cobranzas ventas enero (no apertura)
        cobros_ventas_ene = [
            ("2026-01-16", "0789241007", 6_011_717, "78924100-7", "Inversiones Integrales Ltda", "Cobranza facts vta 1002-1008"),
            ("2026-01-16", "0760988200", 1_962_280, "76098820-0", "Bodegas San Francisco Limitada", "Cobranza facts vta 995-997+999"),
            ("2026-01-16", "0764496213", 2_340_174, "76449621-3", "Constructora Lo Aguirre SpA", "Cobranza fact 993"),
            ("2026-01-23", "0760988200", 681_502,   "76098820-0", "Bodegas San Francisco Limitada", "Cobranza fact vta 1000"),
            ("2026-01-27", "Fenix",      1_452_246, "77499240-5", "Inversiones Fenix Servicios Limitada","Cobranza fact vta 992"),
        ]
        for fecha, ds, monto, rut, nom, glosa in cobros_ventas_ene:
            m = find_mov(fecha, ds, monto)
            if not m:
                print(f"WARN: cobro venta enero no encontrado {fecha} {ds} ${monto}")
                continue
            cp = get_or_create_cp(rut, nom, tipo="CLIENTE")
            crear_asiento(fecha, glosa,
                [
                    ("1.1.02", monto, 0, f"{glosa} — abono banco", None),
                    ("1.1.03", 0, monto, f"Cobranza {nom}", cp),
                ],
                mov_ids=[m[0]])

        # ===== 14. DEVOLUCIÓN MONTAJES (02/01) =====
        # apertura tenía Montajes con saldo H $189.068 (anticipo cliente); ahora se devuelve
        m_dev = find_mov("2026-01-02", "Montajes", 189_068)
        montajes = cp_id("96990610-4")
        crear_asiento("2026-01-02",
            "Devolución a cliente Montajes por fact vta 991 (cancela anticipo apertura)",
            [
                ("1.1.03", 189_068, 0, "Cancela saldo acreedor apertura Montajes (anticipo cliente)", montajes),
                ("1.1.02", 0, 189_068, "TRASPASO A: Montajes y Servicios Industriales — devolución", None),
            ],
            mov_ids=[m_dev[0]])

        # ===== 15. TARJETA DE CRÉDITO (patrón Chilcos) =====
        # 14/01 pago automático
        m_tc_pago = find_mov("2026-01-14", "PAGO AUTOMATICO TARJETA", 389_218)
        crear_asiento("2026-01-14",
            "Pago automático Tarjeta de Crédito (salda ciclo dic 25)",
            [
                ("2.1.14", 389_218, 0, "Salda saldo TC ciclo dic 25", None),
                ("1.1.02", 0, 389_218, "PAGO AUTOMATICO TARJETA DE CREDITO", None),
            ],
            mov_ids=[m_tc_pago[0]])
        # 14/01 espejo abono TC (no asiento, solo marcar mov procesado linkeando al mismo asiento)
        m_tc_espejo = next((m for m in movs if str(m[1])=="2026-01-14" and "[ref 140100000000]" in m[2] and (m[4] or 0)==389_218), None)
        if m_tc_espejo:
            cur.execute("UPDATE movimientos_banco SET asiento_id=?, procesado=1 WHERE id=?",
                        (asientos_creados[-1][0], m_tc_espejo[0]))

        # Cargos TC individuales
        cargos_tc = [
            ("2026-01-14", "EL CUMBION",                 97_900,  "5.2.17", "Gasto alimentación TC"),
            ("2026-01-14", "TRASPASO DEUDA INTERNACIONAL",61_354, "5.2.10", "Publicidad digital internacional TC"),
            ("2026-01-21", "PORVENI",                    159_212, "5.2.08", "Seguro Aseguradora Porvenir TC"),
            ("2026-01-28", "IMPUESTO DECRETO LEY 3475",  40,      "5.2.12", "Impuesto DL 3475 TC"),
            ("2026-01-28", "COMISION MENSUAL",           1_589,   "5.2.12", "Comisión mensual mantención TC"),
            ("2026-01-28", "INTERESES ROTATIVOS",        1_417,   "5.2.12", "Intereses rotativos TC"),
        ]
        for fecha, ds, monto, cta_g, glosa in cargos_tc:
            mov = None
            for m in movs:
                if str(m[1]).startswith(fecha) and ds.upper() in m[2].upper() and abs((m[3] or 0)-monto) < 1:
                    mov = m; break
            if not mov:
                print(f"WARN: TC mov no encontrado {fecha} {ds} ${monto}")
                continue
            # Si hay fact SII vinculada (Porvenir 951441)
            sii_id = None
            if ds == "PORVENI":
                sii_d = next((d for d in sii_docs if d[1]=="COMPRAS" and d[3]=="951441"), None)
                if sii_d:
                    sii_id = sii_d[0]
                    porv = get_or_create_cp(sii_d[5], "Aseguradora Porvenir S.A.")
                    crear_asiento(fecha,
                        "Factura 951441 Aseguradora Porvenir (cargo TC)",
                        [
                            (cta_g, sii_d[6], 0, glosa, None),
                            ("1.1.05", sii_d[7], 0, "IVA CF fact 951441", None),
                            ("2.1.14", 0, sii_d[8], "Cargo TC — fact 951441 Porvenir", None),
                        ],
                        mov_ids=[mov[0]], sii_ids=[sii_id])
                    continue
            # cargos TC sin fact SII (gastos directos)
            crear_asiento(fecha,
                glosa,
                [
                    (cta_g, monto, 0, f"{glosa}", None),
                    ("2.1.14", 0, monto, f"Cargo TC {ds}", None),
                ],
                mov_ids=[mov[0]])

        # ===== 16. TAG / Servipag (06/01 $7.770 y 22/01 $30.571) — gastos transporte =====
        for fecha, monto in [("2026-01-06", 7_770), ("2026-01-22", 30_571)]:
            m = find_mov(fecha, "SERVIPAG", monto)
            if not m: continue
            crear_asiento(fecha,
                "TAG autopista (Servipag)",
                [
                    ("5.2.09", monto, 0, "TAG peajes autopista", None),
                    ("1.1.02", 0, monto, "PAGO EN SERVIPAG.COM* TAG autopista", None),
                ],
                mov_ids=[m[0]])

        # ===== 17. CASINO ENERO — facts 349883 + 350516 (pago parcial 28/01) =====
        casino_rut = "78793360-2"
        casino = cp_id(casino_rut)
        sii_349883 = next(d for d in sii_docs if d[1]=="COMPRAS" and d[3]=="349883")
        sii_350516 = next(d for d in sii_docs if d[1]=="COMPRAS" and d[3]=="350516")
        crear_asiento("2026-01-19",
            "Factura 349883 Casino Expreso (alimentación)",
            [
                ("5.2.17", sii_349883[6], 0, "Gasto alimentación — fact 349883", None),
                ("1.1.05", sii_349883[7], 0, "IVA CF fact 349883", None),
                ("2.1.01", 0, sii_349883[8], "Factura 349883 Casino", casino),
            ],
            sii_ids=[sii_349883[0]])
        crear_asiento("2026-01-27",
            "Factura 350516 Casino Expreso (alimentación)",
            [
                ("5.2.17", sii_350516[6], 0, "Gasto alimentación — fact 350516", None),
                ("1.1.05", sii_350516[7], 0, "IVA CF fact 350516", None),
                ("2.1.01", 0, sii_350516[8], "Factura 350516 Casino", casino),
            ],
            sii_ids=[sii_350516[0]])
        m_casino_28 = next(m for m in movs if str(m[1])=="2026-01-28" and "Alimentacio" in m[2] and (m[3] or 0)==183_173)
        crear_asiento("2026-01-28",
            "Pago parcial facts 349883+350516 Casino — saldo $25.773 queda pendiente",
            [
                ("2.1.01", 183_173, 0, "Pago parcial Casino facts 349883+350516 ene-26", casino),
                ("1.1.02", 0, 183_173, "TRASPASO A: Sociedad De Alimentacion Casino Expreso", None),
            ],
            mov_ids=[m_casino_28[0]])
        # Facturas SII enero NO pagadas en enero: quedan PENDIENTES (no se crea asiento de pago)
        print("\n=== ASIENTOS CREADOS ===")
        for aid, f, desc, t in asientos_creados:
            print(f"  #{aid} {f} ${t:>14,} {desc[:80]}")
        print(f"\nTotal asientos creados: {len(asientos_creados)}")

        # Resumen movs no procesados
        cur.execute(
            "SELECT id, fecha, descripcion, cargo, abono FROM movimientos_banco "
            "WHERE empresa_id=? AND fecha>='2026-01-01' AND fecha<'2026-02-01' AND (procesado IS NULL OR procesado=0) "
            "ORDER BY fecha, id", (EMPRESA_ID,))
        no_proc = cur.fetchall()
        print(f"\n=== MOVS BANCO NO PROCESADOS: {len(no_proc)} ===")
        for r in no_proc:
            print(f"  mov#{r[0]} {r[1]} ${(r[3] or 0):>10,} ${(r[4] or 0):>10,} {r[2][:60]}")

        if dry_run:
            print("\n[DRY-RUN] No se hace commit.")
        else:
            conn.commit()
            print("\nCommit OK.")


if __name__ == "__main__":
    import sys
    main(dry_run=("--dry-run" in sys.argv))
