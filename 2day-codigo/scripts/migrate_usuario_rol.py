"""Agrega usuarios.rol: 'admin' (todo) o 'coordinador' (operación diaria, sin IA
ni cambios de catálogo/configuración). Los usuarios existentes quedan como admin
para no quitarle permisos a nadie de golpe. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol VARCHAR(20) NOT NULL DEFAULT 'admin'"))
    print("OK  usuarios.rol VARCHAR(20) default 'admin'")

print("Migración de roles completa.")
