# Mini CRM Móvil & Preventa (Dual Mode Local-First)

Sistema ágil y autónomo de preventa, toma de pedidos en ruta, gestión de clientes con GPS y cobranzas para distribuidoras y profesionales de servicios en Argentina.

---

## Características Principales

- **Dual-Mode Local-First:**
  - **Modo A (Celular en la calle):** PWA 100% offline. Toma pedidos en zonas sin señal de celular y guarda todo en almacenamiento local.
  - **Modo B (Notebook / PC):** Servidor local FastAPI + SQLite (`crm_local.db` en modo WAL) con `Iniciar_Servidor.bat`, multi-dispositivo por Wi-Fi LAN vía código QR y respaldos en Google Drive.
- **Acceso Seguro con Rate Limiting:**
  - PIN numérico para Dueño y Vendedor (hashing criptográfico PBKDF2).
  - Bloqueo temporal anti-fuerza bruta de 2 minutos (120 segundos) tras 5 intentos fallidos, con cuenta regresiva interactiva en vivo en pantalla.
  - **Llave Maestra de Rescate (`RESCATE-CRM-XXXX`):** Recuperación del PIN de Dueño sin internet ni soporte técnico.
- **Toma de Pedidos en 30 Segundos:**
  - Botones táctiles grandes `+` y `-`, buscador instantáneo y switch mayorista/minorista.
  - Totalizador en pesos argentinos (`$ 12.500,00`).
  - Cobro parcial o total y cálculo de saldo pendiente.
- **Avisos por WhatsApp en 1 Toque (Estándar Cero Rombos):**
  - Textos 100% libres de rombos negros con signo de pregunta (). Prohibición estricta de emojis en los mensajes generados.
  - Formato nativo de WhatsApp (*negrita*, viñetas •) y link directo a cobrar por Alias de Mercado Pago / Banco.
- **Ruta de Clientes con Navegación GPS:**
  - Botón directo "Cómo llegar" con Google Maps.
  - Alerta roja destacada con monto exigible solo cuando el cliente debe; sin etiquetas obvias de "al día".
- **Exportación a Excel (CSV):**
  - Descarga inmediata con formato regional en español (delimitador `;` y UTF-8 BOM).

---

## Cómo Iniciar en la Computadora (Modo B)

Hacé doble clic en:
```bat
Iniciar_Servidor.bat
```
Abrí tu navegador en:
```
http://localhost:8000
```
Para conectar celulares en la misma red Wi-Fi, hacé clic en el botón azul **"QR Wi-Fi"** y escanéalo con la cámara del teléfono.

---

## Documentación Completa

- [Manual de Usuario Final](file:///c:/Users/juanm/.gemini/antigravity/scratch/mini-crm-preventa/MANUAL_USUARIO.md)
- [Manual Técnico y Arquitectura](file:///c:/Users/juanm/.gemini/antigravity/scratch/mini-crm-preventa/MANUAL_TECNICO.md)
