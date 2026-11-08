"""
Motor de base de datos local SQLite para Mini CRM Móvil & Preventa (Local-First Pro).
Idempotente, con WAL mode, PBKDF2 para PINs, y soporte para perfiles Preventista y Servicios.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from formato import ahora_ar_iso, ahora_ar_legible, fmt_moneda

log = logging.getLogger("crm.local_db")
TZ_AR = timezone(timedelta(hours=-3))


def hash_pin(pin: str) -> str:
    """Genera hash seguro PBKDF2-HMAC-SHA256 para el PIN."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"pbkdf2:sha256:100000${salt}${h.hex()}"


def check_pin(pin: str, pin_hash: str) -> bool:
    """Verifica un PIN contra el hash PBKDF2."""
    if not pin_hash:
        return False
    if pin_hash.startswith("pbkdf2:"):
        partes = pin_hash.split("$")
        if len(partes) == 3:
            salt = partes[1]
            esperado = partes[2]
            calculado = hashlib.pbkdf2_hmac(
                "sha256", pin.encode("utf-8"), salt.encode("utf-8"), 100_000
            ).hex()
            return secrets.compare_digest(calculado, esperado)
    return pin == pin_hash


def _crear_esquema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS comercios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_negocio TEXT NOT NULL DEFAULT 'Mi Negocio',
        rubro_perfil TEXT NOT NULL DEFAULT 'preventista',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio_id INTEGER NOT NULL REFERENCES comercios(id),
        nombre TEXT NOT NULL,
        pin_hash TEXT NOT NULL,
        rol TEXT NOT NULL CHECK (rol IN ('admin', 'vendedor')),
        activo INTEGER NOT NULL DEFAULT 1,
        intentos_fallidos INTEGER NOT NULL DEFAULT 0,
        bloqueado_hasta TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (comercio_id, nombre)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS configuracion (
        comercio_id INTEGER PRIMARY KEY REFERENCES comercios(id),
        nombre_negocio TEXT NOT NULL DEFAULT 'Mi Negocio',
        rubro_perfil TEXT NOT NULL DEFAULT 'preventista',
        telefono TEXT NOT NULL DEFAULT '',
        direccion TEXT NOT NULL DEFAULT '',
        alias TEXT NOT NULL DEFAULT '',
        cbu TEXT NOT NULL DEFAULT '',
        titular_cuenta TEXT NOT NULL DEFAULT '',
        logo_imagen TEXT NOT NULL DEFAULT '',
        clave_maestra_rescate TEXT NOT NULL DEFAULT 'RESCATE-CRM-7489',
        wizard_completado INTEGER NOT NULL DEFAULT 0,
        modo_operacion TEXT NOT NULL DEFAULT 'autonomo_offline',
        auto_backup_activo INTEGER NOT NULL DEFAULT 1,
        carpeta_sincronizacion_drive TEXT NOT NULL DEFAULT '',
        google_drive_webhook_url TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS etapas_pipeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio_id INTEGER NOT NULL REFERENCES comercios(id),
        clave TEXT NOT NULL,
        titulo TEXT NOT NULL,
        color TEXT NOT NULL DEFAULT '#64748B',
        orden INTEGER NOT NULL DEFAULT 0,
        es_ganado INTEGER NOT NULL DEFAULT 0,
        es_perdido INTEGER NOT NULL DEFAULT 0,
        activo INTEGER NOT NULL DEFAULT 1,
        UNIQUE (comercio_id, clave)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio_id INTEGER NOT NULL REFERENCES comercios(id),
        nombre TEXT NOT NULL,
        empresa TEXT DEFAULT '',
        telefono TEXT DEFAULT '',
        direccion TEXT DEFAULT '',
        localidad TEXT DEFAULT '',
        notas TEXT DEFAULT '',
        saldo_deudor REAL NOT NULL DEFAULT 0,
        latitud REAL,
        longitud REAL,
        activo INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS catalogo_productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio_id INTEGER NOT NULL REFERENCES comercios(id),
        codigo TEXT NOT NULL,
        nombre TEXT NOT NULL,
        categoria TEXT NOT NULL DEFAULT 'General',
        precio_minorista REAL NOT NULL DEFAULT 0,
        precio_mayorista REAL NOT NULL DEFAULT 0,
        unidad TEXT NOT NULL DEFAULT 'unid',
        stock_referencial REAL NOT NULL DEFAULT 0,
        activo INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (comercio_id, codigo)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS negocios_pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio_id INTEGER NOT NULL REFERENCES comercios(id),
        cliente_id INTEGER NOT NULL REFERENCES clientes(id),
        etapa_clave TEXT NOT NULL,
        titulo TEXT NOT NULL,
        monto_total REAL NOT NULL DEFAULT 0,
        tipo_precio TEXT NOT NULL DEFAULT 'minorista',
        notas TEXT DEFAULT '',
        fecha_recordatorio TEXT,
        hora_recordatorio TEXT,
        cumplido INTEGER NOT NULL DEFAULT 0,
        archivado INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS detalle_pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pedido_id INTEGER NOT NULL REFERENCES negocios_pedidos(id),
        producto_id INTEGER REFERENCES catalogo_productos(id),
        nombre_item TEXT NOT NULL,
        cantidad REAL NOT NULL DEFAULT 1,
        precio_unitario REAL NOT NULL DEFAULT 0,
        subtotal REAL NOT NULL DEFAULT 0
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alertas_seguridad (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio_id INTEGER NOT NULL REFERENCES comercios(id),
        tipo TEXT NOT NULL DEFAULT 'intento_fuerza_bruta',
        ip_origen TEXT NOT NULL DEFAULT '',
        usuario_afectado TEXT NOT NULL DEFAULT '',
        detalles TEXT NOT NULL DEFAULT '',
        fecha_hora TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        leida INTEGER NOT NULL DEFAULT 0
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS auditoria_movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comercio_id INTEGER NOT NULL REFERENCES comercios(id),
        fecha_hora TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        usuario_id INTEGER,
        usuario_nombre TEXT NOT NULL DEFAULT '',
        tipo_accion TEXT NOT NULL,
        detalles TEXT NOT NULL DEFAULT '{}'
    );
    """)

    # Índices de performance
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_comercio ON negocios_pedidos (comercio_id, etapa_clave, archivado);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clientes_comercio ON clientes (comercio_id, nombre, activo);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_catalogo_comercio ON catalogo_productos (comercio_id, nombre, activo);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_alertas_comercio ON alertas_seguridad (comercio_id, leida, fecha_hora DESC);")

    # Asegurar comercio inicial 1
    cur.execute("SELECT id FROM comercios WHERE id = 1;")
    if not cur.fetchone():
        cur.execute("INSERT INTO comercios (id, nombre_negocio, rubro_perfil, created_at) VALUES (1, 'Mi Negocio', 'preventista', ?);", (ahora_ar_iso(),))

    # Asegurar usuarios iniciales: Dueño (1234) y Vendedor 1 (1111)
    cur.execute("SELECT id FROM usuarios WHERE comercio_id = 1 AND rol = 'admin';")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO usuarios (comercio_id, nombre, pin_hash, rol, created_at) VALUES (1, 'Dueño', ?, 'admin', ?);",
            (hash_pin("1234"), ahora_ar_iso()),
        )
    cur.execute("SELECT id FROM usuarios WHERE comercio_id = 1 AND rol = 'vendedor';")
    if not cur.fetchone():
        for nom, pin in [("Vendedor 1", "1111"), ("Vendedor 2", "2222")]:
            cur.execute(
                "INSERT INTO usuarios (comercio_id, nombre, pin_hash, rol, created_at) VALUES (1, ?, ?, 'vendedor', ?);",
                (nom, hash_pin(pin), ahora_ar_iso()),
            )

    # Asegurar configuración por defecto
    cur.execute("SELECT comercio_id FROM configuracion WHERE comercio_id = 1;")
    if not cur.fetchone():
        cur.execute(
            """INSERT INTO configuracion (comercio_id, nombre_negocio, rubro_perfil, updated_at)
               VALUES (1, 'Mi Negocio', 'preventista', ?);""",
            (ahora_ar_iso(),),
        )

    # Sembrar etapas iniciales de Preventista si no existen
    cur.execute("SELECT COUNT(*) FROM etapas_pipeline WHERE comercio_id = 1;")
    if int(cur.fetchone()[0]) == 0:
        sembrar_etapas_pipeline(conn, comercio_id=1, rubro='preventista')

    conn.commit()


def sembrar_etapas_pipeline(conn: sqlite3.Connection, comercio_id: int = 1, rubro: str = 'preventista') -> None:
    """Siembra las columnas del tablero Kanban según el perfil del usuario."""
    cur = conn.cursor()
    cur.execute("DELETE FROM etapas_pipeline WHERE comercio_id = ?;", (comercio_id,))
    
    if rubro == 'servicios':
        etapas = [
            ("oportunidad", "Oportunidad", "#3B82F6", 1, 0, 0),
            ("contactado", "Contactado", "#8B5CF6", 2, 0, 0),
            ("presupuesto", "Presupuesto Enviado", "#F59E0B", 3, 0, 0),
            ("negociacion", "En Negociación", "#EC4899", 4, 0, 0),
            ("ganado", "Venta Ganada", "#10B981", 5, 1, 0),
            ("perdido", "No Concretado", "#64748B", 6, 0, 1),
        ]
    else: # preventista / distribución por defecto
        etapas = [
            ("pedido_tomado", "Pedido Tomado", "#3B82F6", 1, 0, 0),
            ("en_deposito", "En Depósito", "#F59E0B", 2, 0, 0),
            ("en_reparto", "En Reparto", "#8B5CF6", 3, 0, 0),
            ("entregado_cobrado", "Entregado y Cobrado", "#10B981", 4, 1, 0),
            ("cancelado", "Cancelado", "#64748B", 5, 0, 1),
        ]

    for clave, tit, col, ordn, gan, perd in etapas:
        cur.execute(
            """INSERT INTO etapas_pipeline (comercio_id, clave, titulo, color, orden, es_ganado, es_perdido, activo)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1);""",
            (comercio_id, clave, tit, col, ordn, gan, perd),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Connection Pool y Fluent API
# ---------------------------------------------------------------------------
class ConnectionPool:
    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._local = threading.local()

    def get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None


class TableQuery:
    def __init__(self, pool: ConnectionPool, table_name: str):
        self.pool = pool
        self.table_name = table_name
        self._select_cols = "*"
        self._wheres: List[str] = []
        self._params: List[Any] = []
        self._orders: List[str] = []
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None

    def select(self, cols: str = "*") -> TableQuery:
        self._select_cols = cols
        return self

    def eq(self, column: str, value: Any) -> TableQuery:
        self._wheres.append(f"{column} = ?")
        self._params.append(value)
        return self

    def order(self, column: str, desc: bool = False) -> TableQuery:
        self._orders.append(f"{column} {'DESC' if desc else 'ASC'}")
        return self

    def limit(self, count: int) -> TableQuery:
        self._limit = count
        return self

    def execute(self) -> DBResult:
        conn = self.pool.get_connection()
        cur = conn.cursor()
        sql = f"SELECT {self._select_cols} FROM {self.table_name}"
        if self._wheres:
            sql += " WHERE " + " AND ".join(self._wheres)
        if self._orders:
            sql += " ORDER BY " + ", ".join(self._orders)
        if self._limit is not None:
            sql += f" LIMIT {self._limit}"
        cur.execute(sql, tuple(self._params))
        rows = [dict(r) for r in cur.fetchall()]
        return DBResult(data=rows)

    def insert(self, values: Union[Dict[str, Any], List[Dict[str, Any]]]) -> DBResult:
        conn = self.pool.get_connection()
        cur = conn.cursor()
        items = values if isinstance(values, list) else [values]
        if not items:
            return DBResult(data=[])
        keys = list(items[0].keys())
        cols_sql = ", ".join(keys)
        placeholders = ", ".join(["?"] * len(keys))
        sql = f"INSERT INTO {self.table_name} ({cols_sql}) VALUES ({placeholders});"
        
        inserted_rows = []
        for it in items:
            params = [it.get(k) for k in keys]
            cur.execute(sql, tuple(params))
            row_id = cur.lastrowid
            inserted_rows.append({"id": row_id, **it})
        conn.commit()
        return DBResult(data=inserted_rows)

    def update(self, values: Dict[str, Any]) -> TableQueryUpdate:
        return TableQueryUpdate(self.pool, self.table_name, values, self._wheres, self._params)

    def delete(self) -> TableQueryDelete:
        return TableQueryDelete(self.pool, self.table_name, self._wheres, self._params)


class TableQueryUpdate:
    def __init__(self, pool: ConnectionPool, table_name: str, values: Dict[str, Any], wheres: List[str], params: List[Any]):
        self.pool = pool
        self.table_name = table_name
        self.values = values
        self._wheres = list(wheres)
        self._params = list(params)

    def eq(self, column: str, value: Any) -> TableQueryUpdate:
        self._wheres.append(f"{column} = ?")
        self._params.append(value)
        return self

    def execute(self) -> DBResult:
        conn = self.pool.get_connection()
        cur = conn.cursor()
        set_clauses = [f"{k} = ?" for k in self.values.keys()]
        sql = f"UPDATE {self.table_name} SET {', '.join(set_clauses)}"
        if self._wheres:
            sql += " WHERE " + " AND ".join(self._wheres)
        all_params = list(self.values.values()) + self._params
        cur.execute(sql, tuple(all_params))
        conn.commit()
        return DBResult(data=[], rowcount=cur.rowcount)


class TableQueryDelete:
    def __init__(self, pool: ConnectionPool, table_name: str, wheres: List[str], params: List[Any]):
        self.pool = pool
        self.table_name = table_name
        self._wheres = list(wheres)
        self._params = list(params)

    def eq(self, column: str, value: Any) -> TableQueryDelete:
        self._wheres.append(f"{column} = ?")
        self._params.append(value)
        return self

    def execute(self) -> DBResult:
        conn = self.pool.get_connection()
        cur = conn.cursor()
        sql = f"DELETE FROM {self.table_name}"
        if self._wheres:
            sql += " WHERE " + " AND ".join(self._wheres)
        cur.execute(sql, tuple(self._params))
        conn.commit()
        return DBResult(data=[], rowcount=cur.rowcount)


class DBResult:
    def __init__(self, data: Any = None, rowcount: int = 0):
        self.data = data or []
        self.rowcount = rowcount


class LocalDBClient:
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).resolve().parent
            else:
                base_dir = Path(__file__).resolve().parent
            datos_dir = base_dir / "datos"
            datos_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = datos_dir / "pos_local.db"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.pool = ConnectionPool(self.db_path)
        conn = self.pool.get_connection()
        _crear_esquema(conn)

    def table(self, nombre: str) -> TableQuery:
        return TableQuery(self.pool, nombre)

    def close(self) -> None:
        self.pool.close()
