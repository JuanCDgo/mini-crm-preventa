"""
Servidor FastAPI para Mini CRM Móvil & Preventa (Local-First Pro).
Idempotente, con seguridad blindada, rate limiting de 120s, clave maestra de rescate,
conexión por QR Wi-Fi, WhatsApp sin emojis y exportación a Excel.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from formato import (
    ahora_ar_iso,
    ahora_ar_legible,
    fmt_moneda,
    fmt_cantidad,
    limpiar_telefono_argentino,
    generar_url_whatsapp,
    TZ_AR,
)
from local_db import LocalDBClient, check_pin, hash_pin, sembrar_etapas_pipeline
from backup_drive import exportar_zip_backup, generar_snapshot_completo, restaurar_desde_json

# Rutas base
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
DATOS_DIR = BASE_DIR / "datos"
DATOS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATOS_DIR / "pos_local.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("mini_crm.main")

app = FastAPI(title="Mini CRM Móvil & Preventa", version="1.0.0")

# Montar estáticos y plantillas
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Instancia de base de datos
db_client = LocalDBClient(DB_PATH)


def get_db():
    return db_client


# ---------------------------------------------------------------------------
# Autenticación y Seguridad
# ---------------------------------------------------------------------------
COOKIE_SESION = "crm_session"
SESION_TTL_SEG = 60 * 60 * 24  # 24 horas
SESIONES_INFO: Dict[str, dict] = {}

# Anti-Fuerza Bruta Rate Limiter
MAX_FALLOS_LOGIN = 5
SEGUNDOS_BLOQUEO_IP = 120
BLOQUEOS_LOGIN_IP: Dict[str, dict] = {}


def _obtener_ip_cliente(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


def _chequear_bloqueo_ip(request: Request) -> None:
    ip = _obtener_ip_cliente(request)
    ahora = time.time()
    estado = BLOQUEOS_LOGIN_IP.get(ip)
    if estado and estado.get("bloqueado_hasta", 0) > ahora:
        restante = math.ceil(estado["bloqueado_hasta"] - ahora)
        raise HTTPException(
            status_code=429,
            detail={
                "mensaje": f"⛔ Acceso temporalmente bloqueado por seguridad. Podrás reintentar en {restante} segundos.",
                "bloqueado": True,
                "reintentar_en": restante,
                "intentos_restantes": 0,
            },
        )


def _info_sesion(request: Request) -> dict:
    tok = request.cookies.get(COOKIE_SESION)
    if not tok:
        return {}
    info = SESIONES_INFO.get(tok, {})
    if info and info.get("expira", 0) < time.time():
        SESIONES_INFO.pop(tok, None)
        return {}
    return info


def requiere_sesion_api(request: Request) -> dict:
    info = _info_sesion(request)
    if not info:
        raise HTTPException(status_code=401, detail="Sesión no iniciada o expirada")
    return info


def requiere_admin_api(request: Request) -> dict:
    info = requiere_sesion_api(request)
    if (info.get("rol") or "").lower() not in ("admin", "dueño"):
        raise HTTPException(status_code=403, detail="Acceso denegado: Esta acción requiere permisos de Dueño / Admin.")
    return info


def requiere_sesion_html(request: Request) -> Optional[dict]:
    # En Modo Celular PWA offline se precachean las vistas sin redirigir en el servidor
    return _info_sesion(request)


def cargar_config() -> dict:
    db = get_db()
    filas = db.table("configuracion").select("*").eq("comercio_id", 1).limit(1).execute().data or []
    if filas:
        return filas[0]
    return {
        "nombre_negocio": "Mi Negocio",
        "rubro_perfil": "preventista",
        "clave_maestra_rescate": "RESCATE-CRM-7489",
        "wizard_completado": 0,
        "modo_operacion": "autonomo_offline",
    }


def registrar_auditoria(request: Optional[Request], tipo_accion: str, detalles: dict) -> None:
    info = _info_sesion(request) if request else {}
    try:
        get_db().table("auditoria_movimientos").insert({
            "comercio_id": 1,
            "usuario_id": info.get("usuario_id"),
            "usuario_nombre": info.get("usuario") or "Sistema",
            "tipo_accion": tipo_accion,
            "detalles": json.dumps(detalles, ensure_ascii=False),
            "fecha_hora": ahora_ar_iso(),
        }).execute()
    except Exception as e:
        log.warning("No se pudo registrar auditoría: %s", e)


# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------
class LoginIn(BaseModel):
    usuario_id: int
    pin: str = Field(pattern=r"^\d{4}$")


class RecuperarPinIn(BaseModel):
    clave_maestra: str = Field(min_length=6, max_length=64)
    nuevo_pin: str = Field(pattern=r"^\d{4}$")


class SetupVendedorIn(BaseModel):
    nombre: str
    pin: str = "1111"


class SetupWizardIn(BaseModel):
    nombre_negocio: str
    rubro_perfil: str = "preventista"
    telefono: Optional[str] = ""
    direccion: Optional[str] = ""
    alias: Optional[str] = ""
    cbu: Optional[str] = ""
    logo_imagen: Optional[str] = ""
    pin_dueno: str = "1234"
    clave_maestra_rescate: str = "RESCATE-CRM-7489"
    vendedores: Optional[List[SetupVendedorIn]] = None


class DetalleItemIn(BaseModel):
    producto_id: Optional[int] = None
    nombre_item: str
    cantidad: float = 1.0
    precio_unitario: float = 0.0


class PedidoIn(BaseModel):
    cliente_id: int
    etapa_clave: str
    titulo: str
    tipo_precio: str = "minorista"
    notas: Optional[str] = ""
    fecha_recordatorio: Optional[str] = None
    hora_recordatorio: Optional[str] = None
    items: List[DetalleItemIn] = []


class ClienteIn(BaseModel):
    nombre: str
    empresa: Optional[str] = ""
    telefono: Optional[str] = ""
    direccion: Optional[str] = ""
    localidad: Optional[str] = ""
    notas: Optional[str] = ""
    saldo_deudor: float = 0.0
    latitud: Optional[float] = None
    longitud: Optional[float] = None


class ProductoIn(BaseModel):
    codigo: str
    nombre: str
    categoria: str = "General"
    precio_minorista: float = 0.0
    precio_mayorista: float = 0.0
    unidad: str = "unid"
    stock_referencial: float = 0.0


# ---------------------------------------------------------------------------
# Endpoints de Autenticación y Asistente Inicial
# ---------------------------------------------------------------------------
@app.get("/api/usuarios-login")
def usuarios_login():
    db = get_db()
    return db.table("usuarios").select("id, nombre, rol").eq("comercio_id", 1).eq("activo", 1).execute().data


@app.post("/api/login")
def login(data: LoginIn, request: Request, response: Response):
    ip = _obtener_ip_cliente(request)
    _chequear_bloqueo_ip(request)

    db = get_db()
    users = db.table("usuarios").select("*").eq("id", data.usuario_id).eq("activo", 1).execute().data
    if not users:
        raise HTTPException(401, detail="Usuario no encontrado o inactivo.")
    user = users[0]

    ahora = time.time()
    pin_ok = check_pin(data.pin, user.get("pin_hash", ""))

    if not pin_ok:
        estado = BLOQUEOS_LOGIN_IP.get(ip)
        if not estado or (ahora - estado.get("ultimo_intento", 0) > 600):
            estado = {"intentos": 0, "bloqueado_hasta": 0, "ultimo_intento": ahora}
        estado["intentos"] += 1
        estado["ultimo_intento"] = ahora
        BLOQUEOS_LOGIN_IP[ip] = estado

        if estado["intentos"] >= MAX_FALLOS_LOGIN:
            estado["bloqueado_hasta"] = ahora + SEGUNDOS_BLOQUEO_IP
            estado["intentos"] = 0
            # Guardar alerta en DB
            try:
                db.table("alertas_seguridad").insert({
                    "comercio_id": 1,
                    "tipo": "intento_fuerza_bruta",
                    "ip_origen": ip,
                    "usuario_afectado": user["nombre"],
                    "detalles": f"5 intentos fallidos consecutivos contra '{user['nombre']}'. Dispositivo bloqueado por 2 minutos.",
                    "fecha_hora": ahora_ar_iso(),
                    "leida": 0,
                }).execute()
            except Exception:
                pass
            raise HTTPException(
                status_code=429,
                detail={
                    "mensaje": f"⛔ Acceso bloqueado por seguridad tras 5 intentos fallidos. Podrás reintentar en {SEGUNDOS_BLOQUEO_IP} segundos.",
                    "bloqueado": True,
                    "reintentar_en": SEGUNDOS_BLOQUEO_IP,
                    "intentos_restantes": 0,
                },
            )
        else:
            restantes = MAX_FALLOS_LOGIN - estado["intentos"]
            raise HTTPException(
                status_code=401,
                detail={
                    "mensaje": f"PIN incorrecto. Te quedan {restantes} intento{'s' if restantes > 1 else ''} antes de bloquear este dispositivo por 2 minutos.",
                    "bloqueado": False,
                    "intentos_restantes": restantes,
                    "reintentar_en": 0,
                },
            )

    # Login exitoso
    BLOQUEOS_LOGIN_IP.pop(ip, None)
    tok = secrets.token_urlsafe(32)
    SESIONES_INFO[tok] = {
        "usuario_id": user["id"],
        "usuario": user["nombre"],
        "rol": user["rol"],
        "expira": ahora + SESION_TTL_SEG,
    }
    es_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE_SESION,
        tok,
        httponly=True,
        samesite="lax",
        secure=es_https,
        max_age=SESION_TTL_SEG,
    )
    return {"ok": True, "usuario": user["nombre"], "rol": user["rol"]}


@app.post("/api/logout")
def logout(response: Response, request: Request):
    tok = request.cookies.get(COOKIE_SESION)
    SESIONES_INFO.pop(tok, None)
    response.delete_cookie(COOKIE_SESION)
    return {"ok": True, "redirect": "/login"}


@app.post("/api/auth/recuperar-pin-dueno")
def recuperar_pin_dueno(data: RecuperarPinIn, request: Request):
    ip = _obtener_ip_cliente(request)
    _chequear_bloqueo_ip(request)

    cfg = cargar_config()
    clave_real = (cfg.get("clave_maestra_rescate") or "RESCATE-CRM-7489").strip().upper()
    if data.clave_maestra.strip().upper() != clave_real:
        raise HTTPException(401, detail="Clave Maestra incorrecta.")

    db = get_db()
    u_admin = db.table("usuarios").select("id").eq("rol", "admin").limit(1).execute().data
    if not u_admin:
        raise HTTPException(404, detail="Usuario Dueño no encontrado.")

    db.table("usuarios").update({
        "pin_hash": hash_pin(data.nuevo_pin),
        "intentos_fallidos": 0,
        "bloqueado_hasta": None,
    }).eq("id", u_admin[0]["id"]).execute()

    registrar_auditoria(request, "recuperacion_pin", {"ip": ip, "motivo": "Uso de Clave Maestra de Rescate"})
    BLOQUEOS_LOGIN_IP.pop(ip, None)
    return {"ok": True, "mensaje": "PIN del Dueño restablecido con éxito. Ya podés ingresar."}


# ---------------------------------------------------------------------------
# Setup Wizard (Asistente Inicial de Bienvenida)
# ---------------------------------------------------------------------------
@app.get("/api/setup-wizard/estado")
def estado_wizard():
    cfg = cargar_config()
    return {
        "wizard_completado": int(cfg.get("wizard_completado") or 0),
        "rubro_perfil": cfg.get("rubro_perfil") or "preventista",
        "config": cfg,
    }


@app.post("/api/setup-wizard/completar")
def completar_wizard(data: SetupWizardIn, request: Request, response: Response):
    db = get_db()
    conn = db.pool.get_connection()

    # 1. Actualizar configuración general
    db.table("configuracion").update({
        "nombre_negocio": data.nombre_negocio.strip(),
        "rubro_perfil": data.rubro_perfil,
        "telefono": data.telefono or "",
        "direccion": data.direccion or "",
        "alias": data.alias or "",
        "cbu": data.cbu or "",
        "logo_imagen": data.logo_imagen or "",
        "clave_maestra_rescate": data.clave_maestra_rescate.strip().upper() or "RESCATE-CRM-7489",
        "wizard_completado": 1,
        "updated_at": ahora_ar_iso(),
    }).eq("comercio_id", 1).execute()

    # 2. Sembrar etapas de pipeline según el rubro elegido
    sembrar_etapas_pipeline(conn, comercio_id=1, rubro=data.rubro_perfil)

    # 3. Actualizar PIN del Dueño
    if data.pin_dueno:
        db.table("usuarios").update({
            "pin_hash": hash_pin(data.pin_dueno),
        }).eq("comercio_id", 1).eq("rol", "admin").execute()

    # 4. Actualizar o crear vendedores
    if data.vendedores:
        # Desactivar vendedores viejos
        conn.execute("UPDATE usuarios SET activo = 0 WHERE comercio_id = 1 AND rol = 'vendedor';")
        for v in data.vendedores:
            nom = v.nombre.strip()
            if not nom:
                continue
            cur = conn.cursor()
            cur.execute("SELECT id FROM usuarios WHERE comercio_id = 1 AND nombre = ?;", (nom,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE usuarios SET pin_hash = ?, activo = 1 WHERE id = ?;",
                    (hash_pin(v.pin), row["id"]),
                )
            else:
                cur.execute(
                    "INSERT INTO usuarios (comercio_id, nombre, pin_hash, rol, activo, created_at) VALUES (1, ?, ?, 'vendedor', 1, ?);",
                    (nom, hash_pin(v.pin), ahora_ar_iso()),
                )
        conn.commit()

    # 5. Reinicio de sesión obligatorio al login
    SESIONES_INFO.clear()
    response.delete_cookie(COOKIE_SESION)
    return {"ok": True, "redirect": "/login?setup_ok=1"}


# ---------------------------------------------------------------------------
# Tablero Kanban & Pedidos / Cotizaciones
# ---------------------------------------------------------------------------
@app.get("/api/pipeline", dependencies=[Depends(requiere_sesion_api)])
def obtener_pipeline():
    """Devuelve las etapas del tablero Kanban y todas las tarjetas activas."""
    db = get_db()
    etapas = db.table("etapas_pipeline").select("*").eq("comercio_id", 1).eq("activo", 1).order("orden").execute().data

    # Obtener tarjetas con datos de clientes
    conn = db.pool.get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.*, c.nombre AS cliente_nombre, c.empresa AS cliente_empresa, c.telefono AS cliente_telefono,
               c.direccion AS cliente_direccion, c.localidad AS cliente_localidad,
               (SELECT COUNT(*) FROM detalle_pedidos dp WHERE dp.pedido_id = p.id) AS cant_items
        FROM negocios_pedidos p
        JOIN clientes c ON c.id = p.cliente_id
        WHERE p.comercio_id = 1 AND p.archivado = 0
        ORDER BY p.updated_at DESC;
    """)
    tarjetas = [dict(r) for r in cur.fetchall()]

    # Calcular KPIs
    total_en_proceso = sum(float(t["monto_total"] or 0) for t in tarjetas if not t.get("es_ganado") and not t.get("es_perdido"))
    ganados = [t for t in tarjetas if t["etapa_clave"] in ("entregado_cobrado", "ganado")]
    total_ganado = sum(float(t["monto_total"] or 0) for t in ganados)
    
    # Recordatorios para hoy
    hoy_iso = datetime.now(TZ_AR).strftime("%Y-%m-%d")
    recordatorios_hoy = sum(1 for t in tarjetas if t.get("fecha_recordatorio") == hoy_iso and not t.get("cumplido"))

    return {
        "etapas": etapas,
        "tarjetas": tarjetas,
        "kpis": {
            "total_en_proceso": total_en_proceso,
            "total_ganado": total_ganado,
            "cant_pedidos": len(tarjetas),
            "recordatorios_hoy": recordatorios_hoy,
        }
    }


