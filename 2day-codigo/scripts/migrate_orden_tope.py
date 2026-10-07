"""Agrega a `ordenes_despacho` el tope que regía cuando se autorizó cada orden.

POR QUÉ HACE FALTA. `rendimiento.py` dice desde el primer día que el techo del 2% «nace
avisando y no bloqueando: quien autoriza puede pasarse, y el registro de cuántas veces hace
falta es lo que después dice qué unidades tienen mal el rendimiento». Ese registro NO EXISTÍA:
era un `log.warning` de un servidor que corre sin --reload y que rota con el archivo. La
pregunta para la que se diseñó el 2% —cuántas veces se pasó y en qué unidades— no tenía con
qué contestarse.

NO SE PUEDE RECONSTRUIR DESPUÉS, y por eso hace falta la columna y no una consulta. El tope
sale del ÚLTIMO escáner del motor de esa unidad, y cada escaneo nuevo lo mueve: recalcularlo
sobre una orden de hace un mes inventaría un exceso que quien firmó nunca vio.

LAS FILAS VIEJAS SE QUEDAN EN NULO a propósito. Rellenarlas con el tope de hoy sería
exactamente ese invento. Nulo aquí significa «esta orden es anterior al registro», que es
distinto de «no había tope» —el caso de las 27 unidades activas sin escáner, que también
quedará en nulo pero con `tope_motivo` explicando por qué—.

Idempotente: se puede correr las veces que haga falta.
"""
import sys
from pathlib import Path

# La consola de Windows va en cp1252 y se come los acentos del informe.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

COLUMNAS = [
    ("tope_litros", "DOUBLE PRECISION", "el techo que regía, ya con el +2%"),
    ("tope_estimado", "DOUBLE PRECISION", "los litros estimados del viaje, la base del techo"),
    ("tope_motivo", "TEXT", "de dónde salía: km, km/l y qué escáner"),
]

with engine.begin() as conn:
    for nombre, tipo, para_que in COLUMNAS:
        conn.execute(text(
            f"ALTER TABLE ordenes_despacho ADD COLUMN IF NOT EXISTS {nombre} {tipo}"))
        print(f"OK  ordenes_despacho.{nombre:14} — {para_que}")

# ── se dice cuántas órdenes quedan fuera del registro, que es dato, no ruido ──
with engine.begin() as conn:
    total = conn.execute(text("SELECT count(*) FROM ordenes_despacho")).scalar()
    sin = conn.execute(text(
        "SELECT count(*) FROM ordenes_despacho WHERE tope_litros IS NULL")).scalar()

print()
print(f"    {total} órdenes en la tabla; {sin} sin tope registrado.")
if sin:
    print("    Esas son anteriores al registro y se quedan así: rellenarlas con el tope de")
    print("    hoy inventaría un exceso que quien autorizó nunca vio. De aquí en adelante")
    print("    todas nacen con él.")
