"""Qué le contamos al operador de lo que el sistema detectó en sus viajes.

Decisión del dueño (20-sep-2026): al chofer NO se le muestran penalizaciones. Se le avisa de
comportamiento inusual, según el caso y la anomalía. Las anomalías son el mecanismo por el
que el desempeño pesa, pero su pantalla informa; no cobra.

De ahí salen las tres reglas de este módulo:

1. SÓLO LO CONFIRMADO. Una anomalía sin revisar es una sospecha del sistema, no un hecho. De
   las 45 que hay en la base, 40 acabaron RECHAZADAS: enseñarlas todas habría sido acusar al
   chofer 40 veces de cosas que no eran. Se le enseña lo que una persona ya validó.

2. SÓLO LO QUE ES SUYO. Hay anomalías que describen el catálogo o la administración, no su
   trabajo: que el viaje lo conduzca alguien distinto del titular (`operador_no_asignado`) es
   un problema del padrón —2,459 de 2,461 viajes lo tienen— y echárselo encima sería ruido y
   además injusto.

3. NADA DE FRAUDE POR LA APP. `carga_fantasma`, `comprobante_inflado` y `sifoneo_tanque` son
   acusaciones de robo. Eso lo habla una persona mirando a otra, con el expediente delante.
   Una notificación en un teléfono es el peor canal posible para eso, y además no admite
   respuesta. Se excluyen a propósito: el coordinador las ve, el chofer no.

Lo que sí ve: sus datos de viaje y su forma de conducir, que es lo único sobre lo que puede
hacer algo mañana.
"""

from __future__ import annotations

from .validacion import CATEGORIA_ANOMALIA

# Categorías que llegan al operador. `fraude` y `personal` quedan fuera (ver arriba).
_CATEGORIAS_VISIBLES = {"base", "telemetria"}

# De `consistencia` sólo lo que describe SU captura; lo administrativo se queda fuera.
_CONSISTENCIA_VISIBLE = {"km_vs_odometro", "reporte_duplicado",
                         "rendimiento_fuera_banda_fisica"}

# Cómo se lo decimos. Sin jerga, sin cifras de dinero y sin atribuir intención: se describe
# el hecho y qué puede hacer con él. El texto largo de la anomalía lo escribió el motor para
# un auditor; esto es para un chofer en su teléfono.
TEXTO: dict[str, tuple[str, str]] = {
    "exceso_velocidad": (
        "Velocidad alta",
        "En este viaje se registró una velocidad por encima de lo habitual en tu unidad. "
        "Bajar el ritmo alarga la vida del motor y gasta menos diésel."),
    "ralenti_elevado": (
        "Mucho tiempo en ralentí",
        "El motor estuvo encendido y parado más tiempo del normal. Apagarlo en esperas "
        "largas ahorra combustible."),
    "paradas_panico_recurrentes": (
        "Frenadas bruscas",
        "Se registraron más frenadas de pánico de las habituales en tu unidad. Suele venir "
        "de seguir muy de cerca al de adelante."),
    "consumo_atipico": (
        "Consumo fuera de lo normal",
        "El consumo de este viaje se salió de lo que suele gastar tu unidad. Puede ser la "
        "ruta, la carga o algo mecánico: coméntalo en el taller."),
    "desviacion_alta": (
        "Diferencia entre lo cargado y lo quemado",
        "Los litros cargados no cuadran con lo que el motor reporta haber consumido. "
        "Revisa que las fotos del comprobante sean del despacho correcto."),
    "odometro_retrocede": (
        "Odómetro menor que el anterior",
        "El kilometraje que se capturó es más bajo que el del viaje pasado. Revisa la foto "
        "del tablero: casi siempre es un dígito mal leído."),
    "km_imposible": (
        "Kilómetros fuera de rango",
        "Los kilómetros de este viaje no cuadran. Suele ser un error al capturar el "
        "odómetro."),
    "lts_imposible": (
        "Litros fuera de rango",
        "Los litros registrados no cuadran. Revisa la foto del comprobante."),
    "km_vs_odometro": (
        "Los kilómetros no cuadran con el odómetro",
        "Lo recorrido según el odómetro no coincide con los kilómetros del viaje. Revisa la "
        "lectura del tablero."),
    "reporte_duplicado": (
        "Viaje repetido",
        "Este viaje parece estar capturado dos veces. Si fue un error, avísale a tu "
        "coordinador para que lo corrija."),
    "rendimiento_fuera_banda_fisica": (
        "Rendimiento imposible",
        "El rendimiento que sale de estos datos no es posible en una unidad como la tuya, "
        "así que algún número está mal capturado."),
}


def visible_para_operador(tipo: str) -> bool:
    """¿Este tipo de anomalía se le enseña al chofer?"""
    if tipo not in TEXTO:
        return False
    cat = CATEGORIA_ANOMALIA.get(tipo)
    if cat in _CATEGORIAS_VISIBLES:
        return True
    return cat == "consistencia" and tipo in _CONSISTENCIA_VISIBLE


def para_operador(tipo: str, descripcion: str | None = None) -> dict:
    """El aviso tal como lo lee el chofer.

    `descripcion` —el texto que escribió el motor de validación— NO se le enseña: está
    redactado para quien audita y lleva cifras y jerga que aquí sólo confunden. Se conserva
    en la base y la ve el coordinador.
    """
    titulo, cuerpo = TEXTO.get(tipo, ("Revisión", "Hay algo que tu coordinador está revisando."))
    return {"tipo": tipo, "titulo": titulo, "texto": cuerpo,
            "categoria": CATEGORIA_ANOMALIA.get(tipo)}
