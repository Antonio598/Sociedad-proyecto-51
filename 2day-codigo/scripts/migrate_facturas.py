"""Fase 3: crea la tabla 'facturas' (CFDI) y agrega ordenes_despacho.factura_id.

Una factura del proveedor ampara VARIAS órdenes del día (N→1). La tabla se crea con el
esquema exacto del ORM (checkfirst), y luego se agrega la columna/índice en ordenes_despacho.
Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine
from app.models import Factura  # noqa: F401 — registra la tabla en el metadata

# 1) tabla facturas con el esquema del ORM (si falta)
Factura.__table__.create(engine, checkfirst=True)
print("OK  tabla facturas")

# 2) columna factura_id (FK) + índice en ordenes_despacho
with engine.begin() as cx:
    cx.execute(text(
        "ALTER TABLE ordenes_despacho ADD COLUMN IF NOT EXISTS factura_id "
        "INTEGER REFERENCES facturas(id)"))
    cx.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ordenes_despacho_factura_id "
        "ON ordenes_despacho (factura_id)"))
print("OK  ordenes_despacho.factura_id")
print("Migración Fase 3 (facturas) completa.")
