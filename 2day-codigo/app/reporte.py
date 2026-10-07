"""Reporte ejecutivo de combustible (estilo del entregable del cliente).

Compara dos periodos (típicamente el mismo rango de meses de dos años) y responde las
preguntas del reporte: cómo cambió el rendimiento por tipo de configuración frente al
IDEAL, cuánto cuesta el ralentí, qué operadores generan más costo extra, y cuánto dinero
representa todo.

Definiciones (las del cliente):
  rendimiento   = km / litros reales (los del comprobante)
  brecha        = rendimiento - ideal            (negativa = rinde menos de lo debido)
  litros de más = litros reales - km / ideal     (lo que sobró por no alcanzar el ideal)
  costo extra   = litros de más * precio del litro
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Float, and_, func, or_, select
from sqlalchemy.orm import Session

from .models import (
    EscaneoMotor, Operador, TipoConfig, Unidad, Viaje,
)

# Rendimiento ideal por configuración: una sola fuente en models.py.
# La referencia de cada unidad sale de SU escáner, no de una tabla por configuración.
# `RENDIMIENTO_IDEAL` —FULL 1.9, SENCILLO 2.8, THORTON 4.0, igual para las 53 unidades—
# se retiró el 20-sep-2026, y aquí alimentaba un `costo_extra` en PESOS con NOMBRES de
# operadores, en un informe ejecutivo. Era la peor forma del mismo defecto.


def _refs(session: Session, config: str | None = None) -> dict[int, tuple[float, float]]:
    """El km/l medido de cada unidad con escáner, y sobre cuántos km se midió.

    Con `config`, la referencia viene separada para esa configuración: el escáner mide la
    unidad durante una ventana, sin saber si iba jalando uno o dos remolques, y un doble
    quema más por kilómetro. Juzgar un viaje FULL contra la referencia mezclada le cobra a
    una persona una diferencia que es del enganche, no suya.

    Se memoriza por configuración: son 65 unidades y cada consulta de escáner recorre sus
    lecturas, así que sin caché el informe las recorrería una vez por renglón.

    El segundo número no es adorno. T182 marca 4.15 km/L —la referencia más alta de la
    flota— sobre 1,839 km de escaneo, y con ella el informe le cobra $184,926 a una
    persona por 35,109 km recorridos. Medido no es lo mismo que sólido: quien lea el
    reclamo tiene que ver esa proporción.
    """
    from . import rendimiento as _rend
    cache = session.info.setdefault("_reporte_refs", {})
    if config in cache:
        return cache[config]
    out: dict[int, tuple[float, float]] = {}
    for (uid,) in session.execute(select(Unidad.id)).all():
        r = _rend.vigente(session, uid, config=config)
        if r.hay:
            out[uid] = (r.valor, float(r.km or 0.0))
    cache[config] = out
    return out

def _factores(session: Session) -> dict:
    """El factor de configuración, sobre cuánto se midió, y qué no se pudo descartar.

    Lo último no es modestia: es la diferencia entre un número que se puede defender y uno
    que no. Quien lea un cobro tiene derecho a saber sobre qué descansa y dónde se acaba la
    evidencia.
    """
    from . import rendimiento as _rend
    from sqlalchemy import func
    from .models import TipoConfig

    f = _rend.factor_config(session)
    n = session.info.get("_factor_config", {}).get("n", {})
    out = {}
    for c, v in f.items():
        if c == _rend.CONFIG_BASE:
            continue
        km, viajes = session.execute(
            select(func.sum(Viaje.kilometros), func.count(Viaje.id))
            .where(Viaje.tipo_config == TipoConfig(c), Viaje.kilometros > 0,
                   Viaje.lts_real > 0)).one()
        out[c] = {
            "factor": v,
            "unidades_medidas": n.get(c, 0),
            "viajes": int(viajes or 0),
            "km": float(km or 0),
            "contra": _rend.CONFIG_BASE,
            # Lo que sostiene el número, en una línea que se pueda leer en la pantalla.
            "apoyo": ("medido dentro de cada unidad —comparar dos camiones distintos no "
                      "diría nada— y confirmado por la computadora del motor, que no sabe "
                      "de facturas: 0.78 por escáner contra 0.80 por comprobante. Ni la "
                      "longitud del viaje, ni el mes, ni quién manejó lo explican: los "
                      "tres empujan en sentido contrario."),
            # Y lo que NO se pudo descartar.
            "limite": ("no hay ninguna columna de ruta, corredor ni tonelaje en los viajes "
                       "históricos. Si los dobles van sistemáticamente a trayectos más "
                       "duros, parte de este factor es del trayecto y no del enganche."),
        }
    return out


# Los precios viven en config.precios_por_anio (una sola fuente para todo el sistema). Esta
# copia a mano YA había divergido —conservaba el 29.00 de 2026 después de que se corrigiera a
# 27.1551 en config.py— que es exactamente contra lo que avisa el comentario de arriba. Ahora
# se deriva, no se transcribe.
from .config import precio_del_litro

PRECIOS = {a: precio_del_litro(a) for a in (2025, 2026)}

DESC_TIPO = {
    "FULL": "Tracto con doble remolque · mayor carga",
    "SENCILLO": "Tracto con un solo remolque",
    "THORTON": "Unidad mediana de distribución",
}


def _precio(anio: int, precios: dict[int, float] | None = None) -> float:
    """Precio del litro para ese año, de la ÚNICA fuente del sistema (config)."""
    from .config import precio_del_litro

    if precios and anio in precios:
        return precios[anio]
    return precio_del_litro(anio)


def _rango_meses(session: Session, anio: int) -> tuple[date, date] | None:
    r = session.execute(
        select(func.min(Viaje.fecha), func.max(Viaje.fecha))
        .where(func.extract("year", Viaje.fecha) == anio,
               Viaje.kilometros.isnot(None), Viaje.lts_real.isnot(None))
    ).one()
    return (r[0], r[1]) if r[0] else None


# ── Plausibilidad ────────────────────────────────────────────────────────────
# Una fila puede ser inutilizable para el reporte por dos motivos MUY distintos:
#   1. Error de captura: el comprobante dice 27,335 L y el escáner del motor dice 67 L.
#   2. Período corto con carga al cierre: 40 km y un tanque lleno. El rendimiento se ve
#      pésimo, pero el diésel no se quemó — se quedó en el tanque para el período siguiente.
# En ninguno de los dos casos hay un mal operador, así que NO pueden entrar al ranking.
# Se excluyen del cálculo y se listan aparte para que alguien las corrija.
REND_MIN, REND_MAX = 1.0, 7.0      # km/L físicamente posibles en esta flota
RATIO_MAX, RATIO_MIN = 3.0, 0.33   # cuánto puede diferir el comprobante del escáner del motor


def _plausible():
    """Condición SQL de fila creíble. Exige lts_real > 0 (garantizado por _base)."""
    rend = Viaje.kilometros / Viaje.lts_real
    return and_(
        rend >= REND_MIN, rend <= REND_MAX,
        or_(Viaje.lts_scaner.is_(None), Viaje.lts_scaner <= 0,
            and_(Viaje.lts_real <= Viaje.lts_scaner * RATIO_MAX,
                 Viaje.lts_real >= Viaje.lts_scaner * RATIO_MIN)),
    )


def _base(anio: int, hasta_mes: int | None, solo_plausibles: bool = True):
    """Filtro común: viajes utilizables (km + litros reales) del año, hasta cierto mes."""
    cond = [func.extract("year", Viaje.fecha) == anio,
            Viaje.kilometros.isnot(None), Viaje.lts_real.isnot(None),
            Viaje.kilometros > 0, Viaje.lts_real > 0,
            Viaje.retractado_en.is_(None)]      # un reporte retirado no cuenta
    if hasta_mes:
        cond.append(func.extract("month", Viaje.fecha) <= hasta_mes)
    if solo_plausibles:
        cond.append(_plausible())
    return cond


# Una fila de bitácora cubre el período desde la extracción anterior de esa unidad. Cuando
# la bitácora tiene un hueco (jul-2025 a mar-2026), la primera fila al reanudar ACUMULA
# todos esos meses: son datos válidos para los totales de flota, pero NO se le pueden
# imputar a un operador — en 263 días manejaron varios. Se excluyen solo del ranking.
DIAS_MAX_IMPUTABLE = 40


def _periodos():
    """Duración real del período de cada fila: días desde la fila previa de la misma unidad."""
    prev = func.lag(Viaje.fecha).over(partition_by=Viaje.unidad_id, order_by=Viaje.fecha)
    return select(Viaje.id.label("vid"),
                  (Viaje.fecha - prev).label("dias")).subquery()


def dudosos(session: Session, anio: int, hasta_mes: int | None) -> list[dict]:
    """Filas excluidas del reporte por no ser creíbles. Se muestran para corregirlas."""
    filas = session.execute(
        select(Viaje.id, Viaje.fecha, Unidad.clave, Operador.nombre, Viaje.kilometros,
               Viaje.lts_real, Viaje.lts_scaner)
        .outerjoin(Unidad, Viaje.unidad_id == Unidad.id)
        .outerjoin(Operador, Viaje.operador_id == Operador.id)
        .where(*_base(anio, hasta_mes, solo_plausibles=False), ~_plausible())
        .order_by(Viaje.fecha)
    ).all()
    out = []
    for vid, fecha, clave, op, km, real, scan in filas:
        km, real = float(km), float(real)
        rend = km / real
        ratio = (real / float(scan)) if scan else None
        if ratio is not None and (ratio > RATIO_MAX or ratio < RATIO_MIN):
            motivo = "El comprobante y el escáner del motor no coinciden"
        elif rend < REND_MIN:
            motivo = "Rendimiento imposible: probable carga al cierre del período"
        else:
            motivo = "Rendimiento fuera de rango físico"
        out.append({"viaje_id": vid, "fecha": fecha.isoformat(), "unidad": clave,
                    "operador": op, "km": km, "lts_real": real,
                    "lts_scaner": float(scan) if scan else None,
                    "rendimiento": rend, "ratio": ratio, "motivo": motivo})
    return out


def _agregar(filas, refs: dict[int, float], precio: float) -> dict:
    """Suma un grupo de filas (km, litros, unidad) usando la referencia de CADA unidad.

    Antes esto recibía UNA constante por configuración y dividía con ella. Ahora lo
    esperado se calcula unidad por unidad y después se suma, que es lo único correcto
    cuando el grupo mezcla camiones con rendimientos distintos.

    Los totales de km, litros y gasto cuentan TODAS las filas —ese diésel se gastó de
    verdad—, pero la comparación sólo cuenta las unidades con escáner. Por eso viajan
    aparte `km_sin_referencia` y `viajes_sin_referencia`: un informe que cubre menos y
    dice cuánto cubre vale más que uno que lo cubre todo con un número inventado.
    """
    km_t = lts_t = 0.0
    km_c = lts_c = lts_esp = 0.0          # la parte COMPARABLE (unidades con escáner)
    km_sin = 0.0
    n_sin = 0
    km_esc = 0.0                          # kilómetros de ESCÁNER que sostienen la
    vistas: set = set()                   # referencia, y las unidades que los aportan
    for km, lts, uid, n in filas:
        km, lts, n = float(km or 0), float(lts or 0), int(n or 0)
        km_t += km
        lts_t += lts
        ref = refs.get(uid) if uid is not None else None
        if not ref or not ref[0]:
            km_sin += km
            n_sin += n
            continue
        valor, km_medido = ref
        km_c += km
        lts_c += lts
        lts_esp += km / valor
        if uid not in vistas:             # una unidad aporta su escaneo UNA vez, aunque
            vistas.add(uid)               # traiga varias filas en el grupo
            km_esc += km_medido
    # La referencia del GRUPO: los kilómetros comparables entre los litros que deberían
    # haber costado. Es la media ponderada por kilómetro, no el promedio de los km/l.
    ideal = (km_c / lts_esp) if lts_esp else None
    rend = (km_c / lts_c) if lts_c else None
    demas = (lts_c - lts_esp) if lts_esp else None
    return {
        "km": km_t, "litros": lts_t, "gasto": lts_t * precio,
        "rendimiento": rend, "ideal": ideal,
        "brecha": (rend - ideal) if (rend is not None and ideal is not None) else None,
        "litros_demas": demas,
        "costo_extra": (demas * precio) if demas and demas > 0 else 0.0,
        "km_sin_referencia": km_sin, "viajes_sin_referencia": n_sin,
        # `rendimiento` sale de la parte comparable, no del total. Sin estos dos números
        # el renglón no cuadra consigo mismo: THORTON mostraba 252,373 km y 78,296 L junto
        # a un 2.4 que nadie puede reproducir dividiendo.
        "km_comparado": km_c, "litros_comparados": lts_c,
        "km_escaner": km_esc, "unidades_medidas": len(vistas),
    }


def por_tipo(session: Session, anio: int, hasta_mes: int | None, precio: float) -> list[dict]:
    # Se agrupa TAMBIÉN por unidad: la referencia es la de cada camión, así que lo
    # esperado se calcula abajo y se suma después. Agrupar sólo por configuración era lo
    # que obligaba a usar una constante por configuración.
    filas = session.execute(
        select(Viaje.tipo_config, Viaje.unidad_id, func.sum(Viaje.kilometros),
               func.sum(Viaje.lts_real), func.count(Viaje.id))
        .where(*_base(anio, hasta_mes)).group_by(Viaje.tipo_config, Viaje.unidad_id)
    ).all()
    por: dict[str, list] = {}
    viajes: dict[str, int] = {}
    for tc, uid, km, lts, n in filas:
        nombre = tc.value if isinstance(tc, TipoConfig) else (tc or "SIN TIPO")
        por.setdefault(nombre, []).append((km, lts, uid, n))
        viajes[nombre] = viajes.get(nombre, 0) + int(n or 0)
    out = []
    for nombre, grupo in por.items():
        # La referencia de ESTA configuración. Un tracto etiquetado THORTON no la tiene, y
        # ese renglón se queda sin nada contra qué medirse, que es lo correcto: los 19,791
        # km «comparables» del renglón THORTON eran 5 tractos, y la flota de thortons de
        # verdad —17 rígidos, 232,582 km— no tiene un solo escáner.
        m = _agregar(grupo, _refs(session, nombre), precio)
        # Sin una sola unidad con escáner en el grupo no hay contra qué medirse. Antes el
        # hueco era «sin configuración»; ahora es «sin escáner», que es el de verdad.
        sin_ref = m["ideal"] is None
        out.append({"tipo": nombre, "viajes": viajes.get(nombre, 0),
                    "sin_ideal": sin_ref,
                    "descripcion": DESC_TIPO.get(nombre, "Sin configuración")
                                   + ("" if not sin_ref else
                                      " · ninguna de sus unidades tiene lectura de escáner"),
                    **m})
    orden = {"SENCILLO": 0, "THORTON": 1, "FULL": 2}
    out.sort(key=lambda x: orden.get(x["tipo"], 9))
    return out


def mensual(session: Session, anio: int, hasta_mes: int | None) -> dict:
    """Rendimiento mes a mes por tipo de configuración."""
    mes = func.extract("month", Viaje.fecha)
    filas = session.execute(
        select(mes, Viaje.tipo_config, func.sum(Viaje.kilometros), func.sum(Viaje.lts_real))
        .where(*_base(anio, hasta_mes)).group_by(mes, Viaje.tipo_config).order_by(mes)
    ).all()
    series: dict[str, dict[int, float]] = {}
    meses: set[int] = set()
    for m, tc, km, lts in filas:
        nombre = tc.value if isinstance(tc, TipoConfig) else (tc or "SIN TIPO")
        m = int(m)
        meses.add(m)
        if lts:
            series.setdefault(nombre, {})[m] = float(km) / float(lts)
    labels = sorted(meses)
    return {"meses": labels,
            "series": {k: [round(v.get(m), 3) if v.get(m) else None for m in labels]
                       for k, v in series.items()}}


def peores_operadores(session: Session, anio: int, hasta_mes: int | None, precio: float,
                      limite: int = 10, min_km: float = 1000,
                      min_viajes: int = 3) -> dict[str, list[dict]]:
    """Operadores que más costo extra generan, agrupados por tipo de configuración.

    Solo cuenta filas imputables a UN operador: se excluyen los períodos acumulados que
    abarcan meses (ver DIAS_MAX_IMPUTABLE) y las filas no plausibles. Además se exige un
    mínimo de viajes: con uno solo no hay patrón que reclamar, solo ruido.
    """
    per = _periodos()
    # Se baja hasta la unidad por la misma razón que en `por_tipo`: aquí se le pone un
    # número EN PESOS al nombre de una persona, así que la referencia tiene que ser la de
    # los camiones que condujo, no una constante de catálogo. El mínimo de km y de viajes
    # se sigue exigiendo por (operador, configuración), que es el nivel del reclamo.
    filas = session.execute(
        select(Operador.id, Operador.nombre, Operador.numero, Viaje.tipo_config,
               Viaje.unidad_id, func.sum(Viaje.kilometros), func.sum(Viaje.lts_real),
               func.count(Viaje.id))
        .join(Viaje, Viaje.operador_id == Operador.id)
        .join(per, per.c.vid == Viaje.id)
        .where(*_base(anio, hasta_mes),
               per.c.dias.isnot(None), per.c.dias <= DIAS_MAX_IMPUTABLE)
        .group_by(Operador.id, Operador.nombre, Operador.numero, Viaje.tipo_config,
                  Viaje.unidad_id)
    ).all()
    junto: dict[tuple, dict] = {}
    for oid, nombre, numero, tc, uid, km, lts, n in filas:
        tipo = tc.value if isinstance(tc, TipoConfig) else (tc or "SIN TIPO")
        e = junto.setdefault((oid, tipo), {"nombre": nombre, "numero": numero,
                                          "filas": [], "km": 0.0, "n": 0})
        e["filas"].append((km, lts, uid, n))
        e["km"] += float(km or 0)
        e["n"] += int(n or 0)
    por: dict[str, list[dict]] = {}
    for (oid, tipo), e in junto.items():
        if e["km"] < min_km or e["n"] < min_viajes:
            continue          # con uno o dos viajes no hay patrón que reclamar, sólo ruido
        m = _agregar(e["filas"], _refs(session, tipo), precio)
        if not m["rendimiento"] or m["costo_extra"] <= 0:
            continue          # sólo los que están POR DEBAJO de su referencia
        por.setdefault(tipo, []).append(
            {"operador_id": oid, "operador": e["nombre"], "numero": e["numero"],
             "viajes": e["n"], **m})
    for tipo in por:
        por[tipo].sort(key=lambda x: -x["costo_extra"])
        por[tipo] = por[tipo][:limite]
    return por


def cobertura(session: Session, anio: int, hasta_mes: int | None) -> dict:
    """Qué se usó y qué se dejó fuera. Sin esto el reporte parecería cubrirlo todo."""
    per = _periodos()
    total = session.scalar(
        select(func.count(Viaje.id)).where(*_base(anio, hasta_mes, solo_plausibles=False)))
    usadas = session.scalar(select(func.count(Viaje.id)).where(*_base(anio, hasta_mes)))
    acum = session.scalar(
        select(func.count(Viaje.id)).select_from(Viaje).join(per, per.c.vid == Viaje.id)
        .where(*_base(anio, hasta_mes),
               or_(per.c.dias.is_(None), per.c.dias > DIAS_MAX_IMPUTABLE)))
    return {"filas_totales": total or 0, "filas_usadas": usadas or 0,
            "filas_dudosas": (total or 0) - (usadas or 0),
            "filas_acumuladas": acum or 0,
            "dias_max_imputable": DIAS_MAX_IMPUTABLE}


def ralenti(session: Session, anio: int, precio: float) -> dict:
    """Combustible quemado con el camión PARADO, medido por la computadora del motor."""
    r = session.execute(
        select(func.sum(EscaneoMotor.lts_ralenti), func.sum(EscaneoMotor.litros),
               func.count(EscaneoMotor.id), func.min(EscaneoMotor.periodo_fin),
               func.max(EscaneoMotor.periodo_fin),
               func.avg(EscaneoMotor.pct_ralenti.cast(Float)))
        .where(func.extract("year", EscaneoMotor.periodo_fin) == anio)
    ).one()
    lts, total, n, ini, fin, pct = r
    lts = float(lts or 0)
    return {
        "litros": lts, "litros_totales": float(total or 0), "escaneos": n,
        "pct_promedio": float(pct) if pct is not None else None,
        "pct_del_total": (lts / float(total) * 100) if total else None,
        "costo": lts * precio,
        "desde": ini.isoformat() if ini else None,
        "hasta": fin.isoformat() if fin else None,
    }


def generar(session: Session, anio: int, anio_previo: int | None = None,
            precios: dict[int, float] | None = None, limite_op: int = 10) -> dict:
    """Arma el reporte ejecutivo completo, comparando contra el año previo si se pide.

    Para que la comparación sea justa se recorta el año previo al MISMO rango de meses
    que tiene el año actual (si el actual va a la mitad, no se compara contra 12 meses).
    """
    rango = _rango_meses(session, anio)
    hasta_mes = rango[1].month if rango else None
    precio = _precio(anio, precios)

    per = {"anio": anio, "precio": precio,
           "desde": rango[0].isoformat() if rango else None,
           "hasta": rango[1].isoformat() if rango else None,
           "hasta_mes": hasta_mes}

    tipos = por_tipo(session, anio, hasta_mes, precio)
    datos = {
        "periodo": per,
        "por_tipo": tipos,
        "mensual": mensual(session, anio, hasta_mes),
        "ralenti": ralenti(session, anio, precio),
        "operadores": peores_operadores(session, anio, hasta_mes, precio, limite_op),
        "dudosos": dudosos(session, anio, hasta_mes),
        "cobertura": cobertura(session, anio, hasta_mes),
        "criterios": {
            # Ya no hay tabla de ideales: la referencia es el escáner de cada unidad, y el
            # número que publica cada fila es la media ponderada por kilómetro de las suyas.
            "referencia": ("el rendimiento medido por el escáner de cada unidad, "
                           "separado por configuración"),
            "unidades_con_referencia": len(_refs(session)),
            # El factor con el que se separa, y sobre cuántas unidades se midió. Va en los
            # criterios por lo mismo que las referencias: un número que decide dinero tiene
            # que venir con lo que lo sostiene.
            "factor_configuracion": _factores(session),
            "rend_min": REND_MIN, "rend_max": REND_MAX,
            "ratio_comprobante_escaner": RATIO_MAX,
            "dias_max_imputable": DIAS_MAX_IMPUTABLE,
            "min_km_operador": 1000, "min_viajes_operador": 3,
        },
        "totales": {
            "km": sum(t["km"] for t in tipos),
            "litros": sum(t["litros"] for t in tipos),
            "gasto": sum(t["gasto"] for t in tipos),
            "costo_extra": sum(t["costo_extra"] for t in tipos),
            "viajes": sum(t["viajes"] for t in tipos),
        },
    }
    rend_global = (datos["totales"]["km"] / datos["totales"]["litros"]
                   if datos["totales"]["litros"] else None)
    datos["totales"]["rendimiento"] = rend_global

    # Solo se compara si el año previo TIENE datos: anunciar una comparación contra un año
    # vacío deja el reporte diciendo "2024: —", que parece un error de cálculo.
    if anio_previo:
        precio_p = _precio(anio_previo, precios)
        tipos_p = por_tipo(session, anio_previo, hasta_mes, precio_p)
    if anio_previo and tipos_p:
        tot_km = sum(t["km"] for t in tipos_p)
        tot_l = sum(t["litros"] for t in tipos_p)
        datos["previo"] = {
            "periodo": {"anio": anio_previo, "precio": precio_p, "hasta_mes": hasta_mes},
            "por_tipo": tipos_p,
            "mensual": mensual(session, anio_previo, hasta_mes),
            "ralenti": ralenti(session, anio_previo, precio_p),
            "totales": {"km": tot_km, "litros": tot_l,
                        "gasto": sum(t["gasto"] for t in tipos_p),
                        "costo_extra": sum(t["costo_extra"] for t in tipos_p),
                        "viajes": sum(t["viajes"] for t in tipos_p),
                        "rendimiento": (tot_km / tot_l) if tot_l else None},
        }
    return datos
