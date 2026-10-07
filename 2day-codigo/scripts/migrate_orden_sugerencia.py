"""Añade a `ordenes_despacho` lo que PROPUSO la IA, junto a lo que decidió la persona.

POR QUÉ AQUÍ Y NO EN LA BITÁCORA: el dato tiene que poder LEERSE al abrir la solicitud —para
decir «la IA propuso 280 y se autorizaron 370»—, no sólo auditarse. La orden ya se consulta
en esa misma pantalla para sacar folio y litros, así que estas dos columnas viajan en la fila
que de todos modos se está leyendo: coste marginal cero. `RegistroActividad` exigiría una
consulta nueva, sin índice por solicitud, y sólo para pintar una frase.

POR QUÉ NO REUSAR `litros_autorizados`: es exactamente el patrón que ya funciona en la
evidencia, donde `valor_ia` se conserva intacto y `valor_final` guarda la decisión. Un solo
campo no puede contestar «¿le hizo caso?», que es la pregunta que hace útil el registro.

  litros_sugeridos    DOUBLE PRECISION NULL   -- lo que propuso la IA, jamás se sobrescribe
  sugerencia_motivo   TEXT NULL               -- en palabras, de dónde salió el número

Es aditiva: no toca ninguna fila ni ningún dato. La única orden que existe (OD-BM5U3YDS)
queda con las dos en NULL, que es lo correcto: nadie le propuso nada, se autorizó a ciegas.

Uso:
    python -m scripts.migrate_orden_sugerencia
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from app.db import SessionLocal

COLUMNAS = [
    ("litros_sugeridos", "DOUBLE PRECISION"),
    ("sugerencia_motivo", "TEXT"),
]


def main() -> None:
    db = SessionLocal()
    try:
        existentes = {r[0] for r in db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ordenes_despacho'"))}
        print("=" * 74)
        print("ordenes_despacho · lo que propuso la IA frente a lo que decidió la persona")
        print("-" * 74)
        nuevas = 0
        for col, tipo in COLUMNAS:
            if col in existentes:
                print(f"  {col:20} ya existe, no se toca")
                continue
            db.execute(text(f"ALTER TABLE ordenes_despacho ADD COLUMN {col} {tipo}"))
            print(f"  {col:20} creada  ({tipo})")
            nuevas += 1
        db.commit()

        n = db.execute(text("SELECT count(*) FROM ordenes_despacho")).scalar()
        print("-" * 74)
        print(f"{nuevas} columna(s) nueva(s). {n} orden(es) existente(s), ninguna modificada.")
        if nuevas:
            print("\nRevertir:")
            print("  ALTER TABLE ordenes_despacho "
                  + ", ".join(f"DROP COLUMN {c}" for c, _ in COLUMNAS) + ";")
    finally:
        db.close()


if __name__ == "__main__":
    main()
