"""Relación operador<->flota y marca de provisional.
- operadores.provisional (bool): operador auto-creado del chat que espera resolución.
- unidades.operador_asignado_id (FK): operador TITULAR real de la unidad.
Además siembra operador_asignado_id casando el texto operador_asignado con el catálogo.
Idempotente."""
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db import engine, SessionLocal
from app.models import Operador, Unidad

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE operadores ADD COLUMN IF NOT EXISTS provisional BOOLEAN DEFAULT FALSE"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_operadores_provisional ON operadores (provisional)"))
    conn.execute(text("ALTER TABLE unidades ADD COLUMN IF NOT EXISTS operador_asignado_id INTEGER REFERENCES operadores(id)"))
    print("OK  columnas provisional + operador_asignado_id")


def toks(nombre):
    s = unicodedata.normalize("NFD", (nombre or "").upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = "".join(c if c.isalnum() or c == " " else " " for c in s)
    stop = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}
    return {t for t in s.split() if len(t) >= 3 and t not in stop}

# Siembra: casa el texto operador_asignado de cada unidad con un operador del catálogo
s = SessionLocal()
try:
    ops = [(o, toks(o.nombre)) for o in s.query(Operador).all()]
    casados = 0
    for u in s.query(Unidad).all():
        if u.operador_asignado_id is not None or not u.operador_asignado:
            continue
        t = toks(u.operador_asignado)
        if len(t) < 2:
            continue
        cand = [o for (o, ot) in ops if ot and (t <= ot or ot <= t)]
        nombres = {o.nombre for o in cand}
        if len(nombres) == 1:              # match inequívoco
            u.operador_asignado_id = cand[0].id
            casados += 1
    s.commit()
    print(f"OK  {casados} unidades vinculadas a su operador titular por el texto del Excel")
finally:
    s.close()

print("Migración de flota-operador completa.")
