import base64
import logging

import httpx
from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import EventoWhatsapp

log = logging.getLogger("combustible.webhook")

# Envoltorios de Baileys que anidan el mensaje real (mensajes temporales,
# view-once, documentos con caption, ediciones). Muy comunes en grupos con
# mensajes temporales activados.
_WRAPPERS = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "documentWithCaptionMessage",
    "editedMessage",
)

_TIPOS_MEDIA = ("imageMessage", "videoMessage", "documentMessage", "audioMessage")

_EXTENSIONES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
    "audio/ogg": ".ogg",
}


def _desenvolver(message: dict) -> dict:
    """Desenvuelve recursivamente los wrappers de Baileys hasta el mensaje real."""
    desenvuelto = True
    while desenvuelto:
        desenvuelto = False
        for w in _WRAPPERS:
            if w in message:
                message = (message[w] or {}).get("message") or {}
                desenvuelto = True
    return message


def _extraer_texto(message: dict) -> tuple[str, str | None]:
    """Devuelve (tipo_mensaje, texto o caption) del mensaje ya desenvuelto."""
    if not message:
        return "desconocido", None
    if "conversation" in message:
        return "conversation", message["conversation"]
    if "extendedTextMessage" in message:
        return "extendedTextMessage", message["extendedTextMessage"].get("text")
    if "imageMessage" in message:
        return "imageMessage", message["imageMessage"].get("caption")
    if "videoMessage" in message:
        return "videoMessage", message["videoMessage"].get("caption")
    if "documentMessage" in message:
        return "documentMessage", message["documentMessage"].get("fileName")
    # primer tipo presente, para auditoría
    tipo = next(iter(message.keys()), "desconocido")
    return tipo, None


def _mensaje_citado(message: dict, payload: dict) -> str | None:
    """ID del mensaje al que este RESPONDE (stanzaId del citado), si es una respuesta.

    Es el dato clave para las correcciones: cuando alguien responde a su propio reporte
    con el dato corregido, esto dice EXACTAMENTE qué reporte corrige, sin tener que
    adivinarlo por el texto.
    """
    for fuente in (message or {}, ):
        for v in fuente.values():
            if isinstance(v, dict):
                sid = (v.get("contextInfo") or {}).get("stanzaId")
                if sid:
                    return sid
    return ((payload or {}).get("contextInfo") or {}).get("stanzaId")


def revocacion(message: dict) -> str | None:
    """Si el mensaje es un BORRADO ('eliminar para todos'), devuelve el id del borrado.

    WhatsApp no manda un evento aparte: manda un protocolMessage de tipo REVOKE dentro del
    mismo messages.upsert, apuntando al mensaje que se eliminó.
    """
    pm = (message or {}).get("protocolMessage") or {}
    tipo = pm.get("type")
    # El tipo llega como texto ('REVOKE') o como su número en el enum de Baileys (0).
    if tipo in ("REVOKE", 0, "0"):
        return (pm.get("key") or {}).get("id")
    return None


def _es_reenviado(message: dict) -> bool:
    """True si el mensaje viene REENVIADO (isForwarded / forwardingScore>0) en cualquiera
    de sus sub-mensajes. Un reenvío suele traer datos de otro día u otra unidad, así que no
    se auto-registra como reporte fresco."""
    for v in (message or {}).values():
        if isinstance(v, dict):
            ci = v.get("contextInfo") or {}
            if ci.get("isForwarded") or (ci.get("forwardingScore") or 0) > 0:
                return True
    return False


