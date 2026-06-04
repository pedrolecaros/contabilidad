# Sistema Contable Chileno Multi-empresa — guía para Claude

Este archivo es la guía operativa para cualquier instancia de Claude que trabaje con este repositorio. Resume el contexto, las reglas de negocio y el flujo estándar de trabajo. **Leelo completo antes de empezar.**

---

## 1. Contexto del sistema

- **Stack**: Python 3 + Flask + SQLAlchemy + SQLite (`contabilidad.db`)
- **Multi-empresa**: ~14 sociedades chilenas (Ecox, Futrono, Los Robles, Parque Sur, Asesorías Ecox, Cerro Colorado, Chilcos, El Maitén, Santa Delfina, Tabancura, Puerto Octay, Aysen, etc.). Cada una tiene su propio plan de cuentas, asientos, libros SII, cartolas.
- **Idioma**: español. Tono coloquial con Pedro.
- **Fuentes de datos**:
  - Libros SII (compras/ventas/honorarios) — CSV/XLSX del SII
  - Cartolas bancarias (Banco Chile, Santander, etc.) — XLS/XLSX/CSV
  - Tarjetas de crédito — XLS Banco Chile
  - F29/F22 — PDFs del SII
- **Server local**: `flask run --host 0.0.0.0 --port 5000`. Accesible vía Tailscale.

## 2. Regla crítica de seguridad

**SIEMPRE confirmar `empresa_id` antes de modificar la DB.** No hardcodear `asiento_id`. Hubo un incidente real donde un script asumió `asiento_id=1 = Parque Sur` pero era Ecox y se sobrescribió la apertura ajena.

Patrón seguro:
```python
cur.execute("SELECT id FROM asientos WHERE empresa_id=? AND fecha=? AND descripcion LIKE ?",
            (EMPRESA_ID, fecha, patron))
row = cur.fetchone()
if not row: raise SystemExit("Asiento no encontrado")
print(f"Trabajando sobre asiento_id={row[0]} (empresa_id={EMPRESA_ID})")
```

## 3. Plan de cuentas estándar (PCGA chileno)

| Código | Cuenta | Notas |
|---|---|---|
| 1.1.01 | Caja | Solo efectivo + truco Macal |
| 1.1.02 | Banco | Todos los movs cartola |
| 1.1.03 | Clientes | **Requiere aux (contraparte_id)** |
| 1.1.05 | IVA Crédito Fiscal | 19% en compras tipo 33 |
| 1.1.06 | PPM | Pagos Provisionales Mensuales |
| 1.1.07 | Anticipos a Proveedores | Pagos sin factura aún recibida |
| 1.1.09 | Inversiones Fondos Mutuos | RUT FMU Banco Chile = 96571220-8 |
| 1.1.12 | Préstamos a Terceros por Cobrar | Requiere aux |
| 1.1.15 | Impuestos por Recuperar | Destino de PPM en F22 si hay devolución |
| 1.2.03 | Maquinarias y Equipos | Activo fijo, deprecia 1×/año |
| 1.2.08 | Dep. Acum. Maquinarias | Contracuenta de 1.2.03 |
| 1.2.12 | Activos en Leasing | Valor original máquinas leasing |
| 2.1.01 | Proveedores | **Requiere aux** |
| 2.1.03 | IVA Débito Fiscal | 19% en ventas tipo 33 |
| 2.1.04 | Retención Honorarios por Pagar | 15.25% en 2026; salda con F29 cód 151 |
| 2.1.05 | Remuneraciones por Pagar | Sueldos dic se pagan en enero |
| 2.1.06 | Cotizaciones Previsionales por Pagar | Previred |
| 2.1.07 | Impuesto a la Renta por Pagar | F29 pendientes |
| 2.1.10 | Préstamos Bancarios | Leasing (con aux Banco) |
| 2.1.11 | Préstamos de Terceros | **Requiere aux**. Siempre acá (NO 2.1.12) |
| 2.1.14 | Tarjeta de Crédito | Pasivo intermedio TC |
| 3.1.01 | Capital Pagado | |
| 3.1.03 | Utilidades Acumuladas | |
| 3.1.04 | Pérdidas Acumuladas | |
| 3.1.06 | Retiros del Ejercicio | Se cierra cada inicio de año |
| 4.1.02 | Ventas Exentas | Cuotas parcelas inmobiliarias agrícolas |
| 4.1.04 | Servicios Afectos | Arriendo maquinaria, etc. |
| 4.2.03 | Otros Ingresos | Reajustes IPC, recuperos |
| 5.2.01 | Remuneraciones y Sueldos | Sueldo + Previred + Imp 2ª cat |
| 5.2.02 | Honorarios | Boletas tipo 39 |
| 5.2.03 | Arriendo | Incl. sub-arriendos maquinaria |
| 5.2.06 | Materiales y Suministros | Insumos taller, hidrolavadora chica |
| 5.2.07 | Mantención y Reparaciones | |
| 5.2.08 | Seguros | |
| 5.2.09 | Transporte y Movilización | TAGs, peajes, transportes |
| 5.2.10 | Publicidad y Marketing | LinkedIn, Facebook (TC) |
| 5.2.11 | Asesorías y Consultorías | Asesorías Ecox $100K mensual |
| 5.2.12 | Gastos Bancarios e Intereses | Intereses leasing/créditos/TC |
| 5.2.14 | Depreciación del Ejercicio | Anual |
| 5.2.16 | Impuestos y Contribuciones | F29 sin detalle |
| 5.2.17 | Otros Gastos | Alimentación restaurantes, GEOTIM |

