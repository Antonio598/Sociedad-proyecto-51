"""Cambia operadores.foto a TEXT (para base64) y agrega foto_mime. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE operadores ALTER COLUMN foto TYPE TEXT"))
    print("OK  ALTER COLUMN foto TYPE TEXT")
    conn.execute(text("ALTER TABLE operadores ADD COLUMN IF NOT EXISTS foto_mime VARCHAR(40)"))
    print("OK  ADD COLUMN IF NOT EXISTS foto_mime VARCHAR(40)")
    # Limpia cualquier valor previo tipo 'op_1.png' (eran nombres de archivo, no base64)
    conn.execute(text("UPDATE operadores SET foto = NULL WHERE foto IS NOT NULL AND foto LIKE 'op\\_%'"))
    print("OK  limpiados nombres de archivo previos")

print("Migración de foto base64 completa.")
