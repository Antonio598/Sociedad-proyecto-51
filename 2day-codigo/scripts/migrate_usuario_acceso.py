"""Agrega a `usuarios` la columna `ultimo_acceso` (timestamp del último login), base de la
bitácora de acceso del panel de administración ("saber quién ve"). Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultimo_acceso TIMESTAMPTZ"))
    print("OK  usuarios.ultimo_acceso")

print("Migración de último acceso completa.")