**Cuentas que requieren auxiliar (`requiere_aux=1`)**: 1.1.03, 1.1.04, 1.1.07, 1.1.11, 1.1.12, 1.1.13, 2.1.01, 2.1.02, 2.1.09, 2.1.10, 2.1.11, 2.1.12. Toda línea con monto sobre estas cuentas DEBE traer `contraparte_id`. La validación rechaza si falta.

## 4. Reglas estructurales de asientos

1. **Banco/Caja primera línea** (orden=1), incluso si va al haber.
2. **`contraparte_id` en TODA línea de cuentas con `requiere_aux=1`**.
3. **Numero correlativo por empresa**: hay un trigger DB `asientos_assign_numero` que asigna `MAX(numero)+1` si viene NULL. Igualmente al insertar via script setealo explícitamente.
4. **Estado por defecto: `BORRADOR`**. Pedro confirma en UI.
5. **Consolidar pagos múltiples mismo día misma contraparte** en un solo asiento.

## 5. Descripciones (criterio fiscalizador SII)

**SÍ**: categorías genéricas (`Gasto alimentación`, `Software/SaaS oficina`, `Suministros`, `Mantención`, `Honorarios`), nombre del proveedor cuando hay factura, folio del documento.

**NO**:
- Nunca escribir "sin respaldo", "sin documento", "personal", "no relacionado"
- No incluir nombres específicos de restaurantes (en vez de "Comida en Carnal" → "Gasto alimentación")
- No agregar comentarios que sugieran que el gasto no califica

La línea de banco (`1.1.02`) conserva la descripción original de la cartola; el "sanitizado" aplica solo a la descripción del asiento y la línea del gasto.

## 6. Patrones contables recurrentes

### F29 mensual (se paga en el mes siguiente)

Descomponer cód 91 (total) según códigos del F29:
- **cód 62** → DEBE `1.1.06 PPM`
- **cód 151** → DEBE `2.1.04 Retención Honorarios` (salda acumulado mes anterior)
- **cód 48** → DEBE `5.2.01 Remuneraciones` (imp 2ª cat retenido sueldo)
- **cód 89** → DEBE `2.1.03 IVA DF` (IVA a pagar)
- **cód 91** → HABER `1.1.02 Banco`

El detalle del F29 está en tabla `declaraciones_f29` (campos `codigo_*` + `codigos_json`). Si el F29 del mes no está cargado, dejar como gasto único `5.2.16 Impuestos` con nota "pendiente descomposición".

### Apertura (asiento inicial enero)

- Resultado del ejercicio cerrado y dividendos provisorios (3.1.06) → 3.1.03/3.1.04 (Utilidades/Pérdidas Acumuladas). 3.1.06 queda en cero al inicio del año.
- Saldos pasivos sueldos (2.1.05/06/07) suelen saldarse con primeros pagos enero peso a peso.

### PPM y F22 anual

