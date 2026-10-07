"""Trazabilidad mensaje -> viaje, y retractación.

Hasta ahora no había forma de ir de un mensaje de WhatsApp al viaje que produjo: si alguien
borraba un reporte, el viaje quedaba registrado como si nada hubiera pasado. Estas columnas
cierran ese hueco:

  eventos_whatsapp.push_name  -> nombre que la persona tiene en su WhatsApp (llegaba y se perdía)
  eventos_whatsapp.responde_a -> stanzaId del mensaje citado (permite saber QUÉ se corrige)
  viajes.origen_message_id    -> mensaje que originó el viaje
  viajes.retractado_en        -> cuándo se retractó (NULL = vigente)
  viajes.retractado_motivo    -> por qué

Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

CAMBIOS = [
    ("eventos_whatsapp", "push_name", "VARCHAR(120)"),
    ("eventos_whatsapp", "responde_a", "VARCHAR(120)"),
    ("viajes", "origen_message_id", "VARCHAR(120)"),
    ("viajes", "retractado_en", "TIMESTAMPTZ"),
    ("viajes", "retractado_motivo", "VARCHAR(300)"),
    ("viajes", "correcciones", "TEXT"),
]

INDICES = [
    ("ix_eventos_whatsapp_responde_a", "eventos_whatsapp", "responde_a"),
    ("ix_viajes_origen_message_id", "viajes", "origen_message_id"),
    ("ix_viajes_retractado_en", "viajes", "retractado_en"),
]

with engine.begin() as conn:
    for tabla, col, tipo in CAMBIOS:
        conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {col} {tipo}"))
        print(f"OK  {tabla}.{col} {tipo}")
    for nombre, tabla, col in INDICES:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {nombre} ON {tabla} ({col})"))
        print(f"OK  índice {nombre}")

print("Migración de trazabilidad completa.")
