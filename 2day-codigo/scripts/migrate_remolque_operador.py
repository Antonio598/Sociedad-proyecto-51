"""F4 — asignar operadores a remolques:
- `remolques.operador_asignado_id` (FK operadores): operador TITULAR del remolque.
- `asignaciones_viaje.remolque_operadores` (JSONB): override de operador por remolque por viaje.
Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE remolques "
        "ADD COLUMN IF NOT EXISTS operador_asignado_id INTEGER REFERENCES operadores(id)"))
    print("OK  remolques.operador_asignado_id")
    conn.execute(text(
        "ALTER TABLE asignaciones_viaje "
        "ADD COLUMN IF NOT EXISTS remolque_operadores JSONB"))
    print("OK  asignaciones_viaje.remolque_operadores")

print("Migración de titular/override de remolque completa.")