PPM acumulados en 1.1.06 se aplican contra impuesto renta en abril (F22). Si hubo pérdida tributaria → PPM se trasladan a 1.1.15 Imp por Recuperar (devolución). Asiento devolución típico: `1.1.02 Banco / 1.1.06 PPM / 4.2.03 reajuste IPC`.

### Cierre IVA mensual

Al fin de cada mes, traspasar IVA DF contra IVA CF:
- DEBE `2.1.03 IVA DF` (todo el DF del mes)
- HABER `1.1.05 IVA CF` (consume CF del mes + remanente)

Si CF > DF, el remanente queda en 1.1.05. Si DF > CF, hay IVA a pagar.

### Préstamos por cobrar/pagar

**REGLA UNIFICADA**: TODOS los préstamos van a `1.1.12` (por cobrar) o `2.1.11` (por pagar) con auxiliar identificado. **NUNCA usar 1.1.13 ni 2.1.12**, aunque sean empresas relacionadas o socios. Distinguir "crédito privado" vs "préstamo socio" solo en la descripción del asiento.

### Macal (vendedor de parcelas con comisión 5%+IVA)

Bank recibe líquido (precio − comisión). Flujo:
1. Venta lote: `Banco D (neto) + Caja D (comisión total) / 4.1.02 Ventas H (total venta)`
2. Compra factura Macal: `5.2.13 Comisiones (neto) + 1.1.05 IVA CF / 2.1.01 Proveedores Macal`
3. Pago Macal vía Caja: `2.1.01 / 1.1.01 Caja`

Caja queda en cero. La comisión pasa por 1.1.01 porque no atraviesa banco.

### Pagos sin factura todavía (anticipos)

- Pago: `Banco H / 2.1.01 [contraparte] D` (anticipo)
- Cuando llega factura: `5.2.x Gasto D / 2.1.01 [contraparte] H` (salda anticipo)
- 2.1.01 con esa contraparte queda en cero al final.

### Honorarios

- **Con retención (normal)**: pago bancario = bruto × 0.8475 en 2026. Asientos: `5.2.02 bruto / 2.1.01 líquido + 2.1.04 retención → 2.1.01 / Banco líquido`. La retención se salda con F29 mes siguiente.
- **Sin retención (emisor retiene)**: detectable porque pago bancario = total boleta exacto (Carlos Ocampo, Notaría Rieutord). Procesar bruto: `5.2.02 bruto / 2.1.01 bruto → 2.1.01 / Banco bruto`.

### Tarjeta de Crédito (patrón Chilcos / Parque Sur)

Cuenta `2.1.14 Tarjeta de Crédito` actúa como pasivo intermedio.

**Cargo TC** (compra, comisión, intereses rotativos, impuesto DL 3475, traspaso deuda internacional):
- DEBE: cuenta gasto según naturaleza (5.2.10 Publicidad para Facebook/LinkedIn, 5.2.08 Seguros para Porvenir, 5.2.17 Otros para restaurantes, 5.2.12 Gastos Bancarios para comisiones/impuestos/intereses)
- HABER: 2.1.14 Tarjeta de Crédito

**Pago automático TC** (descuento banco al cierre ciclo, ~día 14):
- DEBE: 2.1.14 Tarjeta de Crédito (salda deuda acumulada del ciclo anterior)
- HABER: 1.1.02 Banco

Los movs espejo con `[ref XXXXX]` en la cartola que duplican el pago automático NO generan asiento separado.

Cada mes 2.1.14 cierra con saldo = cargos del mes (que se pagarán auto el próximo mes). Si hay sobrepago acumulado, indica que la apertura no incluyó deuda TC dic — hacer asiento ajuste contra 3.1.04.

### Leasing financiero (ej. Banco Chile)

Cada cuota mensual = factura emitida por el banco. **Dos asientos por cuota**:

1. **Reconocer factura** (misma fecha que el cargo bancario):
   - DEBE: `5.2.12 Gasto Intereses Leasing` (componente interés)
   - DEBE: `1.1.05 IVA CF` (19% del neto, recuperable)
   - DEBE: `2.1.10 Préstamos Bancarios aux Banco` (componente capital)
   - HABER: `2.1.01 Proveedores aux Banco` (total cuota c/IVA)

