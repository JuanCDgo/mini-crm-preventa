# Manual Técnico y Arquitectura - Mini CRM Móvil & Preventa

> **Documentación Técnica para Desarrolladores y Mantenimiento de Sistema**  
> Proyecto: `mini-crm-preventa`  
> Repositorio Oficial: `https://github.com/JuanCDgo/mini-crm-preventa`  
> Fuente Única de la Verdad (SSOT): `C:\Users\juanm\.gemini\antigravity\scratch\Sistema de Gestión Unificado (Next-Gen)\GEMINI.md`

---

## 1. Arquitectura del Sistema (Dual Mode Local-First)

El sistema implementa una arquitectura desacoplada para operar en dos escenarios complementarios:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   MINI CRM MÓVIL & PREVENTA                            │
├───────────────────────────────────┬────────────────────────────────────┤
│   MODO A: PWA OFFLINE PURA        │   MODO B: SERVIDOR LOCAL LAN       │
│   (Celular en la Calle / Ruta)    │   (Notebook / PC de Oficina)       │
├───────────────────────────────────┼────────────────────────────────────┤
│ • Motor: IndexedDB / LocalStorage │ • Motor: FastAPI (Python 3.10+)    │
│ • UI: Tailwind CSS + Alpine.js    │ • BD: SQLite (Modo WAL + PBKDF2)   │
│ • Cero latencia, 100% offline     │ • LAN: Multi-dispositivo por Wi-Fi │
│ • Service Worker con App Cache    │ • Respaldos automáticos en Drive   │
│ • Despliegue: GitHub Pages HTTPS  │ • Launcher: Iniciar_Servidor.bat   │
└───────────────────────────────────┴────────────────────────────────────┘
```

---

## 2. Estructura de Directorios

```
mini-crm-preventa/
├── Iniciar_Servidor.bat         # Launcher Windows para Modo B
├── backup_drive.py              # Motor de copias locales y exportación Google Drive
├── formato.py                   # Formateadores ARS, fechas GMT-3 y WhatsApp Cero Rombos
├── local_db.py                  # Capa de datos SQLite, migraciones, WAL mode y hashes
├── main.py                      # Servidor FastAPI, endpoints REST y plantillas Jinja2
├── MANUAL_USUARIO.md            # Manual operativo de usuario final
├── MANUAL_TECNICO.md            # Este manual de arquitectura
├── dist/                        # Build estático autónomo para GitHub Pages
│   ├── index.html               # SPA completa con motor offline
│   ├── manifest.json            # Manifiesto PWA para instalación móvil
│   ├── sw.js                    # Service Worker con caché de red y fallback
│   ├── icon-192.svg / 512.svg   # Iconografía vectorial
│   └── vendor/                  # Dependencias empaquetadas (Alpine, Lucide, Tailwind)
├── static/                      # Recursos estáticos servidos por FastAPI
│   ├── icon-192.svg / 512.svg
│   ├── manifest.json
│   ├── sw.js
│   └── vendor/
└── templates/                   # Vistas Jinja2 renderizadas por el servidor
    ├── _nav.html                # Barra de navegación adaptable (desktop + móvil bottom)
    ├── _wizard.html             # Asistente de configuración paso a paso
    ├── login.html               # Pantalla de acceso con bloqueo y contador en vivo
    ├── index.html               # Tablero Kanban interactivo
    ├── pedidos.html             # Toma rápida de pedidos táctil para la calle
    ├── clientes.html            # Directorio con navegación GPS Google Maps
    ├── catalogo.html            # Gestión de precios minoristas y mayoristas
    ├── configuracion.html       # Datos de empresa, CBU/Alias y Llave de Rescate
    └── auditoria.html           # Registro de eventos de seguridad y rate limiting
```

---

## 3. Modelo de Datos Relacional (SQLite)

La base de datos local se aloja en `crm_local.db`. Se ejecuta con:
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
```

