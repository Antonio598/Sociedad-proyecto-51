"""Idempotencia de la cola offline: agrega solicitudes_recarga.client_uuid.

La app del operador genera un UUID por solicitud. Si un reintento de sincronización repite
el POST (la red se cayó justo tras crearla), el servidor devuelve la existente en vez de
duplicar la carga. El índice único sobre columna nullable permite múltiples NULL en
Postgres, así que las solicitudes previas (sin uuid) no chocan. Idempotente (IF NOT EXISTS).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

SENTENCIAS = [
    "ALTER TABLE solicitudes_recarga ADD COLUMN IF NOT EXISTS client_uuid VARCHAR(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_solicitudes_recarga_client_uuid "
    "ON solicitudes_recarga (client_uuid)",
]

with engine.begin() as conn:
    for s in SENTENCIAS:
        conn.execute(text(s))
        print("OK ", s)

print("Migración client_uuid completa.")
