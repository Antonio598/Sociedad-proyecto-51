"""E1 · Catálogo: alias, propuestas y procedencia. Idempotente y reversible.

Solo AGREGA: tres tablas nuevas y dos columnas opcionales. No modifica ni borra un
solo dato existente. Revertir es DROP de las tres tablas y de las dos columnas.

Al final SIEMBRA los alias desde lo que ya está en la base (clave, económico viejo,
económico nuevo y placas), que es lo que hace que 53113 y 400917 resuelvan al MISMO
remolque y ningún importador vuelva a crear un duplicado.

Uso:
    python -m scripts.migrate_catalogo
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.models import AliasEco, ImportacionPlacas, PropuestaCatalogo

with engine.begin() as conn:
    for tabla in ("unidades", "remolques"):
        conn.execute(text(
            f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS fuente_catalogo VARCHAR(16)"))
        conn.execute(text(
            f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS verificado_en TIMESTAMPTZ"))
        print(f"OK  {tabla}.fuente_catalogo / .verificado_en")

for modelo in (AliasEco, ImportacionPlacas, PropuestaCatalogo):
    modelo.__table__.create(engine, checkfirst=True)
    print(f"OK  tabla {modelo.__tablename__}")

# ── siembra de alias desde el catálogo actual ───────────────────────────────
from app.catalogo import sembrar_alias  # noqa: E402  (después de crear las tablas)

with SessionLocal() as db:
    nuevos = sembrar_alias(db)
    db.commit()
    total = db.query(AliasEco).count()
print(f"OK  alias sembrados: {nuevos} nuevos · {total} en total")
print("\nMigración de catálogo completa. Revertir:")
print("  DROP TABLE propuestas_catalogo, importaciones_placas, alias_eco;")
print("  ALTER TABLE unidades  DROP COLUMN fuente_catalogo, DROP COLUMN verificado_en;")
print("  ALTER TABLE remolques DROP COLUMN fuente_catalogo, DROP COLUMN verificado_en;")
