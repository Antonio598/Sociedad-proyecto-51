"""Etapa 2 — Validación automática.

Replica la lógica de la bitácora y detecta anomalías contra el historial de
cada unidad. No decide solo ante datos atípicos: crea excepciones (Anomalia)
para que una persona las confirme (confirmación humana).

Fórmulas confirmadas con el cliente:
  RTO       = KILOMETROS / LTS SCANER      (rendimiento estimado, km/l)
  RTO REAL  = KILOMETROS / LTS REAL        (rendimiento real, km/l)
  DIF       = LTS REAL - LTS SCANER        (diferencia en litros)
  %         = DIF / LTS SCANER             (desviación como fracción)
  Código c  = Cargado/Vacío ida-vuelta (C-C, C-V, ...) — dato capturado, no calculado
"""

import logging
import math
from statistics import mean, pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Anomalia, EstadoAnomalia, TipoUnidad, Viaje

log = logging.getLogger("combustible.validacion")

# Categoría de cada tipo de anomalía (para el panel y el diagnóstico de precisión).
CATEGORIA_ANOMALIA = {
    "km_imposible": "base", "lts_imposible": "base", "odometro_retrocede": "base",
    "consumo_atipico": "base", "desviacion_alta": "base",
    "rendimiento_fuera_banda_fisica": "consistencia", "km_vs_odometro": "consistencia",
    "reporte_duplicado": "consistencia", "rendimiento_cv_incoherente": "consistencia",
    "carga_fantasma": "fraude", "comprobante_inflado": "fraude", "sifoneo_tanque": "fraude",
    "exceso_velocidad": "telemetria", "ralenti_elevado": "telemetria",
    "paradas_panico_recurrentes": "telemetria",
    "operador_no_disponible": "personal", "operador_no_asignado": "personal",
    # El reporte que originó el viaje se borró o se corrigió en el grupo. No la genera
    # validar(): la levanta app/retractacion.py cuando llega el borrado desde WhatsApp.
    "reporte_retractado": "consistencia",
    # Una corrección desde el grupo cambió la unidad o el operador del viaje: no es
    # rectificar una cifra, es reasignarlo. La levanta captura._aplicar_correccion.
    "correccion_sospechosa": "consistencia",
}

# Estatus de operador que implican que NO debería estar conduciendo.
ESTATUS_NO_DISPONIBLE = {"VACACIONES", "INCAPACIDAD", "BAJA", "INACTIVO"}


def calcular(viaje: Viaje) -> None:
    """Rellena —o LIMPIA— rto, rto_real, dif y pct a partir de km y litros. In-place.

    Es simétrica a propósito: si un insumo desaparece (p.ej. al corregir un viaje desde el
    panel se borran los litros), el derivado vuelve a None. Antes solo ASIGNABA, así que el
    valor viejo se quedaba persistido sin corresponder a los insumos: un rendimiento o una
    desviación fantasma que ya nadie podía explicar.
    """
    km, scan, real = viaje.kilometros, viaje.lts_scaner, viaje.lts_real

    viaje.rto = (km / scan) if (km is not None and scan not in (None, 0)) else None
    viaje.rto_real = (km / real) if (km is not None and real not in (None, 0)) else None
    if scan is not None and real is not None:
        viaje.dif = real - scan
        viaje.pct = (viaje.dif / scan) if scan != 0 else None
    else:
        viaje.dif = None
        viaje.pct = None


def _historial_rto_real(session: Session, unidad_id: int, excluir_viaje_id: int | None) -> list[float]:
    q = select(Viaje.rto_real).where(
        Viaje.unidad_id == unidad_id,
        Viaje.rto_real.isnot(None),
    )
    if excluir_viaje_id is not None:
        q = q.where(Viaje.id != excluir_viaje_id)
    return [r for (r,) in session.execute(q) if r is not None and r > 0]


def _ultimo_odometro(session: Session, unidad_id: int, hasta_fecha, excluir_viaje_id: int | None) -> float | None:
    """Último odómetro de la unidad en una fecha ANTERIOR o igual al viaje que se valida."""
    row = _ultimo_odometro_con_fecha(session, unidad_id, hasta_fecha, excluir_viaje_id)
    return row[0] if row else None


