"""Agrega a `solicitudes_recarga` la columna `tipo_recarga` ('motor' | 'termo'). Un camión
refrigerado hace 2 recargas bajo el mismo económico: motor y termo. Las filas existentes son
'motor' por defecto. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE solicitudes_recarga "
        "ADD COLUMN IF NOT EXISTS tipo_recarga VARCHAR(10) NOT NULL DEFAULT 'motor'"))
    print("OK  solicitudes_recarga.tipo_recarga")

print("Migración de tipo_recarga completa.")
