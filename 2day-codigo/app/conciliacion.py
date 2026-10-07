"""Vinculación escaneo del motor -> fila de bitácora.

HALLAZGO que sustenta este módulo: cada fila de la bitácora del Excel NO es un viaje
individual, sino UN PERÍODO DE ESCANEO, fechado el día de la extracción. Se verificó
contra los 45 PDF reales: 36 coinciden EXACTO en km y litros con la fila de esa
unidad+fecha, 0 difieren.

Por eso el escaneo puede LLENAR la fila automáticamente (que es justo lo que el analista
hacía a mano), dejando como única captura manual los `lts_real` (los comprobantes de carga).
Con ambos:  DIF = lts_real - lts_scaner  ->  detección de robo de diésel.

El mapeo escaneo->columna fue verificado dato por dato contra la bitácora existente:
  km            -> kilometros          litros        -> lts_scaner
  vel_max       -> vel_max             pct_ralenti   -> ralenti
  pct_crucero   -> crucero             pct_top_gear  -> top_gear
  km_top_gear   -> km_top_gear         carga_prom    -> c_manejo
  frenadas      -> num_frenadas        paradas_panico-> paradas_panico
  fuera_marcha  -> neutralizacion      (pct_top_gear real = km_top_gear/km*100)
NO se llena `rpm`: la columna de la bitácora no corresponde a ninguna lectura del
escaneo (bitácora 2781 vs escaneo 971 rpm), así que se deja intacta en vez de inventarla.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EscaneoMotor, Unidad, Viaje

log = logging.getLogger("combustible.conciliacion")

# escaneo -> campo del viaje. Solo lo verificado empíricamente.
MAPEO = {
    "km": "kilometros",
    "litros": "lts_scaner",
    "vel_max": "vel_max",
    "pct_ralenti": "ralenti",
    "pct_crucero": "crucero",
    "pct_top_gear": "top_gear",
    "km_top_gear": "km_top_gear",
    "carga_prom": "c_manejo",
    "frenadas": "num_frenadas",
    "paradas_panico": "paradas_panico",
    "fuera_marcha": "neutralizacion",
}


def viaje_del_escaneo(session: Session, esc: EscaneoMotor, crear: bool = False) -> Viaje | None:
    """La fila de bitácora que corresponde al escaneo (misma unidad, fecha = fin del período)."""
    v = session.execute(
        select(Viaje).where(Viaje.unidad_id == esc.unidad_id, Viaje.fecha == esc.periodo_fin)
        .order_by(Viaje.id)
    ).scalars().first()
    if v is None and crear:
        v = Viaje(unidad_id=esc.unidad_id, fecha=esc.periodo_fin)
        session.add(v)
        session.flush()
        log.info("Fila de bitácora creada desde escaneo: unidad %s fecha %s (viaje %s)",
                 esc.unidad.clave if esc.unidad else esc.unidad_id, esc.periodo_fin, v.id)
    return v


def aplicar_escaneo(esc: EscaneoMotor, viaje: Viaje, sobrescribir: bool = False) -> list[str]:
    """Copia los datos medidos por el motor a la fila. Devuelve los campos que cambiaron.

    Por defecto NO sobrescribe lo que ya tiene valor (respeta la captura previa del
    analista); con `sobrescribir=True` el escaneo manda (es la medición del propio camión).
    """
    cambios = []
    for origen, destino in MAPEO.items():
        nuevo = getattr(esc, origen, None)
        if nuevo is None:
            continue
        actual = getattr(viaje, destino)
        if actual is not None and not sobrescribir:
            continue
        if actual is not None and abs(float(actual) - float(nuevo)) < 1e-9:
            continue
        setattr(viaje, destino, nuevo)
        cambios.append(destino)
    # % de top gear sobre la distancia del período (la bitácora lo guarda calculado)
    if esc.km and esc.km_top_gear is not None:
        pct = esc.km_top_gear / esc.km * 100
        if viaje.pct_top_gear is None or sobrescribir:
            viaje.pct_top_gear = pct
            cambios.append("pct_top_gear")
    # El odómetro del escaneo es el acumulado del motor al cierre del período
    if esc.odometro_total is not None and (viaje.odometro is None or sobrescribir):
        viaje.odometro = esc.odometro_total
        cambios.append("odometro")
    return cambios


def conciliar(session: Session, esc: EscaneoMotor) -> dict:
    """Compara lo que dice el MOTOR contra lo capturado (comprobantes) en esa fila.

    DIF = litros del comprobante - litros del motor. Positivo grande = se cargó más diésel del
    que el motor quemó -> señal de robo/sifoneo. Devuelve None en 'dif' si aún no hay comprobante.
    """
    v = viaje_del_escaneo(session, esc)
    lts_real = v.lts_real if v is not None else None
    dif = (lts_real - esc.litros) if lts_real is not None else None
    return {
        "escaneo_id": esc.id,
        "unidad": esc.unidad.clave if esc.unidad else None,
        "periodo_inicio": esc.periodo_inicio,
        "periodo_fin": esc.periodo_fin,
        "km_motor": esc.km,
        "lts_motor": esc.litros,
        "rend_motor": esc.rendimiento,
        "lts_ralenti": esc.lts_ralenti,
        "pct_ralenti": esc.pct_ralenti,
        "viaje_id": v.id if v is not None else None,
        "lts_comprobante": lts_real,
        "dif": dif,
        "pct_dif": (dif / esc.litros) if (dif is not None and esc.litros) else None,
        "rend_real": (esc.km / lts_real) if lts_real else None,
    }


# ── Cruce a nivel VIAJE (lo que cubre el 99% de las filas) ───────────────────
# La conciliación por escaneo (arriba) da el detalle fino de 45 períodos. Pero los
# litros del motor ya están copiados en 2,443 viajes, así que el cruce contra el
# comprobante se puede hacer sobre TODA la bitácora, no solo sobre los PDF que tenemos.
#
# OJO AL INTERPRETAR LA DIFERENCIA: el escáner mide lo que quemó el MOTOR. NO mide el
# equipo de refrigeración (termo), que también consume diésel del mismo tanque. Con
# ~54,000 horas de termo registradas a 2–4 L/h, la refrigeración explica un volumen
# del mismo orden que la brecha observada. Por eso esto se presenta como "a explicar",
# nunca como robo: sin descontar el termo, la cifra no es interpretable.

# Un comprobante que difiere del escáner más de esto NO es una brecha: es un error de
# captura. Ya pasó (T230: comprobante 27,335 L contra 67 L del escáner, 409x). Si esas filas
# entran, se llevan el primer lugar del ranking y acusan a la unidad equivocada.
RATIO_MAX = 3.0


def _plausible():
    from sqlalchemy import and_
    return and_(Viaje.lts_real <= Viaje.lts_scaner * RATIO_MAX,
                Viaje.lts_real >= Viaje.lts_scaner / RATIO_MAX)


def cruce_por_unidad(session: Session, anio: int | None = None,
                     solo_plausibles: bool = True) -> dict:
    """Motor vs comprobante agrupado por unidad: dónde está la brecha y de qué tamaño."""
    from sqlalchemy import extract, func

    cond = [Viaje.lts_scaner.isnot(None), Viaje.lts_real.isnot(None),
            Viaje.lts_scaner > 0, Viaje.lts_real > 0,
            Viaje.retractado_en.is_(None)]
    if anio:
        cond.append(extract("year", Viaje.fecha) == anio)
    if solo_plausibles:
        cond.append(_plausible())

    filas = session.execute(
        select(Unidad.clave, Unidad.tipo,
               func.count(Viaje.id), func.sum(Viaje.kilometros),
               func.sum(Viaje.lts_scaner), func.sum(Viaje.lts_real),
               func.min(Viaje.fecha), func.max(Viaje.fecha))
        .join(Viaje, Viaje.unidad_id == Unidad.id)
        .where(*cond).group_by(Unidad.clave, Unidad.tipo)
    ).all()

    # Períodos que abarcan más de 40 días (días desde la fila previa de la misma unidad).
    prev = func.lag(Viaje.fecha).over(partition_by=Viaje.unidad_id, order_by=Viaje.fecha)
    # La subconsulta lleva el ID DE LA FILA: unir por unidad daría un producto cartesiano
    # (cada viaje contra todos los de su unidad) y el conteo saldría inflado.
    sub = select(Viaje.id.label("vid"), (Viaje.fecha - prev).label("dias")).subquery()
    _largos = {c: n for c, n in session.execute(
        select(Unidad.clave, func.count())
        .select_from(Viaje).join(Unidad, Viaje.unidad_id == Unidad.id)
        .join(sub, sub.c.vid == Viaje.id)
        .where(*cond, sub.c.dias > 40).group_by(Unidad.clave)
    ).all()}

    unidades = []
    for clave, tipo, n, km, motor, comprobante in ((f[0], f[1], f[2], f[3], f[4], f[5]) for f in filas):
        motor, comprobante, km = float(motor or 0), float(comprobante or 0), float(km or 0)
        if motor <= 0:
            continue
        unidades.append({
            "unidad": clave, "tipo": tipo.value if tipo else None, "periodos": n,
            "km": km, "lts_motor": motor, "lts_comprobante": comprobante,
            "dif": comprobante - motor, "pct_dif": (comprobante - motor) / motor * 100,
            "rend_motor": (km / motor) if motor else None,
            "rend_comprobante": (km / comprobante) if comprobante else None,
            # Un período que abarca meses (el hueco jul-2025→mar-2026) mueve el total de
            # una unidad él solo. Es dato válido para un balance de combustible, pero hay
            # que poder verlo: si la brecha de una unidad viene de una sola fila larga,
            # no es lo mismo que una desviación sostenida a lo largo del año.
            "periodos_largos": _largos.get(clave, 0),
        })
    unidades.sort(key=lambda x: -x["dif"])

    # Cuántas filas se dejaron fuera y por qué: nada se descarta en silencio.
    base_sin = [c for c in cond if c is not _plausible] if not solo_plausibles else [
        Viaje.lts_scaner.isnot(None), Viaje.lts_real.isnot(None),
        Viaje.lts_scaner > 0, Viaje.lts_real > 0, Viaje.retractado_en.is_(None)]
    if anio:
        base_sin = base_sin + [extract("year", Viaje.fecha) == anio]
    total_filas = session.scalar(select(func.count(Viaje.id)).where(*base_sin)) or 0
    usadas = sum(u["periodos"] for u in unidades)

    t_km = sum(u["km"] for u in unidades)
    t_motor = sum(u["lts_motor"] for u in unidades)
    t_comprobante = sum(u["lts_comprobante"] for u in unidades)
    return {
        "unidades": unidades,
        "descartadas": max(total_filas - usadas, 0),
        "ratio_max": RATIO_MAX,
        "totales": {
            "unidades": len(unidades),
            "periodos": sum(u["periodos"] for u in unidades),
            "km": t_km, "lts_motor": t_motor, "lts_comprobante": t_comprobante,
            "dif": t_comprobante - t_motor,
            "pct_dif": ((t_comprobante - t_motor) / t_motor * 100) if t_motor else None,
            "rend_motor": (t_km / t_motor) if t_motor else None,
            "rend_comprobante": (t_km / t_comprobante) if t_comprobante else None,
        },
    }


def cruce_por_anio(session: Session) -> list[dict]:
    """La misma comparación año por año: muestra si la brecha se está abriendo.

    Usa EXACTAMENTE el mismo filtro de plausibilidad que `cruce_por_unidad`. Sin eso, la
    tarjeta de arriba y la tabla de abajo de la MISMA pantalla mostraban dos diferencias
    distintas para el mismo año (+101,941 L contra +169,632 L): el usuario no tenía forma
    de saber cuál creer.
    """
    from sqlalchemy import extract, func

    anio = extract("year", Viaje.fecha)
    filas = session.execute(
        select(anio, func.count(Viaje.id), func.sum(Viaje.kilometros),
               func.sum(Viaje.lts_scaner), func.sum(Viaje.lts_real))
        .where(Viaje.lts_scaner > 0, Viaje.lts_real > 0, Viaje.retractado_en.is_(None),
               _plausible())
        .group_by(anio).order_by(anio)
    ).all()
    out = []
    for a, n, km, motor, comprobante in filas:
        motor, comprobante, km = float(motor or 0), float(comprobante or 0), float(km or 0)
        if motor <= 0:
            continue
        out.append({
            "anio": int(a), "periodos": n, "km": km,
            "lts_motor": motor, "lts_comprobante": comprobante,
            "dif": comprobante - motor, "pct_dif": (comprobante - motor) / motor * 100,
            "rend_motor": km / motor, "rend_comprobante": (km / comprobante) if comprobante else None,
        })
    return out


def horas_termo(session: Session, meses: float = 6.0) -> dict:
    """Cuánto diésel quema el equipo de frío — el consumo que el escáner NO ve.

    El consumo está MEDIDO, no supuesto: 12 camiones auditados en junio dan 1.97 L/h con
    un rango estrecho (1.71–2.15). Lo que sí es extrapolación es cuántos equipos de la
    flota consumen a ese ritmo, así que se devuelve una BANDA:
      - piso: solo los camiones refrigerados (lo que efectivamente se auditó);
      - techo: además los tractos que jalan remolque con termo.
    La base es delgada (un mes, doce unidades) y eso se declara aquí para que quien lea
    la cifra sepa cuánto pesa.
    """
    from sqlalchemy import func

    from .models import AuditoriaThermo, TipoUnidad, Unidad

    medidos = [a for a in session.execute(select(AuditoriaThermo)).scalars()
               if (a.litros_thermo or 0) > 0 and (a.horas_trab or 0) > 0]
    if not medidos:
        return {"medido": False, "nota": "Sin mediciones de consumo del termo todavía."}

    litros = sum(a.litros_thermo for a in medidos)
    horas = sum(a.horas_trab for a in medidos)
    lts_hora = litros / horas
    horas_mes = horas / len(medidos)

    camiones = session.scalar(select(func.count(Unidad.id)).where(
        Unidad.tipo == TipoUnidad.CAMION, Unidad.activo.is_(True))) or 0
    tractos = session.scalar(select(func.count(Unidad.id)).where(
        Unidad.tipo == TipoUnidad.TRACTO, Unidad.activo.is_(True))) or 0

    por_equipo = horas_mes * lts_hora * meses
    return {
        "medido": True,
        "lts_hora": lts_hora,
        "horas_mes_por_equipo": horas_mes,
        "unidades_medidas": len(medidos),
        "horas_medidas": horas,
        "litros_medidos": litros,
        "camiones": camiones, "tractos": tractos, "meses": meses,
        "lts_min": camiones * por_equipo,
        "lts_max": (camiones + tractos) * por_equipo,
        "nota": ("El escáner mide solo el motor; el termo consume del mismo tanque y no "
                 "aparece ahí. El consumo por hora está medido, pero cuántos equipos "
                 "operan a ese ritmo es una estimación: por eso es una banda."),
    }


def vincular_todos(session: Session, crear_faltantes: bool = True,
                   sobrescribir: bool = False) -> dict:
    """Recorre todos los escaneos, los vincula con su fila y llena los campos medidos."""
    from .validacion import validar

    escaneos = session.execute(
        select(EscaneoMotor).order_by(EscaneoMotor.unidad_id, EscaneoMotor.periodo_fin)
    ).scalars().all()
    creadas = actualizadas = sin_cambio = 0
    campos_tocados: dict[str, int] = {}
    for esc in escaneos:
        antes = session.execute(
            select(Viaje.id).where(Viaje.unidad_id == esc.unidad_id, Viaje.fecha == esc.periodo_fin)
        ).first()
        v = viaje_del_escaneo(session, esc, crear=crear_faltantes)
        if v is None:
            continue
        if antes is None:
            creadas += 1
        cambios = aplicar_escaneo(esc, v, sobrescribir=sobrescribir)
        if cambios:
            actualizadas += 1
            for c in cambios:
                campos_tocados[c] = campos_tocados.get(c, 0) + 1
            validar(session, v)      # recalcula rto/dif/pct y re-evalúa anomalías
        else:
            sin_cambio += 1
    session.commit()
    return {"escaneos": len(escaneos), "filas_creadas": creadas,
            "filas_actualizadas": actualizadas, "sin_cambio": sin_cambio,
            "campos": campos_tocados}
