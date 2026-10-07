"""Envío de mensajes del bot al grupo de WhatsApp (Evolution API).

El bot responde con un acuse por cada viaje capturado (confirmación por captura).
"""

import logging

import httpx

from .config import _tz, settings

log = logging.getLogger("combustible.notify")


def enviar_texto(texto: str, jid: str | None = None) -> bool:
    """Envía un mensaje de texto al grupo (o al JID dado) vía Evolution API."""
    destino = jid or settings.group_jid
    if not destino:
        log.warning("Sin GROUP_JID configurado; no se envía el mensaje")
        return False
    url = f"{settings.evolution_url}/message/sendText/{settings.evolution_instance}"
    headers = {"apikey": settings.evolution_apikey, "Content-Type": "application/json"}
    # Formato Evolution v2 (plano). Fallback al formato anidado de v1 si el server lo pide.
    plano = {"number": destino, "text": texto}
    anidado = {"number": destino, "textMessage": {"text": texto}}
    try:
        r = httpx.post(url, headers=headers, json=plano, timeout=30)
        if r.status_code == 400:
            r = httpx.post(url, headers=headers, json=anidado, timeout=30)
        r.raise_for_status()
        return True
    except Exception:
        log.exception("Fallo enviando mensaje al grupo de WhatsApp")
        return False


# Zonas donde la AGUJA se satura: pegada a F (o a E) el instrumento —y la lectura por
# visión— no permiten distinguir un 95% de un 100%. Ahí se reporta una BANDA en vez de
# fingir un número exacto; en el rango medio sí se da el porcentaje puntual.
NIVEL_LLENO = 0.95
NIVEL_VACIO = 0.05


def _texto_nivel(nivel: float, cap: int) -> str:
    """Cómo se muestra el nivel del tanque: banda en los extremos, número en el medio."""
    if not cap:
        return f"*≈{nivel * 100:.0f}%*"
    if nivel >= NIVEL_LLENO:
        return (f"*lleno* (≥{NIVEL_LLENO * 100:.0f}%  ·  "
                f"~{_fmt_num(round(NIVEL_LLENO * cap))}-{_fmt_num(cap)} L)")
    if nivel <= NIVEL_VACIO:
        return (f"*casi vacío* (≤{NIVEL_VACIO * 100:.0f}%  ·  "
                f"~0-{_fmt_num(round(NIVEL_VACIO * cap))} L)")
    return f"*≈{nivel * 100:.0f}%* (~{_fmt_num(round(nivel * cap, 1))} L de {cap:,} L)"


