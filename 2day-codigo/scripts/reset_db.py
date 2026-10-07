"""Recrea todas las tablas del backend (DROP + CREATE).

⚠️ BORRA TODOS LOS DATOS. Úsalo solo en desarrollo, cuando el esquema cambió y aún no hay
nada que conservar.

Pide confirmación escribiendo el nombre de la base, y antes muestra cuántos registros se
van a perder. Se ejecuta contra lo que diga DATABASE_URL, y esa variable puede apuntar a
producción sin que se note: antes borraba el esquema completo sin preguntar nada.

Uso (desde backend/, con el venv activado):
    python -m scripts.reset_db
    python -m scripts.reset_db --forzar     # sin preguntar (para automatizar)
"""

import argparse
import sys

from sqlalchemy import MetaData, func, select

from app import models  # noqa: F401 — registra los modelos
from app.db import Base, SessionLocal, engine


def _resumen() -> list[tuple[str, int]]:
    """Qué se va a perder, contado de verdad."""
    filas = []
    with SessionLocal() as s:
        for tabla in Base.metadata.sorted_tables:
            try:
                n = s.scalar(select(func.count()).select_from(tabla)) or 0
            except Exception:
                n = 0          # la tabla aún no existe
            if n:
                filas.append((tabla.name, n))
    return filas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--forzar", action="store_true", help="no preguntar")
    a = ap.parse_args()

    url = engine.url
    nombre = url.database or "?"
    print(f"Base de datos: {nombre}  (host {url.host}:{url.port})")

    datos = _resumen()
    if datos:
        print("\nSe van a BORRAR:")
        for t, n in sorted(datos, key=lambda x: -x[1]):
            print(f"  {n:>8,}  {t}")
        print(f"\n  TOTAL: {sum(n for _, n in datos):,} registros")
    else:
        print("\n(La base está vacía.)")

    if not a.forzar and datos:
        print("\nEsto NO se puede deshacer. Escribe el nombre de la base para confirmar.")
        try:
            if input(f"  Nombre de la base [{nombre}]: ").strip() != nombre:
                sys.exit("Cancelado: el nombre no coincide.")
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nCancelado.")

    # Reflejar y borrar TODO lo que exista (incluye tablas de esquemas viejos que ya no
    # están en los modelos), en orden de dependencias.
    existente = MetaData()
    existente.reflect(bind=engine)
    existente.drop_all(bind=engine)

    Base.metadata.create_all(engine)
    print("\nEsquema recreado. Tablas:")
    for tabla in Base.metadata.sorted_tables:
        print(f"  - {tabla.name}")


if __name__ == "__main__":
    main()
