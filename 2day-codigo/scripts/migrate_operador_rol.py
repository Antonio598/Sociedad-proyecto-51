"""Agrega la columna 'rol' (TITULAR/RELEVO) a operadores. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE operadores ADD COLUMN IF NOT EXISTS rol VARCHAR(20)"))
    print("OK  ADD COLUMN IF NOT EXISTS rol VARCHAR(20)")

print("Migración de rol de operador completa.")
