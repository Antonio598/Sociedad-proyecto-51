"""Qué hacer cuando un reporte se borra o se corrige en el grupo.

DECISIÓN DE DISEÑO: un viaje retractado NO se borra.

Borrarlo sería lo intuitivo y es lo peor que se puede hacer aquí. Tres razones:

  1. Un borrado en WhatsApp puede ser un dedazo del coordinador, pero también puede ser
     alguien tapando una carga que no cuadra. Si el sistema borra el viaje, el rastro de
     lo que se reportó ANTES desaparece — justo la evidencia que hace útil a la auditoría.
  2. El borrado llega sin explicación: WhatsApp solo dice "este mensaje se eliminó".
     El sistema no puede saber si el dato estaba mal o si alguien se arrepintió.
  3. Un viaje ya validado puede tener anomalías, conciliación con el escáner del motor y
     cálculos derivados colgando de él. Borrarlo en cascada es destructivo e irreversible.

Por eso: se MARCA como retractado (queda fuera de reportes, indicadores y bitácora), se
levanta una anomalía para que una persona lo revise, y se avisa al grupo. La información
sigue ahí; lo que cambia es que deja de contar hasta que alguien decida.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Anomalia, EventoWhatsapp, Viaje

log = logging.getLogger("combustible.retractacion")


def viajes_del_mensaje(session: Session, message_id: str) -> list[Viaje]:
    """Viajes que se originaron en ese mensaje (normalmente uno; un reporte múltiple, varios)."""
    return list(session.execute(
        select(Viaje).where(Viaje.origen_message_id == message_id)
    ).scalars())


def retractar(session: Session, viaje: Viaje, motivo: str,
              recibido_en: datetime | None = None) -> bool:
    """Marca el viaje como retractado y levanta la anomalía de revisión. Idempotente.

    Si el viaje YA EXISTÍA antes de ese mensaje (por ejemplo una fila importada del Excel a
    la que el bot solo le añadió una foto), NO se retracta entero: borrar un mensaje no
    puede tumbar un registro histórico que traía datos de otra fuente. En ese caso solo se
    levanta la anomalía, para que una persona decida qué parte se retira.
    """
    if viaje.retractado_en is not None:
        return False

    preexistente = bool(
        recibido_en and viaje.creado_en
        and viaje.creado_en < recibido_en - timedelta(minutes=5))

    if not preexistente:
        viaje.retractado_en = datetime.now(timezone.utc)
        viaje.retractado_motivo = motivo[:300]
    else:
        motivo = (f"{motivo} El viaje ya existía antes de ese mensaje (traía datos de otra "
                  f"fuente), así que NO se retiró completo: hay que revisar qué parte "
                  f"corresponde al reporte borrado.")
        log.warning("Viaje %s NO se retracta entero: es anterior al mensaje borrado", viaje.id)

    ya = session.execute(
        select(Anomalia).where(Anomalia.viaje_id == viaje.id,
                               Anomalia.tipo == "reporte_retractado")
    ).scalars().first()
    if ya is None:
        session.add(Anomalia(
            viaje_id=viaje.id,
            tipo="reporte_retractado",
            descripcion=(f"{motivo} El viaje se conserva pero queda FUERA de reportes e "
                         f"indicadores hasta que alguien confirme si el dato era válido."),
        ))
    return True


def por_borrado(message_id: str, participante: str | None = None) -> dict:
    """Alguien borró un mensaje en el grupo: retracta lo que ese mensaje haya producido."""
    quien = (participante or "").split("@")[0] or "alguien"
    resumen = {"message_id": message_id, "viajes": [], "evento": None}

    with SessionLocal() as session:
        ev = session.execute(
            select(EventoWhatsapp).where(EventoWhatsapp.message_id == message_id)
        ).scalars().first()
        if ev is not None:
            resumen["evento"] = ev.id
            # Si aún no se procesaba, se cancela: no tiene caso registrar algo ya borrado.
            if ev.estado_proceso == "pendiente":
                ev.estado_proceso = "fallido"
                ev.error = "Mensaje borrado en el grupo antes de poder procesarse"

        recibido = ev.recibido_en if ev is not None else None
        viajes = viajes_del_mensaje(session, message_id)
        for v in viajes:
            if retractar(session, v, f"El reporte fue borrado del grupo por {quien}.", recibido):
                resumen["viajes"].append(v.id)
        session.commit()

    if resumen["viajes"]:
        log.warning("Borrado de %s: %s viaje(s) marcados como retractados %s",
                    message_id, len(resumen["viajes"]), resumen["viajes"])
        _avisar(resumen["viajes"], quien)
    elif resumen["evento"] is not None:
        log.info("Borrado de %s: el mensaje no había producido viajes", message_id)
    else:
        # Borrado de un mensaje que nunca vimos (anterior a la conexión del bot, o de otro
        # grupo). No es un error: simplemente no hay nada que retractar.
        log.debug("Borrado de %s: mensaje desconocido, nada que hacer", message_id)
    return resumen


def _avisar(viaje_ids: list[int], quien: str) -> None:
    """Avisa al grupo. El silencio sería peor: quien borró creería que no pasó nada."""
    try:
        from . import notify
        n = len(viaje_ids)
        notify.enviar_texto(
            f"⚠️ Se borró un reporte del grupo ({quien}).\n"
            f"{'El registro correspondiente quedó marcado' if n == 1 else f'Los {n} registros correspondientes quedaron marcados'} "
            f"como *retractado* y ya no cuenta{'' if n == 1 else 'n'} para los indicadores.\n"
            f"Si el dato era correcto, avísenme para reactivarlo; si estaba mal, "
            f"manden el reporte corregido."
        )
    except Exception:
        log.exception("No se pudo avisar al grupo de la retractación")
