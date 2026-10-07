"""Crea todas las tablas en la base de datos `combustible`.

Uso (desde la carpeta backend/, con el venv activado):
    python -m scripts.init_db
"""

from app.db import Base, engine
from app import models  # noqa: F401 — registra los modelos en Base.metadata


def main() -> None:
    Base.metadata.create_all(engine)
    print("Tablas creadas/verificadas:")
    for tabla in Base.metadata.sorted_tables:
        print(f"  - {tabla.name}")


if __name__ == "__main__":
    main()
