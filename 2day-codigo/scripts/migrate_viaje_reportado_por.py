"""Agrega la columna 'reportado_por' (número/JID de quien reportó) a viajes. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE viajes ADD COLUMN IF NOT EXISTS reportado_por VARCHAR(40)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_viajes_reportado_por ON viajes (reportado_por)"))
    print("OK  ADD COLUMN reportado_por + índice")

print("Migración de reportado_por completa.")