2. **Pago factura** (mismo día):
   - DEBE: `2.1.01 Proveedores aux Banco`
   - HABER: `1.1.02 Banco`

El capital decrementa 2.1.10. Interés es gasto del período.

### Compra de maquinaria

Compras a Ahern, Toyota, etc. de maquinaria como activo (no leasing):
- DEBE: `1.2.03 Maquinarias y Equipos` (gasto bruto)
- DEBE: `1.1.05 IVA CF`
- HABER: `2.1.01 Proveedor`

Depreciación 1×/año (no mensual).

### Mandatos (sociedad actúa como mandatario por cuenta de un tercero)

Cuando la sociedad recibe plata y hace pagos por cuenta y riesgo de un mandante (ej. Cerro Colorado opera por cuenta de Ecox Real Estate Florida — EREF), **NO usar** las cuentas separadas `1.1.14 Otros Activos Circulantes` y `2.1.13 Otros Pasivos Circulantes`. Esas dos cuentas crecen en paralelo y obligan a compensar manualmente.

**Patrón correcto**: una sola cuenta corriente del mandato con auxiliar por mandante.

- Cuenta: **`1.1.16 Cta. Cte. Mandatos`** (tipo ACTIVO, naturaleza DEUDORA, `requiere_aux=1`)
- Crearla con `INSERT INTO cuentas` la primera vez que se necesite en cada empresa.
- Aux: contraparte del mandante (ej. EREF id=168 en Cerro Colorado).

**Asientos**:
- Ingreso recibido para el mandante (entra plata al banco): `1.1.02 Banco D / 1.1.16 Mandato aux H` (la sociedad ahora le debe al mandante)
- Pago hecho por cuenta del mandante (sale plata): `1.1.16 Mandato aux D / 1.1.02 Banco H` (la sociedad recupera lo que ya entregó)

**Saldo neto en cualquier momento**:
- D = lo que el mandante le debe a la sociedad (pagamos más de lo que recibimos por él)
- H = lo que la sociedad le debe al mandante (recibimos más de lo que pagamos)

Sin necesidad de compensar mes a mes. Si hay múltiples mandantes, distintos aux en la misma cuenta.

### Créditos privados (cuotas en UF mensuales)

Sin factura SII. Asiento por cada cuota:
- DEBE: `2.1.11 Préstamos de Terceros aux acreedor` (componente capital)
- DEBE: `5.2.12 Gastos Bancarios e Intereses` (componente interés)
- HABER: `1.1.02 Banco` (total cuota)

## 7. Notas específicas por empresa

Cada empresa tiene sus notas operativas en la tabla `notas_contables` (`empresa_id`, `contenido`). **Leer SIEMPRE al inicio de trabajar con una empresa nueva.** Ejemplo Parque Sur tiene la regla "todo préstamo va a 2.1.11 con aux" + reglas leasing/créditos privados/TC.

Para Parque Sur específicamente (`empresa_id=1`): arriendo maquinaria, leasing Banco Chile (6 contratos), créditos privados UF (FHB/PLS/DHB/JOPA/JLI/Asesorías Ecox/Majo), TC patrón Chilcos.

## 8. Workflow estándar "hagamos [mes] de [empresa]"

1. **Confirmar empresa** explícitamente (mostrar nombre + RUT + ID).
2. **Leer notas globales + nota_global + notas_contables de la empresa**.
3. **Verificar prerequisitos del mes**: cartola subida, libros SII (compras/ventas/honorarios) cargados, F29 del mes anterior (si aplica). Si falta algo, avisar y ofrecer importar antes.
4. **Listar movs banco del mes** + docs SII pendientes (incluir acumulados de meses previos).
5. **Matchear SII ↔ Banco** por (fecha cercana + monto exacto o líquido vía retención).
6. **Para cada match**: crear asiento compra/honorario (origen LIBRO_COMPRAS/HONORARIOS) + asiento pago (origen BANCO).
7. **Para movs sin match SII**: asiento simple banco-vs-cuenta + conciliación tipo MANUAL.
8. **Para docs SII sin pago en el mes**: reconocer la factura igualmente (`5.x Gasto / 1.1.05 IVA / 2.1.01 Proveedor con aux`), dejar saldo H en 2.1.01 (se paga después). **NO dejar pendientes "para el siguiente mes"** — todas las facts SII deben tener asiento de compra incluso si no se pagaron.
9. **Casos especiales**: F29 (descomponer), Macal (Caja flow), pagos consolidados (1 asiento), anticipos (2.1.01 D), TC (espejo no genera asiento), aporte/rescate FFMM.
10. **Confirmar al usuario casos ambiguos** antes de crear (cuenta a usar, factura cross-month, hidrolavadora → activo o gasto, etc.).
11. **Verificar al final**:
    - 0 asientos descuadrados
    - Saldo Banco libros = saldo última fila cartola (no el "Saldo Disponible" del header, suele diferir por timing)
    - 2.1.01 por contraparte = anticipos pendientes (idealmente $0 si todas las facturas llegaron)
    - 2.1.04 Ret Honorarios = retenciones del mes actual (se saldan con F29 siguiente)
    - 2.1.14 TC = cargos del mes (se paga auto el mes sig.)
    - 1.1.02 IVA CF y 2.1.03 IVA DF cerrados con asiento mensual