def _ultimo_odometro_con_fecha(session: Session, unidad_id: int, hasta_fecha,
                               excluir_viaje_id: int | None):
    """(odometro, fecha) de la última lectura, para saber si es CONTIGUA al viaje actual."""
    q = (
        select(Viaje.odometro, Viaje.fecha)
        .where(
            Viaje.unidad_id == unidad_id,
            Viaje.odometro.isnot(None),
            Viaje.fecha <= hasta_fecha,
            # Un reporte retractado no puede disparar la anomalía "el odómetro retrocede":
            # el dato se retiró justo porque estaba mal, y compararse contra él acusaría al
            # reporte bueno que vino a sustituirlo.
            Viaje.retractado_en.is_(None),
        )
        .order_by(Viaje.fecha.desc(), Viaje.id.desc())
    )
    if excluir_viaje_id is not None:
        q = q.where(Viaje.id != excluir_viaje_id)
    return session.execute(q.limit(1)).first()


# Días máximos entre dos lecturas de odómetro para poder comparar km contra su avance.
# Si hay más hueco, el avance abarca viajes sin foto y la comparación no significa nada.
DIAS_ODOMETRO_CONTIGUO = 2


def _nombres_distintos(a: str | None, b: str | None) -> bool:
    """True si dos nombres claramente NO son la misma persona (no comparten ninguna
    palabra de >=3 letras). Tolera formatos distintos: 'ANTONIO HERNÁNDEZ' vs 'A. HERNÁNDEZ'
    comparten 'HERNÁNDEZ' -> misma persona -> False."""
    def toks(s: str | None) -> set[str]:
        return {w for w in (s or "").upper().replace(".", " ").split() if len(w) >= 3}
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return False  # sin datos suficientes para afirmar que difieren
    return ta.isdisjoint(tb)


def _normaliza_cv(codigo: str | None) -> str | None:
    """Normaliza el código C (cargado/vacío) a la forma 'C-C' / 'C-V'."""
    if not codigo:
        return None
    c = codigo.strip().upper().replace(" ", "").replace("/", "-")
    if "-" not in c and len(c) == 2:      # 'CC' -> 'C-C', 'CV' -> 'C-V'
        c = f"{c[0]}-{c[1]}"
    return c


def _media_rto_codigo(session: Session, unidad_id: int, codigo: str, excluir_viaje_id: int | None) -> float | None:
    """Media de rto_real de la unidad para un código C dado (p. ej. 'C-C' cargado)."""
    q = select(Viaje.codigo_cv, Viaje.rto_real).where(
        Viaje.unidad_id == unidad_id,
        Viaje.rto_real.isnot(None),
        Viaje.codigo_cv.isnot(None),
    )
    if excluir_viaje_id is not None:
        q = q.where(Viaje.id != excluir_viaje_id)
    vals = [r for (cv, r) in session.execute(q) if r and r > 0 and _normaliza_cv(cv) == codigo]
    return mean(vals) if len(vals) >= 3 else None


def _es_duplicado(session: Session, viaje: Viaje) -> int | None:
    """Devuelve el id de otro viaje que parece el mismo (unidad+fecha y mismo
    odómetro, o mismos km+lts_real). Señal de captura repetida."""
    q = select(Viaje.id, Viaje.odometro, Viaje.kilometros, Viaje.lts_real).where(
        Viaje.unidad_id == viaje.unidad_id,
        Viaje.fecha == viaje.fecha,
        Viaje.id != viaje.id,
    )
    for oid, odo, km, real in session.execute(q):
        if viaje.odometro is not None and odo is not None and abs(odo - viaje.odometro) < 1:
            return oid
        if (viaje.kilometros is not None and km is not None and abs(km - viaje.kilometros) < 1
                and viaje.lts_real is not None and real is not None and abs(real - viaje.lts_real) < 1):
            return oid
    return None


def _ventana_dif(session: Session, unidad_id: int, k: int, excluir_viaje_id: int | None):
    """Suma de dif y de lts_scaner de los últimos k viajes de la unidad (para sifoneo).
    Devuelve (suma_dif, suma_scaner, positivos, n)."""
    q = (
        select(Viaje.dif, Viaje.lts_scaner)
        .where(Viaje.unidad_id == unidad_id, Viaje.dif.isnot(None), Viaje.lts_scaner.isnot(None))
        .order_by(Viaje.fecha.desc(), Viaje.id.desc())
    )
    if excluir_viaje_id is not None:
        q = q.where(Viaje.id != excluir_viaje_id)
    filas = list(session.execute(q.limit(k)))
    suma_dif = sum(d for d, _ in filas)
    suma_scan = sum(s for _, s in filas if s and s > 0)
    positivos = sum(1 for d, _ in filas if d and d > 0)
    return suma_dif, suma_scan, positivos, len(filas)