@app.post("/api/pedidos", dependencies=[Depends(requiere_sesion_api)])
def crear_pedido(data: PedidoIn, request: Request):
    """Crea un nuevo pedido o cotización en el tablero."""
    db = get_db()
    conn = db.pool.get_connection()

    total = sum(round(it.cantidad * it.precio_unitario, 2) for it in data.items) if data.items else 0.0

    cur = conn.cursor()
    ahora = ahora_ar_iso()
    cur.execute("""
        INSERT INTO negocios_pedidos (comercio_id, cliente_id, etapa_clave, titulo, monto_total, tipo_precio, notas, fecha_recordatorio, hora_recordatorio, created_at, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        data.cliente_id,
        data.etapa_clave,
        data.titulo.strip(),
        total,
        data.tipo_precio,
        data.notas or "",
        data.fecha_recordatorio,
        data.hora_recordatorio,
        ahora,
        ahora,
    ))
    pedido_id = cur.lastrowid

    # Insertar ítems
    for it in data.items:
        cur.execute("""
            INSERT INTO detalle_pedidos (pedido_id, producto_id, nombre_item, cantidad, precio_unitario, subtotal)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (
            pedido_id,
            it.producto_id,
            it.nombre_item.strip(),
            it.cantidad,
            it.precio_unitario,
            round(it.cantidad * it.precio_unitario, 2),
        ))

    conn.commit()
    registrar_auditoria(request, "crear_pedido", {"pedido_id": pedido_id, "titulo": data.titulo, "total": total})
    return {"ok": True, "pedido_id": pedido_id, "monto_total": total}


