"""Etiquetas físicas (hologramas): dos tablas nuevas. Idempotente y reversible.

Solo AGREGA. No modifica ni borra un solo dato existente. Revertir es DROP de las dos.

Uso:
    python -m scripts.migrate_etiquetas
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.db import engine
from app.models import EtiquetaActivo, ImportacionEtiquetas

for modelo in (EtiquetaActivo, ImportacionEtiquetas):
    modelo.__table__.create(engine, checkfirst=True)
    print(f"OK  tabla {modelo.__tablename__}")

print("\nMigración de etiquetas completa. Revertir:")
print("  DROP TABLE etiquetas_activo, importaciones_etiquetas;")
