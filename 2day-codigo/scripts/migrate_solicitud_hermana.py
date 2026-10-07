"""Agrega a `solicitudes_recarga` la columna `hermana_id`: el vínculo entre las dos
solicitudes que nacen cuando el operador declara que carga motor Y termo del mismo económico.

POR QUÉ HACE FALTA. El parentesco existía sólo como prosa dentro de la nota de la transición
("Creada junto con la solicitud 123"), así que el servidor no podía razonar sobre él. La
consecuencia era concreta: la hermana del termo exigía su propia evidencia de económico, el
operador escanea el sticker UNA vez, esa evidencia se guarda en la del motor, y la del termo
quedaba imposible de enviar — con un 400 determinista, no intermitente.

El vínculo es recíproco: cada una apunta a la otra. Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE solicitudes_recarga ADD COLUMN IF NOT EXISTS hermana_id INTEGER"))
    # La clave foránea va aparte y tolerante: si ya existe, no se vuelve a crear.
    existe = conn.execute(text(
        "SELECT 1 FROM pg_constraint WHERE conname = 'solicitudes_recarga_hermana_fk'"
    )).scalar()
    if not existe:
        conn.execute(text(
            "ALTER TABLE solicitudes_recarga ADD CONSTRAINT solicitudes_recarga_hermana_fk "
            "FOREIGN KEY (hermana_id) REFERENCES solicitudes_recarga(id)"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_solicitudes_hermana ON solicitudes_recarga (hermana_id)"))
    print("OK  solicitudes_recarga.hermana_id")

# ── se rescata el parentesco de las que ya existen, leyendo la nota ───────────
# Sólo para las filas viejas: de aquí en adelante lo escribe el propio endpoint.
with engine.begin() as conn:
    filas = conn.execute(text(
        "SELECT solicitud_id, nota FROM transiciones_solicitud "
        "WHERE nota LIKE 'Creada junto con la solicitud %'")).all()
    n = 0
    for hermana_id, nota in filas:
        try:
            principal_id = int(nota.split("solicitud ")[1].split(":")[0].strip())
        except (IndexError, ValueError):
            continue
        r = conn.execute(text(
            "UPDATE solicitudes_recarga SET hermana_id = :p WHERE id = :h AND hermana_id IS NULL"),
            {"p": principal_id, "h": hermana_id})
        n += r.rowcount
        conn.execute(text(
            "UPDATE solicitudes_recarga SET hermana_id = :h WHERE id = :p AND hermana_id IS NULL"),
            {"h": hermana_id, "p": principal_id})
    print(f"OK  parentesco rescatado de {n} solicitudes antiguas")

print("Migración de hermana_id completa.")