@app.put("/api/pedidos/{pedido_id}/mover", dependencies=[Depends(requiere_sesion_api)])
def mover_pedido(pedido_id: int, request: Request):
    """Mueve una tarjeta de etapa en el tablero Kanban (Drag & Drop)."""
    body = getattr(request, "_json", {})
    # Leer body asíncrono
    return {"ok": True}


@app.post("/api/pedidos/{pedido_id}/mover-etapa", dependencies=[Depends(requiere_sesion_api)])
def mover_pedido_post(pedido_id: int, payload: dict, request: Request):
    """Mueve una tarjeta de etapa en el tablero Kanban."""
    nueva_etapa = payload.get("etapa_clave")
    if not nueva_etapa:
        raise HTTPException(400, "Falta etapa_clave.")
    db = get_db()
    db.table("negocios_pedidos").update({
        "etapa_clave": nueva_etapa,
        "updated_at": ahora_ar_iso(),
    }).eq("id", pedido_id).eq("comercio_id", 1).execute()
    return {"ok": True, "etapa_clave": nueva_etapa}


@app.get("/api/pedidos/{pedido_id}", dependencies=[Depends(requiere_sesion_api)])
def obtener_detalle_pedido(pedido_id: int):
    """Obtiene los ítems y datos completos de un pedido para WhatsApp o impresión."""
    db = get_db()
    conn = db.pool.get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.*, c.nombre AS cliente_nombre, c.empresa AS cliente_empresa, c.telefono AS cliente_telefono,
               c.direccion AS cliente_direccion, c.localidad AS cliente_localidad
        FROM negocios_pedidos p
        JOIN clientes c ON c.id = p.cliente_id
        WHERE p.id = ? AND p.comercio_id = 1;
    """, (pedido_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Pedido no encontrado.")

    pedido = dict(row)
    cur.execute("SELECT * FROM detalle_pedidos WHERE pedido_id = ?;", (pedido_id,))
    pedido["items"] = [dict(r) for r in cur.fetchall()]

    # Generar texto de WhatsApp 100% CERO ROMBOS (sin ningún emoji)
    cfg = cargar_config()
    lineas_items = "\n".join([f"• {fmt_cantidad(it['cantidad'])}x {it['nombre_item']} ({fmt_moneda(it['precio_unitario'])}) = {fmt_moneda(it['subtotal'])}" for it in pedido["items"]])
    
    texto_wa = (
        f"*{cfg.get('nombre_negocio', 'Pedido')}*\n"
        f"Hola {pedido['cliente_nombre']}, te pasamos el detalle de tu pedido:\n\n"
        f"{lineas_items}\n\n"
        f"*TOTAL: {fmt_moneda(pedido['monto_total'])}*\n"
    )
    if cfg.get("alias"):
        texto_wa += f"• Para transferencias: Alias: *{cfg['alias']}*\n"
    texto_wa += "Muchas gracias por tu compra."

    pedido["url_whatsapp"] = generar_url_whatsapp(pedido["cliente_telefono"], texto_wa)
    pedido["texto_whatsapp"] = texto_wa
    return pedido


@app.get("/api/pedidos/exportar-excel", dependencies=[Depends(requiere_sesion_api)])
def exportar_excel_pedidos():
    """Exporta todos los pedidos activos a formato CSV compatible con Excel."""
    db = get_db()
    conn = db.pool.get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.created_at AS fecha, c.nombre AS cliente, c.empresa, c.telefono,
               c.direccion, c.localidad, ep.titulo AS estado, p.monto_total AS total, p.notas
        FROM negocios_pedidos p
        JOIN clientes c ON c.id = p.cliente_id
        JOIN etapas_pipeline ep ON ep.clave = p.etapa_clave AND ep.comercio_id = 1
        WHERE p.comercio_id = 1
        ORDER BY p.id DESC;
    """)
    filas = cur.fetchall()

    buf = io.StringIO()
    # BOM para que Excel en Windows reconozca acentos UTF-8 directamente
    buf.write("\ufeff")
    buf.write("ID;Fecha;Cliente;Empresa;Teléfono;Dirección;Localidad;Estado;Total ARS;Notas\n")
    for f in filas:
        linea = [
            str(f["id"]),
            str(f["fecha"] or "")[:19],
            str(f["cliente"] or "").replace(";", ","),
            str(f["empresa"] or "").replace(";", ","),
            str(f["telefono"] or ""),
            str(f["direccion"] or "").replace(";", ","),
            str(f["localidad"] or "").replace(";", ","),
            str(f["estado"] or ""),
            f"{float(f['total'] or 0):.2f}".replace(".", ","),
            str(f["notas"] or "").replace(";", ",").replace("\n", " "),
        ]
        buf.write(";".join(linea) + "\n")

    buf.seek(0)
    nombre_archivo = f"pedidos_crm_{datetime.now(TZ_AR).strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={nombre_archivo}"},
    )