def _fmt_num(x: float | None) -> str:
    """Formatea un número con separador de miles y CONSERVANDO los decimales cuando los
    hay (p.ej. 382208.5 -> '382,208.5'), sin '.0' innecesario en los enteros."""
    if x is None:
        return "—"
    if float(x).is_integer():
        return f"{int(x):,}"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def _termo_reciente(session, unidad_id: int, minutos: int = 15) -> float | None:
    """Últimas horas del termo de la unidad registradas hace poco (parte del mismo
    reporte). Devuelve None si no hay una lectura reciente."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from .models import AuditoriaThermo

    limite = datetime.now(timezone.utc) - timedelta(minutes=minutos)
    row = session.execute(
        select(AuditoriaThermo.hrs_fin)
        .where(
            AuditoriaThermo.unidad_id == unidad_id,
            AuditoriaThermo.hrs_fin.isnot(None),
            AuditoriaThermo.creado_en >= limite,
        )
        .order_by(AuditoriaThermo.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _consumo_aprox(session, viaje, cap: int):
    """Consumo APROXIMADO desde la última lectura del tanque de la misma unidad, usando
    el cambio de nivel de la aguja × capacidad fija. Devuelve (litros, distancia_km,
    rendimiento_km_l) o None si no hay una lectura previa útil o si el nivel subió
    (recarga). Es una aproximación: la aguja no es precisa."""
    from sqlalchemy import select

    from .models import Viaje

    if not cap or viaje.nivel_tanque is None:
        return None
    prev = session.execute(
        select(Viaje)
        .where(
            Viaje.unidad_id == viaje.unidad_id,
            Viaje.id != viaje.id,
            Viaje.nivel_tanque.isnot(None),
            Viaje.creado_en <= viaje.creado_en,
        )
        .order_by(Viaje.creado_en.desc(), Viaje.id.desc())
        .limit(1)
    ).scalars().first()
    if prev is None:
        return None
    delta_nivel = prev.nivel_tanque - viaje.nivel_tanque
    if delta_nivel <= 0:
        return None  # el nivel subió o quedó igual (probable recarga): no estimamos
    litros = delta_nivel * cap
    # Distancia del MISMO periodo que el consumo: se prefiere el Δodómetro entre las dos
    # lecturas de nivel (abarca el mismo lapso, aunque haya huecos de varios días); el km
    # del viaje es solo respaldo (es el tramo actual, puede no cubrir todo el periodo).
    dist = None
    if viaje.odometro is not None and prev.odometro is not None and viaje.odometro > prev.odometro:
        dist = viaje.odometro - prev.odometro
    elif viaje.kilometros is not None:
        dist = viaje.kilometros
    rend = (dist / litros) if (dist and litros > 0) else None
    return litros, dist, rend


def texto_confirmacion(viaje) -> str:
    """Acuse cordial de un viaje capturado que MUESTRA a detalle los datos extraídos de
    las fotos (km del tablero, nivel del tanque con litros aproximados por capacidad fija,
    horas del termo, consumo aproximado). Deja claro qué falta. NO menciona anomalías (eso
    queda solo en el panel). Llamar con la sesión abierta."""
    from datetime import datetime

    from sqlalchemy.orm import object_session

    from .models import capacidad_tanque

    session = object_session(viaje)
    unidad_obj = viaje.unidad
    unidad = unidad_obj.clave if unidad_obj else "la unidad"
    operador = viaje.operador.nombre if viaje.operador else "sin operador"
    fecha = viaje.fecha.strftime("%d/%m/%Y") if viaje.fecha else "—"
    registrado = datetime.now(_tz()).strftime("%d/%m/%Y a las %H:%M")
    cap = capacidad_tanque(unidad_obj)

    partes = [
        f"¡Gracias por tu reporte! 🙌 Registré el viaje de *{unidad}*.",
        f"👤 {operador}",
        f"📅 Fecha del viaje: {fecha}",
        f"🕐 Registrado: {registrado}",
        "",
        "📋 *Datos extraídos:*",
    ]

    # Datos leídos, a detalle
    datos = []
    if viaje.odometro is not None:
        datos.append(f"🛣️ Km del tablero: *{_fmt_num(viaje.odometro)} km*")
    if viaje.kilometros is not None:
        datos.append(f"📏 Distancia del viaje: *{_fmt_num(viaje.kilometros)} km*")
    if viaje.nivel_tanque is not None:
        datos.append(f"⛽ Nivel de tanque: {_texto_nivel(viaje.nivel_tanque, cap)}")
    # Litros del comprobante/scanner (si los mandaron): habilitan el cálculo de robo (DIF/RTO).
    if viaje.lts_real is not None:
        datos.append(f"🎫 Litros cargados (comprobante): *{_fmt_num(viaje.lts_real)} L*")
    if viaje.lts_scaner is not None:
        datos.append(f"🖥️ Litros del scanner: *{_fmt_num(viaje.lts_scaner)} L*")
    if viaje.rto_real is not None:
        datos.append(f"📈 Rendimiento real: *{viaje.rto_real:.2f} km/L*")
    if viaje.dif is not None and abs(viaje.dif) >= 1:
        signo = "+" if viaje.dif > 0 else ""
        datos.append(f"⚠️ Diferencia comprobante vs scanner: *{signo}{_fmt_num(round(viaje.dif, 1))} L*")
    termo = _termo_reciente(session, unidad_obj.id) if (session is not None and unidad_obj) else None
    if termo is not None:
        datos.append(f"❄️ Horas de termo: *{_fmt_num(termo)} h*")
    if datos:
        partes.extend(datos)
    else:
        partes.append("_(todavía sin datos numéricos legibles)_")

    # Consumo aproximado desde la última lectura del tanque (aproximación por la aguja)
    if session is not None and viaje.nivel_tanque is not None and unidad_obj is not None:
        cons = _consumo_aprox(session, viaje, cap)
        if cons is not None:
            litros_c, dist, rend = cons
            linea = f"📉 Consumo aprox. desde la última lectura: *~{_fmt_num(round(litros_c, 1))} L*"
            if rend is not None:
                linea += f"  ·  rendimiento ~{rend:.2f} km/L"
            partes.append(linea)

    # Claridad sobre datos parciales: qué falta para completar el viaje
    faltan = []
    if viaje.odometro is None:
        faltan.append("el km del tablero")
    if viaje.nivel_tanque is None:
        faltan.append("nivel del tanque (la aguja)")
    partes.append("")
    if faltan:
        partes.append(
            f"📝 Aún me faltan: *{', '.join(faltan)}*. Cuando puedas, mándame la foto "
            "del tablero. 🙏")
    else:
        partes.append("✅ Reporte completo. ¡Buen viaje! 🚚💨")

    return "\n".join(partes)


_NOMBRE_INSTRUMENTO = {
    "odometro": "del odómetro (tablero)",
    "lts_scaner": "del scanner",
    "lts_real": "del comprobante de la carga",
    "nivel_tanque": "del nivel del tanque",
    "horas_termo": "del display Thermo King",
    "placa": "de la placa",
    "serie": "de la serie",
}


def mensaje_foto_borrosa(tipo: str | None = None) -> str:
    """Mensaje cordial pidiendo reenviar una foto que salió borrosa/ilegible."""
    q = _NOMBRE_INSTRUMENTO.get(tipo or "", "")
    ref = f" {q}" if q else ""
    return (
        f"📷 ¡Gracias por la foto! 🙌  Se ve un poco borrosa y no alcancé a leer bien el dato{ref}. "
        "¿Me la puedes reenviar más clara y de cerca, por favor? 🙏  "
        "Con buena luz y sin reflejos queda perfecta."
    )