def _stat_columna(session: Session, unidad_id: int, columna, excluir_viaje_id: int | None):
    """(media, sigma, n) de una columna de telemetría sobre el historial de la unidad.
    Devuelve None si hay menos de 5 muestras (base insuficiente para comparar)."""
    q = select(columna).where(Viaje.unidad_id == unidad_id, columna.isnot(None))
    if excluir_viaje_id is not None:
        q = q.where(Viaje.id != excluir_viaje_id)
    vals = [v for (v,) in session.execute(q) if v is not None]
    if len(vals) < 5:
        return None
    return mean(vals), pstdev(vals), len(vals)


def _telemetria_normal(session: Session, viaje: Viaje) -> bool:
    """True si el estilo de manejo NO es anómalo (ninguna métrica clave supera
    media+σ del historial). Se usa para no culpar de fraude un exceso de consumo
    que en realidad explica un manejo agresivo."""
    for columna in (Viaje.ralenti, Viaje.paradas_panico):
        st = _stat_columna(session, viaje.unidad_id, columna, viaje.id)
        val = getattr(viaje, columna.key)
        if st and val is not None:
            mu, sigma, _ = st
            if sigma > 0 and val > mu + settings.umbral_sigma * sigma:
                return False
    return True


def _crear_anomalia(session: Session, viaje: Viaje, tipo: str, descripcion: str) -> Anomalia:
    """Crea la anomalía si no existe ya una del mismo tipo (no rechazada) para el viaje.

    Idempotente: como validar() corre en cada evento del mismo viaje, esto evita
    acumular anomalías duplicadas en la cola de confirmación humana.
    """
    existente = session.execute(
        select(Anomalia).where(
            Anomalia.viaje_id == viaje.id,
            Anomalia.tipo == tipo,
            Anomalia.estado != EstadoAnomalia.RECHAZADA,
        )
    ).scalars().first()
    if existente is not None:
        existente.descripcion = descripcion  # refresca por si cambiaron los datos
        return existente
    a = Anomalia(
        viaje_id=viaje.id, tipo=tipo, descripcion=descripcion,
        estado=EstadoAnomalia.PENDIENTE,
    )
    session.add(a)
    log.info("Anomalía [%s] viaje %s: %s", tipo, viaje.id, descripcion)
    return a


