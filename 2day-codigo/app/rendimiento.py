"""El rendimiento de una unidad y el tope de litros que se le pueden despachar.

Decisión del dueño (16-sep-2026): el rendimiento NO es una meta que alguien fije, es el dato
que reporta el escáner del motor. Y sobre ese dato hay un umbral al recargar: si el viaje
estima N litros, no se autoriza más de N × (1 + tope).

POR QUÉ ESE UMBRAL SÍ FUNCIONA Y OTROS NO. El 2% aquí no mide nada: limita una cantidad que
la empresa entrega. Se cumple por decreto. Es distinto de `tolerancia_pct`, que compara lo
cargado contra lo que el motor dice haber quemado —dos instrumentos -y ahí un 2% choca con
el ruido de la medición (marcaría el 88.9% de los viajes ya capturados).

LA REFERENCIA SALE DE LA SERIE, NO DE LA ÚLTIMA LECTURA. Durante un tiempo la regla fue "el
escaneo más reciente que pase el piso de km", y eso dejaba la referencia en un EXTREMO de la
propia serie de la unidad en la mayoría de los casos: T182 lee 2.98 y 4.15 en dos
extracciones consecutivas de 1,800 km y regía 4.15, de donde salían $184,926 a nombre de una
persona. Ahora se juntan las lecturas —sus kilómetros entre sus litros— y da 3.47.

JUNTAR NO ES PROMEDIAR, y por eso se puede. Los escaneos son períodos CERRADOS y CONSECUTIVOS
que embaldosan la operación de la unidad: el odómetro de cierre de uno más los km del
siguiente da el odómetro del siguiente. En los 23 pares de la base, 19 cuadran al metro, 1
dentro del 1%, y los 3 que no son períodos importados dos veces (`_serie` los quita por el
odómetro de cierre, porque la idempotencia del importador es por nombre de archivo y esos
venían con nombres distintos). Sumar km y sumar litros de períodos que no se pisan MIDE SOBRE
UNA VENTANA MÁS LARGA, que es justo lo que le falta a una lectura de 792 km. Y km entre
litros es además la única agregación correcta para un cociente: pondera por kilómetro sola,
mientras que la mediana de las lecturas trataría igual una de 609 km y una de 5,701.

EL PISO DE KILÓMETROS, y por qué cambió de sitio. Un escaneo no siempre es una medición: hay
extracciones de 124, 143 y 172 km que son arranque en frío, maniobras y ralentí, sin
carretera. Como el rendimiento va en el DIVISOR, uno bajo autoriza MÁS diésel: T205 con su
extracción de 172 km (1.54 km/L) pediría 1,327 L para un viaje de 2,000 km contra 805 L con
la de 2,718 km, y sobre las 19 unidades con escáner eso son 899 L de más en un solo viaje de
cada una. Antes el piso descartaba esas lecturas enteras; ahora se le exige al TOTAL juntado,
así que esos 172 km siguen siendo operación real de la unidad y pesan dentro de la suma lo
poco que les toca, en vez de perderse o de mandar solos.

LO QUE SIGUE SIN ARREGLARSE. Juntar reduce la variación pero no la elimina, y la referencia
de una unidad con una sola lectura sigue siendo una sola lectura. Por eso el tope nace
avisando y no bloqueando: quien autoriza puede pasarse, y el registro de cuántas veces hace
falta es lo que después dice qué unidades tienen mal el rendimiento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import (AsignacionViaje, EscaneoMotor, EstadoAsignacion, SolicitudRecarga,
                     Unidad, Viaje)

log = logging.getLogger("combustible.rendimiento")


@dataclass
class Rendimiento:
    """Qué rendimiento rige para una unidad y de dónde salió.

    `motivo` no es decoración: es lo que se le enseña a quien autoriza, para que un tope
    raro se pueda entender sin abrir la base.
    """

    valor: float | None = None          # km/l
    escaneo_id: int | None = None
    km: float | None = None             # los que cubrió ese escaneo
    litros: float | None = None
    # El odómetro con el que cerró ese escaneo es el ANTERIOR contra el que se miden los
    # kilómetros del viaje. Estaba en la base y ninguna API lo devolvía.
    odometro: float | None = None
    fecha: str | None = None
    motivo: str = "sin escáner"

    # Con qué configuración se pidió, y con qué factor se separó del valor mezclado que
    # midió el escáner. `config=None` es la referencia de siempre, sin separar.
    config: str | None = None
    factor: float | None = None
    base: float | None = None           # el km/l en SENCILLO del que sale `valor`

    @property
    def hay(self) -> bool:
        return self.valor is not None and self.valor > 0


# La configuración base: contra ella se expresa el factor de las demás.
CONFIG_BASE = "SENCILLO"

# Cuántas unidades tienen que haber corrido AMBAS configuraciones para creerle al factor.
# Con dos o tres no hay factor, hay anécdota.
MIN_UNIDADES_FACTOR = 4


# Con menos de este porcentaje de sus kilómetros en una familia de unidad, no se puede
# decir que la configuración sea de esa familia, y no se decide por ella.
DUENO_CLARO = 0.90


def dueno_config(session: Session) -> dict[str, bool]:
    """De qué familia de unidad es cada configuración: True = tracto, False = rígido.

    No es una lista escrita a mano: se lee de dónde están los kilómetros. Hoy FULL y
    SENCILLO son 100% de tractos y THORTON es 96% de rígidos, así que la pertenencia sale
    con margen de sobra. Una configuración repartida entre las dos familias no entra aquí,
    y entonces no se decide por ella.

    Esto es lo que impide el razonamiento circular: los seis viajes de tracto etiquetados
    THORTON no alcanzan para hacer de THORTON una configuración de tractos, así que no
    generan un factor, y sin factor no se les fabrica una referencia.
    """
    cache = session.info.setdefault("_dueno_config", {})
    if "v" in cache:
        return cache["v"]

    from sqlalchemy import func
    from .models import TipoConfig

    km: dict[str, dict[bool, float]] = {}
    for tc, usa, k in session.execute(
            select(Viaje.tipo_config, Unidad.usa_remolque, func.sum(Viaje.kilometros))
            .join(Unidad, Viaje.unidad_id == Unidad.id)
            .where(Viaje.kilometros > 0)
            .group_by(Viaje.tipo_config, Unidad.usa_remolque)).all():
        nombre = tc.value if isinstance(tc, TipoConfig) else tc
        if nombre:
            km.setdefault(nombre, {})[bool(usa)] = float(k or 0)

    out: dict[str, bool] = {}
    for nombre, v in km.items():
        tracto, rigido = v.get(True, 0.0), v.get(False, 0.0)
        tot = tracto + rigido
        if tot <= 0:
            continue
        if tracto / tot >= DUENO_CLARO:
            out[nombre] = True
        elif rigido / tot >= DUENO_CLARO:
            out[nombre] = False
        else:
            log.info("la configuración %s está repartida entre tractos (%.0f%%) y rígidos: "
                     "no se decide de quién es", nombre, tracto / tot * 100)
    cache["v"] = out
    return out


def factor_config(session: Session) -> dict[str, float]:
    """Cuánto rinde un tracto en cada configuración, relativo a SENCILLO.

    Se mide DENTRO de cada unidad y después se toma la mediana entre unidades. El cociente
    dentro de una misma unidad es lo único que aísla el efecto del enganche: comparar el
    FULL de un camión contra el SENCILLO de otro mide la diferencia entre los dos camiones.

    La mediana y no el promedio porque una unidad con dos viajes FULL no puede mover el
    número que decide cuánto se le cobra a los demás.

    Devuelve {} cuando no hay con qué medirlo. Un {} vacío significa «no se descompone»,
    que es la respuesta honesta: la alternativa sería inventar un factor.
    """
    cache = session.info.setdefault("_factor_config", {})
    if "v" in cache:
        return cache["v"]

    from statistics import median
    from sqlalchemy import func
    from .models import TipoConfig

    por_unidad: dict[int, dict[str, tuple[float, float]]] = {}
    for uid, tc, km, lts in session.execute(
            select(Viaje.unidad_id, Viaje.tipo_config, func.sum(Viaje.kilometros),
                   func.sum(Viaje.lts_real))
            .where(Viaje.kilometros > 0, Viaje.lts_real > 0, Viaje.unidad_id.isnot(None))
            .group_by(Viaje.unidad_id, Viaje.tipo_config)).all():
        nombre = tc.value if isinstance(tc, TipoConfig) else tc
        if nombre:
            por_unidad.setdefault(uid, {})[nombre] = (float(km or 0), float(lts or 0))

    # Sólo tractos: entre un tracto y un rígido no hay factor que valga —son poblaciones
    # que no se tocan (el peor rígido rinde más que el mejor tracto).
    tractos = {u.id for u in session.execute(
        select(Unidad).where(Unidad.usa_remolque.is_(True))).scalars()}
    # ...y sólo entre configuraciones que un tracto SÍ puede hacer. Sin esta línea, los
    # seis viajes de tracto mal etiquetados THORTON producían un factor de 1.119 y con él
    # una referencia THORTON para tractos: el error de captura fabricando su propia vara.
    del_tracto = {c for c, es in dueno_config(session).items() if es}

    cocientes: dict[str, list[float]] = {}
    for uid, confs in por_unidad.items():
        if uid not in tractos:
            continue
        base = confs.get(CONFIG_BASE)
        if not base or base[1] <= 0 or base[0] <= 0:
            continue
        r_base = base[0] / base[1]
        for nombre, (km, lts) in confs.items():
            if nombre == CONFIG_BASE or lts <= 0 or km <= 0:
                continue
            if nombre not in del_tracto:
                continue
            cocientes.setdefault(nombre, []).append((km / lts) / r_base)

    out = {CONFIG_BASE: 1.0}
    for nombre, vs in cocientes.items():
        if len(vs) >= MIN_UNIDADES_FACTOR:
            # DOS decimales, no tres. El intervalo de confianza del factor es de ±0.04
            # (bootstrap sobre 8 unidades y 35 viajes FULL): el tercer decimal no lo
            # sostiene la muestra, y publicarlo finge una precisión que no existe.
            out[nombre] = round(median(vs), 2)
        else:
            log.info("factor de %s no medible: sólo %d unidad(es) corrieron ambas",
                     nombre, len(vs))
    cache["v"] = out
    cache["n"] = {k: len(v) for k, v in cocientes.items()}
    return out


def config_del_escaneo(session: Session, esc: EscaneoMotor) -> str | None:
    """Qué configuración midió este escaneo, preguntándoselo a su viaje gemelo.

    `Viaje.lts_scaner` es la lectura del motor de ese período, así que el renglón de
    bitácora con los mismos kilómetros y los mismos litros que el escaneo ES el escaneo,
    visto desde la bitácora. Y ese renglón trae `tipo_config`. De los 43 escaneos, 36 tienen
    gemelo ÚNICO, 6 tienen dos candidatos y 1 no tiene ninguno; contando el desempate por fecha
    y los gemelos que vienen sin etiquetar, la configuración se resuelve en 34 de 43 (79%). Los
    9 restantes caen a la deducción por ventana o a la referencia sin separar, que es lo
    correcto: no saber no es lo mismo que suponer.

    Esto es exacto donde el cruce por fechas era una deducción, y sobre todo donde no podía
    serlo: cinco escaneos vigentes no traen principio de período, y ahí la «ventana» era en
    realidad todo el historial anterior al cierre.

    Cuando cuadran DOS renglones —pasa en cuatro escaneos— se desempata por fecha: el que
    está fechado en el cierre del período ES ese período, y el otro es una coincidencia o
    un renglón repetido. Si ninguno cuadra en fecha no se elige: adivinar cuál es sería
    peor que no saber.
    """
    if esc is None or not esc.km or not esc.litros:
        return None
    km, lts = float(esc.km), float(esc.litros)
    gemelos = session.execute(
        select(Viaje).where(
            Viaje.unidad_id == esc.unidad_id,
            Viaje.kilometros.between(km - 1, km + 1),
            Viaje.lts_scaner.between(lts - 1, lts + 1))).scalars().all()
    if len(gemelos) > 1:
        gemelos = [g for g in gemelos if g.fecha == esc.periodo_fin]
    if len(gemelos) != 1:
        return None
    tc = gemelos[0].tipo_config
    return tc.value if tc else None


def _mezcla_ventana(session: Session, unidad_id: int, esc: EscaneoMotor | None,
                    factores: dict[str, float]) -> dict[str, float]:
    """Qué proporción de los km de la ventana del escaneo hizo cada configuración.

    `periodo_inicio` puede venir NULL —el formato Cummins no lo trae— y entonces la ventana
    no tiene principio: se toma todo lo anterior al cierre. Es impreciso, pero el sesgo va
    hacia la mezcla histórica de la unidad, que es mejor suposición que ninguna.
    """
    from sqlalchemy import func
    from .models import TipoConfig

    q = select(Viaje.tipo_config, func.sum(Viaje.kilometros)).where(
        Viaje.unidad_id == unidad_id, Viaje.kilometros > 0)
    if esc is not None:                   # `None` = todo el historial de la unidad
        q = q.where(Viaje.fecha <= esc.periodo_fin)
        if esc.periodo_inicio:
            q = q.where(Viaje.fecha >= esc.periodo_inicio)
    mezcla: dict[str, float] = {}
    for tc, km in session.execute(q.group_by(Viaje.tipo_config)).all():
        nombre = tc.value if isinstance(tc, TipoConfig) else tc
        # Los viajes sin configuración no pueden ponderarse: se dejan fuera de la mezcla en
        # vez de contarlos como SENCILLO. Contarlos sería decidir por ellos.
        if nombre in factores:
            mezcla[nombre] = mezcla.get(nombre, 0.0) + float(km or 0)
    return mezcla


def _separar(session: Session, r: Rendimiento, uni: Unidad, esc: EscaneoMotor,
             config: str) -> Rendimiento:
    """Del km/l mezclado que midió el escáner, saca el de UNA configuración.

    Si la ventana tuvo una proporción s_c de sus kilómetros en cada configuración c, y cada
    una rinde f_c veces lo que rinde un SENCILLO:

        L = Σ(km_c / r_c) = Σ(km_c / (r_base·f_c)) = (K/r_base)·Σ(s_c/f_c)
        r_base = (K/L)·Σ(s_c/f_c) = r_escáner · Σ(s_c/f_c)
        r_c    = f_c · r_base

    Con una ventana 100% SENCILLO el multiplicador es 1 y el SENCILLO no se mueve: sólo
    aparece el FULL, más abajo. Con la ventana 94% FULL de T181 el multiplicador es 1.239,
    y el FULL que sale —1.76— coincide al 2% con el que dicen sus facturas, que es un
    camino de medición completamente distinto.
    """
    factores = factor_config(session)
    dueno = dueno_config(session)
    es_tracto = bool(uni.usa_remolque)

    # ¿Esta unidad puede siquiera hacer un viaje de esta configuración? La respuesta sale
    # de dónde están los kilómetros de esa configuración en la flota, no de una lista.
    if config not in dueno:
        return Rendimiento(config=config, motivo=(
            f"no se sabe de qué familia de unidad es la configuración {config}: "
            "sus kilómetros están repartidos entre tractos y rígidos"))
    if dueno[config] != es_tracto:
        familia = "un tracto" if es_tracto else "un camión rígido"
        suya = "de rígidos" if es_tracto else "de tractos"
        return Rendimiento(config=config, motivo=(
            f"{uni.clave} es {familia} y {config} es una configuración {suya}: "
            "la captura está equivocada y no hay contra qué medir ese viaje"))

    # Un rígido no engancha nada: su configuración ES el camión, y lo que midió su escáner
    # ya es la referencia de esa configuración. No hay nada que separar.
    if not es_tracto:
        return replace(r, config=config, factor=1.0, base=r.valor,
                       motivo=f"{r.motivo}; es un rígido, su escáner ya mide esa operación")

    if config not in factores:
        return Rendimiento(config=config, motivo=(
            f"no hay suficientes unidades que hayan corrido {config} y {CONFIG_BASE} "
            f"para medir el factor entre las dos"))

    # PRIMERO se le pregunta al viaje gemelo qué midió el escáner. Es exacto y no depende
    # de fechas. Sólo si no hay gemelo, o si viene sin etiqueta, se deduce de la ventana.
    medida = config_del_escaneo(session, esc)
    if medida in factores:
        mezcla = {medida: 1.0}
        de_donde = f"ese escáner midió un viaje {medida}"
    elif medida is not None:
        # El gemelo existe pero su configuración no es de esta familia —o no se sabe
        # ponderar—: no se puede usar esa lectura para separar nada.
        return Rendimiento(config=config, motivo=(
            f"{r.motivo}; ese escáner midió un viaje {medida} y no hay factor para "
            f"relacionarlo con {config}"))
    else:
        mezcla = _mezcla_ventana(session, uni.id, esc, factores)
        # Sin gemelo hay que deducirlo de las fechas, y cinco escaneos vigentes no traen
        # principio de período —el formato Cummins no lo da—, así que ahí esto no es la
        # ventana: es todo lo anterior al cierre. Se dice, porque de este texto sale la
        # frase con la que alguien va a defender un cobro.
        # Aquí sí hay que cruzar por fechas, y `periodo_inicio` es de fiar sólo en DDEC: en
        # Cummins lo fabrica el importador. Se dice de cuál de los dos casos se trata en vez
        # de llamar «ventana» a un rango que puede estar inventado.
        de_donde = ("esa ventana" if (esc.periodo_inicio and esc.formato == "DDEC") else
                    f"lo que hizo hasta el {esc.periodo_fin} (ese escáner no declara qué días "
                    f"cubrió, así que la ventana es aproximada)")
    if sum(mezcla.values()) <= 0:
        # Sin viajes dentro de la ventana no se sabe qué se jaló mientras el motor medía.
        # Se recurre a la mezcla histórica de la unidad, que es una suposición declarada y
        # no un silencio. Antes esta rama devolvía el MISMO número para SENCILLO y para
        # FULL, que es justo el daño que se viene a reparar, sólo que sin avisar.
        mezcla = _mezcla_ventana(session, uni.id, None, factores)
        de_donde = "su historial (no hay viajes dentro de la ventana del escáner)"
    total = sum(mezcla.values())
    if total <= 0:
        # La unidad no tiene un solo viaje con configuración. Se supone que el escáner midió
        # la configuración base —12 de las 19 ventanas lo son— y se DICE que se supuso.
        mezcla, total = {CONFIG_BASE: 1.0}, 1.0
        de_donde = f"se supone {CONFIG_BASE}: la unidad no tiene viajes con configuración"

    mult = sum((km / total) / factores[nombre] for nombre, km in mezcla.items())
    base = r.valor * mult
    f = factores[config]
    reparto = ", ".join(f"{n} {km / total * 100:.0f}%"
                        for n, km in sorted(mezcla.items(), key=lambda x: -x[1]))
    return replace(r, valor=round(base * f, 2), config=config, factor=f, base=round(base, 2),
                   motivo=(f"{r.motivo}; {de_donde}: {reparto}, de donde sale "
                           f"{base:.2f} km/l en {CONFIG_BASE} y {base * f:.2f} en {config} "
                           f"(factor {f})"))


def llave_periodo(e: EscaneoMotor) -> tuple:
    """Qué hace que dos lecturas sean LA MISMA lectura.

    El odómetro de por vida es estrictamente creciente dentro de una unidad, así que dos
    lecturas que cierran en el mismo kilómetro son el mismo período —pasa con los dos
    archivos que se importaron con distinto nombre—. Cuando no hay odómetro (el formato
    «Summary» de Cummins no lo imprime) se cae al período y sus cifras, que es más débil
    pero existe.

    Vive aquí y no dentro de `_serie` porque la usan dos cosas: juntar la serie para la
    referencia, y construir el historial previo contra el que la alerta compara cada
    lectura. Un período duplicado colado en cualquiera de las dos las estropea igual.
    """
    if e.odometro_total is not None:
        return (round(float(e.odometro_total), 2),)
    return (e.periodo_fin, round(float(e.km or 0), 2), round(float(e.litros or 0), 2))


def _serie(session: Session, uid: int) -> list[EscaneoMotor]:
    """Los escaneos de una unidad, del más reciente hacia atrás y SIN períodos repetidos.

    Hay dos períodos importados dos veces con distinto nombre de archivo (T203 y T228), y
    la idempotencia del importador es por `archivo`, así que no los atrapó. El odómetro de
    cierre sí identifica el período: dos lecturas que cierran en el mismo kilómetro de por
    vida son la misma. Sin esto, sumarlas contaría ese período dos veces.
    """
    es = session.execute(
        select(EscaneoMotor)
        .where(EscaneoMotor.unidad_id == uid, EscaneoMotor.km > 0, EscaneoMotor.litros > 0)
        .order_by(EscaneoMotor.periodo_fin.desc(), EscaneoMotor.id.desc())).scalars().all()
    vistos: set = set()
    out: list[EscaneoMotor] = []
    for e in es:
        clave = llave_periodo(e)
        if clave in vistos:
            continue
        vistos.add(clave)
        out.append(e)
    if not out:
        return out
    # La ventana se cuenta desde la lectura más reciente, no desde hoy: una unidad que
    # lleva meses sin escanearse conserva su referencia en vez de quedarse sin ninguna.
    corte = out[0].periodo_fin - timedelta(days=settings.dias_serie_escaneo)
    return [e for e in out if e.periodo_fin >= corte]


def _juntar(grupo: list[EscaneoMotor]) -> tuple[float, float]:
    """Kilómetros y litros de un grupo de escaneos. Los períodos no se pisan: se suman."""
    return (sum(float(e.km) for e in grupo), sum(float(e.litros) for e in grupo))


def _de_la_serie(grupo: list[EscaneoMotor], que: str) -> Rendimiento | None:
    """La referencia que sale de juntar las lecturas de la unidad, hasta un tope de km.

    Se recorre de la más reciente hacia atrás acumulando kilómetros y se para al llegar a
    `km_serie_escaneo`, que hoy no recorta a nadie: es un freno para el futuro, no un punto
    de corte elegido. Se midió contra una vara independiente —el km/l de TODOS los viajes de
    la unidad según la computadora del motor, quitando los gemelos de los escaneos para que
    la comparación no fuera circular— y el error medio va de 12.4% con sólo la última lectura
    a 8.8% juntando la serie, tocando fondo a partir de unos 5,000 km.

    Se probó un tope de 3,000 km, por miedo a arrastrar un régimen viejo (T145 parece haber
    cambiado: su km/l correlaciona -0.86 con el ORDEN cronológico y sólo -0.07 con el tamaño
    de la ventana). Era peor: 9.6%, y en 4 de los 10 pares con varias lecturas devolvía
    exactamente la última, o sea que no corregía nada. La vara tampoco respalda el miedo —para
    T145 acierta más juntando (3.9%) que con la última (4.5%)—, aunque hay que decir que esa
    vara promedia meses y por eso no puede arbitrar bien un cambio de régimen. Donde juntar sí
    sale peor es T206, cuyas dos lecturas LARGAS discrepan 11%: una unidad de 19.

    km_total entre litros_total es la única agregación correcta para un cociente: pondera
    por kilómetro sola, así que una extracción de 609 km no pesa lo mismo que una de 5,701.
    La mediana de las lecturas las trataría igual.
    """
    if not grupo:
        return None
    bastante = settings.km_serie_escaneo
    usados: list[EscaneoMotor] = []
    km = 0.0
    for e in grupo:                       # de la más reciente hacia atrás
        usados.append(e)
        km += float(e.km)
        if km >= bastante:
            break
    grupo = usados
    km, lts = _juntar(grupo)
    if lts <= 0 or km < settings.piso_km_escaneo:
        return None
    reciente = grupo[0]
    # Sólo se citan fechas de EXTRACCIÓN (`periodo_fin`), que son reales. `periodo_inicio` no
    # viene en el PDF de Cummins: lo escribe el importador copiando el cierre del escaneo
    # anterior, así que 33 de los 43 lo traen nulo o fabricado. De este texto sale la frase
    # con la que alguien va a discutir un cobro: no puede apoyarse en una fecha inventada.
    if len(grupo) == 1:
        cuando = f"1 lectura {que} del {reciente.periodo_fin}"
    else:
        cuando = (f"{len(grupo)} lecturas {que}, de la del {grupo[-1].periodo_fin} "
                  f"a la del {reciente.periodo_fin}")
    return Rendimiento(
        valor=round(km / lts, 2), escaneo_id=reciente.id, km=km, litros=lts,
        # El odómetro tiene que ser el de la lectura MÁS RECIENTE y no algo juntado: de él
        # cuelga el cálculo de los kilómetros recorridos desde la última medición.
        odometro=float(reciente.odometro_total) if reciente.odometro_total else None,
        fecha=reciente.periodo_fin.isoformat() if reciente.periodo_fin else None,
        motivo=f"escáner: {cuando}, {km:,.0f} km en total")


def _lectura(e: EscaneoMotor) -> Rendimiento:
    """La referencia que sale de un escaneo concreto."""
    return Rendimiento(valor=round(float(e.km) / float(e.litros), 2), escaneo_id=e.id,
                       km=float(e.km), litros=float(e.litros),
                       odometro=float(e.odometro_total) if e.odometro_total else None,
                       fecha=e.periodo_fin.isoformat() if e.periodo_fin else None,
                       motivo=f"escáner del {e.periodo_fin} ({float(e.km):,.0f} km)")


def vigente(session: Session, unidad: Unidad | int, *,
            config: str | None = None) -> Rendimiento:
    """El rendimiento que rige hoy: la SERIE de escaneos de la unidad, no el último.

    Quedarse con la última lectura dejaba la referencia en un extremo de la propia serie en
    la mayoría de los casos —T182 lee 2.98 y 4.15 y regía 4.15—, y de ahí salen pesos con
    nombre y apellido. Se juntan las lecturas: sus kilómetros entre sus litros.

    JUNTAR NO ES PROMEDIAR. Los escaneos son períodos cerrados y consecutivos que embaldosan
    la operación de la unidad: el odómetro de cierre de uno más los km del siguiente da el
    odómetro del siguiente (19 de los 23 pares de la base cuadran al metro, y los 3 que no
    son períodos duplicados, que `_serie` quita). Sumarlos mide sobre una ventana más larga,
    que es exactamente lo que le falta a una lectura de 792 km.

    Tres ramas, en orden de calidad del dato:
      1. Hay lecturas de ESA configuración → se juntan. Medición directa, sin factor.
      2. No hay ninguna → el escaneo más reciente, separado con el factor de flota.
      3. Sin `config` → se junta todo: los kilómetros de la unidad entre sus litros.

    El piso de km se le exige al TOTAL juntado y no a cada lectura, así que una extracción de
    172 km deja de tirarse y pasa a pesar dentro de la suma lo poco que le toca.
    """
    uid = unidad.id if isinstance(unidad, Unidad) else unidad
    piso = settings.piso_km_escaneo
    serie = _serie(session, uid)
    if not serie:
        return Rendimiento()

    if config is None:
        # «La referencia de la unidad», sin distinguir lo que llevaba enganchado.
        r = _de_la_serie(serie, "del escáner")
        if r is not None:
            return r
        km, _ = _juntar(serie)
        return Rendimiento(motivo=(f"sus {len(serie)} escaneos suman {km:,.0f} km, menos "
                                   f"del piso de {piso:,.0f} km"))

    # ── 1. las lecturas que midieron ESTA configuración ──────────────────────
    propias = [e for e in serie if config_del_escaneo(session, e) == config]
    r = _de_la_serie(propias, f"de operación {config}")
    if r is not None:
        return replace(r, config=config, factor=1.0)

    uni = unidad if isinstance(unidad, Unidad) else session.get(Unidad, uid)

    # ── 2. no hay lecturas de ésta, pero sí de la configuración BASE ─────────
    # Entonces el factor se aplica a ESA, no a la referencia mezclada. T210 publicaba
    # SENCILLO 2.24 —juntando sus dos lecturas etiquetadas— y un FULL derivado del 2.39
    # mezclado: dos números de la misma unidad que no se sostenían entre sí.
    if uni is not None and config != CONFIG_BASE:
        factores = factor_config(session)
        f = factores.get(config)
        de_base = _de_la_serie(
            [e for e in serie if config_del_escaneo(session, e) == CONFIG_BASE],
            f"de operación {CONFIG_BASE}")
        if f and de_base is not None and dueno_config(session).get(config) == bool(
                uni.usa_remolque):
            return replace(
                de_base, valor=round(de_base.valor * f, 2), config=config, factor=f,
                base=de_base.valor,
                motivo=(f"{de_base.motivo}; de ahí salen {de_base.valor * f:.2f} km/l en "
                        f"{config} (factor {f})"))

    # ── 3. no hay ninguna lectura etiquetada: se separa la mezclada ──────────
    utiles = [e for e in serie if float(e.km) >= piso]
    if not utiles:
        km, _ = _juntar(serie)
        return Rendimiento(config=config, motivo=(
            f"sus {len(serie)} escaneos suman {km:,.0f} km, menos del piso de "
            f"{piso:,.0f} km, y ninguno midió operación {config}"))
    esc = utiles[0]
    base = _de_la_serie(serie, "del escáner") or _lectura(esc)
    if uni is None:
        return base
    return _separar(session, base, uni, esc, config)




def litros_estimados(km: float, rend: float) -> float:
    """Lo que debería gastar el viaje. km ÷ (km/l) = litros."""
    return km / rend


@dataclass
class Tope:
    tope: float | None = None           # litros máximos autorizables
    estimado: float | None = None       # litros que el viaje debería gastar
    km: float | None = None
    rendimiento: Rendimiento | None = None
    motivo: str = ""

    @property
    def hay(self) -> bool:
        return self.tope is not None

    def excede(self, litros: float | None) -> bool:
        return self.hay and litros is not None and litros > self.tope


def _km_del_viaje(session: Session, solicitud: SolicitudRecarga) -> float | None:
    """Los kilómetros contra los que se estima, por orden de fiabilidad.

    `SolicitudRecarga.viaje_id` es la fuente buena pero está SIEMPRE en nulo: ninguna pantalla
    lo escribe, porque el viaje no existe como fila hasta que se congela. Apoyarse sólo en él
    dejaba `tope.hay` en falso para todas las solicitudes, y el techo del 2% no se evaluaba
    nunca: era código, no comportamiento. La asignación ACTIVA del operador es de donde sale
    el viaje en la práctica, y es la misma consulta que ya hace el contexto de la solicitud.
    """
    if solicitud.viaje_id is not None:
        v = session.get(Viaje, solicitud.viaje_id)
        if v is not None and v.kilometros:
            return float(v.kilometros)
    # EL VIAJE DE ESTA SOLICITUD. Antes se resolvía por «la asignación activa del operador
    # ahora», así que levantarle un viaje nuevo movía el tope de las solicitudes vivas al
    # kilometraje del siguiente, sin que nada lo dijera.
    if solicitud.asignacion_id is not None:
        a = session.get(AsignacionViaje, solicitud.asignacion_id)
        if a is not None:
            return km_efectivo(a)
    if solicitud.operador_id is None:
        return None
    # Caída para las solicitudes anteriores a que esto se guardara.
    a = session.scalar(
        select(AsignacionViaje)
        .where(AsignacionViaje.operador_id == solicitud.operador_id,
               AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
        .order_by(AsignacionViaje.creada_en.desc()).limit(1))
    return km_efectivo(a)


def km_efectivo(a: AsignacionViaje | None) -> float | None:
    """Los kilómetros que valen: los del coordinador, y los de la ruta sólo si no los tocó.

    `km_estimado` es el trazo automático; `km_destino`, lo que el coordinador ajusta
    cuando ese trazo sale mal —sobre todo cuando no hubo ruta y se cayó a la línea
    recta, que siempre queda corta—. Hasta hoy todo lo que dimensionaba litros leía el
    automático, así que el número corregido se veía en la ficha del viaje y no entraba
    en ningún cálculo: se autorizaba contra el valor que el coordinador había descartado.

    NO se arregla copiando el uno sobre el otro: `km_modificado` sale de compararlos, y
    pisar el estimado haría que la etiqueta «ajustados a mano» dijera siempre que no.

    Se compara contra None y no por verdad: unos kilómetros en cero son un dato —un
    viaje sin distancia—, no un hueco, y `or` los confundiría con el vacío.
    """
    if a is None:
        return None
    v = a.km_destino if a.km_destino is not None else a.km_estimado
    return float(v) if v is not None else None


def config_de_asignacion(session: Session, a: AsignacionViaje | None,
                         uni: Unidad | None = None) -> str | None:
    """Qué configuración es este viaje. Lo dice el enganche, no una etiqueta.

    Dos remolques es un FULL y uno es un SENCILLO: eso no es una suposición, es lo que
    significan las palabras. Y está disponible al despachar —mucho antes de que exista un
    `Viaje` que etiquetar—, que es justo cuando hace falta para calcular el tope.

    Un rígido no engancha nada: su configuración es la de su familia, y se pregunta cuál
    es en vez de escribirla aquí. Un tracto en bobtail (sin remolque) no tiene factor
    medido —no hay viajes así en la bitácora— y devuelve None, que significa «usa la
    referencia sin separar», no «cero».
    """
    if uni is not None and not uni.usa_remolque:
        propias = [c for c, es_tracto in dueno_config(session).items() if not es_tracto]
        return propias[0] if len(propias) == 1 else None
    if a is None:
        return None
    n = len(a.remolque_ids or [])
    return {1: "SENCILLO", 2: "FULL"}.get(n)


def _asignacion_de(session: Session, solicitud: SolicitudRecarga) -> AsignacionViaje | None:
    """La asignación de la que cuelga esta solicitud.

    MISMO orden de resolución que `_km_del_viaje`, a propósito: los kilómetros y la
    configuración del tope tienen que salir del MISMO viaje. Si cada uno resolviera por su
    cuenta, un tope podría acabar mezclando los kilómetros de un viaje con el enganche de otro,
    y nadie lo notaría hasta que las cifras no cuadraran.
    """
    if solicitud.asignacion_id is not None:
        return session.get(AsignacionViaje, solicitud.asignacion_id)
    if solicitud.operador_id is None:
        return None
    return session.scalar(
        select(AsignacionViaje)
        .where(AsignacionViaje.operador_id == solicitud.operador_id,
               AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
        .order_by(AsignacionViaje.creada_en.desc()).limit(1))


def tope_autorizable(session: Session, solicitud: SolicitudRecarga,
                     km: float | None = None) -> Tope:
    """Cuántos litros como máximo se pueden autorizar para esta solicitud.

    Devuelve un tope vacío —no un cero— cuando falta el rendimiento o los kilómetros. Sin
    base no hay tope, y fingir uno sería peor que no tenerlo: un cero bloquearía despachos
    legítimos de las 27 unidades activas que hoy no tienen escáner.
    """
    if solicitud.unidad_id is None:
        return Tope(motivo="la solicitud no tiene unidad")
    # La referencia de LO QUE VA ENGANCHADO. Con la mezclada, un FULL se estimaba 21% por
    # debajo de lo que iba a quemar y el tope salía corto en esa proporción —210 L en un
    # viaje de 2,000 km—. Un tope que hay que saltarse cada vez deja de avisar de nada.
    uni = session.get(Unidad, solicitud.unidad_id)
    cfg = config_de_asignacion(session, _asignacion_de(session, solicitud), uni)
    r = vigente(session, solicitud.unidad_id, config=cfg)
    if not r.hay and cfg is not None:
        # Sin referencia para esa configuración, antes que quedarse sin tope se usa la
        # mezclada, diciendo que no se pudo separar. Un tope impreciso sirve más que
        # ninguno: sin él no hay nada que comparar al autorizar.
        _m = r.motivo
        r = vigente(session, solicitud.unidad_id)
        if r.hay:
            r = replace(r, motivo=f"{r.motivo}; sin separar por configuración ({_m})")
    if not r.hay:
        return Tope(rendimiento=r, motivo=f"sin rendimiento: {r.motivo}")

    km = km if km is not None else _km_del_viaje(session, solicitud)
    if not km or km <= 0:
        return Tope(rendimiento=r, motivo="sin kilómetros contra los que estimar")

    est = litros_estimados(km, r.valor)
    pct = settings.tope_despacho_pct
    return Tope(tope=round(est * (1 + pct), 2), estimado=round(est, 2), km=km, rendimiento=r,
                motivo=(f"{km:,.0f} km ÷ {r.valor} km/l = {est:,.0f} L, "
                        f"+{pct * 100:.0f}% = {est * (1 + pct):,.0f} L máx · {r.motivo}"))


# La reserva que el dueño fijó (16-sep-2026). Va aquí y no en `settings.reserva_litros`,
# que es una sola cifra global de 200 L y se la aplicaba también a los camiones.
RESERVA_L = {"CAMION": 100.0, "TRACTO": 200.0}


def panorama(session: Session, solicitud: SolicitudRecarga) -> dict:
    """Todo lo que se puede decir del combustible de esta unidad, junto y con su procedencia.

    Reproduce el cálculo que el dueño hace a mano en su hoja:

        km recorridos = odómetro actual - odómetro anterior
        consumido     = km recorridos / rendimiento
        nivel         = capacidad - consumido

    y añade lo que la hoja no trae pero el sistema sí sabe: los kilómetros del viaje que
    viene, lo que ese viaje debería gastar y el techo del 2%.

    EL NIVEL SE DEVUELVE CONDICIONADO, no afirmado. La tercera línea de la hoja supone el
    tanque lleno, y eso sólo es cierto la primera vez: el nivel del tanque no se captura
    nunca y ninguna carga de esta flota llena un tanque (la mayor de 260 cargas de tracto es
    de 650 L sobre 1,000). Así que se entrega como `nivel_si_lleno` —con su condición escrita
    al lado— en vez de como un dato. Cuando haya libro de tanque, ese campo lo sustituye un
    saldo de verdad.
    """
    from .models import capacidad_tanque

    uni = solicitud.unidad
    out = {"rendimiento": None, "motivo_rendimiento": None, "escaneo_fecha": None,
           "odometro_anterior": None, "odometro_actual": solicitud.odometro,
           "km_recorridos": None, "consumo_recorrido": None,
           "km_viaje": None, "consumo_viaje": None, "tope": None,
           # El tope EN PALABRAS. Se calculaba y se tiraba, así que la pantalla no tenía
           # de dónde sacar su procedencia y acababa escribiendo «+2%» a mano —un número
           # que deja de ser verdad en cuanto se toca `settings.tope_despacho_pct`—.
           "tope_motivo": None,
           "capacidad": None, "reserva": None, "nivel_si_lleno": None,
           "caben_al_menos": None, "nota": None}
    if uni is None:
        out["nota"] = "la solicitud no tiene unidad"
        return out

    cap = capacidad_tanque(uni)
    tipo = (uni.tipo.name if hasattr(uni.tipo, "name") else str(uni.tipo or "")).upper()
    out["capacidad"] = float(cap) if cap else None
    out["reserva"] = RESERVA_L.get(tipo.split(".")[-1])

    r = vigente(session, uni, config=config_de_asignacion(
        session, _asignacion_de(session, solicitud), uni))
    if not r.hay:
        r = vigente(session, uni)          # sin separar antes que sin nada
    out["motivo_rendimiento"] = r.motivo
    if not r.hay:
        out["nota"] = f"sin rendimiento: {r.motivo}"
        return out
    out["rendimiento"] = r.valor
    out["escaneo_fecha"] = r.fecha
    out["odometro_anterior"] = r.odometro

    # Lo recorrido desde la última lectura del motor.
    if r.odometro is not None and solicitud.odometro is not None:
        km = float(solicitud.odometro) - float(r.odometro)
        if km >= 0:
            out["km_recorridos"] = round(km, 2)
            out["consumo_recorrido"] = round(km / r.valor, 2)
            if cap:
                out["nivel_si_lleno"] = round(float(cap) - km / r.valor, 2)
                # La única cota que se puede AFIRMAR sobre el espacio libre. Salir lleno es
                # el supuesto más optimista, así que el nivel real es como mucho
                # `nivel_si_lleno`; luego el hueco es al menos lo consumido desde la lectura.
                # No depende de ninguna suposición: es la misma resta vista al revés.
                out["caben_al_menos"] = round(km / r.valor, 2)
        else:
            out["nota"] = (f"el odómetro de la solicitud ({solicitud.odometro:,.0f}) es MENOR "
                           f"que el del escáner ({r.odometro:,.0f})")

    # El viaje que viene, y su techo.
    t = tope_autorizable(session, solicitud)
    if t.hay:
        out["km_viaje"] = t.km
        out["consumo_viaje"] = t.estimado
        out["tope"] = t.tope
        out["tope_motivo"] = t.motivo
    elif out["nota"] is None:
        out["nota"] = t.motivo
    return out