12. **Reportar resumen**: # SII + # MANUAL + # PENDIENTE, saldos clave, docs/movs pendientes flagueados.

## 9. Reglas adicionales detectadas

- **Si un mes no tiene docs SII cargados**: avisar al usuario y ofrecer importar con `sii_scraper` antes de procesar.
- **Facturas/boletas GEOTIM** → cuenta `5.2.17 Gastos Generales`.
- **Asientos sin `numero` muestran "None" en UI**: el trigger DB lo resuelve para inserts, pero verificar antes de insertar.
- **Validación RUT al subir cartola/F29/F22/TC**: el sistema rechaza archivos cuyo RUT no coincida con la empresa activa.
- **Filtros asientos persisten en sesión Flask**: por empresa, hasta `?reset=1`.
- **Régimen PYME (12 de 14 empresas)**: el RLI se calcula base caja (cobranzas - pagos), no devengado. Módulo `/empresa/<id>/tributario/rli` redirige a cálculo correcto según `empresa.regimen`. Considera pérdida de arrastre del F22 cód 1440.

## 10. Comandos útiles

```bash
# Levantar server
FLASK_APP=app.py python3 -m flask run --host 0.0.0.0 --port 5000

# Confirmar empresa antes de tocar DB
python3 -c "import sqlite3; print(sqlite3.connect('contabilidad.db').execute('SELECT id, razon_social, rut FROM empresas WHERE id=?', (EID,)).fetchone())"

# Verificar cuadre
python3 -c "import sqlite3; r=sqlite3.connect('contabilidad.db').execute('SELECT SUM(la.debe), SUM(la.haber) FROM lineas_asiento la JOIN asientos a ON a.id=la.asiento_id WHERE a.empresa_id=?', (EID,)).fetchone(); print(r, 'diff', r[0]-r[1])"

# Listar asientos descuadrados
python3 -c "import sqlite3; c=sqlite3.connect('contabilidad.db').cursor(); c.execute('SELECT a.id, a.numero, a.fecha, a.descripcion FROM asientos a JOIN lineas_asiento la ON la.asiento_id=a.id WHERE a.empresa_id=? GROUP BY a.id HAVING ABS(SUM(la.debe)-SUM(la.haber))>1', (EID,)); [print(r) for r in c.fetchall()]"
```

## 11. Estructura del repo

- `app.py` — entry point Flask
- `models.py` — modelos SQLAlchemy (Empresa, Asiento, LineaAsiento, Cuenta, MovimientoBanco, DocumentoSII, etc.)
- `database.py` — migraciones + seeders (incluye trigger `asientos_assign_numero`)
- `routes/` — blueprints por área (asientos, importar, conciliacion, tributario, etc.)
- `templates/` — Jinja2
- `engine/` — lógica de dominio (`asientos.py:confirmar_asiento`, `plan_cuentas_default.py`, etc.)
- `importers/` — parsers SII y bancos
- `scripts/` — scripts ad-hoc de conciliación mensual por empresa (referencia, no se reutilizan tal cual)

## 12. Si trabajás desde otro PC (cliente remoto)

**Setup (una vez)**:
```bash
git clone https://github.com/pedrolecaros/contabilidad.git
cd contabilidad
claude
```

