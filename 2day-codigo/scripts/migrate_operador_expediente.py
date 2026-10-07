"""Agrega las columnas de expediente editable a la tabla operadores.
Idempotente (ADD COLUMN IF NOT EXISTS). Correr una vez tras extender el modelo."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine

COLS = [
    "telefono VARCHAR(30)",
    "licencia VARCHAR(60)",
    "licencia_vence DATE",
    "ingreso DATE",
    "estatus VARCHAR(20)",
    "notas TEXT",
]

with engine.begin() as conn:
    for col in COLS:
        conn.execute(text(f"ALTER TABLE operadores ADD COLUMN IF NOT EXISTS {col}"))
        print(f"OK  ADD COLUMN IF NOT EXISTS {col}")

print("Migración de expediente de operador completa.")
