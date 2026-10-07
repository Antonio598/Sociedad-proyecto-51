"""Bitácora en el formato NUEVO del cliente (hoja 'BITACORA', 30 columnas).

El archivo que mandó el cliente trae la maqueta con dos filas de ejemplo y SIN fórmulas:
los valores están escritos a mano. Los cálculos de aquí se reconstruyeron a partir de esas
dos filas y se verificaron contra ambas; donde el ejemplo no alcanzaba para decidir, se
dice explícitamente en el comentario en vez de adivinar.

Lo que cambia respecto al formato viejo, y por qué importa:

  · El consumo ya NO se supone igual a lo cargado. Ahora es
        consumo = nivel del tanque al inicio + litros cargados − nivel al cierre
    que es justo la corrección por nivel de tanque que faltaba: si la unidad termina el
    período con más diésel del que empezó, cargó más de lo que quemó, y contarlo como
    consumo la hacía parecer ineficiente. El nivel sale de la AGUJA que lee el bot.

  · Aparece el concepto de RESERVA (200 L): cuánto de lo que trae el tanque cuenta como
    reserva y cuántos litros faltan para completarla.

  · Entra el costo en pesos (litros × precio) y el costo del ralentí.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    CAPACIDAD_TANQUE_L, RENDIMIENTO_IDEAL, EscaneoMotor, Unidad, Viaje,
)

# Rendimiento objetivo (columna V del formato): una sola fuente en models.py.
OBJETIVO = RENDIMIENTO_IDEAL

# Encabezados EXACTOS del archivo del cliente, en orden. La exportación debe salir así
# para que puedan pegarla en su hoja sin reacomodar nada.
COLUMNAS = [
    ("fecha", "FECHA"),
    ("unidad", "UNIDAD"),
    ("tipo", "TIPO"),
    ("operador", "NOMBRE DEL OPERADOR"),
    ("odometro_final", "KILOMETRAJE FINAL ODOMETRO"),
    ("odometro_inicial", "KILOMETRAJE INICIAL ODOMETRO"),
    ("km_recorridos", "KILOMETROS RECORRIDOS ODOMETRO"),
    ("lts_cargados", "LITROS CARGADOS"),
    ("nivel_litros", "NIVEL DE COMBUSTIBLE TOTAL ACTUAL LITROS"),
    ("rto_operacion", "KMS/LT OPERACION REAL"),
    ("lts_reserva", "LITROS ACTUAL EN RESERVA"),
    ("lts_completar_reserva", "LITROS PARA COMPLETAR RESERVA"),
    ("costo_combustible", "COSTO COMBUSTIBLE"),
    ("fecha_escaner", "FECHA ESCANER"),
    ("tipo_motor", "TIPO DE MOTOR"),
    ("km_escaner", "KILOMETROS SCANER"),
    ("lts_escaner", "LITROS ESCANER"),
    ("rto_escaner", "RENDIMIENTO ESCANER"),
    ("dif_rto", "DIF RO vs RE kms/ltr"),
    ("lts_debidos", "LITROS QUE SE DEBIERON CONSUMIR SEGÚN ESCANER"),
    ("pct_diferencia", "% DIFERENCIA"),
    ("rto_objetivo", "RENDIMIENTO OBJETIVO"),
    ("dif_costo", "DIFERENCIA EN COSTO"),
    ("descuento_operador", "DESCUENTO OPERADOR RECOMENDADO PESOS"),
    ("pct_ralenti", "% RALENTI"),
    ("lts_ralenti", "LITROS USADOS EN RALENTI"),
    ("costo_ralenti", "COSTO RALENTI"),
    ("analisis_escaner", "ANALISIS GENERAL ESCANER"),
    ("thermo", "THERMO"),
    ("horas_finales", "HORAS FINALES"),
]


@dataclass
class Fila:
    """Una fila de la bitácora nueva, ya calculada."""

    viaje_id: int | None = None
    fecha: date | None = None
    unidad: str | None = None
    tipo: str | None = None
    operador: str | None = None
    odometro_final: float | None = None
    odometro_inicial: float | None = None
    km_recorridos: float | None = None
    lts_cargados: float | None = None
    nivel_litros: float | None = None
    rto_operacion: float | None = None
    lts_reserva: float | None = None
    lts_completar_reserva: float | None = None
    costo_combustible: float | None = None
    fecha_escaner: date | None = None
    tipo_motor: str | None = None
    km_escaner: float | None = None
    lts_escaner: float | None = None
    rto_escaner: float | None = None
    dif_rto: float | None = None
    lts_debidos: float | None = None
    pct_diferencia: float | None = None
    rto_objetivo: float | None = None
    dif_costo: float | None = None
    descuento_operador: float | None = None
    pct_ralenti: float | None = None
    lts_ralenti: float | None = None
    costo_ralenti: float | None = None
    analisis_escaner: str | None = None
    thermo: str | None = None
    horas_finales: float | None = None
    # Trazabilidad: de dónde salió el nivel inicial, que es el supuesto más delicado.
    nota_nivel: str | None = None


def _cap(unidad: Unidad | None) -> float | None:
    if unidad is None or unidad.tipo is None:
        return None
    return CAPACIDAD_TANQUE_L.get(unidad.tipo)


def nivel_en_litros(viaje: Viaje) -> float | None:
    """Litros que trae el tanque: la aguja (fracción 0–1) por la capacidad de la unidad."""
    if viaje.nivel_tanque is None:
        return None
    cap = _cap(viaje.unidad)
    return round(viaje.nivel_tanque * cap, 1) if cap else None


def _escaneo_del_viaje(session: Session, viaje: Viaje) -> EscaneoMotor | None:
    """El escaneo del motor que cierra en la fecha del viaje (así se vinculó la bitácora)."""
    return session.execute(
        select(EscaneoMotor)
        .where(EscaneoMotor.unidad_id == viaje.unidad_id,
               EscaneoMotor.periodo_fin == viaje.fecha)
        .order_by(EscaneoMotor.id)
    ).scalars().first()


def _viaje_previo(session: Session, viaje: Viaje) -> Viaje | None:
    return session.execute(
        select(Viaje).where(Viaje.unidad_id == viaje.unidad_id, Viaje.fecha < viaje.fecha)
        .order_by(Viaje.fecha.desc(), Viaje.id.desc())
    ).scalars().first()


def calcular(session: Session, viaje: Viaje, precio: float | None = None,
             reserva: float | None = None) -> Fila:
    """Calcula la fila del formato nuevo para un viaje."""
    # Precio del AÑO del viaje: un período de 2025 no se valúa al precio de hoy, y sale
    # de la misma fuente que el reporte ejecutivo.
    if precio is None:
        from .config import precio_del_litro
        precio = precio_del_litro(viaje.fecha.year if viaje.fecha else None)
    reserva = settings.reserva_litros if reserva is None else reserva

    f = Fila(viaje_id=viaje.id, fecha=viaje.fecha,
             unidad=viaje.unidad.clave if viaje.unidad else None,
             tipo=viaje.tipo_config.value if viaje.tipo_config else None,
             operador=viaje.operador.nombre if viaje.operador else None)

    # ── Odómetro ────────────────────────────────────────────────────────────
    f.odometro_final = viaje.odometro
    prev = _viaje_previo(session, viaje)
    if viaje.odometro is not None and viaje.kilometros is not None:
        f.odometro_inicial = viaje.odometro - viaje.kilometros
    elif prev is not None:
        f.odometro_inicial = prev.odometro
    if f.odometro_final is not None and f.odometro_inicial is not None:
        f.km_recorridos = f.odometro_final - f.odometro_inicial
    else:
        f.km_recorridos = viaje.kilometros

    # ── Combustible y nivel ─────────────────────────────────────────────────
    f.lts_cargados = viaje.lts_real
    f.nivel_litros = nivel_en_litros(viaje)

    # Nivel al INICIO del período. El ejemplo del cliente no permite distinguir si es el
    # nivel de cierre del período anterior o la reserva fija (en sus dos filas ambas dan
    # 200), así que se usa el encadenado —que es lo físicamente correcto— y se cae a la
    # reserva solo si no hay período previo con lectura. La nota deja dicho cuál se usó.
    nivel_ini = None
    if prev is not None:
        nivel_ini = nivel_en_litros(prev)
        if nivel_ini is not None:
            f.nota_nivel = f"nivel de cierre del período anterior ({prev.fecha})"
    if nivel_ini is None:
        nivel_ini = reserva
        f.nota_nivel = f"sin lectura previa: se supone la reserva de {reserva:g} L"

    if f.lts_cargados is not None and f.nivel_litros is not None:
        consumo = nivel_ini + f.lts_cargados - f.nivel_litros
        if consumo > 0 and f.km_recorridos:
            f.rto_operacion = round(f.km_recorridos / consumo, 2)

    if f.nivel_litros is not None:
        f.lts_reserva = min(f.nivel_litros, reserva)
        f.lts_completar_reserva = max(reserva - f.nivel_litros, 0)

    if f.lts_cargados is not None:
        f.costo_combustible = round(f.lts_cargados * precio, 2)

    # ── Escáner del motor ───────────────────────────────────────────────────
    esc = _escaneo_del_viaje(session, viaje)
    if esc is not None:
        f.fecha_escaner = esc.periodo_fin
        f.tipo_motor = esc.motor
        f.km_escaner = esc.km
        f.lts_escaner = esc.litros
        f.rto_escaner = esc.rendimiento or (
            round(esc.km / esc.litros, 2) if esc.km and esc.litros else None)
        f.pct_ralenti = esc.pct_ralenti
        f.lts_ralenti = esc.lts_ralenti
        f.analisis_escaner = esc.analisis
        if esc.lts_ralenti is not None:
            f.costo_ralenti = round(esc.lts_ralenti * precio, 2)
    else:
        f.lts_escaner = viaje.lts_scaner
        f.pct_ralenti = viaje.ralenti
        if f.lts_escaner and f.km_recorridos:
            f.rto_escaner = round(f.km_recorridos / f.lts_escaner, 2)

    # DIF RO vs RE: escáner menos operación (negativo = la operación rindió "más")
    if f.rto_escaner is not None and f.rto_operacion is not None:
        f.dif_rto = round(f.rto_escaner - f.rto_operacion, 2)

    # Litros que se debieron consumir según el escáner: km del período / rendimiento medido
    if f.km_recorridos and f.rto_escaner:
        f.lts_debidos = round(f.km_recorridos / f.rto_escaner, 2)

    # El rendimiento medido de ESA unidad manda; la constante por tipo es el último
    # recurso. Estaba al revés, y así la constante (1.9 / 2.8 / 4.0) tapaba siempre al
    # dato real: `rendimiento_objetivo` no llegaba a usarse nunca.
    #
    # Y entre los dos entra el ESCÁNER, que es la referencia que el dueño dejó en pie el
    # 20-sep-2026 al retirar el catálogo. El objetivo capturado lo tiene 1 unidad de 53 y
    # el escáner 19, así que sin este paso la columna la llenaba casi siempre la constante
    # retirada. No se quita la constante del todo: esta columna reproduce el formato del
    # Excel del cliente y vaciarla en dos tercios de las filas cambiaría un documento que
    # quizá concilian contra el suyo. Lo que se hace es que el dato medido gane cuando existe.
    f.rto_objetivo = viaje.unidad.rendimiento_objetivo if viaje.unidad is not None else None
    if f.rto_objetivo is None and viaje.unidad is not None:
        from . import rendimiento as _rend
        _r = _rend.vigente(session, viaje.unidad.id,
                           config=f.tipo if f.tipo in ("SENCILLO", "FULL", "THORTON")
                           else None)
        f.rto_objetivo = _r.valor if _r.hay else None
    if f.rto_objetivo is None:
        f.rto_objetivo = OBJETIVO.get(f.tipo or "")

    # ── Thermo (equipo de refrigeración del remolque) ───────────────────────
    f.thermo = viaje.remolque_thermo
    f.horas_finales = viaje.horas_termo

    return f


def filas(session: Session, desde: date | None = None, hasta: date | None = None,
          unidad: str | None = None, limite: int = 500) -> list[dict]:
    """Bitácora nueva de un rango, lista para mostrar o exportar."""
    # Los retractados quedan fuera: la bitácora es el entregable, y un reporte que su autor
    # retiró no puede ir ahí como si siguiera vigente.
    q = select(Viaje).where(Viaje.retractado_en.is_(None)).order_by(Viaje.fecha.desc(), Viaje.id.desc())
    if desde:
        q = q.where(Viaje.fecha >= desde)
    if hasta:
        q = q.where(Viaje.fecha <= hasta)
    if unidad:
        q = q.join(Unidad, Viaje.unidad_id == Unidad.id).where(Unidad.clave == unidad)
    viajes = list(session.execute(q.limit(limite)).scalars())
    return [asdict(calcular(session, v)) for v in viajes]
