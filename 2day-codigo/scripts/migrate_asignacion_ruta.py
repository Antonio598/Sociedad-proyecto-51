"""Agrega a `asignaciones_viaje` los campos del mapa/ruta del viaje:
- km_estimado (double): km de la ruta OSRM (o línea recta) al crear.
- km_modificado (bool) + km_modificado_por_id (FK usuarios) + km_modificado_en (timestamptz):
  auditoría de cuándo el coordinador ajusta los km a mano.
- es_retorno (bool): tipo de viaje (retorno a base vs ida).
Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

DDL = [
    "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS km_estimado DOUBLE PRECISION",
    "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS km_modificado BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS km_modificado_por_id INTEGER REFERENCES usuarios(id)",
    "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS km_modificado_en TIMESTAMPTZ",
    "ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS es_retorno BOOLEAN NOT NULL DEFAULT FALSE",
]

with engine.begin() as conn:
    for ddl in DDL:
        conn.execute(text(ddl))
        print("OK ", ddl.split("EXISTS ")[1].split(" ")[0])

print("Migración de ruta/retorno de asignación completa.")
