"""Expediente de la licencia: tipo, expedición, CURP y el documento escaneado.

Idempotente (ADD COLUMN IF NOT EXISTS). Agrega a `operadores`:
  - licencia_tipo        FEDERAL/ESTATAL + categoría; hoy vive dentro del texto del folio
  - licencia_expedida    fecha de expedición
  - curp                 identidad inequívoca (el padrón tiene homónimos reales)
  - licencia_doc         nombre del archivo bajo media_dir/licencias (el PDF/JPG vive en disco)
  - licencia_doc_mime / licencia_doc_nombre / licencia_doc_en

No toca ni un dato existente: `licencia` y `licencia_vence` se quedan como están. Los 265
operadores tienen HOY los seis campos vacíos, así que no hay nada que rellenar ni migrar;
se llenan cuando alguien sube la licencia.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.config import settings
from app.db import engine

COLUMNAS = [
    ("licencia_tipo", "VARCHAR(30)"),
    ("licencia_expedida", "DATE"),
    ("curp", "VARCHAR(18)"),
    ("licencia_doc", "VARCHAR(120)"),
    ("licencia_doc_mime", "VARCHAR(60)"),
    ("licencia_doc_nombre", "VARCHAR(160)"),
    ("licencia_doc_en", "TIMESTAMPTZ"),
]

with engine.begin() as conn:
    antes = conn.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name='operadores' AND column_name = ANY(:c)"),
        {"c": [c for c, _ in COLUMNAS]}).scalar_one()
    for col, tipo in COLUMNAS:
        conn.execute(text(f"ALTER TABLE operadores ADD COLUMN IF NOT EXISTS {col} {tipo}"))
        print(f"OK  operadores.{col}")
    print(f"    ({antes} de {len(COLUMNAS)} ya existían)")

# El documento vive en disco, no en la base. Se crea aquí para que el primer alta no
# dependa de que el proceso tenga permiso de crear carpetas en caliente.
d = settings.media_dir / "licencias"
d.mkdir(parents=True, exist_ok=True)
print(f"OK  carpeta {d}")

with engine.connect() as conn:
    n, con_lic, con_doc = conn.execute(text(
        "SELECT count(*), count(licencia), count(licencia_doc) FROM operadores")).one()
print(f"\n{n} operadores · {con_lic} con folio de licencia · {con_doc} con documento.")
print("Migración de licencia completa.")
