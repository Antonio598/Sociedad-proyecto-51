"""Fase A de la migración a plataforma web: solicitud de recarga y orden de despacho.

Crea las tablas nuevas y la columna usuarios.operador_id. Es ADITIVA: no toca ninguna
tabla existente ni ningún dato. Los cuatro perfiles y todo el ciclo de vida se montan
sobre el modelo actual (unidades, viajes, operadores) sin alterarlo.

Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app import models  # noqa: F401 — registra los modelos
from app.db import Base, engine
from app.models import (  # noqa: E402
    EvidenciaRecarga, OrdenDespacho, SolicitudRecarga, TransicionSolicitud,
)

TABLAS = [SolicitudRecarga, EvidenciaRecarga, OrdenDespacho, TransicionSolicitud]

# create_all solo agrega tablas que faltan; nunca borra ni recrea las existentes.
Base.metadata.create_all(engine, tables=[t.__table__ for t in TABLAS])
for t in TABLAS:
    print(f"OK  tabla {t.__tablename__}")

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS operador_id INTEGER "
        "REFERENCES operadores(id)"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_usuarios_operador_id ON usuarios(operador_id)"))
    print("OK  usuarios.operador_id + índice")

print("Migración de Fase A completa.")
