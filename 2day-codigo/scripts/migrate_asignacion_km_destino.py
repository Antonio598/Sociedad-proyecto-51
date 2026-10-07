"""Agrega a `asignaciones_viaje` la columna `km_destino` (double): km estimado al destino
(línea recta desde las coords del mapa), editable por el coordinador. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS km_destino DOUBLE PRECISION"))
    print("OK  asignaciones_viaje.km_destino")

print("Migración de km_destino completa.")
