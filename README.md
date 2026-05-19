# Sistema de Contabilidad

Aplicación web de contabilidad chilena multi-empresa. Partida doble, importación de documentos SII y cartolas bancarias, reportes, conciliación bancaria y auxiliar de cuentas por cobrar/pagar.

---

## Requisitos

- Python 3.10 o superior
- Las dependencias listadas en `requirements.txt`

```bash
pip install -r requirements.txt
```

---

## Iniciar el servidor

### Red local (acceso desde otros equipos en la misma red)

```bash
python3 -m flask run --host 0.0.0.0 --port 5000
```

Acceder desde cualquier equipo de la red en: `http://<IP-del-servidor>:5000`

Para encontrar la IP del servidor:

```bash
hostname -I
```

### Solo local

```bash
python3 -m flask run
```

Acceder en: `http://127.0.0.1:5000`

---

## Primera vez

1. Al iniciar por primera vez se crea automáticamente la base de datos `contabilidad.db`.
2. Crear una empresa desde la pantalla principal → **Nueva empresa**.
3. El plan de cuentas se carga automáticamente al crear la empresa.

---

## Módulos principales

### Empresas
Gestión multi-empresa. Cada empresa tiene su propio plan de cuentas, asientos, proveedores y clientes.

### Dashboard
Resumen financiero: activos, pasivos, patrimonio, resultado del período y próximas alertas.

### Asientos contables
- Crear asientos manuales con múltiples líneas debe/haber
- Confirmar, anular o editar asientos
- Adjuntar respaldo (imagen, PDF o link a Drive)
- Importar desde SII (libro de compras/ventas, honorarios) o cartola bancaria (CSV)

### Importar documentos
- **SII**: Conecta con el SII usando RUT y clave para descargar libros automáticamente
- **Cartola bancaria**: Subir CSV del banco para registrar movimientos masivamente
- Los documentos importados quedan en "Pendientes" hasta ser contabilizados

### Pendientes
Cola de documentos y movimientos bancarios importados aún sin asiento contable. Permite contabilizarlos individualmente o en lote.

### Conciliación bancaria
Cruzar movimientos bancarios con asientos del libro mayor. Identifica movimientos sin respaldo contable.

### Proveedores y Clientes (Contrapartes)
Registro de proveedores y clientes con RUT. Se pueden importar desde CSV o agregar manualmente.

### CxC / CxP (Auxiliar Cuentas por Cobrar/Pagar)
Registrar créditos financieros (no comerciales):
- **Tipos**: Bancario, Terceros, Empresa relacionada
- **Dirección**: Por Pagar (nosotros debemos) o Por Cobrar (nos deben)
- Saldo inicial + movimientos vinculados a asientos contables
- Cuentas contables: `2.1.10/11` (por pagar), `1.1.10/11` (por cobrar)

Para vincular un asiento a un crédito: al crear/editar el asiento, si se usa la cuenta `2.1.10`, `2.1.11`, `1.1.10` o `1.1.11`, aparece el panel **Vincular a CxC/CxP**.

### Remuneraciones
Liquidaciones de sueldo mensuales con variables (horas extra, bonos, descuentos). Genera asientos automáticamente.

### Reportes
- Balance general
- Estado de resultados
- Libro mayor por cuenta
- Cuentas por pagar/cobrar (contrapartes)
- Balance comparativo entre períodos

### Tributario
- RLI (Renta Líquida Imponible)

### Plan de cuentas
Ver y editar las cuentas contables de la empresa. Se puede activar/desactivar cuentas.

---

## Base de datos

La base de datos es un archivo SQLite: `contabilidad.db` en la raíz del proyecto.

### Respaldo manual
Desde la pantalla principal → **Configuración** → **Gestionar / Restaurar BD** se puede:
- Ver el estado de la base de datos
- Restaurar desde un archivo `.db` anterior

Para hacer backup manual, copiar el archivo `contabilidad.db` a un lugar seguro.

### Backup automático a Google Drive
Si tienes Google Drive montado localmente, puedes mover o vincular `contabilidad.db` a esa carpeta y configurar la ruta en `config.py`:

```python
SQLALCHEMY_DATABASE_URI = f"sqlite:////ruta/a/tu/gdrive/contabilidad.db"
```

> **Advertencia**: SQLite sobre carpetas sincronizadas en tiempo real (Drive, Dropbox) puede corromperse si hay sincronización durante una escritura. Se recomienda hacer backups periódicos en vez de ejecutar la DB directamente desde Drive.

---

## Configuración

El archivo `config.py` permite ajustar:

| Variable | Descripción |
|----------|-------------|
| `SQLALCHEMY_DATABASE_URI` | Ruta a la base de datos SQLite |
| `UPLOAD_FOLDER` | Carpeta donde se guardan adjuntos subidos |
| `SECRET_KEY` | Clave secreta para sesiones Flask |

---

## Estructura del proyecto

```
contabilidad/
├── app.py              # Inicialización Flask
├── config.py           # Configuración
├── database.py         # Migraciones y setup DB
├── models.py           # Modelos SQLAlchemy
├── requirements.txt    # Dependencias
├── contabilidad.db     # Base de datos (se crea al iniciar)
├── engine/             # Lógica de negocio (asientos, auditoría)
├── routes/             # Blueprints Flask por módulo
├── templates/          # Plantillas Jinja2
└── static/             # CSS, JS, imágenes
```

---

## Acceso remoto (fuera de la red local)

Para acceder desde internet sin VPN, la opción más simple es usar **ngrok**:

```bash
# Instalar ngrok desde https://ngrok.com
ngrok http 5000
```

Esto genera una URL pública temporal que apunta al servidor local.

Para acceso permanente se recomienda desplegar en un VPS con nginx como proxy inverso.
