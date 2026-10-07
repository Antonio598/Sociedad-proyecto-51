"""Agrega a `solicitudes_recarga` la columna `remolque_id` (FK remolques): a qué remolque se
liga una recarga de TERMO de un tracto (su económico y sus horas de termo). Null en recargas
de motor y en camiones. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE solicitudes_recarga "
        "ADD COLUMN IF NOT EXISTS remolque_id INTEGER REFERENCES remolques(id)"))
    print("OK  solicitudes_recarga.remolque_id")

print("Migración de remolque_id completa.")