**Uso diario**:
```bash
cd contabilidad
git pull        # opcional, traer cambios
claude
> hagamos marzo de Parque Sur
```

### Servidor

**URL por defecto**: `http://192.168.100.128:5000` (server local de Pedro en la red WiFi de la oficina).

Alternativas si la principal no responde:
- `http://localhost:5000` (si corre en la misma máquina)
- `http://notebook-pedro:5000` (si está configurado Tailscale)

**Al iniciar la primera sesión, Claude debe**:
1. Probar `GET /api/health` contra la URL por defecto
2. Si responde 200 → seguir usando esa URL
3. Si NO responde (timeout, connection refused, etc.) → **PREGUNTAR al usuario**: *"No puedo alcanzar http://192.168.100.128:5000. ¿En qué IP/URL está corriendo el server hoy?"* Esperar respuesta antes de continuar.

NO tener DB local. Toda operación va via API HTTP al server (ver sección 13).

## 13. API REST para clientes remotos

Base URL: `http://notebook-pedro:5000/api`

### Lectura

| Endpoint | Devuelve |
|---|---|
| `GET /api/health` | OK + timestamp |
| `GET /api/empresas?activas=1` | Lista empresas (incl. régimen, tc_activa) |
| `GET /api/empresa/<id>` | Detalle empresa |
| `GET /api/empresa/<id>/cuentas` | Plan de cuentas + `requiere_aux` flag |
| `GET /api/empresa/<id>/contrapartes` | Lista clientes/proveedores |
| `GET /api/empresa/<id>/movs-banco?desde=&hasta=&procesado=0` | Movs cartola filtrados |
| `GET /api/empresa/<id>/sii?desde=&hasta=&libro=COMPRAS&procesado=0` | Docs SII |
| `GET /api/empresa/<id>/asientos?desde=&hasta=&estado=` | Asientos |
| `GET /api/asiento/<id>` | Detalle asiento + líneas |
| `GET /api/empresa/<id>/saldos?hasta=YYYY-MM-DD` | Saldo por cuenta |
| `GET /api/empresa/<id>/cuenta/<codigo>/mayor?desde=&hasta=` | Mayor de cuenta |

### Escritura

```bash
# Crear contraparte
POST /api/empresa/<id>/contraparte
{"rut": "76.123.456-7", "razon_social": "Foo SpA", "tipo": "PROVEEDOR"}

# Crear asiento (valida cuadre + aux + trigger numero)
POST /api/empresa/<id>/asiento
{
  "fecha": "2026-04-15",
  "descripcion": "Pago factura 123 Proveedor X",
  "estado": "BORRADOR",          // o "CONFIRMADO"
  "origen": "BANCO",              // MANUAL|BANCO|LIBRO_COMPRAS|...
  "lineas": [
    {"cuenta_codigo": "2.1.01", "debe": 119000, "haber": 0,
     "contraparte_id": 42, "descripcion": "Pago fact 123"},
    {"cuenta_codigo": "1.1.02", "debe": 0, "haber": 119000,
     "descripcion": "TRASPASO A: Proveedor X"}
  ],
  "mov_banco_ids": [1234],        // opcional, marca mov como procesado
  "sii_doc_ids": [567]            // opcional, marca SII como procesado
}

POST /api/asiento/<id>/confirmar
POST /api/asiento/<id>/anular
```

### SQL libre (solo SELECT)

```bash
POST /api/sql
{"sql": "SELECT codigo, COUNT(*) FROM cuentas GROUP BY codigo", "params": {}}
```

### Archivos en backup (lectura para Claude remoto)

Cada empresa tiene archivos respaldados en filesystem (libros SII, cartolas, F29/F22, TC, otros). Claude puede listarlos y **leerlos parseados** sin descargarlos:

```bash
# Lista todos los archivos en backup de una empresa
GET /api/empresa/<id>/archivos
# Respuesta: [{rel_path, nombre, tipo, periodo, tamano, mtime, es_global}, ...]

# Lee contenido parseado de un Excel/CSV/TXT
GET /api/empresa/<id>/archivo?rel=COMPRAS/2026-03/foo.csv&max_rows=1000
# CSV/XLSX/XLS → {rows: [[...], [...]], count: N}
# TXT/LOG → {texto: "..."}
# PDF/imagen → error 415, usar /empresa/<id>/archivos/descargar
```

