"""Columnas que pide el formato NUEVO de bitácora del cliente (30 columnas).

  viajes.remolque_thermo  -> columna THERMO (económico del remolque, p.ej. '531834')
  viajes.horas_termo      -> columna HORAS FINALES (horómetro del equipo de frío)
  escaneos_motor.analisis -> columna ANALISIS GENERAL ESCANER (texto redactado por la IA)

Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

CAMBIOS = [
    ("viajes", "remolque_thermo", "VARCHAR(20)"),
    ("viajes", "horas_termo", "DOUBLE PRECISION"),
    ("escaneos_motor", "analisis", "TEXT"),
]

with engine.begin() as conn:
    for tabla, col, tipo in CAMBIOS:
        conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {col} {tipo}"))
        print(f"OK  {tabla}.{col} {tipo}")

print("Migración del formato nuevo de bitácora completa.")