def _descargar_base64(key: dict) -> str | None:
    """Pide la media a Evolution API cuando no vino en el payload del webhook
    (pasa con media envuelta en ephemeralMessage/viewOnce: Evolution solo
    adjunta base64 para media de nivel superior)."""
    try:
        r = httpx.post(
            f"{settings.evolution_url}/chat/getBase64FromMediaMessage/{settings.evolution_instance}",
            headers={"apikey": settings.evolution_apikey},
            json={"message": {"key": key}, "convertToMp4": False},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("base64")
    except Exception:
        log.exception("Falló la descarga de media vía Evolution API")
        return None


def tiene_media(mensaje: dict) -> bool:
    """¿El mensaje trae foto/video/documento? (aunque no se haya podido bajar)."""
    return any(mensaje.get(k) for k in _TIPOS_MEDIA)


def _guardar_media(data: dict, mensaje: dict, message_id: str) -> str | None:
    """Guarda en disco la media del mensaje; devuelve la ruta o None."""
    mimetype = ""
    tiene_media = False
    for key in _TIPOS_MEDIA:
        media = mensaje.get(key)
        if media:
            mimetype = media.get("mimetype", "")
            tiene_media = True
            break
    if not tiene_media:
        return None

    # Con webhookBase64=true la media de nivel superior viene incrustada
    b64 = (data.get("message") or {}).get("base64")
    if not b64:
        b64 = _descargar_base64(data.get("key") or {})
    if not b64:
        log.warning("No se pudo obtener la media del mensaje %s", message_id)
        return None

    ext = _EXTENSIONES.get(mimetype.split(";")[0], ".bin")
    safe_id = "".join(c for c in message_id if c.isalnum() or c in "-_")
    path = settings.media_dir / f"{safe_id}{ext}"
    try:
        path.write_bytes(base64.b64decode(b64))
    except Exception:
        # Disco lleno, permisos, base64 corrupto… Antes la excepción salía de aquí y se
        # llevaba el mensaje ENTERO: no quedaba ni la fila del evento, así que la foto
        # desaparecía sin dejar rastro y nadie se enteraba.
        log.exception("No se pudo escribir la media del mensaje %s", message_id)
        return None
    # Sólo el nombre; se resuelve con config.ruta_media() contra media_dir.
    return path.name


def reintentar_media(evento: "EventoWhatsapp") -> str | None:
    """Vuelve a pedirle la media a Evolution para un evento que ya está guardado.

    El payload crudo se conserva, así que un fallo de descarga es recuperable: sin esto,
    el botón "Reintentar" del panel reprocesaba un evento que seguía sin foto y volvía a
    fallar igual.
    """
    data = evento.payload or {}
    mensaje = _desenvolver(data.get("message") or {})
    if not tiene_media(mensaje):
        return None
    path = _guardar_media(data, mensaje, evento.message_id)
    if path:
        log.info("Media del evento %s recuperada en el reintento", evento.id)
    return path


def procesar_messages_upsert(payload: dict) -> None:
    """Maneja el evento messages.upsert de Evolution API."""
    data = payload.get("data") or {}
    key = data.get("key") or {}
    remote_jid = key.get("remoteJid", "")
    message_id = key.get("id", "")

    if not remote_jid or not message_id:
        log.debug("Evento sin remoteJid/id, ignorado")
        return

    # Solo grupos
    if not remote_jid.endswith("@g.us"):
        return

    # Si aún no se configuró GROUP_JID, registramos el JID para que el usuario lo copie
    if not settings.group_jid:
        log.info("Mensaje de grupo detectado. JID del grupo: %s  (cópialo a GROUP_JID en backend/.env)", remote_jid)
    elif remote_jid != settings.group_jid:
        return  # otro grupo, no es el nuestro

    if key.get("fromMe"):
        return  # mensajes enviados por el propio bot

    mensaje = _desenvolver(data.get("message") or {})
    tipo, texto = _extraer_texto(mensaje)
    participante = key.get("participant") or data.get("participant")

    # ¿Es un BORRADO? Se atiende antes que nada: no trae contenido que guardar, y lo que
    # importa es lo que hay que hacer con el reporte que se eliminó.
    borrado_de = revocacion(mensaje)
    if borrado_de:
        log.info("Mensaje %s BORRADO en el grupo por %s", borrado_de, participante or "?")
        from . import retractacion
        retractacion.por_borrado(borrado_de, participante)
        return

    # El duplicado se descarta ANTES de bajar la media: Evolution reintenta las entregas, y
    # cada reintento volvía a descargar y escribir la foto para luego tirarla.
    with SessionLocal() as session:
        ya_existe = session.execute(
            select(EventoWhatsapp.id).where(EventoWhatsapp.message_id == message_id)
        ).first()
    if ya_existe:
        return

    media_path = _guardar_media(data, mensaje, message_id)
    reenviado = _es_reenviado(mensaje)
    citado = _mensaje_citado(mensaje, data)

    with SessionLocal() as session:
        evento = EventoWhatsapp(
            message_id=message_id,
            remote_jid=remote_jid,
            participante=participante,
            tipo_mensaje=tipo,
            texto=texto,
            push_name=data.get("pushName"),
            responde_a=citado,
            media_path=media_path,
            payload=data,
        )
        session.add(evento)
        session.commit()
        evento_id = evento.id

    log.info(
        "Mensaje guardado [%s] de %s: %s%s",
        tipo,
        participante or "desconocido",
        (texto or "")[:80],
        f" (media: {media_path})" if media_path else "",
    )

    # Los eventos que a propósito NO se procesan se marcan 'omitido', no se dejan en
    # 'pendiente'. Dos motivos: (1) rehidratar_cola() reencola TODO lo pendiente al
    # arrancar, así que un reenviado dejado en 'pendiente' terminaba procesándose como
    # reporte fresco en el siguiente reinicio — justo lo que esta regla evita; y (2) en el
    # panel un 'pendiente' dice "va en camino", y esto nunca va a llegar.
    # Una foto que NO se pudo bajar no es un mensaje "sin contenido": es un reporte que se
    # está perdiendo. Antes caía en 'omitido' —invisible en el panel y sin reintento— y la
    # lectura del tablero desaparecía en silencio. Ahora queda FALLIDO: se ve, se explica y
    # se puede reintentar (el payload guardado permite volver a pedirle la media a Evolution).
    if tiene_media(mensaje) and not media_path:
        log.error("Mensaje %s trae foto pero NO se pudo obtener: queda para reintentar",
                  message_id)
        with SessionLocal() as session:
            ev = session.get(EventoWhatsapp, evento_id)
            if ev is not None:
                ev.estado_proceso = "fallido"
                ev.error = ("No se pudo descargar la foto desde Evolution API. "
                            "Reintenta cuando el servicio responda.")
                session.commit()
        return

    motivo_omitir = None
    if reenviado:
        motivo_omitir = "Mensaje reenviado: trae datos de otro día o unidad, no se registra como reporte nuevo"
    elif not (texto or media_path):
        motivo_omitir = f"Mensaje sin contenido aprovechable (tipo {tipo})"
    elif not settings.anthropic_api_key:
        motivo_omitir = "Sin ANTHROPIC_API_KEY configurada: no se puede leer el reporte"

    if motivo_omitir:
        log.info("Mensaje %s OMITIDO — %s", message_id, motivo_omitir)
        with SessionLocal() as session:
            ev = session.get(EventoWhatsapp, evento_id)
            if ev is not None:
                ev.estado_proceso = "omitido"
                ev.error = motivo_omitir
                session.commit()
        return

    # Etapa 1 + 2: encolar para procesarlo en serie (IA + validación) sin bloquear el webhook
    from . import captura
    captura.encolar(evento_id)
