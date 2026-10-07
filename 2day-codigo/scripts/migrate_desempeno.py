"""Sección Desempeño: instrumentación de actividad y actores que faltaban.

Idempotente. Agrega:
  - anomalias.resuelto_por_id      (quién resolvió la anomalía)
  - asignaciones_viaje.finalizada_por_id  (quién cerró/canceló)
  - asignaciones_viaje.visto_en    (cuándo el operador vio su asignación → latencia de "notificación")
  - tabla registro_actividad       (bitácora general de acciones por persona/rol)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine
from app.models import RegistroActividad

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE anomalias ADD COLUMN IF NOT EXISTS resuelto_por_id INTEGER REFERENCES usuarios(id)"))
    print("OK  anomalias.resuelto_por_id")
    conn.execute(text(
        "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS finalizada_por_id INTEGER REFERENCES usuarios(id)"))
    print("OK  asignaciones_viaje.finalizada_por_id")
    conn.execute(text(
        "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS visto_en TIMESTAMPTZ"))
    print("OK  asignaciones_viaje.visto_en")

# La tabla se crea con todas sus columnas + índices tal cual el modelo (checkfirst = idempotente).
RegistroActividad.__table__.create(engine, checkfirst=True)
print("OK  tabla registro_actividad")

print("Migración de Desempeño completa.")
