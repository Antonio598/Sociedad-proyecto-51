"""Secuencia de Postgres para el folio de las órdenes de despacho.

El folio es la llave de conciliación con las facturas; no puede reutilizarse ni siquiera
tras borrar una orden. Una secuencia lo garantiza por construcción. Arranca por encima del
folio más alto que ya exista, para no chocar con órdenes previas. Idempotente.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import OrdenDespacho

with SessionLocal() as s:
    folios = s.execute(select(OrdenDespacho.folio)).scalars().all()
mx = 0
for f in folios:
    m = re.match(r"OD-(\d+)$", f or "")
    if m:
        mx = max(mx, int(m.group(1)))

with engine.begin() as conn:
    conn.execute(text("CREATE SEQUENCE IF NOT EXISTS folio_orden_seq"))
    # setval con is_called=true -> el próximo nextval devuelve mx+1 (o 1 si no había ninguno)
    conn.execute(text("SELECT setval('folio_orden_seq', :v, true)"), {"v": max(mx, 1)})
    print(f"OK  secuencia folio_orden_seq arrancada en {max(mx, 1)} "
          f"(próximo folio: OD-{max(mx, 1) + (1 if mx else 0):06d})")

print("Migración de la secuencia de folio completa.")
