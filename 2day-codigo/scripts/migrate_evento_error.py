"""Agrega eventos_whatsapp.error: el motivo del último fallo del evento.

Antes el motivo solo iba al log del servidor, así que el panel podía decir "falló" pero no
por qué — y sin eso no hay forma de decidir si vale la pena reprocesar o si hay que
arreglar algo antes. Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE eventos_whatsapp ADD COLUMN IF NOT EXISTS error TEXT"))
    print("OK  eventos_whatsapp.error TEXT")

print("Migración del motivo de fallo completa.")
