"""Tabla uso_ia: registro de cada llamada a la IA y lo que costó.

Se creó porque el crédito de la API se agotó sin que nadie pudiera decir cuántas
consultas se habían hecho ni en qué se fueron. Idempotente.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, engine  # noqa: E402
from app.models import UsoIA  # noqa: E402,F401  — registra la tabla en el metadata

Base.metadata.create_all(engine, tables=[UsoIA.__table__])
print("OK  tabla uso_ia lista")
