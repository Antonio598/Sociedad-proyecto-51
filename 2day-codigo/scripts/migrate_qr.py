"""Añade 'ESCANEADA' a la enumeración `procedencia`, para el económico leído de un QR.

POR QUÉ UN VALOR PROPIO Y NO 'MANUAL'. El campo existe para medir de dónde sale cada dato.
Un código escaneado no se teclea ni se interpreta: llega exacto. Meterlo en 'manual' sería
registrar algo falso justo en el campo que mide la confiabilidad de la captura.

OJO CON EL NOMBRE: SQLAlchemy guarda el NOMBRE del miembro del Enum, no su valor, así que
en la base los valores son 'IA_ACEPTADA' / 'MANUAL' en mayúsculas. El nuevo tiene que ser
'ESCANEADA'; en minúscula quedaría un valor que la aplicación no escribiría nunca.

CÓMO SE AÑADE. Postgres no deja QUITAR un valor de un enum, así que en vez de un
`ALTER TYPE ... ADD VALUE` —que sería irreversible— se RECREA el tipo con la lista exacta
que declara el modelo. Así la migración es idempotente de verdad: deja el tipo con esos
cuatro valores y ni uno más, corra las veces que corra. Sólo puede hacerlo mientras la
columna sea convertible; el script lo comprueba y se detiene si encontrara un valor que la
lista nueva no contempla.

Uso:
    python -m scripts.migrate_qr
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from app.db import engine
from app.models import Procedencia

QUERIDOS = [m.name for m in Procedencia]      # IA_ACEPTADA, IA_CORREGIDA, MANUAL, ESCANEADA


def valores(conn):
    return [r[0] for r in conn.execute(text(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = 'procedencia' ORDER BY e.enumsortorder"))]


with engine.begin() as conn:
    actuales = valores(conn)
    print("procedencia ANTES :", ", ".join(actuales))

    if actuales == QUERIDOS:
        print("=  ya coincide con el modelo; no se toca nada")
    else:
        # Ningún dato guardado puede quedarse fuera de la lista nueva.
        usados = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT procedencia::text FROM evidencias_recarga "
            "WHERE procedencia IS NOT NULL"))]
        huerfanos = [u for u in usados if u not in QUERIDOS]
        if huerfanos:
            raise SystemExit(
                f"ABORTA: hay filas con {huerfanos}, que la lista nueva no contempla. "
                "Decide qué hacer con esas filas antes de recrear el tipo.")

        lista = ", ".join(f"'{v}'" for v in QUERIDOS)
        conn.execute(text(f"CREATE TYPE procedencia_nuevo AS ENUM ({lista})"))
        conn.execute(text(
            "ALTER TABLE evidencias_recarga ALTER COLUMN procedencia TYPE procedencia_nuevo "
            "USING procedencia::text::procedencia_nuevo"))
        conn.execute(text("DROP TYPE procedencia"))
        conn.execute(text("ALTER TYPE procedencia_nuevo RENAME TO procedencia"))
        print(f"OK tipo recreado con {len(QUERIDOS)} valores "
              f"({len(usados)} filas convertidas sin pérdida)")

    print("procedencia DESPUÉS:", ", ".join(valores(conn)))
    n = conn.execute(text("SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar()
    print(f"tipos enum en la base: {n}   (debe seguir en 6: se recreó uno, no se añadió)")
