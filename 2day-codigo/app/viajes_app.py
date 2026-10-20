"""Del viaje ASIGNADO al registro del HISTORIAL (tabla `viajes`).

POR QUÉ EXISTE. La tabla `viajes` —historial, rendimiento por operador, gasto por conductor,
anomalías, penalizaciones, «Mi rendimiento» del operador— sólo la llenaba el bot de
WhatsApp, que está retirado. En la plataforma web un viaje se asigna, se pide combustible y
se finaliza, pero nada escribía su renglón: el historial se quedaba vacío para siempre y
`SolicitudRecarga.viaje_id` siempre en nulo (lo reconoce `rendimiento._km_del_viaje`).

Aquí, cuando una asignación se CIERRA, se arma su viaje con lo que ya se sabe:

  · kilómetros: la diferencia de odómetro entre la primera y la última carga del viaje
    cuando hay dos lecturas que avanzan; si no, los km de la ruta (los que el coordinador
    confirmó, `rendimiento.km_efectivo`).
  · litros reales: lo despachado en el viaje (motor). Con dos lecturas de odómetro sólo
    cuentan las cargas DESPUÉS de la primera —el diésel de la primera carga se quema en los
    kilómetros que siguen—; con una sola lectura, todo lo cargado en el viaje.
  · litros del motor (`lts_scaner`): km ÷ el rendimiento real de la unidad según sus
    lecturas de motor (`rendimiento.vigente`), separado por configuración. Es «lo que el
    motor dice que debía quemar», contra lo que se compara lo cargado.
  · después, `validacion.validar` calcula rendimiento y desviación y levanta anomalías,
    igual que con los reportes del bot.

Es idempotente: cada viaje lleva `origen_message_id = 'asig:<id>'` y no se crea dos veces.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import rendimiento
from .config import fecha_flota
from .models import (AsignacionViaje, EstadoAsignacion, EstadoSolicitud, Remolque,
                     SolicitudRecarga, TipoConfig, Unidad, Viaje)

log = logging.getLogger("combustible.viajes_app")

_DESPACHADAS = (EstadoSolicitud.DESPACHADA, EstadoSolicitud.FACTURADA,
                EstadoSolicitud.CONCILIADA, EstadoSolicitud.EN_DISCREPANCIA)


def _origen(a: AsignacionViaje) -> str:
    return f"asig:{a.id}"


def _valor(evidencias, tipo: str) -> float | None:
    vals = [(e.valor_final if e.valor_final is not None else e.valor_ia)
            for e in (evidencias or []) if e.tipo == tipo]
    vals = [v for v in vals if v is not None]
    return float(vals[-1]) if vals else None


def _litros(s: SolicitudRecarga) -> float:
    o = s.orden
    if o is None:
        return 0.0
    v = o.litros_reales if o.litros_reales is not None else o.litros_autorizados
    return float(v or 0)


def _cuando(s: SolicitudRecarga):
    o = s.orden
    return (o.despachada_en if o and o.despachada_en else None) or s.creada_en


def viaje_de_asignacion(db: Session, a: AsignacionViaje, recalcular: bool = False) -> Viaje | None:
    """Crea el viaje del historial de una asignación cerrada (o, con `recalcular`, rehace sus
    cifras si ya existe: una carga despachada DESPUÉS de cerrar el viaje). No hace commit."""
    if a.estado == EstadoAsignacion.ACTIVA:
        return None
    ya = db.execute(select(Viaje).where(Viaje.origen_message_id == _origen(a))).scalar()
    if ya is not None and not recalcular:
        return ya
    sols = db.execute(select(SolicitudRecarga).where(
        SolicitudRecarga.asignacion_id == a.id,
        SolicitudRecarga.estado.in_(_DESPACHADAS))).scalars().all()
    motor = sorted((s for s in sols if (s.tipo_recarga or "motor") == "motor"), key=_cuando)
    termo = sorted((s for s in sols if s.tipo_recarga == "termo"), key=_cuando)
    # Un viaje CANCELADO sin diésel despachado no ocurrió: no se registra.
    if a.estado == EstadoAsignacion.CANCELADA and not sols:
        return None

    lecturas = []
    for s in motor:
        odo = s.odometro if s.odometro is not None else _valor(s.evidencias, "odometro")
        if odo is not None:
            lecturas.append((s, float(odo)))
    km, lts, fuente_km = None, None, None
    if len(lecturas) >= 2 and lecturas[-1][1] > lecturas[0][1]:
        km = lecturas[-1][1] - lecturas[0][1]
        primera = lecturas[0][0]
        posteriores = [s for s in motor if _cuando(s) > _cuando(primera)]
        lts = sum(_litros(s) for s in posteriores) or None
        fuente_km = "odómetro"
    else:
        km = rendimiento.km_efectivo(a)
        lts = sum(_litros(s) for s in motor) or None
        fuente_km = "ruta"

    uni = db.get(Unidad, a.unidad_id)
    cfg = rendimiento.config_de_asignacion(db, a, uni)
    lts_motor = None
    if km:
        r = rendimiento.vigente(db, a.unidad_id, config=cfg)
        if not r.hay and cfg is not None:
            r = rendimiento.vigente(db, a.unidad_id)
        if r.hay:
            lts_motor = round(km / r.valor, 2)

    rem_eco = None
    for rid in (a.remolque_ids or []):
        rem = db.get(Remolque, rid)
        if rem is not None and (rem.usa_combustible or rem_eco is None):
            rem_eco = rem.eco
            if rem.usa_combustible:
                break

    cuando = a.finalizada_en or datetime.now(timezone.utc)
    campos = dict(
        fecha=fecha_flota(cuando), unidad_id=a.unidad_id, operador_id=a.operador_id,
        tipo_config=TipoConfig(cfg) if cfg in TipoConfig._value2member_map_ else None,
        kilometros=round(km, 1) if km else None, lts_real=round(lts, 2) if lts else None,
        lts_scaner=lts_motor,
        odometro=lecturas[-1][1] if lecturas else None,
        horas_termo=next((h for h in (_valor(s.evidencias, "termo") for s in reversed(termo))
                          if h is not None), None),
        remolque_thermo=rem_eco,
        reportado_por="app", origen_message_id=_origen(a),
        correcciones=(f"Generado al cerrar la asignación #{a.id} ({a.destino or 'sin destino'}). "
                      f"Km de {fuente_km}; litros del motor = km ÷ rendimiento de sus lecturas "
                      f"de motor; {len(motor)} carga(s) de motor y {len(termo)} de termo.")[:2000],
    )
    if ya is not None:
        # Recalcular: se rehacen las cifras del MISMO renglón (nada se borra ni se duplica).
        if ya.retractado_en is not None:
            return ya
        for k, val in campos.items():
            setattr(ya, k, val)
        v = ya
    else:
        v = Viaje(**campos)
        db.add(v)
    db.flush()
    for s in sols:
        if s.viaje_id is None:
            s.viaje_id = v.id
    from .validacion import validar
    validar(db, v)
    return v


def recalcular_de_solicitud(db: Session, solicitud: SolicitudRecarga) -> None:
    """Si la solicitud es de un viaje YA CERRADO, rehace su renglón del historial (p.ej. la
    carga se despachó después de que el coordinador finalizó el viaje). Commit."""
    if not solicitud.asignacion_id:
        return
    a = db.get(AsignacionViaje, solicitud.asignacion_id)
    if a is None or a.estado == EstadoAsignacion.ACTIVA:
        return
    viaje_de_asignacion(db, a, recalcular=True)
    db.commit()


def registrar_pendientes(db: Session) -> int:
    """Crea los viajes del historial de TODAS las asignaciones cerradas que aún no lo tienen.
    Se llama después de cada cierre y al arrancar (para las que se cerraron antes). Commit."""
    hechos = set(db.execute(select(Viaje.origen_message_id)
                            .where(Viaje.origen_message_id.like("asig:%"))).scalars())
    n = 0
    for a in db.execute(select(AsignacionViaje).where(
            AsignacionViaje.estado != EstadoAsignacion.ACTIVA)
            .order_by(AsignacionViaje.finalizada_en, AsignacionViaje.id)).scalars().all():
        if _origen(a) in hechos:
            continue
        try:
            with db.begin_nested():
                if viaje_de_asignacion(db, a) is not None:
                    n += 1
        except Exception:
            log.exception("No se pudo registrar el viaje de la asignación %s", a.id)
    if n:
        db.commit()
        log.info("Historial: %d viaje(s) registrados desde asignaciones cerradas", n)
    return n