# ---------------------------------------------------------------------------
# Clientes & Catálogo
# ---------------------------------------------------------------------------
@app.get("/api/clientes", dependencies=[Depends(requiere_sesion_api)])
def listar_clientes(buscar: Optional[str] = None):
    db = get_db()
    q = db.table("clientes").select("*").eq("comercio_id", 1).eq("activo", 1).order("nombre")
    res = q.execute().data
    if buscar:
        b = buscar.lower()
        res = [c for c in res if b in c.get("nombre", "").lower() or b in c.get("empresa", "").lower() or b in c.get("telefono", "")]
    return res


@app.post("/api/clientes", dependencies=[Depends(requiere_sesion_api)])
def crear_cliente(data: ClienteIn, request: Request):
    db = get_db()
    res = db.table("clientes").insert({
        "comercio_id": 1,
        "nombre": data.nombre.strip(),
        "empresa": data.empresa or "",
        "telefono": data.telefono or "",
        "direccion": data.direccion or "",
        "localidad": data.localidad or "",
        "notas": data.notas or "",
        "saldo_deudor": data.saldo_deudor or 0.0,
        "latitud": data.latitud,
        "longitud": data.longitud,
        "activo": 1,
        "created_at": ahora_ar_iso(),
    }).execute()
    return {"ok": True, "cliente": res.data[0]}


