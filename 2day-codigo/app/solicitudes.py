"""Ciclo de vida de la solicitud de recarga — la máquina de estados y sus reglas.

Toda la operación gira alrededor de este objeto. Aquí vive UNA sola definición de qué
transiciones son válidas y quién puede hacerlas, para que ningún endpoint invente un salto
de estado por su cuenta. Reglas de integridad (sección 7.2 del documento de migración):

  · Nada se borra: una solicitud equivocada se ANULA con motivo y queda en el historial.
  · Cada transición deja registro de quién, cuándo y qué cambió.
  · No se puede saltar estados: no se factura lo que no se despachó ni se despacha lo que
    no se autorizó.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy.orm import Session

from . import rendimiento

from .models import (
    EstadoSolicitud, OrdenDespacho, SolicitudRecarga, TransicionSolicitud,
)

log = logging.getLogger("combustible.solicitudes")

E = EstadoSolicitud

# Transiciones permitidas: estado actual -> estados a los que puede pasar. Cualquier salto
# que no esté aquí se rechaza. La bandeja del coordinador y el módulo de combustible solo
# pueden mover la solicitud por estos caminos.
TRANSICIONES: dict[EstadoSolicitud, set[EstadoSolicitud]] = {
    E.BORRADOR: {E.ENVIADA, E.ANULADA},
    E.ENVIADA: {E.EN_VALIDACION, E.ANULADA},
    E.EN_VALIDACION: {E.AUTORIZADA, E.DEVUELTA, E.RECHAZADA, E.ANULADA},
    E.DEVUELTA: {E.ENVIADA, E.ANULADA},          # el operador la corrige y reenvía
    E.AUTORIZADA: {E.DESPACHADA, E.EN_DISCREPANCIA, E.ANULADA},
    # CONCILIADA directa: retiradas las facturas, la prueba de que la recarga se cobró es
    # el renglón del Excel del proveedor, no un CFDI. Sin esta salida toda recarga
    # despachada se quedaría atascada en DESPACHADA para siempre —hoy hay tres así—.
    # FACTURADA se conserva: no se borra un estado por el que podrían haber pasado
    # expedientes. Hoy no pasó ninguno, y por eso este cambio no migra nada.
    E.DESPACHADA: {E.FACTURADA, E.CONCILIADA, E.EN_DISCREPANCIA},
    E.FACTURADA: {E.CONCILIADA, E.EN_DISCREPANCIA},
    E.EN_DISCREPANCIA: {E.CONCILIADA, E.ANULADA},  # una persona la resuelve
    # RECHAZADA, CONCILIADA y ANULADA son finales: no salen a ningún lado.
    E.RECHAZADA: set(),
    E.CONCILIADA: set(),
    E.ANULADA: set(),
}

# Qué rol puede provocar cada transición de ENTRADA a un estado. 'admin' puede todo, así que
# se añade en todas. El operador solo mueve lo suyo hasta ENVIADA; el resto es coordinador
# o combustible.
#
# EL GERENTE AUTORIZA (decisión del dueño, 20-sep-2026). Su panel llevaba el botón desde
# agosto y NUNCA pudo funcionar: el rol no estaba aquí, así que cada pulsación devolvía un
# 409 sin explicación. Ahora entra en los dos destinos que hacen falta para autorizar de un
# tirón —tomar la solicitud y autorizarla—, y en ninguno más: devolver, rechazar y anular
# siguen siendo del coordinador, igual que el despacho sigue siendo de combustible.
_PERMISO_DESTINO: dict[EstadoSolicitud, set[str]] = {
    E.ENVIADA: {"operador", "coordinador", "admin"},
    E.EN_VALIDACION: {"coordinador", "gerente", "admin"},
    E.AUTORIZADA: {"coordinador", "gerente", "admin"},
    E.DEVUELTA: {"coordinador", "admin"},
    E.RECHAZADA: {"coordinador", "admin"},
    E.DESPACHADA: {"combustible", "admin"},
    E.FACTURADA: {"combustible", "admin"},
    E.CONCILIADA: {"coordinador", "combustible", "admin"},
    E.EN_DISCREPANCIA: {"coordinador", "combustible", "admin"},
    E.ANULADA: {"coordinador", "admin"},
}


class TransicionInvalida(ValueError):
    """El salto de estado pedido no está permitido, o el rol no puede hacerlo."""


def _coherencia_fisica(solicitud: SolicitudRecarga, destino: EstadoSolicitud) -> None:
    """Los dos estados finales tienen que coincidir con lo que pasó de verdad en la pipa.

    Esto NO cabe en el grafo de arriba porque no depende de por dónde viene la solicitud sino
    de si el diésel se movió. Ambos caminos eran legales estado a estado y llevaban a un
    expediente que mentía:

      · AUTORIZADA -> EN_DISCREPANCIA -> CONCILIADA cerraba el ciclo («todo cuadró») de una
        recarga que nunca se despachó, con `litros_reales` en NULL y sin CFDI. Y CONCILIADA
        es final: no hay marcha atrás.
      · DESPACHADA/FACTURADA -> EN_DISCREPANCIA -> ANULADA rotulaba como «anulada» un diésel
        que ya estaba en el tanque, mientras sus litros seguían sumando en los tableros
        —que agregan `OrdenDespacho.litros_reales` sin mirar el estado de la solicitud—.

    El salto directo estaba bien cerrado en los dos casos; se colaban por EN_DISCREPANCIA.
    """
    orden = solicitud.orden
    surtido = orden.litros_reales if orden is not None else None

    if destino is E.CONCILIADA and surtido is None:
        raise TransicionInvalida(
            "No se puede conciliar una recarga que nunca se despachó: no hay litros reales "
            "que cuadrar contra el renglón del proveedor. Si ya no procede, anúlala.")

    if destino is E.ANULADA and surtido is not None:
        raise TransicionInvalida(
            f"Ya se despacharon {surtido:g} L contra esta solicitud. Anularla los borraría "
            f"del expediente sin sacar el diésel del tanque: los tableros seguirían "
            f"contándolos y el folio seguiría pegado a su factura.")


def puede(actual: EstadoSolicitud, destino: EstadoSolicitud, rol: str) -> bool:
    if destino not in TRANSICIONES.get(actual, set()):
        return False
    return rol in _PERMISO_DESTINO.get(destino, set())


def transicionar(session: Session, solicitud: SolicitudRecarga,
                 destino: EstadoSolicitud, usuario: dict,
                 nota: str | None = None, cambios: dict | None = None) -> TransicionSolicitud:
    """Mueve la solicitud a un estado nuevo dejando el registro de quién y qué cambió.

    Lanza TransicionInvalida si el salto no es válido o el rol no tiene permiso. NO hace
    commit: el endpoint decide cuándo cerrar la transacción.
    """
    actual = solicitud.estado
    rol = usuario.get("rol", "")
    if not puede(actual, destino, rol):
        raise TransicionInvalida(
            f"No se puede pasar de '{actual.value}' a '{destino.value}'"
            f"{' con rol ' + rol if rol else ''}.")
    _coherencia_fisica(solicitud, destino)

    tr = TransicionSolicitud(
        solicitud_id=solicitud.id,
        estado_anterior=actual.value,
        estado_nuevo=destino.value,
        por_usuario_id=usuario.get("id"),
        cambios=cambios or None,
        # Recortada AQUÍ además de en `solicitud.motivo`: la columna es String(400) y la
        # misma cadena larga llegaba entera, así que Postgres la rechazaba, se caía la
        # transacción completa y la devolución no ocurría —con un 500 sin explicación y la
        # solicitud donde estaba—. Un motivo largo es torpe, no un fallo del sistema.
        nota=nota[:400] if nota else nota,
    )
    session.add(tr)
    solicitud.estado = destino
    # EN_DISCREPANCIA entra en la lista por la misma razón que las otras tres: son los
    # estados en los que la solicitud se detiene y alguien tiene que leer por qué.
    if nota and destino in (E.DEVUELTA, E.RECHAZADA, E.ANULADA, E.EN_DISCREPANCIA):
        solicitud.motivo = nota[:400]
    log.info("Solicitud %s: %s -> %s (por usuario %s)",
             solicitud.id, actual.value, destino.value, usuario.get("id"))
    return tr


_FOLIO_ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # sin O/0/I/1/L (caracteres ambiguos)


def generar_folio(session: Session) -> str:
    """Folio ALEATORIO alfanumérico, único e inmutable, para una orden de despacho.

    Es la llave visible que une orden, despacho y factura, así que NUNCA debe reutilizarse.
    Antes era consecutivo ('OD-000123'); ahora es 'OD-' + 8 caracteres al azar de un alfabeto
    SIN caracteres ambiguos (O/0/I/1/L), para que el folio no revele volumen ni sea adivinable.
    La garantía REAL de unicidad es el índice UNIQUE de la columna (más el reintento de
    autorizar() ante colisión); aquí solo se descartan de antemano los folios ya usados.
    """
    from sqlalchemy import select

    for _ in range(8):
        folio = "OD-" + "".join(secrets.choice(_FOLIO_ALFABETO) for _ in range(8))
        if not session.scalar(select(OrdenDespacho.id).where(OrdenDespacho.folio == folio)):
            return folio
    return "OD-" + "".join(secrets.choice(_FOLIO_ALFABETO) for _ in range(8))


def autorizar(session: Session, solicitud: SolicitudRecarga, usuario: dict,
              litros: float | None = None, sugeridos: float | None = None,
              sugerencia_motivo: str | None = None) -> OrdenDespacho:
    """Autoriza una solicitud: la pasa a AUTORIZADA y genera su orden de despacho con folio.

    La orden nace con unidad, viaje y operador, así que la factura después solo confirma —
    no hay que adivinar a qué viaje pertenece la carga.

    `sugeridos` es lo que la IA había propuesto, y se guarda AL LADO de lo autorizado, nunca
    encima: es lo único que permite preguntar después si a la propuesta se le hizo caso.
    """
    from sqlalchemy.exc import IntegrityError

    transicionar(session, solicitud, EstadoSolicitud.AUTORIZADA, usuario,
                 nota="Autorizada; se genera orden de despacho")
    litros_aut = litros if litros is not None else solicitud.litros_solicitados

    # Techo del 2%: no se BLOQUEA, se deja constancia. La variación del propio escáner
    # entre lecturas (12.8% típico) haría que un candado duro parara despachos buenos;
    # lo que sirve es el registro de cuántas veces hubo que pasarse y en qué unidad.
    #
    # Ese registro NO EXISTÍA: era el log.warning de abajo y nada más, y un log rota con
    # el archivo y no contesta «¿cuántas veces y en qué unidades?». Ahora el tope vigente
    # se copia a la orden —la fila que de todos modos se está escribiendo—, así que la
    # pregunta pasa de ser irrecuperable a ser una consulta.
    tope = rendimiento.tope_autorizable(session, solicitud)
    if tope.excede(litros_aut):
        log.warning("Solicitud %s: se autorizan %.0f L sobre un tope de %.0f L (%s)",
                    solicitud.id, litros_aut, tope.tope, tope.motivo)
    elif not tope.hay:
        log.info("Solicitud %s: sin tope de despacho (%s)", solicitud.id, tope.motivo)
    # El folio es aleatorio: la colisión es astronómicamente improbable, pero el índice UNIQUE
    # es la red real. Se reintenta dentro de un SAVEPOINT para que un choque revierta SOLO la
    # orden y NO la transición a AUTORIZADA ya escrita arriba.
    orden = None
    for _ in range(5):
        try:
            with session.begin_nested():
                orden = OrdenDespacho(
                    folio=generar_folio(session),
                    solicitud_id=solicitud.id,
                    autorizada_por_id=usuario.get("id"),
                    litros_autorizados=litros_aut,
                    litros_sugeridos=sugeridos,
                    sugerencia_motivo=(sugerencia_motivo or None),
                    # El tope tal como estaba EN ESTE INSTANTE. Los tres juntos porque
                    # sueltos no se leen: un «se pasó 40 L» sin la base no dice si fue
                    # mucho, y sin el motivo no dice de dónde salía el techo.
                    tope_litros=tope.tope,
                    tope_estimado=tope.estimado,
                    tope_motivo=(tope.motivo or None),
                )
                session.add(orden)
                session.flush()
            break
        except IntegrityError:
            orden = None
    if orden is None:
        raise RuntimeError("No se pudo generar un folio único para la orden de despacho")
    log.info("Orden de despacho %s generada para la solicitud %s", orden.folio, solicitud.id)
    return orden
