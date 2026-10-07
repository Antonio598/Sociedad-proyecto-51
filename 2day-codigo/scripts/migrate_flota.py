"""Migración: renombra el tipo de unidad THORTON->CAMION, agrega las columnas del
catálogo de flotilla a `unidades` y crea la tabla `remolques`. Idempotente.

Uso (desde backend/, venv activado):
    python -m scripts.migrate_flota
"""

from sqlalchemy import text

from app.db import Base, engine
from app import models  # noqa: F401 — registra Remolque/Usuario

RENAME_ENUM = """
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid
    WHERE t.typname = 'tipounidad' AND e.enumlabel = 'THORTON'
  ) THEN
    ALTER TYPE tipounidad RENAME VALUE 'THORTON' TO 'CAMION';
  END IF;
END $$;
"""

COLUMNAS = [
    "ADD COLUMN IF NOT EXISTS serie VARCHAR(40)",
    "ADD COLUMN IF NOT EXISTS motor VARCHAR(40)",
    "ADD COLUMN IF NOT EXISTS motor_cc VARCHAR(15)",
    "ADD COLUMN IF NOT EXISTS placa VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS placas_nuevas VARCHAR(20)",
    "ADD COLUMN IF NOT EXISTS anio INTEGER",
    "ADD COLUMN IF NOT EXISTS marca VARCHAR(40)",
    "ADD COLUMN IF NOT EXISTS descripcion TEXT",
    "ADD COLUMN IF NOT EXISTS operador_asignado VARCHAR(120)",
    "ADD COLUMN IF NOT EXISTS usa_remolque BOOLEAN NOT NULL DEFAULT TRUE",
    "ADD COLUMN IF NOT EXISTS rendimiento_objetivo DOUBLE PRECISION",
    "ADD COLUMN IF NOT EXISTS pct_tolerancia DOUBLE PRECISION",
]


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text(RENAME_ENUM))
        for col in COLUMNAS:
            conn.execute(text(f"ALTER TABLE unidades {col}"))
        # usa_remolque coherente con el tipo (TRACTO engancha; CAMION lleva termo pegado)
        conn.execute(text("UPDATE unidades SET usa_remolque = (tipo::text = 'TRACTO')"))
    # Crea tablas faltantes (remolques, usuarios) sin tocar las existentes
    Base.metadata.create_all(engine)
    print("Migración aplicada: enum THORTON->CAMION, columnas de catálogo y tabla remolques.")


if __name__ == "__main__":
    main()