def validar(session: Session, viaje: Viaje) -> list[Anomalia]:
    """Calcula rendimientos y marca anomalías. El viaje ya debe estar en la sesión (con id)."""
    calcular(viaje)
    anomalias: list[Anomalia] = []

    # 1) Valores imposibles
    if viaje.kilometros is not None and viaje.kilometros <= 0:
        anomalias.append(_crear_anomalia(session, viaje, "km_imposible",
                                         f"Kilómetros no positivos: {viaje.kilometros}"))
    if viaje.lts_real is not None and viaje.lts_real <= 0:
        anomalias.append(_crear_anomalia(session, viaje, "lts_imposible",
                                         f"Litros reales no positivos: {viaje.lts_real}"))

    # 2) Odómetro que retrocede respecto al último registrado de la unidad
    if viaje.odometro is not None:
        prev = _ultimo_odometro(session, viaje.unidad_id, viaje.fecha, viaje.id)
        if prev is not None and viaje.odometro < prev:
            anomalias.append(_crear_anomalia(session, viaje, "odometro_retrocede",
                                             f"Odómetro {viaje.odometro} < último registrado {prev}"))

    # 3) Rendimiento real fuera del historial de la unidad (consumo atípico)
    if viaje.rto_real is not None:
        hist = _historial_rto_real(session, viaje.unidad_id, viaje.id)
        if len(hist) >= 5:
            mu, sigma = mean(hist), pstdev(hist)
            if sigma > 0 and abs(viaje.rto_real - mu) > settings.umbral_sigma * sigma:
                anomalias.append(_crear_anomalia(
                    session, viaje, "consumo_atipico",
                    f"RTO real {viaje.rto_real:.3f} fuera de rango histórico "
                    f"(media {mu:.3f} ± {sigma:.3f} km/l, n={len(hist)})"))

    # 4) Desviación de litros más allá de la tolerancia -> propone descuento a confirmar.
    #    La tolerancia es POR UNIDAD (determinada en auditoría de campo), no global.
    if viaje.pct is not None:
        tol = settings.tolerancia_pct
        if viaje.unidad is not None and viaje.unidad.pct_tolerancia is not None:
            tol = abs(viaje.unidad.pct_tolerancia)
        if abs(viaje.pct) > tol:
            # Exceso de consumo (real > scanner) penaliza; el monto lo confirma la oficina
            exceso = viaje.dif if viaje.dif and viaje.dif > 0 else 0.0
            if viaje.descuentos is None:
                viaje.descuentos = round(exceso, 2)
            anomalias.append(_crear_anomalia(
                session, viaje, "desviacion_alta",
                f"Desviación {viaje.pct*100:.2f}% (DIF {viaje.dif:.2f} L) supera la "
                f"tolerancia {tol*100:.1f}% de la unidad. Descuento propuesto: "
                f"{viaje.descuentos:.2f} L (confirmar)."))

    def add(tipo: str, desc: str) -> None:
        anomalias.append(_crear_anomalia(session, viaje, tipo, desc))

    # ── CONSISTENCIA ─────────────────────────────────────────────────────────
    # 5) Rendimiento fuera de la banda física posible según el tipo de unidad
    if viaje.rto_real is not None and viaje.unidad is not None:
        if viaje.unidad.tipo == TipoUnidad.TRACTO:
            lo, hi = settings.rto_min_tracto, settings.rto_max_tracto
        else:
            lo, hi = settings.rto_min_camion, settings.rto_max_camion
        if viaje.rto_real < lo or viaje.rto_real > hi:
            add("rendimiento_fuera_banda_fisica",
                f"RTO real {viaje.rto_real:.2f} km/l fuera de la banda física "
                f"[{lo:.1f}–{hi:.1f}] para {viaje.unidad.tipo.value.lower()}. Revisar km/litros.")

    # 6) Km declarados no cuadran con el avance del odómetro.
    #    Solo si la lectura previa es CONTIGUA: con el modelo nuevo puede haber días sin
    #    foto, y entonces el avance abarca varios viajes -> el km de ESTE viaje nunca
    #    cuadraría y la anomalía saltaba siempre en falso.
    if viaje.odometro is not None and viaje.kilometros is not None:
        prev = _ultimo_odometro_con_fecha(session, viaje.unidad_id, viaje.fecha, viaje.id)
        if prev is not None:
            prev_odo, prev_fecha = prev
            hueco = (viaje.fecha - prev_fecha).days if (viaje.fecha and prev_fecha) else None
            contigua = hueco is not None and hueco <= DIAS_ODOMETRO_CONTIGUO
            delta = viaje.odometro - prev_odo
            if contigua and delta >= 0:  # el retroceso ya lo cubre 'odometro_retrocede'
                umbral = max(30.0, 0.05 * delta)
                if abs(viaje.kilometros - delta) > umbral:
                    add("km_vs_odometro",
                        f"Km reportados {viaje.kilometros:.0f} no cuadran con el avance de "
                        f"odómetro {delta:.0f} (diferencia {viaje.kilometros - delta:+.0f} km).")

    # 7) Reporte duplicado (misma unidad+fecha y mismo odómetro, o mismos km+litros)
    dup_id = _es_duplicado(session, viaje)
    if dup_id is not None:
        add("reporte_duplicado",
            f"Posible captura duplicada: coincide con el viaje #{dup_id} de la misma "
            f"unidad y fecha. Verificar antes de contabilizar.")

    # 8) Un tramo VACÍO (C-V) debería rendir más que el promedio CARGADO (C-C)
    if viaje.rto_real is not None and _normaliza_cv(viaje.codigo_cv) == "C-V":
        media_cc = _media_rto_codigo(session, viaje.unidad_id, "C-C", viaje.id)
        if media_cc is not None and viaje.rto_real <= media_cc * 0.95:
            add("rendimiento_cv_incoherente",
                f"Tramo vacío (C-V) rinde {viaje.rto_real:.2f} km/l, ≤ media cargado "
                f"{media_cc:.2f} km/l de la unidad. Rendimiento incoherente.")

    # ── FRAUDE ───────────────────────────────────────────────────────────────
    # 9) Carga fantasma: mucho combustible con casi nada de recorrido ni consumo de motor
    if (viaje.lts_real is not None and viaje.lts_real >= settings.carga_fantasma_lts_min
            and viaje.kilometros is not None and viaje.kilometros <= settings.carga_fantasma_km_max
            and viaje.lts_scaner is not None and viaje.lts_scaner < 1):
        add("carga_fantasma",
            f"Carga de {viaje.lts_real:.0f} L con solo {viaje.kilometros:.0f} km y sin "
            f"consumo de motor (scanner {viaje.lts_scaner:.1f} L). Posible carga no usada por la unidad.")

    # 10) Comprobante inflado: exceso de consumo real por encima de la tolerancia, con
    #     rendimiento muy por debajo del histórico y SIN manejo agresivo que lo explique.
    if (viaje.pct is not None and viaje.dif is not None and viaje.dif > 0 and viaje.rto_real is not None):
        tol_ti = settings.tolerancia_pct
        if viaje.unidad is not None and viaje.unidad.pct_tolerancia is not None:
            tol_ti = abs(viaje.unidad.pct_tolerancia)
        if viaje.pct > tol_ti:
            hist = _historial_rto_real(session, viaje.unidad_id, viaje.id)
            if len(hist) >= 5:
                media_hist = mean(hist)
                if viaje.rto_real < media_hist * 0.90 and _telemetria_normal(session, viaje):
                    add("comprobante_inflado",
                        f"Sobreconsumo de {viaje.dif:.0f} L ({viaje.pct*100:.1f}%) con rendimiento "
                        f"{viaje.rto_real:.2f} km/l muy bajo vs media {media_hist:.2f} y manejo normal. "
                        f"Posible comprobante inflado.")

    # 11) Sifoneo del tanque: exceso acumulado y persistente en la ventana reciente
    s_dif, s_scan, positivos, n = _ventana_dif(session, viaje.unidad_id, settings.sifoneo_ventana, None)
    if n >= 4 and s_scan > 0:
        ratio = s_dif / s_scan
        if ratio > settings.sifoneo_umbral and positivos >= n * 0.75:
            add("sifoneo_tanque",
                f"Exceso acumulado {ratio*100:.1f}% en los últimos {n} viajes de la unidad "
                f"({positivos} con sobreconsumo). Patrón compatible con sifoneo de tanque.")

    # ── TELEMETRÍA (relativo al historial de la unidad + piso/tope absoluto) ──
    # 12) Exceso de velocidad
    if viaje.vel_max is not None:
        st = _stat_columna(session, viaje.unidad_id, Viaje.vel_max, viaje.id)
        supera_hist = st and st[1] > 0 and viaje.vel_max > st[0] + settings.umbral_sigma * st[1]
        supera_tope = viaje.vel_max > settings.vel_max_tope
        if supera_hist or supera_tope:
            ref = f"media {st[0]:.0f}±{st[1]:.0f}" if st else f"tope {settings.vel_max_tope:.0f}"
            add("exceso_velocidad",
                f"Velocidad máxima {viaje.vel_max:.0f} km/h atípica ({ref} km/h).")

    # 13) Ralentí elevado (por encima del historial y del piso absoluto)
    if viaje.ralenti is not None and viaje.ralenti > settings.ralenti_piso:
        st = _stat_columna(session, viaje.unidad_id, Viaje.ralenti, viaje.id)
        if st and st[1] > 0 and viaje.ralenti > st[0] + settings.umbral_sigma * st[1]:
            add("ralenti_elevado",
                f"Ralentí {viaje.ralenti:.1f} atípico vs media {st[0]:.1f}±{st[1]:.1f} de la unidad.")

    # 14) Paradas de pánico recurrentes (por 100 km, contra el historial de la unidad)
    if viaje.paradas_panico is not None and viaje.kilometros and viaje.kilometros > 0:
        tasa = viaje.paradas_panico / viaje.kilometros * 100
        q = select(Viaje.paradas_panico, Viaje.kilometros).where(
            Viaje.unidad_id == viaje.unidad_id, Viaje.id != viaje.id,
            Viaje.paradas_panico.isnot(None), Viaje.kilometros.isnot(None), Viaje.kilometros > 0)
        tasas = [pp / km * 100 for pp, km in session.execute(q)]
        if len(tasas) >= 5:
            mu_t, sg_t = mean(tasas), pstdev(tasas)
            if sg_t > 0 and tasa > mu_t + settings.umbral_sigma * sg_t:
                add("paradas_panico_recurrentes",
                    f"{viaje.paradas_panico} paradas de pánico en {viaje.kilometros:.0f} km "
                    f"({tasa:.2f}/100km) vs media {mu_t:.2f}±{sg_t:.2f} de la unidad.")

    # ── PERSONAL / ROLES (concordancia operador ↔ estado y asignación) ───────
    op = viaje.operador
    if op is not None:
        # 15) Operador reportado que NO está disponible (vacaciones/incapacidad/baja/inactivo)
        estatus = (op.estatus or "").strip().upper()
        no_disponible = (op.activo is False) or (estatus in ESTATUS_NO_DISPONIBLE)
        if no_disponible:
            motivo = estatus or "INACTIVO"
            add("operador_no_disponible",
                f"El operador {op.nombre} está en estatus {motivo} pero se reportó conduciendo"
                f"{f' la unidad {viaje.unidad.clave}' if viaje.unidad else ''}. Verificar quién condujo.")

        # 16) Operador que no es el asignado a la unidad.
        #     APAGADA POR DEFECTO. La asignación del catálogo es NOMINAL, no operativa: al
        #     medirlo contra el historial real, el 99.9% de los viajes (2,459 de 2,461) los
        #     conduce alguien distinto al titular, y hay unidades con 12 conductores
        #     distintos. El propio cliente lo confirma —"la rotación es alta"— y su bitácora
        #     de ejemplo pone a un operador en una unidad que no es la suya. Con esa
        #     realidad, la regla no marcaría una excepción: marcaría todo, y el panel de
        #     anomalías dejaría de servir. Se conserva porque en una flota con asignación
        #     estricta sí es señal útil; se enciende con AVISAR_OPERADOR_NO_ASIGNADO=1.
        rol = (op.rol or "TITULAR").strip().upper()
        u = viaje.unidad
        if settings.avisar_operador_no_asignado and rol != "RELEVO" and u is not None:
            distinto = None
            if u.operador_asignado_id is not None:
                distinto = op.id != u.operador_asignado_id
                titular = u.operador_titular.nombre if u.operador_titular else "?"
            elif u.operador_asignado:
                distinto = _nombres_distintos(op.nombre, u.operador_asignado)
                titular = u.operador_asignado
            if distinto:
                add("operador_no_asignado",
                    f"El operador {op.nombre} no es el asignado a la unidad {u.clave} "
                    f"(asignado: {titular}). Confirmar si fue un relevo autorizado.")

    _retirar_obsoletas(session, viaje, {a.tipo for a in anomalias})
    return anomalias


def _retirar_obsoletas(session: Session, viaje: Viaje, tipos_vigentes: set[str]) -> None:
    """Retira las anomalías PENDIENTES del viaje cuyo tipo ya NO se dispara.

    validar() vuelve a correr cada vez que el viaje cambia (llega otra foto, se corrige a
    mano). Antes solo AGREGABA: si el dato se corregía y la anomalía dejaba de aplicar, se
    quedaba en la cola de revisión para siempre —contradiciendo lo que promete el endpoint
    de edición ("re-evalúa las anomalías")— y ensuciaba la métrica de precisión.

    Solo se tocan las PENDIENTES: una anomalía que un humano ya confirmó o rechazó es su
    decisión y no se pisa.
    """
    pendientes = session.execute(
        select(Anomalia).where(
            Anomalia.viaje_id == viaje.id,
            Anomalia.estado == EstadoAnomalia.PENDIENTE,
        )
    ).scalars().all()
    for a in pendientes:
        if a.tipo not in tipos_vigentes:
            log.info("Anomalía [%s] del viaje %s ya no aplica — se retira", a.tipo, viaje.id)
            session.delete(a)
