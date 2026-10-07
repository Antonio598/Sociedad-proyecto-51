"""Agrega a `usuarios` las columnas de personalización de la cuenta: foto de perfil
(base64), tipo MIME, teléfono y preferencias (color de acento, tema). NO toca el nombre.
Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS foto TEXT"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS foto_mime VARCHAR(40)"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS telefono VARCHAR(30)"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prefs JSONB"))
    print("OK  usuarios.foto / foto_mime / telefono / prefs")

print("Migración de perfil de usuario completa.")