### 3.1. Tablas Principales
- **`configuracion`:** `id` (PK), `nombre_negocio`, `rubro_perfil` (`preventista` / `servicios`), `alias_mp`, `cbu_mp`, `telefono_contacto`, `pin_dueno_hash`, `pin_vendedor_hash`, `llave_maestra_rescate`, `configuracion_completada`, `actualizado_el`.
- **`clientes`:** `id` (PK), `nombre`, `telefono`, `direccion`, `lat`, `lng`, `saldo_deudor`, `notas`, `creado_el`.
- **`productos`:** `id` (PK), `codigo`, `nombre`, `categoria`, `precio_unitario`, `precio_mayorista`, `activo`, `creado_el`.
- **`pedidos`:** `id` (PK), `cliente_id` (FK), `fecha`, `estado` (`nuevo`, `en_camino`, `entregado`, `cobrado`, `cancelado`), `total`, `monto_cobrado`, `saldo_pendiente`, `metodo_pago`, `tipo_tarifa`, `observaciones`.
- **`pedido_items`:** `id` (PK), `pedido_id` (FK), `producto_id` (FK), `nombre`, `cantidad`, `precio_unitario`, `subtotal`.
- **`intentos_login`:** `id` (PK), `ip`, `rol`, `exitoso`, `timestamp`.
- **`alertas_seguridad`:** `id` (PK), `tipo`, `detalle`, `ip`, `nivel` (`info`, `medio`, `alto`), `fecha`.

---

## 4. Módulo de Seguridad y Rate Limiting

### 4.1. Hashing Criptográfico de PINs
No se almacenan PINs en texto plano. Se utiliza **PBKDF2-HMAC-SHA256** con 100.000 iteraciones y salt aleatorio de 16 bytes:
```python
def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 100000)
    return salt.hex() + "$" + dk.hex()
```

### 4.2. Protección Anti-Fuerza Bruta (120 Segundos)
- Cada intento fallido en `/api/login` se registra en memoria y en la tabla `intentos_login`.
- Si se superan 5 intentos fallidos desde una misma IP o rol en una ventana de 10 minutos:
  - Se genera un registro de nivel `alto` en `alertas_seguridad`.
  - Se activa el bloqueo temporal por **120 segundos**.
  - La API retorna código HTTP `429 Too Many Requests` con el payload:
    ```json
    { "detail": "Demasiados intentos fallidos", "bloqueado": true, "segundos_restantes": 120 }
    ```
  - La interfaz gráfica inhabilita el teclado numérico y muestra un temporizador regresivo interactivo.

### 4.3. Llave Maestra de Rescate (Mecanismo Offline)
Durante la inicialización se genera una llave pseudoaleatoria criptográfica de formato `RESCATE-CRM-XXXX`.
- Si el Dueño olvida su PIN, el endpoint `/api/seguridad/rescate-pin` valida la llave maestra de rescate directamente en la base de datos local y permite reescribir `pin_dueno_hash` sin requerir conectividad de red ni servidores SMTP externos.

---

## 5. Reglas de Negocio Estrictas (`GEMINI.md`)

1. **Formato Argentino:**
   - Moneda: `$ 12.500,00` (punto como separador de miles, coma como separador decimal).
   - Zona horaria: `America/Argentina/Buenos_Aires` (GMT-3, reloj de 24 horas).
2. **Cero Rombos en WhatsApp:**
   - La función `crear_link_whatsapp()` en `formato.py` y los módulos JS filtran cualquier emoji o carácter unicode fuera del plano básico para prevenir fallas de codificación en navegadores móviles (rombos negros ``).
   - El formato utiliza viñetas estándar (`•`), negrita nativa de WhatsApp (`*texto*`), codificación vía `quote(safe='')` o `encodeURIComponent()` y destino `https://api.whatsapp.com/send`.
3. **UI Limpia sin Redundancias:**
   - La interfaz prohíbe insignias o badges de "al día". El estado normal del cliente no se satura visualmente; solo se destaca con badge rojo (`bg-rose-50 text-rose-700`) cuando existe deuda exigible con el monto exacto en ARS.
4. **Exportación a Excel Compatible:**
   - El endpoint `/api/pedidos/exportar-excel` inyecta el Byte Order Mark UTF-8 (`\ufeff`) y utiliza el delimitador `;` para garantizar apertura directa en Microsoft Excel en Windows con configuración regional en español.

---

## 6. Despliegue en GitHub Pages

El repositorio se gestiona bajo el usuario `JuanCDgo`:
- Rama `main`: Contiene el código fuente completo del backend FastAPI, scripts de respaldo y plantillas Jinja2.
- Rama `gh-pages`: Contiene el contenido de la carpeta `dist/` para servir la PWA estática con HTTPS directo vía `https://juancdgo.github.io/mini-crm-preventa`.
