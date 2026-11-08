# Manual de Usuario - Mini CRM Móvil & Preventa

> **Aplicación de Gestión de Rutas, Preventa y Clientes (Local-First & Offline)**  
> Desarrollado bajo las directivas de arquitectura y calidad del archivo maestro `GEMINI.md`.

---

## 1. ¿Qué es esta aplicación y para quién está pensada?

El **Mini CRM Móvil & Preventa** es una herramienta ágil diseñada especialmente para:
1. **Preventistas y Distribuidores:** Personas que recorren la calle o rutas comerciales tomando pedidos en almacenes, kioscos, autoservicios o comercios.
2. **Profesionales y Empresas de Servicios:** Personas que gestionan presupuestos, visitas técnicas, presupuestación y seguimiento de etapas comerciales.

La aplicación funciona de forma **100% autónoma y offline**. Podés entrar a depósitos, sótanos o parajes sin señal de celular y seguir cargando clientes, precios y pedidos con total normalidad.

---

## 2. Los Dos Modos de Trabajo

| Característica | Modo A: Celular en la Calle (PWA) | Modo B: PC / Notebook (LAN Wi-Fi) |
| :--- | :--- | :--- |
| **Dónde se usa** | Celular o tablet en el bolsillo del vendedor | Notebook o PC de la distribuidora/oficina |
| **Conexión necesaria** | **Cero (100% Offline)**. No gasta datos móviles | Red Wi-Fi local de la oficina/casa |
| **Inicio** | Se instala como app tocando "Agregar a pantalla principal" | Doble clic en `Iniciar_Servidor.bat` |
| **Base de datos** | Almacenamiento local seguro en el navegador | SQLite (`crm_local.db`) en modo WAL de alto rendimiento |
| **Exportación** | Descarga directa de Excel (CSV) y copia JSON | Exportación automática a Google Drive y Excel |

---

## 3. Acceso Seguro y Protección Anti-Fuerza Bruta

### 3.1. Selección de Perfil y PIN
Al abrir la aplicación, elegís tu usuario:
- **Dueño:** Acceso completo a precios, configuraciones, llaves de seguridad y auditoría.
- **Vendedor:** Vista operativa optimizada para tomar pedidos y ver la ruta de clientes.

### 3.2. Bloqueo de Seguridad (Rate Limiting de 120 segundos)
Si una persona intenta adivinar el PIN e ingresa 5 veces de forma incorrecta:
- El sistema se bloquea automáticamente por **2 minutos (120 segundos)**.
- Aparece una **cuenta regresiva en vivo** en pantalla y se deshabilitan los botones.
- Cada intento fallido y bloqueo queda registrado con fecha, hora exacta argentina (GMT-3) y dirección IP en el módulo de auditoría.

### 3.3. Olvidé mi PIN: Llave Maestra de Rescate
Si olvidaste tu PIN de Dueño, **no necesitás internet, correos ni soporte técnico**:
1. Tocá el botón **"Olvidé mi PIN"** en la pantalla de acceso.
2. Ingresá la **Llave Maestra de Rescate** (ej: `RESCATE-CRM-XXXX`) que guardaste durante la configuración inicial.
3. Ingresá el nuevo PIN deseado y tocá **"Restablecer"**. El acceso se desbloquea de inmediato.

---

## 4. Cómo Tomar un Pedido en la Calle (Paso a Paso)

1. Ingresá a la pestaña **"Tomar Pedido"** (o ícono de `+` en la barra inferior táctil).
2. **Elegí el Cliente:**
   - Seleccionalo del menú desplegable.
   - Si tiene saldo deudor anterior, el sistema te muestra una **alerta roja destacada** con el monto exacto que te debe.
   - Si es un cliente nuevo, tocá **"+ Nuevo Cliente"**, cargás nombre y dirección en 5 segundos y ya queda listo.
3. **Elegí la Tarifa:** Tocá **Minorista** o **Mayorista**. Los precios se actualizan al instante en pantalla.
4. **Cargá los Productos:**
   - Usá el buscador en vivo para encontrar el producto por nombre o código.
   - Tocá el botón **`+`** para sumar unidades y **`-`** para restar.
5. **Cobro y Saldo:**
   - Si te paga una parte o la totalidad en mano o transferencia, escribí el monto en **"Monto Cobrado ($)"**.
   - El sistema calcula automáticamente el saldo que queda pendiente y te indica si queda deuda.
6. Tocá **"Confirmar Pedido"**. El pedido pasa de inmediato al tablero de reparto.

---

## 5. Tablero Kanban de Seguimiento de Etapas

En el **Tablero**:
- **Preventa:** Las columnas son `Nuevo Pedido` ➔ `En Reparto` ➔ `Entregado` ➔ `Cobrado`.
- **Servicios:** Las columnas son `Contacto Inicial` ➔ `Presupuestado` ➔ `En Negociación` ➔ `Aprobado`.

Podés:
- **Arrastrar las tarjetas** de una columna a otra con el dedo o mouse.
- Tocar la flecha **`>`** para que avance de etapa automáticamente.
- Tocar el ícono del **ojo** para ver el desglose completo del pedido.

---

## 6. Avisos por WhatsApp en 1 Toque (Estándar Cero Rombos)

Al tocar el botón verde **"WhatsApp"** en cualquier pedido o cliente:
- Se abre la conversación oficial de WhatsApp de forma directa.
- **Garantía Cero Rombos:** El mensaje utiliza negrita nativa (`*texto*`) y viñetas (`•`) **sin ningún emoji**, evitando que en celulares o navegadores aparezcan signos de pregunta negros (``).
- Incluye el desglose de lo pedido, el total en ARS, y si tiene deuda pendiente, incluye tu **Alias de Mercado Pago / Banco** para que te transfiera al instante sin comisiones.

---

## 7. Directorio de Clientes y Navegación GPS

En la pestaña **"Clientes"**:
- Cada tarjeta muestra nombre, teléfono, dirección y notas de entrega.
- **Filtro visual limpio:** Si el cliente está al día, no se muestra ningún cartel innecesario. Si tiene deuda pendiente, aparece el badge rojo con el importe exacto.
- **Botón "Cómo llegar":** Abre automáticamente Google Maps con la ruta trazada en tiempo real hacia el negocio del cliente.

---

## 8. Exportar a Excel y Respaldos

- **Botón "Excel":** Descarga un archivo `.csv` formateado específicamente con codificación UTF-8 BOM y punto y coma (`;`), listo para abrirse en Microsoft Excel en Windows sin caracteres extraños ni desfasaje de columnas.
- **Copia JSON:** En la pestaña Configuración podés tocar **"Descargar Copia JSON"** para guardar una copia de seguridad en tu computadora o pendrive.
- **Google Drive:** En Modo B (Notebook/PC), el sistema respalda automáticamente una copia en tu carpeta de Google Drive.