**Cuándo usar**: cuando el usuario menciona "el Excel de créditos privados", "la tabla de leasing", "la cartola del mes X" — Claude puede leerlos directo via API en lugar de pedirlos.

**Archivos con período vacío** (`es_global: true`) son archivos que el usuario quiere conservar (Excels de créditos, contratos). NO eliminarlos al limpiar respaldos.

### Autenticación

Sin auth hoy. Si se expone públicamente (no solo Tailscale), agregar API key en header o token.

### Validaciones automáticas al crear asiento

- Fecha en formato ISO YYYY-MM-DD
- Cuenta existe en la empresa (busca por código o ID)
- Si cuenta tiene `requiere_aux=1` → debe traer `contraparte_id` válido de la misma empresa
- Cuadre Debe = Haber (tolerancia $1)
- Devuelve 400 con mensaje claro si algo falla; 201 con el asiento creado si todo OK

### Notas contables por empresa (P1)

```bash
# Leer la nota markdown
GET  /api/empresa/<id>/nota
# → {"empresa_id": 1, "contenido": "...", "actualizado_en": "..."} o 404

# Crear/actualizar (upsert)
PUT  /api/empresa/<id>/nota
{"contenido": "## Notas operativas\n- regla X..."}
```

### Saldos con estados configurables (P2)

```bash
# Solo CONFIRMADO (default — backwards compatible con /api/empresa/<id>/saldos)
GET  /api/empresa/<id>/saldos-estados?hasta=2026-03-31&estados=CONFIRMADO

# Proyección incluyendo borradores no confirmados
GET  /api/empresa/<id>/saldos-estados?hasta=2026-03-31&estados=BORRADOR,CONFIRMADO
```

### Resumen ejecutivo del mes (P3)

```bash
GET  /api/empresa/<id>/mes/2026-03/resumen
# →
# {
#   "periodo": "2026-03",
#   "movs_banco": {"total": 68, "sin_procesar": 0, "ingresos": $, "egresos": $},
#   "sii": {
#     "COMPRAS":    {"total": N, "sin_procesar": N, "monto": $},
#     "VENTAS":     {"total": N, "sin_procesar": N, "monto": $},
#     "HONORARIOS": {"total": N, "sin_procesar": N, "monto": $}
#   },
#   "asientos": {"BORRADOR": 0, "CONFIRMADO": 148, "ANULADO": 0, "descuadrados": 0},
#   "saldos_clave": {"banco": $, "caja": $, "iva_cf": $, "iva_df": $,
#                    "ret_honorarios": $, "ppm": $},
#   "f29_mes_anterior": {"periodo": "2026-02", "cargado": true, "codigo_91": 81466}
# }
```

### Editar y eliminar asiento BORRADOR (P4)

```bash
# Editar (mismos campos opcionales que POST; lineas reemplaza todas)
PATCH  /api/asiento/<id>
# Rechaza 400 si estado == CONFIRMADO (debe anularse primero)
{"descripcion": "...", "lineas": [...], "estado": "CONFIRMADO"}  # estado opcional confirma

# Eliminar BORRADOR (marca movs/sii asociados como procesado=0)
DELETE /api/asiento/<id>
# Rechaza 400 si CONFIRMADO
```

### Confirmación bulk (P5)

```bash
POST /api/asientos/confirmar
{"ids": [1804, 1805, 1806]}
# → {"confirmados": [1804, 1805], "fallidos": [{"id": 1806, "error": "..."}]}
# Atómico por id, no rollback global.
```

### Búsqueda contrapartes (P6)

```bash
# Sin q: trae todas (hasta el orden por razon_social)
GET  /api/empresa/<id>/contrapartes
# Con q: busca por razon_social o RUT, max 50 resultados
GET  /api/empresa/<id>/contrapartes?q=ahern
```

### Lectura F29 con descomposición (P6)

```bash
GET  /api/empresa/<id>/f29/2026-01
# → {"codigo_62": 39134, "codigo_48": 34375, "codigo_151": 0, "codigo_91": 73509,
#    "codigos_completos": {"77": ..., "504": ..., ...}}
# 404 si no cargado
```