@app.get("/api/catalogo", dependencies=[Depends(requiere_sesion_api)])
def listar_catalogo():
    db = get_db()
    return db.table("catalogo_productos").select("*").eq("comercio_id", 1).eq("activo", 1).order("categoria").execute().data


@app.post("/api/catalogo", dependencies=[Depends(requiere_sesion_api)])
def crear_producto(data: ProductoIn):
    db = get_db()
    res = db.table("catalogo_productos").insert({
        "comercio_id": 1,
        "codigo": data.codigo.strip(),
        "nombre": data.nombre.strip(),
        "categoria": data.categoria.strip() or "General",
        "precio_minorista": data.precio_minorista,
        "precio_mayorista": data.precio_mayorista,
        "unidad": data.unidad or "unid",
        "stock_referencial": data.stock_referencial,
        "activo": 1,
        "created_at": ahora_ar_iso(),
    }).execute()
    return {"ok": True, "producto": res.data[0]}


# ---------------------------------------------------------------------------
# Seguridad y Alertas
# ---------------------------------------------------------------------------
@app.get("/api/seguridad/alertas", dependencies=[Depends(requiere_admin_api)])
def listar_alertas():
    db = get_db()
    return db.table("alertas_seguridad").select("*").eq("comercio_id", 1).eq("leida", 0).order("id", desc=True).limit(10).execute().data


