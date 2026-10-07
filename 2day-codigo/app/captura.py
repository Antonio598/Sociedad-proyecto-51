"""Etapa 1 — Captura inteligente.

Convierte los eventos crudos de WhatsApp (texto + fotos) en registros de Viaje,
usando la capa de IA para parsear el mensaje y leer las fotos. Al completar los
datos suficientes, dispara la validación (Etapa 2).

Asociación foto→viaje: si la foto trae la clave de unidad visible, se usa; si no,
se adjunta al viaje más reciente que aún no tenga ese dato (ventana de tiempo).
Esta heurística cubre el flujo real donde los datos "caen" al grupo en varios
mensajes. Para máxima precisión conviene que el coordinador incluya la unidad.
"""

import logging
import queue
import re
import threading
import unicodedata
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ai
from .config import fecha_flota, ruta_media
from .models import (
    Anomalia,
    AuditoriaThermo,
    EventoWhatsapp,
    Operador,
    TipoConfig,
    TipoUnidad,
    Unidad,
    Viaje,
)
from .validacion import validar

log = logging.getLogger("combustible.captura")

# Ventana para asociar una foto suelta al viaje más reciente
VENTANA_ASOCIACION = timedelta(hours=6)


def _tipo_unidad(clave: str) -> TipoUnidad:
    return TipoUnidad.TRACTO if clave.upper().startswith("T") else TipoUnidad.CAMION


# Formato válido de clave de unidad: T### (tracto) o C### (camión). Solo se dan de alta
# unidades que lo cumplan: una clave mal leída creaba una unidad fantasma en el catálogo
# que además se realimentaba al prompt de la IA.
_CLAVE_RE = re.compile(r"^[TC]\d{2,4}$")


def resolver_unidad(session: Session, clave_raw: str | None, crear: bool = True) -> Unidad | None:
    """Busca la unidad por clave. Con `crear=False` NO da de alta (para el flujo de foto,
    donde la clave sale de un OCR y puede ser basura)."""
    if not clave_raw:
        return None
    clave = "".join(str(clave_raw).strip().upper().split())
    if len(clave) < 2:
        return None
    u = session.execute(select(Unidad).where(Unidad.clave == clave)).scalar_one_or_none()
    if u is not None:
        return u
    if not crear:
        return None
    if not _CLAVE_RE.match(clave):
        log.info("Clave de unidad con formato inválido (%r): no se da de alta", clave)
        return None
    u = Unidad(clave=clave, tipo=_tipo_unidad(clave))
    session.add(u)
    session.flush()
    log.info("Unidad nueva creada: %s", clave)
    return u


# Palabras que no distinguen a una persona (no cuentan para casar nombres).
_STOP_NOMBRE = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}


