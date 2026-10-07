"""Agrega a `asignaciones_viaje` las coordenadas opcionales (lat/lng) de origen y destino,
elegidas en el mapa (Leaflet/OSM). El texto de origen/destino sigue siendo la etiqueta
legible; estas columnas son un extra nullable. Idempotente."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

COLS = ("origen_lat", "origen_lng", "destino_lat", "destino_lng")

with engine.begin() as conn:
    for c in COLS:
        conn.execute(text(
            f"ALTER TABLE asignaciones_viaje ADD COLUMN IF NOT EXISTS {c} DOUBLE PRECISION"))
        print(f"OK  asignaciones_viaje.{c}")

print("Migración de coordenadas de asignación completa.")
