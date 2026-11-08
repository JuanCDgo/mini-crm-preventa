"""
Módulo de Respaldo Local (ZIP/JSON) y Sincronización con Google Drive para Mini CRM.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("crm.backup")
TZ_AR = timezone(timedelta(hours=-3))


def generar_snapshot_completo(db_path: Path) -> dict:
    """Extrae todas las tablas de pos_local.db en un diccionario JSON serializable."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tablas = [
        "comercios",
        "usuarios",
        "configuracion",
        "etapas_pipeline",
        "clientes",
        "catalogo_productos",
        "negocios_pedidos",
        "detalle_pedidos",
        "alertas_seguridad",
        "auditoria_movimientos",
    ]

    snapshot = {
        "version": "1.0",
        "app": "mini-crm-preventa",
        "timestamp": datetime.now(TZ_AR).isoformat(),
        "tablas": {},
    }

    for t in tablas:
        try:
            cur.execute(f"SELECT * FROM {t};")
            snapshot["tablas"][t] = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            log.warning("No se pudo exportar tabla %s: %s", t, e)
            snapshot["tablas"][t] = []

    conn.close()
    return snapshot


def exportar_zip_backup(db_path: Path) -> bytes:
    """Genera un archivo ZIP en memoria con snapshot.json y la base SQLite."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Base SQLite directa si existe
        if db_path.exists():
            zf.write(str(db_path), arcname="pos_local.db")
        # 2. Snapshot JSON limpio
        snap = generar_snapshot_completo(db_path)
        zf.writestr("snapshot_crm.json", json.dumps(snap, ensure_ascii=False, indent=2))

    buf.seek(0)
    return buf.getvalue()


def restaurar_desde_json(db_path: Path, data: dict) -> dict:
    """Restaura la base SQLite a partir de un snapshot JSON."""
    tablas = data.get("tablas") or {}
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=OFF;")

    restauradas = {}
    for nom_tabla, filas in tablas.items():
        if not filas or not isinstance(filas, list):
            continue
        try:
            cur.execute(f"DELETE FROM {nom_tabla};")
            cols = list(filas[0].keys())
            placeholders = ", ".join(["?"] * len(cols))
            sql = f"INSERT OR REPLACE INTO {nom_tabla} ({', '.join(cols)}) VALUES ({placeholders});"
            for f in filas:
                cur.execute(sql, tuple(f.get(c) for c in cols))
            restauradas[nom_tabla] = len(filas)
        except Exception as e:
            log.error("Error al restaurar tabla %s: %s", nom_tabla, e)

    cur.execute("PRAGMA foreign_keys=ON;")
    conn.commit()
    conn.close()
    return {"ok": True, "restauradas": restauradas}
