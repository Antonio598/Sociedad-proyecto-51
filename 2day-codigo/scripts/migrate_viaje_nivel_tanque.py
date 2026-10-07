"""Agrega la columna 'nivel_tanque' (fracción 0.0-1.0 de la aguja del tablero) a
viajes. Base de la aproximación de litros: nivel_tanque * capacidad_tanque(unidad).
Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE viajes ADD COLUMN IF NOT EXISTS nivel_tanque DOUBLE PRECISION"))
    print("OK  ADD COLUMN nivel_tanque")

print("Migración de nivel_tanque completa.")