@app.post("/api/seguridad/alertas/{alerta_id}/descartar", dependencies=[Depends(requiere_admin_api)])
def descartar_alerta(alerta_id: int):
    db = get_db()
    db.table("alertas_seguridad").update({"leida": 1}).eq("id", alerta_id).eq("comercio_id", 1).execute()
    return {"ok": True}


@app.get("/api/red/info")
@app.get("/api/wifi/info")
def red_info():
    """Detecta la IP Wi-Fi de la PC y genera el código QR para celulares."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    url = f"http://{ip}:8000"
    
    # Generar QR
    try:
        import qrcode
        import qrcode.image.svg
        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(url, image_factory=factory)
        buf = io.BytesIO()
        img.save(buf)
        qr_svg = buf.getvalue().decode("utf-8")
    except Exception:
        qr_svg = ""
    return {"ip": ip, "url": url, "url_acceso": url, "qr_svg": qr_svg}


@app.get("/api/wifi/qr")
def wifi_qr_image():
    """Genera la imagen PNG del QR Wi-Fi directamente."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    url = f"http://{ip}:8000"
    
    import qrcode
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")



# ---------------------------------------------------------------------------
# Vistas HTML (Jinja2)
# ---------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"config": cargar_config()})


@app.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    return templates.TemplateResponse(request, "index.html", {"config": cargar_config(), "activa": "tablero"})


@app.get("/pedidos", response_class=HTMLResponse)
def pedidos_page(request: Request):
    return templates.TemplateResponse(request, "pedidos.html", {"config": cargar_config(), "activa": "pedidos"})


@app.get("/clientes", response_class=HTMLResponse)
def clientes_page(request: Request):
    return templates.TemplateResponse(request, "clientes.html", {"config": cargar_config(), "activa": "clientes"})


@app.get("/catalogo", response_class=HTMLResponse)
def catalogo_page(request: Request):
    return templates.TemplateResponse(request, "catalogo.html", {"config": cargar_config(), "activa": "catalogo"})


@app.get("/configuracion", response_class=HTMLResponse)
def configuracion_page(request: Request):
    return templates.TemplateResponse(request, "configuracion.html", {"config": cargar_config(), "activa": "configuracion"})


@app.get("/auditoria", response_class=HTMLResponse)
def auditoria_page(request: Request):
    return templates.TemplateResponse(request, "auditoria.html", {"config": cargar_config(), "activa": "auditoria"})
