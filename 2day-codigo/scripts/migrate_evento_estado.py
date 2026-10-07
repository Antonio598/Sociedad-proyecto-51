"""Cola durable: estado de procesamiento por evento de WhatsApp.
- eventos_whatsapp.estado_proceso ('pendiente'|'procesado'|'fallido')
- eventos_whatsapp.intentos (contador de reintentos)
Los eventos VIEJOS se marcan 'procesado' para no reprocesar toda la historia al arrancar.
Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine

with engine.begin() as conn:
    nuevo = not conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='eventos_whatsapp' AND column_name='estado_proceso'"
    )).first()
    conn.execute(text("ALTER TABLE eventos_whatsapp ADD COLUMN IF NOT EXISTS estado_proceso VARCHAR(12) DEFAULT 'pendiente'"))
    conn.execute(text("ALTER TABLE eventos_whatsapp ADD COLUMN IF NOT EXISTS intentos INTEGER DEFAULT 0"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_eventos_estado ON eventos_whatsapp (estado_proceso)"))
    if nuevo:
        # Todo lo que ya existe se considera procesado (no reprocesar el histórico).
        n = conn.execute(text("UPDATE eventos_whatsapp SET estado_proceso='procesado'")).rowcount
        print(f"OK  {n} eventos existentes marcados 'procesado'")
    print("OK  columnas estado_proceso + intentos")

print("Migración de estado de eventos completa.")