def _tokens_nombre(nombre: str | None) -> set[str]:
    """Palabras significativas de un nombre, sin acentos ni puntuación."""
    s = unicodedata.normalize("NFD", (nombre or "").upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")   # quita acentos
    s = re.sub(r"[^A-ZÑ0-9 ]", " ", s)
    return {t for t in s.split() if len(t) >= 3 and t not in _STOP_NOMBRE}


def _casar_con_catalogo(session: Session, nombre: str) -> Operador | None:
    """Casa un nombre libre del chat con un operador EXISTENTE por COINCIDENCIA TOTAL de
    palabras: un conjunto de nombres es subconjunto del otro (así casa des-ordenado y ya
    sea que reporten por nombre o por apellidos), siempre que la parte común tenga >=2
    palabras y el match sea INEQUÍVOCO (un solo operador). Prefiere el catálogo real (no
    provisional) y el que tenga número. Devuelve None si no hay match único -> se crea
    provisional para resolución humana, en vez de duplicar en silencio.
    """
    t = _tokens_nombre(nombre)
    if len(t) < 2:
        return None      # con una sola palabra ('Jesús') no hay forma de casar sin riesgo
    matches = []
    for o in session.execute(select(Operador)).scalars():
        ot = _tokens_nombre(o.nombre)
        # coincidencia total: uno contenido en el otro, y el lado chico con >=2 palabras
        if ot and min(len(t), len(ot)) >= 2 and (t <= ot or ot <= t):
            matches.append(o)
    if not matches:
        return None
    reales = [o for o in matches if not o.provisional]
    grupo = reales or matches
    if len({o.nombre for o in grupo}) > 1:
        log.info("Nombre '%s' casa con %d operadores distintos — ambiguo, no se auto-asigna",
                 nombre, len({o.nombre for o in grupo}))
        return None
    grupo.sort(key=lambda o: (o.provisional, o.numero is None))   # catálogo con número primero
    return grupo[0]


def resolver_operador(session: Session, nombre_raw: str | None) -> Operador | None:
    if not nombre_raw or not str(nombre_raw).strip():
        return None
    nombre = " ".join(str(nombre_raw).strip().upper().split())
    # .first() (no scalar_one_or_none): el nombre no es único, pueden existir
    # homónimos del historial importado y romper la carga.
    o = session.execute(select(Operador).where(Operador.nombre == nombre)).scalars().first()
    if o is not None:
        return o
    o = _casar_con_catalogo(session, nombre)
    if o is not None:
        log.info("Operador '%s' casado con el catálogo: %s (#%s)", nombre, o.nombre, o.numero)
        return o
    # Sin match: se da de alta EN EL ACTO, con su número consecutivo, para que el viaje
    # quede registrado y corran todas las reglas sin esperar a nadie. La rotación de
    # operadores en esta flota es alta y el alta no puede ser un trámite: si llega un
    # requerimiento con alguien que no está en el listado, el sistema lo incorpora.
    # Queda marcado `provisional` —no como registro a medias, sino como pendiente de
    # confirmar contra el padrón oficial desde el panel (confirmarlo o fusionarlo con el
    # conductor real si resultó ser una variante del nombre de alguien que ya existía).
    # SIN número de empleado, a propósito: lo asigna Recursos Humanos, no un generador.
    # `provisional=True` ya significa «espera a que una persona lo resuelva desde el
    # panel», y es ahí donde se le captura el número que le corresponde.
    o = Operador(nombre=nombre, numero=None, provisional=True)
    session.add(o)
    session.flush()
    log.info("Operador dado de alta en tiempo real: %s (#%s) — pendiente de confirmar",
             nombre, o.numero)
    return o


def resolver_operador_por_numero(session: Session, numero) -> Operador | None:
    """Busca por NÚMERO DE EMPLEADO, que ahora es texto y puede traer letras.

    Se normaliza igual que al guardarlo (mayúsculas, sin espacios) porque el número puede
    venir de una foto leída por la IA: «cfruit 056» y «CFRUIT056» son la misma persona.

    Ya no existe `_siguiente_numero`: un número de empleado lo asigna Recursos Humanos, no
    un generador, y con números alfanuméricos «el siguiente» ni siquiera está definido
    (¿qué va después de CFRUIT056?).
    """
    if numero is None or numero == "":
        return None
    clave = re.sub(r"\s+", "", str(numero)).upper()
    if not clave:
        return None
    return session.execute(
        select(Operador).where(Operador.numero == clave)).scalars().first()


def _buscar_o_crear_viaje(session: Session, unidad: Unidad, fecha: date) -> tuple[Viaje, bool]:
    """Devuelve (viaje, creado). `creado` es True solo si se creó uno nuevo.

    Un viaje RETRACTADO no se reusa. Cuando alguien borra su reporte, el bot le pide que
    mande el corregido; si el reenvío cayera sobre el mismo viaje retractado, `_set_si_vacio`
    no pisaría el dato malo, el viaje seguiría fuera de los indicadores y el bot igual
    contestaría "registré el viaje". El dato corregido se perdía y el usuario creía que
    todo había quedado bien. Ahora el reenvío crea un renglón NUEVO y el retractado queda
    como evidencia de lo que se reportó antes.
    """
    retractado = None
    for cand in session.execute(
        select(Viaje).where(Viaje.unidad_id == unidad.id, Viaje.fecha == fecha)
        .order_by(Viaje.id.desc())
    ).scalars():
        if cand.retractado_en is None:
            return cand, False
        retractado = retractado or cand

    v = Viaje(unidad_id=unidad.id, fecha=fecha)
    if retractado is not None:
        # Dejar el vínculo en los dos sentidos: sin esto, una auditoría vería dos viajes
        # sueltos de la misma unidad y fecha sin saber que uno sustituye al otro.
        nota = f"Sustituye al viaje #{retractado.id}, retractado ({retractado.retractado_motivo or 'sin motivo'})."
        v.correcciones = nota
        retractado.correcciones = ((retractado.correcciones or "") +
                                   f"\nSustituido por un reporte nuevo.").strip()[:2000]
        log.warning("Reporte nuevo para %s %s: NO se reusa el viaje %s (retractado)",
                    unidad.clave, fecha, retractado.id)
    session.add(v)
    session.flush()
    log.info("Viaje nuevo: unidad %s fecha %s (id=%s)", unidad.clave, fecha, v.id)
    return v, True


def _viaje_reciente(session: Session, unidad_id: int | None = None) -> Viaje | None:
    """El viaje más reciente dentro de la ventana (para heredar unidad/operador
    a una foto suelta, p. ej. las horas de termo que llegan sin clave visible)."""
    limite = datetime.now(timezone.utc) - VENTANA_ASOCIACION
    # Un viaje retractado no puede servir de ancla: anclarle una foto le metería datos a
    # un registro que ya no cuenta para nada, y la foto se perdería sin dejar rastro.
    q = select(Viaje).where(Viaje.creado_en >= limite, Viaje.retractado_en.is_(None))
    if unidad_id is not None:
        q = q.where(Viaje.unidad_id == unidad_id)
    return session.execute(q.order_by(Viaje.creado_en.desc())).scalars().first()


def _viaje_reciente_por_remitente(session: Session, participante: str | None) -> Viaje | None:
    """El viaje más reciente (dentro de la ventana) reportado por el MISMO número de
    WhatsApp. Ancla las fotos sueltas (aguja/termo en primer plano, sin ECO visible) al
    reporte que ESA persona está completando, en vez de a un viaje cualquiera."""
    if not participante:
        return None
    limite = datetime.now(timezone.utc) - VENTANA_ASOCIACION
    q = select(Viaje).where(Viaje.reportado_por == participante, Viaje.creado_en >= limite)
    return session.execute(q.order_by(Viaje.creado_en.desc())).scalars().first()


# Ventana para agrupar los mensajes de un MISMO reporte (ráfaga): las fotos y el texto
# llegan con segundos de diferencia y en CUALQUIER orden (a veces las fotos primero).
VENTANA_RAFAGA = timedelta(minutes=3)

# Memo: evento de texto -> (viaje_id, cambiado). Evita re-parsear con la IA cuando una foto
# ya adelantó el texto de su ráfaga y la cola llega después a ese mismo evento.
# OrderedDict para poder expulsar SOLO lo más viejo al llegar al tope. Antes se hacía
# clear() completo y eso borraba entradas de ráfagas en curso todavía sin consumir.
_texto_a_viaje: "OrderedDict[int, tuple[int | None, bool]]" = OrderedDict()
_MEMO_MAX = 500

# Lo anotado durante la transacción EN CURSO. Solo se publica al memo real tras un commit
# exitoso. Es crítico: el viaje.id se obtiene de un flush() (aún sin confirmar), así que si
# la transacción hace rollback (p.ej. la IA responde 529 a media ráfaga) ese id no existe.
# Publicarlo de inmediato envenenaba el memo: el reintento veía el memo, no encontraba el
# viaje y NUNCA volvía a parsear el texto -> el reporte se perdía en silencio.
_memo_pendiente: "list[tuple[int, int | None, bool]]" = []


def _memo_texto(evento_id: int, viaje: Viaje | None, cambiado: bool) -> None:
    """Anota el parseo; no se publica hasta que la transacción confirme."""
    _memo_pendiente.append((evento_id, viaje.id if viaje is not None else None, cambiado))


def _memo_publicar() -> None:
    """La transacción confirmó: ya se puede confiar en lo anotado."""
    for eid, vid, cambiado in _memo_pendiente:
        _texto_a_viaje[eid] = (vid, cambiado)
        _texto_a_viaje.move_to_end(eid)
    _memo_pendiente.clear()
    while len(_texto_a_viaje) > _MEMO_MAX:
        _texto_a_viaje.popitem(last=False)   # expulsa lo más viejo, nunca lo recién usado


def _memo_descartar() -> None:
    """La transacción hizo rollback: esos viajes no existen, se tira lo anotado."""
    _memo_pendiente.clear()


def _reusar_o_parsear_texto(session: Session, evento: EventoWhatsapp) -> tuple[Viaje | None, bool]:
    """Devuelve (viaje, cambiado) del texto de un evento, reusando el memo si SIGUE siendo
    válido. Si el viaje memorizado ya no existe (rollback previo), el memo se invalida y el
    texto se vuelve a parsear — antes se saltaba y el reporte quedaba huérfano para siempre.
    """
    memo = _texto_a_viaje.get(evento.id)
    if memo is not None:
        vid, cambiado = memo
        if vid is None:
            return None, False                  # ya se sabía que ese texto no es un reporte
        v = session.get(Viaje, vid)
        if v is not None:
            return v, cambiado
        _texto_a_viaje.pop(evento.id, None)     # memo inválido: el viaje no existe
    viaje, cambiado = _procesar_texto(session, evento)
    _memo_texto(evento.id, viaje, cambiado)
    return viaje, cambiado


def _viaje_del_reporte_cercano(session: Session, evento: EventoWhatsapp) -> Viaje | None:
    """El viaje del REPORTE DE TEXTO de la misma ráfaga (mismo remitente, minutos de
    diferencia, en cualquier orden).

    Resuelve el caso real de fotos que llegan ANTES que su texto: el texto es quien dice la
    unidad del reporte, así que manda aunque la cola lo procese después. Se toma el texto
    más CERCANO en el tiempo y, si aún no se convirtió en viaje, se adelanta aquí (el memo
    evita que la cola lo re-parsee con la IA).
    """
    if not evento.participante or evento.recibido_en is None:
        return None
    desde = evento.recibido_en - VENTANA_RAFAGA
    hasta = evento.recibido_en + VENTANA_RAFAGA
    candidatos = session.execute(
        select(EventoWhatsapp).where(
            EventoWhatsapp.participante == evento.participante,
            EventoWhatsapp.id != evento.id,
            EventoWhatsapp.texto.isnot(None),
            EventoWhatsapp.recibido_en >= desde,
            EventoWhatsapp.recibido_en <= hasta,
        )
    ).scalars().all()
    # El texto más cercano en el tiempo es el de ESTA ráfaga (no el de un reporte anterior).
    candidatos.sort(key=lambda e: abs((e.recibido_en - evento.recibido_en).total_seconds()))
    for ev in candidatos:
        viaje, _ = _reusar_o_parsear_texto(session, ev)
        if viaje is not None:
            log.info("Foto anclada al reporte de su ráfaga (evento %s -> viaje %s, unidad %s)",
                     ev.id, viaje.id, viaje.unidad.clave if viaje.unidad else "?")
            return viaje
    return None


def _viaje_objetivo_foto(session: Session, evento: EventoWhatsapp, unidad_foto: Unidad | None) -> Viaje | None:
    """Elige UN viaje destino para toda la foto, con ancla confiable:
      1) el REPORTE DE TEXTO de la misma ráfaga (dice la unidad; vale en cualquier orden y
         es más fiable que la clave leída en la foto, que puede salir mal);
      2) la UNIDAD visible en la foto (si no hay viaje de esa unidad, se crea);
      3) el reporte reciente del REMITENTE.
    Devuelve None si no hay ancla — mejor no asociar que escribir en la unidad equivocada.
    """
    v = _viaje_del_reporte_cercano(session, evento)
    if v is not None:
        return v
    if unidad_foto is not None:
        v = _viaje_reciente(session, unidad_id=unidad_foto.id)
        if v is None:
            v, creado = _buscar_o_crear_viaje(session, unidad_foto, fecha_flota(evento.recibido_en))
            if creado and evento.participante and v.reportado_por is None:
                v.reportado_por = evento.participante
            if v.origen_message_id is None:   # ver nota en _procesar_texto
                v.origen_message_id = evento.message_id
        return v
    return _viaje_reciente_por_remitente(session, evento.participante)


_MESES_ES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO",
             "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]


def _ultimo_hrs_thermo(session: Session, unidad_id: int) -> float | None:
    """Últimas horas-fin registradas del termo de esa unidad (para calcular horas trabajadas)."""
    row = session.execute(
        select(AuditoriaThermo.hrs_fin)
        .where(AuditoriaThermo.unidad_id == unidad_id, AuditoriaThermo.hrs_fin.isnot(None))
        .order_by(AuditoriaThermo.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


# Cuánto puede avanzar un horómetro respecto al tiempo real transcurrido. Es 1.0 por
# física (una hora de reloj = máximo una hora de motor); se deja algo de holgura para
# desfases de reloj y para lecturas que llegan tarde.
MARGEN_HOROMETRO = 1.2


def _fecha_ultimo_hrs_thermo(session: Session, unidad_id: int) -> datetime | None:
    """Cuándo se registró la última lectura, para saber cuánto tiempo pudo trabajar."""
    row = session.execute(
        select(AuditoriaThermo.creado_en)
        .where(AuditoriaThermo.unidad_id == unidad_id, AuditoriaThermo.hrs_fin.isnot(None))
        .order_by(AuditoriaThermo.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _registrar_horas_termo(session: Session, valor: float, unidad: Unidad,
                           operador_id: int | None) -> AuditoriaThermo | None:
    """Crea un registro de AuditoriaThermo con la lectura de horas del termo.

    hrs_fin = lectura actual; hrs_inic = último hrs_fin de la unidad (si hay);
    horas_trab = diferencia cuando el contador avanza (no retrocede).

    Si la lectura es IDÉNTICA a la última registrada, no se crea otro registro: es la
    misma foto reenviada (antes generaba duplicados con horas_trab=0).
    """
    ultimo = _ultimo_hrs_thermo(session, unidad.id)
    if ultimo is not None and valor == ultimo:
        log.info("Horas de termo %s repetidas para unidad %s — no se duplica el registro",
                 valor, unidad.clave)
        return None
    if ultimo is not None and valor < ultimo:
        # Un horómetro NO retrocede: o la foto es de otro termo (los tractos enganchan
        # remolques, cada uno con el suyo) o se leyó mal. Guardarla contaminaba la base:
        # quedaba como `ultimo` y inflaba las horas_trab de la siguiente lectura real.
        log.warning("Horas de termo %s RETROCEDEN respecto a la última (%s) de %s — se omite "
                    "(¿foto de otro termo o mal leída?)", valor, ultimo, unidad.clave)
        return None
    horas_trab = (valor - ultimo) if (ultimo is not None and valor >= ultimo) else None

    # Un horómetro no puede avanzar más horas que las que han pasado en el reloj. Si la
    # diferencia supera el tiempo transcurrido, la lectura es de OTRO termo: el equipo de
    # frío va en el REMOLQUE y un tracto engancha remolques distintos, así que dos fotos
    # seguidas de la "misma unidad" pueden ser de horómetros diferentes. Sin esta guardia
    # se generaban saltos de 7,768 h que inflaban el consumo estimado casi 60 veces.
    if horas_trab is not None and horas_trab > 0:
        desde = _fecha_ultimo_hrs_thermo(session, unidad.id)
        if desde is not None:
            transcurridas = (datetime.now(timezone.utc) - desde).total_seconds() / 3600
            if horas_trab > transcurridas * MARGEN_HOROMETRO:
                log.warning(
                    "Horas de termo %s implican %.0f h trabajadas para %s, pero solo han "
                    "pasado %.0f h desde la última lectura — es OTRO termo (el equipo va en "
                    "el remolque). Se guarda la lectura sin calcular horas trabajadas.",
                    valor, horas_trab, unidad.clave, transcurridas)
                horas_trab = None

    reg = AuditoriaThermo(
        unidad_id=unidad.id,
        operador_id=operador_id,
        hrs_inic=ultimo,
        hrs_fin=valor,
        horas_trab=horas_trab,
        mes=_MESES_ES[fecha_flota().month - 1],
    )
    session.add(reg)
    session.flush()
    log.info("Horas de termo %s registradas para unidad %s (inic=%s, trab=%s, thermo_id=%s)",
             valor, unidad.clave, ultimo, horas_trab, reg.id)
    return reg


def _set_si_vacio(viaje: Viaje, campo: str, valor) -> bool:
    if valor is not None and getattr(viaje, campo) is None:
        setattr(viaje, campo, valor)
        return True
    return False


def _aplicar_dato_foto(viaje: Viaje, campo: str, valor) -> bool:
    """Aplica al viaje un dato leído de una foto. Devuelve True si cambió algo.

    Gana la lectura MÁS RECIENTE: el operador está reportando el estado actual, así que si
    manda otra foto del tablero, esa es la buena. Antes se ignoraba en silencio cualquier
    valor si el campo ya estaba lleno, y el panel seguía mostrando la lectura vieja — que
    parecía un error de OCR cuando en realidad era el dato anterior.

    Si el ODÓMETRO retrocede se avisa: no puede bajar en la misma unidad (o la foto es de
    otro camión, o es una foto vieja). Se aplica igual —es lo último que reportaron— pero
    queda registrado para poder revisarlo.
    """
    actual = getattr(viaje, campo)
    if valor is None or actual == valor:
        return False
    if actual is not None:
        if campo == "odometro" and valor < actual:
            log.warning("Odómetro de la foto (%s) MENOR que el ya registrado (%s) en el viaje %s "
                        "— se toma el nuevo, pero revisar (¿otra unidad? ¿foto vieja?)",
                        valor, actual, viaje.id)
        else:
            log.info("Viaje %s: %s se actualiza %s -> %s (lectura más reciente)",
                     viaje.id, campo, actual, valor)
    setattr(viaje, campo, valor)
    return True


def _viaje_citado(session: Session, evento: EventoWhatsapp) -> Viaje | None:
    """El viaje que produjo el mensaje al que este RESPONDE, si lo hay.

    En el grupo la gente corrige respondiendo a su propio reporte ("perdón, era T182").
    El id del mensaje citado llega en el propio evento, así que la corrección se puede
    atribuir con certeza en vez de inferirla.
    """
    if not evento.responde_a:
        return None
    v = session.execute(
        select(Viaje).where(Viaje.origen_message_id == evento.responde_a)
    ).scalars().first()
    if v is None:
        log.info("El mensaje %s cita a %s, que no produjo ningún viaje: se trata como "
                 "reporte normal", evento.message_id, evento.responde_a)
    return v


# Campos que una corrección puede rectificar desde el texto. Los litros no están: son
# lecturas físicas que llegan por foto, no las escribe quien corrige.
_CAMPOS_CORREGIBLES = ("kilometros", "odometro")


def _es_correccion(session: Session, evento: EventoWhatsapp, viaje: Viaje, datos) -> bool:
    """¿El mensaje citado es una CORRECCIÓN de ese viaje, o un reporte nuevo?

    Responder citando es el gesto normal para mantener el hilo en un grupo, así que citar
    NO basta para concluir que se corrige. Sin esta distinción, contestar el reporte de un
    compañero con el propio le reescribía SU viaje: unidad, operador y kilómetros.

    Se decide por el CONTENIDO, no por quién escribe: exigir "mismo autor" no sirve —
    citar tu propio reporte anterior para mandar el de hoy es igual de común, y esa guardia
    lo dejaría pasar pisando los km del viaje viejo.
    """
    # Un reporte que nombra OTRA unidad no corrige a este viaje: habla de otro camión.
    if datos.unidad:
        otra = resolver_unidad(session, datos.unidad, crear=False)
        if otra is not None and otra.id != viaje.unidad_id:
            log.info("Mensaje %s cita al viaje %s pero reporta la unidad %s: es reporte "
                     "NUEVO, no corrección", evento.message_id, viaje.id, otra.clave)
            return False

    # Un reporte COMPLETO (unidad + operador + kilometraje) es un reporte, no una
    # rectificación. Quien corrige manda el dato suelto: "perdón, eran 1150 km".
    completo = bool(datos.unidad) and bool(datos.operador or datos.numero_operador) \
        and (datos.kilometros is not None or datos.odometro is not None)
    if completo:
        log.info("Mensaje %s cita al viaje %s pero trae un reporte completo: se registra "
                 "aparte", evento.message_id, viaje.id)
        return False

    # Un viaje retractado no se corrige: se sustituye por un reporte nuevo.
    if viaje.retractado_en is not None:
        log.info("Mensaje %s corrige un viaje RETRACTADO (%s): se registra como reporte nuevo",
                 evento.message_id, viaje.id)
        return False
    return True


def _aplicar_correccion(session: Session, evento: EventoWhatsapp, viaje: Viaje,
                        datos) -> Viaje:
    """Rectifica un viaje ya registrado con los datos del mensaje que lo corrige.

    A diferencia de la captura normal, aquí SÍ se sobrescribe: el sentido de una corrección
    es reemplazar un dato que estaba mal. Lo que se corrigió queda anotado en el viaje para
    que el cambio sea rastreable y no un dato que mutó sin explicación.
    """
    cambios: list[str] = []

    unidad = resolver_unidad(session, datos.unidad) if datos.unidad else None
    if unidad is not None and unidad.id != viaje.unidad_id:
        anterior = viaje.unidad.clave if viaje.unidad else "?"
        viaje.unidad_id = unidad.id
        cambios.append(f"unidad {anterior} → {unidad.clave}")

    if datos.fecha and datos.fecha != viaje.fecha:
        cambios.append(f"fecha {viaje.fecha} → {datos.fecha}")
        viaje.fecha = datos.fecha

    op = resolver_operador_por_numero(session, datos.numero_operador)
    if op is None and datos.operador:
        op = resolver_operador(session, datos.operador)
    if op is not None and op.id != viaje.operador_id:
        anterior = viaje.operador.nombre if viaje.operador else "sin operador"
        viaje.operador_id = op.id
        cambios.append(f"operador {anterior} → {op.nombre}")

    if datos.tipo_config and (viaje.tipo_config is None
                              or viaje.tipo_config.value != datos.tipo_config):
        cambios.append(f"tipo {viaje.tipo_config.value if viaje.tipo_config else '—'} "
                       f"→ {datos.tipo_config}")
        viaje.tipo_config = TipoConfig(datos.tipo_config)

    for campo in _CAMPOS_CORREGIBLES:
        nuevo = getattr(datos, campo, None)
        if nuevo is None:
            continue
        actual = getattr(viaje, campo)
        if actual is not None and abs(float(actual) - float(nuevo)) < 1e-9:
            continue
        cambios.append(f"{campo} {_fmt(actual)} → {_fmt(nuevo)}")
        setattr(viaje, campo, nuevo)

    if not cambios:
        log.info("Corrección del viaje %s sin cambios reales", viaje.id)
        return viaje

    quien = (evento.push_name or (evento.participante or "").split("@")[0] or "alguien")
    nota = f"Corregido por {quien}: " + "; ".join(cambios)
    viaje.correcciones = ((viaje.correcciones or "") + "\n" + nota).strip()[:2000]
    log.warning("Viaje %s CORREGIDO desde el grupo — %s", viaje.id, "; ".join(cambios))

    # Cambiar de unidad o de operador no es rectificar una cifra: es reasignar el viaje a
    # otro camión o a otra persona. Puede ser legítimo, pero nunca debe pasar en silencio.
    if any(c.startswith(("unidad ", "operador ")) for c in cambios):
        session.add(Anomalia(
            viaje_id=viaje.id, tipo="correccion_sospechosa",
            descripcion=(f"Una corrección desde el grupo cambió datos de identidad del viaje: "
                         f"{'; '.join(c for c in cambios if c.startswith(('unidad ', 'operador ')))}. "
                         f"La pidió {quien}. Verificar que sea correcta."),
        ))

    # Quien corrige pasa a ser el responsable del reporte: si no se actualiza, el viaje
    # queda con la unidad y el operador nuevos pero el remitente viejo, y ese remitente
    # incoherente ancla mal las fotos sueltas que lleguen después.
    if evento.participante:
        viaje.reportado_por = evento.participante
    return viaje


def _fmt(v) -> str:
    if v is None:
        return "—"
    f = float(v)
    return f"{f:g}"


def _procesar_texto(session: Session, evento: EventoWhatsapp) -> tuple[Viaje | None, bool]:
    """Devuelve (viaje, cambiado). `cambiado` es True solo si el texto CREÓ el viaje o
    llenó algún dato nuevo; así un reenvío/duplicado que no aporta nada no dispara un
    acuse redundante."""
    # La fecha de referencia es cuándo se RECIBIÓ el mensaje (hora local de la flota), no
    # ahora: un reporte de las 23:58 procesado a las 00:01 (o reintentado al día siguiente)
    # debe quedar en SU día, no en el del procesamiento.
    ref = fecha_flota(evento.recibido_en)
    datos = ai.parse_mensaje(evento.texto or "", fecha_ref=ref)
    if not datos.es_viaje:
        log.info("Mensaje no es reporte de viaje, ignorado: %s", (evento.texto or "")[:60])
        return None, False

    # ¿Es una CORRECCIÓN? Si el mensaje responde citando a otro que ya produjo un viaje,
    # se corrige ESE viaje en vez de crear uno nuevo. Se usa el mensaje citado y no las
    # palabras del texto ("perdón", "era"...) porque el citado es un hecho: dice sin
    # ambigüedad qué reporte se está corrigiendo. Adivinar por el texto se equivocaría
    # justo cuando más caro sale.
    corregido = _viaje_citado(session, evento)
    if corregido is not None and _es_correccion(session, evento, corregido, datos):
        return _aplicar_correccion(session, evento, corregido, datos), True

    unidad = resolver_unidad(session, datos.unidad)
    if unidad is None:
        log.info("Mensaje de viaje sin unidad reconocible: %s", (evento.texto or "")[:60])
        return None, False
    fecha = datos.fecha or ref

    viaje, creado = _buscar_o_crear_viaje(session, unidad, fecha)
    cambiado = creado
    if viaje.reportado_por is None and evento.participante:
        viaje.reportado_por = evento.participante   # número (JID) de quien envió el reporte
    # Vínculo mensaje -> viaje: permite retractar el viaje si el reporte se borra, y saber
    # qué reporte se corrige cuando alguien responde citándolo.
    #
    # Se marca el PRIMER mensaje que aporta datos, no solo el que crea la fila. Antes decía
    # `creado and ...` y el bot casi siempre cae sobre un viaje que YA existe (importado del
    # Excel para esa unidad y fecha), así que la columna quedaba vacía SIEMPRE: 0 de 2,468
    # filas, incluidas las 19 del bot. Con la columna vacía, borrar un reporte no retractaba
    # nada y una corrección citada nunca encontraba su viaje: las dos funciones existían
    # pero jamás se activaban.
    if viaje.origen_message_id is None:
        viaje.origen_message_id = evento.message_id
    if viaje.operador_id is None:
        # Resolver por número (llave confiable del catálogo) y, si no, por nombre.
        op = resolver_operador_por_numero(session, datos.numero_operador)
        if op is None and datos.operador:
            op = resolver_operador(session, datos.operador)
        if op:
            viaje.operador_id = op.id
            cambiado = True
    if datos.tipo_config and viaje.tipo_config is None:
        viaje.tipo_config = TipoConfig(datos.tipo_config)
        cambiado = True

    # Del MENSAJE de texto solo se toman km y odómetro. Los litros (scanner y reales)
    # son lecturas FÍSICAS que llegan por foto (pantalla del motor / comprobante), no las
    # escribe el operador; así una cifra mal interpretada del texto no bloquea la
    # lectura real de la foto (_set_si_vacio solo llena lo que está vacío).
    for campo in ("kilometros", "odometro"):
        if _set_si_vacio(viaje, campo, getattr(datos, campo)):
            cambiado = True
    return viaje, cambiado


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


# Campos que una foto del tablero aporta a un Viaje: el KM del tablero (que es el
# odómetro ACUMULADO del vehículo) y el nivel del tanque (fracción de la aguja).
# `kilometros` NO se llena por foto: es la DISTANCIA del viaje (avance del odómetro),
# no la lectura del tablero — meter ahí el acumulado disparaba km_vs_odometro en falso.
# Los litros ya NO se leen (ni scanner ni comprobante). El termo va aparte (auditoría, en horas).
_CAMPOS_VIAJE_FOTO = ("odometro", "nivel_tanque", "lts_scaner", "lts_real")


def _procesar_foto(session: Session, evento: EventoWhatsapp,
                   viaje_texto: Viaje | None = None) -> tuple[Viaje | None, str | None]:
    """Lee los datos de la foto y los enriquece en UN viaje objetivo.

    `viaje_texto` es el viaje que el CAPTION del mismo mensaje creó/identificó (si lo
    hubo): tiene prioridad como ancla, porque el texto dice la unidad del reporte y la
    clave leída en la foto puede salir mal. Devuelve (viaje_actualizado, señal_reenvio).
    """
    # Solo imágenes al OCR: audio/video/PDF (notas de voz, etc.) no son legibles.
    ext = evento.media_path[evento.media_path.rfind("."):].lower() if "." in evento.media_path else ""
    if ext not in IMAGE_EXTS:
        log.info("Media no-imagen ignorada para OCR: %s", evento.media_path)
        return None, None

    ruta = ruta_media(evento.media_path)
    if ruta is None:
        raise FileNotFoundError(f"La foto {evento.media_path} no está en el disco")
    lectura = ai.leer_imagen(str(ruta), pista=evento.texto or None)

    # Unidad detectada en la foto (solo si la clave se ve clara). Es una pista, no manda.
    # crear=False a propósito: la clave sale de un OCR y una lectura basura daba de alta
    # una unidad fantasma en el catálogo. Si la unidad no existe, se ignora la pista.
    unidad_foto = (resolver_unidad(session, lectura.unidad_detectada, crear=False)
                   if lectura.unidad_detectada else None)

    algo_leido = any(getattr(lectura, c) is not None for c in _CAMPOS_VIAJE_FOTO) \
        or lectura.horas_termo is not None or bool(lectura.placa or lectura.serie)

    # UN SOLO viaje destino para toda la foto. Prioridad del ancla:
    #   1) el viaje del CAPTION de este mismo mensaje (el texto dice la unidad del reporte),
    #   2) la unidad visible en la foto, 3) el reporte reciente del remitente.
    # Nunca por sola recencia de otra unidad. Así una clave mal leída en la foto no desvía
    # los datos hacia la unidad equivocada cuando el mensaje ya trae el reporte.
    objetivo = viaje_texto if viaje_texto is not None else _viaje_objetivo_foto(session, evento, unidad_foto)

    # Horas de termo -> auditoría Thermo (en horas), no al Viaje. La unidad viene del viaje
    # objetivo (mismo ancla) o, si no hay, de la clave visible — NUNCA de un viaje reciente
    # cualquiera. Sin unidad confiable, solo se loguea (no se crea registro cruzado).
    if lectura.horas_termo is not None:
        unidad = (objetivo.unidad if objetivo is not None else None) or unidad_foto
        operador_id = objetivo.operador_id if objetivo is not None else None
        if unidad is not None:
            _registrar_horas_termo(session, lectura.horas_termo, unidad, operador_id)
        else:
            log.info("Horas de termo leídas (%s) sin unidad confiable — no se guarda", lectura.horas_termo)

    # Campos que ENRIQUECEN el viaje objetivo (no se reparten): km, odómetro, nivel de
    # tanque. Solo se llenan los campos vacíos de ESE viaje.
    viaje_actualizado: Viaje | None = None
    if objetivo is not None:
        for campo in _CAMPOS_VIAJE_FOTO:
            valor = getattr(lectura, campo)
            if _aplicar_dato_foto(objetivo, campo, valor):
                viaje_actualizado = objetivo
                log.info("Foto -> viaje %s: %s=%s (%s)", objetivo.id, campo, valor, lectura.confianza)
    else:
        leidos = [c for c in _CAMPOS_VIAJE_FOTO if getattr(lectura, c) is not None]
        if leidos:
            log.info("Foto con %s sin viaje/unidad al cual asociar (remitente=%s)",
                     leidos, evento.participante)

    # No es un instrumento (meme, captura de chat, foto casual): no molestar.
    if not lectura.es_instrumento:
        log.info("Foto no reconocida como instrumento: %s", evento.media_path)
        return viaje_actualizado, None

    # Pedir reenvío SOLO si es un instrumento genuino que salió ilegible (nada leído y
    # baja confianza). Una foto legible siempre extrae algo -> no cae aquí -> sin bucle.
    if not algo_leido and lectura.confianza == "baja":
        log.info("Foto de instrumento ilegible sin datos extraíbles: %s", evento.media_path)
        return viaje_actualizado, "foto"

    return viaje_actualizado, None


# ── Procesamiento en serie (un solo worker, en orden de llegada) ─────────────
# Evita que varios mensajes del mismo reporte (texto + fotos) se procesen en
# paralelo y desordenados, lo que rompía la asociación foto→viaje.
_cola: "queue.Queue[tuple[int, bool]]" = queue.Queue()
_worker_lock = threading.Lock()
_worker_thread: "threading.Thread | None" = None


def _worker() -> None:
    while True:
        evento_id, acusar = _cola.get()
        try:
            _procesar(evento_id, acusar=acusar)
        except Exception:
            # El worker NO debe morir por un evento; se registra y sigue con el siguiente.
            log.exception("Error en worker de captura (evento %s)", evento_id)
        finally:
            _cola.task_done()


def _asegurar_worker() -> None:
    """Arranca el worker si no está vivo (watchdog): si el hilo muriera, se repone."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker, daemon=True, name="captura-worker")
            _worker_thread.start()


def encolar(evento_id: int, acusar: bool = True) -> None:
    """Encola un evento para procesarlo en serie. Lo llama el webhook.

    `acusar=False` procesa en SILENCIO, sin mandar el acuse al grupo. Se usa al reprocesar
    a mano un evento que YA se había procesado: el debounce del acuse solo fusiona dentro
    de 25 s, así que un reproceso posterior mandaría un segundo mensaje al grupo por un
    reporte que ya se había confirmado hace horas. El grupo del cliente no es un log.
    """
    _asegurar_worker()
    _cola.put((evento_id, acusar))


def rehidratar_cola() -> int:
    """Al ARRANCAR: reencola los eventos que quedaron PENDIENTES (guardados en BD pero no
    procesados) de un reinicio previo. Sin esto, un reinicio con la cola no vacía perdía
    esos reportes en silencio. Solo reprocesa los recientes (24 h) para no arrastrar viejos."""
    from datetime import datetime, timedelta, timezone
    from .db import SessionLocal
    limite = datetime.now(timezone.utc) - timedelta(hours=24)
    with SessionLocal() as session:
        ids = [i for (i,) in session.execute(
            select(EventoWhatsapp.id).where(
                EventoWhatsapp.estado_proceso == "pendiente",
                EventoWhatsapp.recibido_en >= limite,
            ).order_by(EventoWhatsapp.id))]
    if ids:
        log.warning("Rehidratando cola: %s evento(s) pendiente(s) por reprocesar tras reinicio", len(ids))
        for i in ids:
            encolar(i)
    return len(ids)


def _marcar_evento(evento_id: int, estado: str, inc_intento: bool = False,
                   error: str | None = None) -> int:
    """Actualiza el estado durable del evento en una sesión propia. Devuelve nº de intentos.

    `error` guarda el motivo del fallo para poder diagnosticarlo desde el panel: sin él,
    un evento fallido es una fila que dice "falló" y nada más.
    """
    from .db import SessionLocal
    with SessionLocal() as s:
        ev = s.get(EventoWhatsapp, evento_id)
        if ev is None:
            return 0
        if inc_intento:
            ev.intentos = (ev.intentos or 0) + 1
        ev.estado_proceso = estado
        if error is not None:
            ev.error = error[:2000]
        elif estado == "procesado":
            ev.error = None       # se resolvió: no dejar el motivo viejo colgando
        n = ev.intentos or 0
        s.commit()
        return n


def procesar_evento(evento: EventoWhatsapp) -> None:
    """Compat: procesa un evento (objeto) directamente, sin cola (usado en pruebas)."""
    _procesar(evento.id)


# ── Acuse consolidado (debounce por viaje) ───────────────────────────────────
# Un reporte llega en varios mensajes (texto + fotos) que se procesan por separado.
# Para mandar UN SOLO acuse con todo lo extraído, cada contribución a un viaje
# (re)programa un temporizador; cuando pasan ~25 s sin novedades de ese viaje, se
# envía un único acuse con el estado consolidado.
_DEBOUNCE_ACUSE_S = 25.0
_acuse_timers: "dict[int, threading.Timer]" = {}
# Generación por viaje: distingue "mi" temporizador del que lo reprogramó. Sin esto, un
# timer que ya estaba disparando borraba del dict el temporizador NUEVO que acababa de
# registrar _programar_acuse, y ese quedaba huérfano -> se enviaba más de un acuse.
_acuse_gen: "dict[int, int]" = {}
_acuse_lock = threading.Lock()


def _programar_acuse(viaje_id: int) -> None:
    """(Re)inicia el temporizador del acuse consolidado de un viaje."""
    with _acuse_lock:
        anterior = _acuse_timers.get(viaje_id)
        if anterior is not None:
            anterior.cancel()
        gen = _acuse_gen.get(viaje_id, 0) + 1
        _acuse_gen[viaje_id] = gen
        t = threading.Timer(_DEBOUNCE_ACUSE_S, _enviar_acuse_consolidado, args=(viaje_id, gen))
        t.daemon = True
        _acuse_timers[viaje_id] = t
        t.start()


def _enviar_acuse_consolidado(viaje_id: int, gen: int = 0) -> None:
    """Arma y envía UN acuse con el estado final del viaje (corre en un hilo Timer)."""
    from . import notify
    from .config import settings
    from .db import SessionLocal

    with _acuse_lock:
        if _acuse_gen.get(viaje_id) != gen:
            return   # me reprogramaron mientras disparaba: que lo mande el temporizador nuevo
        _acuse_gen.pop(viaje_id, None)
        _acuse_timers.pop(viaje_id, None)
    if not settings.bot_confirmar:
        return
    try:
        with SessionLocal() as session:
            viaje = session.get(Viaje, viaje_id)
            if viaje is None:
                return
            if viaje.retractado_en is not None:
                # Se borró el reporte mientras el acuse esperaba en el debounce. Mandar
                # "registré el viaje" después de haber avisado que el reporte se retiró
                # es contradecirse delante del grupo.
                log.info("Acuse del viaje %s cancelado: el reporte se retractó", viaje_id)
                return
            texto = notify.texto_confirmacion(viaje)   # con la sesión abierta (lazy loads)
        notify.enviar_texto(texto)
    except Exception:
        log.exception("Error enviando acuse consolidado del viaje %s", viaje_id)


# ── Reintentos ante fallos TRANSITORIOS de la IA ─────────────────────────────
# La API puede devolver 529 (sobrecargada), 429 (límite de tasa) o cortarse la red.
# Antes eso tiraba el reporte en silencio; ahora el evento se reencola con espera
# creciente, así que la información del grupo NO se pierde por una caída pasajera.
_MAX_REINTENTOS = 5


def _es_error_transitorio(exc: BaseException) -> bool:
    """True si el fallo es pasajero (vale la pena reintentar) y no un error de datos."""
    import anthropic

    if isinstance(exc, ai.SalidaNoEstructurada):
        return True   # la IA respondió mal formada: reintentar, no dar el dato por perdido
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in (408, 409, 429, 500, 502, 503, 529)

    # La base de datos también se cae, se reinicia o pierde la conexión un momento. Antes
    # eso caía en "error de datos" y el reporte quedaba FALLIDO permanente: un hipo de
    # Postgres —o el reinicio del contenedor— tiraba reportes buenos que solo había que
    # volver a intentar. Un deadlock o un timeout de lock es lo mismo: pasajero.
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, TimeoutError as SATimeout

    if isinstance(exc, (OperationalError, InterfaceError, SATimeout)):
        return True
    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return True
    return False


def _reintentar_o_descartar(evento_id: int, motivo: str | None = None) -> None:
    """Fallo transitorio de la IA: sube el contador (en BD), reencola con espera creciente
    (30/60/120/240/300 s) y, si se agotan los intentos, deja el evento 'fallido' para poder
    reprocesarlo a mano desde el panel. El estado en BD sobrevive a reinicios."""
    n = _marcar_evento(evento_id, "pendiente", inc_intento=True, error=motivo)
    if n > _MAX_REINTENTOS:
        _marcar_evento(evento_id, "fallido", error=motivo)
        log.error("Evento %s marcado FALLIDO: la IA siguió fallando tras %s reintentos "
                  "(reprocesable desde el panel)", evento_id, _MAX_REINTENTOS)
        return
    espera = min(30 * (2 ** (n - 1)), 300)
    log.warning("IA no disponible — reintento %s/%s del evento %s en %ss",
                n, _MAX_REINTENTOS, evento_id, espera)
    # El reintento automático SÍ acusa: ese reporte nunca llegó a confirmarse al grupo.
    t = threading.Timer(espera, encolar, args=(evento_id, True))
    t.daemon = True
    t.start()


def _procesar(evento_id: int, acusar: bool = True) -> None:
    """Procesa un evento crudo ya guardado y valida el viaje resultante.

    `acusar=False` hace todo el trabajo pero sin avisar al grupo (ver `encolar`).
    """
    from . import notify
    from .config import settings

    from .db import SessionLocal

    viaje_id: int | None = None
    reenvio: str | None = None
    reintentar = False
    permanente = False
    motivo: str | None = None
    with SessionLocal() as session:
        ev = session.get(EventoWhatsapp, evento_id)
        if ev is None:
            return
        try:
            tipo_borrosa = None
            viaje = None
            novedad = False   # ¿este evento creó o enriqueció algo? (para no acusar de más)

            # 1) Texto: puede ser un mensaje suelto O el pie de foto (caption) de
            #    una imagen. En ambos casos se intenta parsear como reporte de viaje.
            #    Si una foto de la misma ráfaga ya lo adelantó, se reusa (sin re-parsear).
            if ev.texto:
                viaje, cambiado = _reusar_o_parsear_texto(session, ev)
                # Un REPORTE siempre se acusa, aunque no traiga datos nuevos (p.ej. lo
                # reenviaron o el viaje ya estaba completo). Para quien lo manda, el
                # silencio parece que el bot no funcionó, y eso es peor que un acuse de
                # más: mejor confirmarle qué hay registrado. El debounce ya evita que una
                # ráfaga genere varios.
                if viaje is not None:
                    novedad = True

            # 2) Foto: enriquece el viaje objetivo. Si el caption de este mismo mensaje
            #    ya creó/identificó un viaje, ese manda como ancla (viaje_texto).
            if ev.media_path:
                viaje_foto, tipo_borrosa = _procesar_foto(session, ev, viaje_texto=viaje)
                if viaje_foto is not None:
                    novedad = True
                if viaje is None:
                    viaje = viaje_foto

            ev.estado_proceso = "procesado"   # marca durable: junto al commit del viaje
            if viaje is not None:
                anomalias = validar(session, viaje)
                session.commit()
                log.info("Viaje %s validado con %d anomalía(s) (rto_real=%s)",
                         viaje.id, len(anomalias), viaje.rto_real)
                if novedad:   # solo acusamos si el evento aportó algo (evita duplicados)
                    viaje_id = viaje.id
            else:
                session.commit()
                reenvio = tipo_borrosa   # foto huérfana ilegible, sin viaje al cual asociar
            _memo_publicar()   # confirmado en BD: ya se puede publicar el memo del texto
        except Exception as exc:
            session.rollback()
            _memo_descartar()  # se revirtió: esos viajes no existen, el memo no debe recordarlos
            motivo = f"{type(exc).__name__}: {exc}"
            if _es_error_transitorio(exc):
                # La IA está caída/sobrecargada: NO se pierde el reporte, se reintenta.
                reintentar = True
                log.warning("IA falló (%s) en el evento %s: %s",
                            type(exc).__name__, ev.id, str(exc)[:100])
            else:
                # Error de datos/código: se marca fallido para revisión (no reintento infinito).
                permanente = True
                log.exception("Error procesando evento %s", ev.id)

    if reintentar:
        _reintentar_o_descartar(evento_id, motivo)
        return
    if permanente:
        _marcar_evento(evento_id, "fallido", error=motivo)
        return

    # Fuera de la sesión: UN SOLO acuse consolidado por viaje (debounce). Si fue una foto
    # huérfana ilegible sin viaje, pedimos el reenvío de inmediato (no hay nada que acumular).
    if settings.bot_confirmar and acusar:
        if viaje_id is not None:
            _programar_acuse(viaje_id)
        elif reenvio:
            notify.enviar_texto(notify.mensaje_foto_borrosa(reenvio))
