"""Fase 5a: remolques del viaje + verificación de evidencia por número económico (ECO).

- asignaciones_viaje.remolque_ids: JSON con los ids de remolque enganchados al viaje.
- evidencias_recarga: etiqueta/esperado/texto_ia/coincide para las fotos de ECO rotulado
  (la IA lee el económico y se coteja contra lo que asignó el coordinador). Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

SENTENCIAS = [
    "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS remolque_ids JSONB",
    "ALTER TABLE evidencias_recarga ADD COLUMN IF NOT EXISTS etiqueta VARCHAR(60)",
    "ALTER TABLE evidencias_recarga ADD COLUMN IF NOT EXISTS esperado VARCHAR(40)",
    "ALTER TABLE evidencias_recarga ADD COLUMN IF NOT EXISTS texto_ia VARCHAR(60)",
    "ALTER TABLE evidencias_recarga ADD COLUMN IF NOT EXISTS coincide BOOLEAN",
]

with engine.begin() as conn:
    for s in SENTENCIAS:
        conn.execute(text(s))
        print("OK ", s)

print("Migración Fase 5a completa.")
