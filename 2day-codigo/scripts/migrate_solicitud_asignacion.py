"""Agrega a `solicitudes_recarga` la columna `asignacion_id`: a qué viaje pertenece.

POR QUÉ HACE FALTA. La solicitud no guardaba de qué viaje salía. El contexto y el tope
resolvían el viaje como «la asignación ACTIVA de ese operador AHORA», y levantar un viaje
nuevo finaliza el anterior sin mirar si algo cuelga de él. Consecuencia: una solicitud
ENVIADA con las fotos y el odómetro del viaje A pasaba a juzgarse, sin que nada lo dijera,
con el destino, los kilómetros y el TOPE del viaje B. El coordinador fijaba litros contra un
viaje que esa solicitud nunca hizo.

`viaje_id` no servía: apunta a `Viaje`, que es historia CONGELADA y no existe como fila hasta
mucho después; está SIEMPRE en nulo mientras la solicitud está viva.

EL RELLENO DE LAS FILAS VIEJAS ES EXACTO, no aproximado: `asignaciones_viaje` guarda
`creada_en` y `finalizada_en`, así que para cada solicitud se busca la asignación de SU
operador que estaba abierta en el momento en que la solicitud nació. Las que no encajen en
ninguna ventana se quedan en nulo, y ahí el código sigue cayendo al comportamiento de antes
—la asignación activa— que es lo único que se puede saber de ellas.

Idempotente: se puede correr las veces que haga falta.
"""
import sys
from pathlib import Path

# La consola de Windows va en cp1252 y se come los acentos del informe.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE solicitudes_recarga ADD COLUMN IF NOT EXISTS asignacion_id INTEGER"))
    existe = conn.execute(text(
        "SELECT 1 FROM pg_constraint WHERE conname = 'solicitudes_recarga_asignacion_fk'"
    )).scalar()
    if not existe:
        conn.execute(text(
            "ALTER TABLE solicitudes_recarga ADD CONSTRAINT solicitudes_recarga_asignacion_fk "
            "FOREIGN KEY (asignacion_id) REFERENCES asignaciones_viaje(id)"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_solicitudes_asignacion "
        "ON solicitudes_recarga (asignacion_id)"))
    print("OK  solicitudes_recarga.asignacion_id")

# ── el relleno: la asignación que estaba abierta cuando nació la solicitud ────
# Se toma la MÁS RECIENTE de las que encajan, por si dos ventanas se solapan (no debería
# pasar, pero las solapadas son precisamente el defecto que esto viene a cerrar).
with engine.begin() as conn:
    n = conn.execute(text("""
        UPDATE solicitudes_recarga s
           SET asignacion_id = (
                 SELECT a.id FROM asignaciones_viaje a
                  WHERE a.operador_id = s.operador_id
                    AND a.creada_en <= s.creada_en
                    AND (a.finalizada_en IS NULL OR a.finalizada_en >= s.creada_en)
                  ORDER BY a.creada_en DESC
                  LIMIT 1)
         WHERE s.asignacion_id IS NULL
           AND s.operador_id IS NOT NULL
           AND s.creada_en IS NOT NULL
    """)).rowcount

with engine.begin() as conn:
    total = conn.execute(text("SELECT count(*) FROM solicitudes_recarga")).scalar()
    con = conn.execute(text(
        "SELECT count(*) FROM solicitudes_recarga WHERE asignacion_id IS NOT NULL")).scalar()

print()
print(f"    {total} solicitudes · {con} con su viaje identificado · {total - con} sin él.")
if total - con:
    print("    Las que quedan sin viaje son anteriores a que existieran las asignaciones, o")
    print("    nacieron fuera de toda ventana. Para ésas el código sigue cayendo a la")
    print("    asignación activa, que es lo único que se puede saber.")
