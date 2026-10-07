"""Agrega a `seguimiento_descuentos` las columnas de auditoría de aplicación desde el panel
de Combustible: aplicada_por_id (FK usuarios), aplicada_en (timestamp) y viaje_id (FK viajes,
para no volver a sugerir un viaje ya penalizado). Idempotente. Las filas importadas del Excel
histórico quedan con estas columnas en NULL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE seguimiento_descuentos ADD COLUMN IF NOT EXISTS aplicada_por_id INTEGER REFERENCES usuarios(id)"))
    conn.execute(text("ALTER TABLE seguimiento_descuentos ADD COLUMN IF NOT EXISTS aplicada_en TIMESTAMPTZ"))
    conn.execute(text("ALTER TABLE seguimiento_descuentos ADD COLUMN IF NOT EXISTS viaje_id INTEGER REFERENCES viajes(id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_seguimiento_descuentos_viaje_id ON seguimiento_descuentos(viaje_id)"))
    print("OK  seguimiento_descuentos.aplicada_por_id / aplicada_en / viaje_id")

print("Migración de auditoría de penalizaciones completa.")
