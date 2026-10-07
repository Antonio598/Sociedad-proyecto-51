"""Renombra 'ticket' -> 'comprobante' en datos YA guardados (idempotente):
- EvidenciaRecarga.tipo: 'ticket' -> 'comprobante'
- Anomalia.tipo: 'ticket_inflado' -> 'comprobante_inflado'

Acompaña al renombrado de terminología en el código (2026-08-17). Correr una vez:
  PYTHONPATH=backend  backend/.venv/Scripts/python.exe backend/scripts/migrate_ticket_comprobante.py
"""
from sqlalchemy import update

from app.db import SessionLocal
from app.models import Anomalia, EvidenciaRecarga

db = SessionLocal()
try:
    ev = db.execute(
        update(EvidenciaRecarga)
        .where(EvidenciaRecarga.tipo == "ticket")
        .values(tipo="comprobante")
    ).rowcount
    an = db.execute(
        update(Anomalia)
        .where(Anomalia.tipo == "ticket_inflado")
        .values(tipo="comprobante_inflado")
    ).rowcount
    db.commit()
    print(f"evidencias 'ticket' -> 'comprobante': {ev}")
    print(f"anomalias 'ticket_inflado' -> 'comprobante_inflado': {an}")
finally:
    db.close()
