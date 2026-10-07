"""Agrega la columna 'foto' a operadores. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE operadores ADD COLUMN IF NOT EXISTS foto VARCHAR(120)"))
    print("OK  ADD COLUMN IF NOT EXISTS foto VARCHAR(120)")

print("Migración de foto de operador completa.")
