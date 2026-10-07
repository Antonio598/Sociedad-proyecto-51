"""Añade a `evidencias_recarga` el juicio de AUTENTICIDAD de la foto.

POR QUE DOS COLUMNAS NUEVAS Y NO REUSAR `confianza`: son dos preguntas distintas.
`confianza` dice si los números SE LEEN; esto dice si la foto es DE FIAR. El fraude que
importa —fotografiar la pantalla de otro teléfono que muestra una foto vieja del odómetro—
se lee perfecto y es falso, así que en un solo campo una de las dos preguntas se perdería.

  sospechosa       BOOLEAN NOT NULL DEFAULT false
  sospecha_motivo  TEXT NULL   -- en palabras, para quien autoriza

Es aditiva: no toca ninguna fila existente ni ningún dato. Las 6 evidencias que ya hay
quedan con sospechosa=false, que es lo correcto: nadie las juzgó.

Uso:
    python -m scripts.migrate_evidencia_sospecha
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from app.db import SessionLocal

COLUMNAS = [
    ("sospechosa", "BOOLEAN NOT NULL DEFAULT false"),
    ("sospecha_motivo", "TEXT"),
]


def main() -> None:
    db = SessionLocal()
    try:
        existentes = {r[0] for r in db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'evidencias_recarga'"))}
        print("=" * 74)
        print("evidencias_recarga · juicio de autenticidad de la foto")
        print("-" * 74)
        nuevas = 0
        for col, tipo in COLUMNAS:
            if col in existentes:
                print(f"  {col:18} ya existe, no se toca")
                continue
            db.execute(text(f"ALTER TABLE evidencias_recarga ADD COLUMN {col} {tipo}"))
            print(f"  {col:18} creada  ({tipo})")
            nuevas += 1
        db.commit()

        n = db.execute(text("SELECT count(*) FROM evidencias_recarga")).scalar()
        print("-" * 74)
        print(f"{nuevas} columna(s) nueva(s). {n} evidencias existentes, ninguna modificada.")
        if nuevas:
            print("\nRevertir:")
            print("  ALTER TABLE evidencias_recarga "
                  + ", ".join(f"DROP COLUMN {c}" for c, _ in COLUMNAS) + ";")
    finally:
        db.close()


if __name__ == "__main__":
    main()
