"""E2 · Lectura VERBATIM de los archivos de proveedor. No escribe nada, no interpreta.

EL PROBLEMA QUE RESUELVE
El lector de `scripts/diagnostico.py` fue escrito para CONTAR: se queda con nueve
columnas, tira la hora de Xyga con un `split(" ")[0]` y descarta filas con `continue`.
Para contar litros basta; para GUARDAR no, porque lo que no se lee no se puede volver a
mirar y lo que se descarta en silencio no aparece en ningún total. Este módulo lee las
26/25 columnas de cada archivo, conserva el texto exacto de cada celda y ADEMÁS entrega
los campos ya tipados que pide el esquema, sin que lo segundo pueda pisar lo primero.

LAS TRES REGLAS QUE LO GOBIERNAN
  1. NADA SE DESCARTA. Una fila se omite SOLO si todas sus celdas están vacías, y aun
     así se cuenta y se devuelve su número de fila con el motivo. `n_filas_leidas` es
     siempre `len(filas) + len(omitidas)`: que no se pierda una fila es VERIFICABLE, no
     una promesa.
  2. LA POSICIÓN ES EL CONTRATO. Nunca se busca una columna por nombre. El encabezado se
     compara entero contra el esperado y, si difiere, la lectura SE DETIENE nombrando la
     diferencia. El encabezado de Oxxo trae seis entradas None y el de Xyga trae erratas
     ('Departmento', 'Descripcion'): buscar por nombre significaría leer basura corrida
     el día que el proveedor arregle una errata o rellene una columna fantasma.
  3. VERBATIM Y TIPADO VIVEN LADO A LADO. `fila_cruda` guarda las celdas convertidas a
     texto, en orden; los campos tipados son una lectura de esas mismas celdas. Si la
     conversión se equivoca, el original sigue ahí para desmentirla.

LO QUE ESTE MÓDULO NO HACE, A PROPÓSITO
No abre la base, no resuelve el activo contra el catálogo, no deriva `combustible`,
`fuera_de_flota`, `destino`, `momento_ref` ni `zona_aplicada`: todo eso necesita datos
declarados en la base (la zona horaria del proveedor, el vínculo confirmado de la
tarjeta) y vive en `scripts/import_proveedor.py`. Aquí solo entra lo que se puede
afirmar teniendo únicamente el archivo delante.
"""

import hashlib
import io as _io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import openpyxl

# Los normalizadores se IMPORTAN, jamás se copian: `eco_norm` tiene que salir de la misma
# función que usa el resolvedor. Una segunda implementación con otro regex guardaría
# económicos que `resolver_activo` nunca podría encontrar, y el fallo sería invisible.
from .catalogo import norm_eco, norm_placa


class LayoutInesperado(Exception):
    """El archivo no trae el layout contratado. Detiene la ingesta ANTES de leer datos."""


# ─────────────────────────────────────────────────────────────────────────────
# EL CONTRATO DE LECTURA DE CADA PROVEEDOR
#
# Las cadenas del encabezado están copiadas LITERALMENTE del archivo, erratas incluidas:
# Xyga escribe 'Departmento' con una 'a' de más, 'Descripcion' sin acento, 'No.Ticket'
# sin espacio tras el punto pero 'No. Economico' con él. Corregirlas aquí para que se
# vean bonitas rompería la comprobación de layout, que es justamente la que avisaría el
# día que el proveedor las corrija en el archivo.
#
# Estas constantes son también lo que `scripts/migrate_e2.py` siembra en la tabla
# `proveedores`: el contrato se escribe UNA vez y la base guarda la copia auditable.
# ─────────────────────────────────────────────────────────────────────────────

ENCABEZADO_OXXO = [
    "Cliente", "Grupo", "No. Transacción", "Fecha Histórica", "No. Tarjeta",
    "Placas", "No. Económico", "Descripción del Vehículo", "No. Estación", "Estación",
    "Bomba", "Producto", "Consumo en Pesos", "Consumo en Litros", "Contingencia",
    "Fecha de Facturación(CDMX)", "Centro de Costos", "VIN", "No. Empleado",
    "Nombre Conductor",
    # Seis columnas fantasma: existen en la hoja, no tienen nombre y hoy llegan vacías en
    # las 326 filas. Se declaran para que el ancho sea 26 y para que el día que Oxxo las
    # rellene la comprobación lo note en vez de leerlas como si fueran otra cosa.
    None, None, None, None, None, None,
]

ENCABEZADO_XYGA = [
    "Fecha", "No.Ticket", "Folio QR", "Desc", "Comentario", "Referencia",
    "Departmento", "Tarjeta", "Descripcion", "Placas", "No. Economico", "No. Operador",
    "No.Estacion", "Estacion", "Bomba", "Lts", "Producto", "Kms", "Kms/Lt", "Precio",
    "Factura", "Subtotal", "IVA", "IEPS", "Total",
]


@dataclass(frozen=True)
class Contrato:
    """Cómo se lee el archivo de UN proveedor. Es dato, no código: se guarda en la base."""

    clave: str
    nombre: str
    hoja: str
    fila_encabezado: int
    fila_datos: int
    n_columnas: int
    encabezado: list
    # NULL en Oxxo, que entrega `datetime` real; en Xyga la fecha llega como TEXTO y hay
    # que traducir el meridiano en español antes de que strptime la entienda.
    formato_fecha: str | None = None
    regimen_factura: str = "sin_dato"
    cliente_texto: str | None = None
    grupo_texto: str | None = None
    # Fila donde el proveedor imprime CUÁNDO generó el reporte (la A2 de Xyga). Es la
    # única fecha de origen real que existe: estos archivos no traen docProps/core.xml y
    # openpyxl inventa `created` con el instante en que uno los abre, de modo que fiarse
    # de `wb.properties` produciría una bitácora falsa.
    fila_impresion: int | None = None


CONTRATOS = {
    "OXXO": Contrato(
        clave="OXXO", nombre="Oxxo Gas", hoja="Reporte",
        fila_encabezado=6, fila_datos=7, n_columnas=26, encabezado=ENCABEZADO_OXXO,
        formato_fecha=None,
        # Oxxo no emite columna de folio fiscal: el reporte ES el documento de liquidación.
        regimen_factura="sin_dato",
        cliente_texto="FRUIT2DAY", grupo_texto="UTILITARIOS",
    ),
    "XYGA": Contrato(
        clave="XYGA", nombre="Xyga", hoja="worksheet",
        fila_encabezado=5, fila_datos=6, n_columnas=25, encabezado=ENCABEZADO_XYGA,
        formato_fecha="%d/%m/%Y %I:%M:%S %p",
        # Un solo folio (A-1971351) para las 313 cargas del mes.
        regimen_factura="consolidada_mensual",
        fila_impresion=2,
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# APERTURA DEL ARCHIVO
# ─────────────────────────────────────────────────────────────────────────────

def leer_archivo(path) -> tuple[bytes, str]:
    """Devuelve (contenido, sha256). El importador necesita los bytes para `archivo_b64`
    y la huella para la idempotencia; se leen UNA vez y de ahí sale todo, para que el
    sha guardado sea demostrablemente el del archivo que se acaba de parsear y no el de
    una segunda lectura del disco entre medias."""
    with open(path, "rb") as fh:
        datos = fh.read()
    return datos, hashlib.sha256(datos).hexdigest()


def abrir_datos(datos: bytes):
    """Abre un xlsx que ya está en memoria."""
    return openpyxl.load_workbook(_io.BytesIO(datos), data_only=True, read_only=True)


def abrir(path):
    """Abre el xlsx. Se lee a memoria a propósito: el archivo de Xyga llega con extensión
    .xls aunque por dentro es xlsx, y openpyxl lo rechaza por el NOMBRE, no por el
    contenido. Pasándole los bytes, la extensión mentirosa deja de importar."""
    datos, _ = leer_archivo(path)
    return abrir_datos(datos)


# ─────────────────────────────────────────────────────────────────────────────
# GUARDIA DE LAYOUT
# ─────────────────────────────────────────────────────────────────────────────

def leer_encabezado(ws, fila: int, n_columnas: int) -> list:
    """El encabezado tal cual está en la hoja, sin normalizar ni recortar."""
    for r in ws.iter_rows(min_row=fila, max_row=fila, values_only=True):
        return _ajustar(r, n_columnas)
    return []


def leer_impresion(ws, fila: int | None) -> str | None:
    """La primera celda de la fila donde el proveedor estampa cuándo generó el reporte,
    VERBATIM ('Fecha Impresión:  06/08/2026 12:15'). No se parsea: es una leyenda del
    proveedor, no un campo, y basta con poder mostrarla al lado de la corrida."""
    if not fila:
        return None
    for r in ws.iter_rows(min_row=fila, max_row=fila, max_col=1, values_only=True):
        return _texto(r[0]) if r else None
    return None


def diferencias_encabezado(leido: list, esperado: list) -> list[str]:
    """Enumera, en castellano, en qué difiere el encabezado leído del contratado.

    Devuelve TODAS las diferencias, no la primera: si el proveedor inserta una columna,
    las 25 siguientes se corren y quien lea el error tiene que ver el desplazamiento
    completo para entender que no son 25 errores distintos.
    """
    fallas = []
    if len(leido) != len(esperado):
        fallas.append(f"el ancho es {len(leido)} y el contrato dice {len(esperado)}")
    for i in range(max(len(leido), len(esperado))):
        a = leido[i] if i < len(leido) else "<no hay columna>"
        b = esperado[i] if i < len(esperado) else "<no hay columna>"
        if a != b:
            fallas.append(f"columna {i}: se esperaba {b!r} y llegó {a!r}")
    return fallas


def contar_filas_xml(datos: bytes, desde_fila: int) -> int | None:
    """Cuenta las filas del Excel leyendo el XML crudo del zip. Devuelve None si no puede.

    POR QUE NO SE USA openpyxl PARA ESTO. En modo `read_only` openpyxl obedece el elemento
    <dimension> que el exportador del proveedor escribió, y ese elemento puede mentir: si
    declara una última fila menor que la real, `iter_rows` corta ahí y las filas de atrás no
    se leen, no se cuentan y no se registran. El cuadre "leídas == nuevas+repetidas+
    corregidas+omitidas" seguiría dando True, porque las leídas salen de la misma lectura
    recortada: el invariante se estaría comparando consigo mismo.

    Contar los elementos <row> del XML no pasa por la dimensión, así que es lo único que
    permite AFIRMAR cuántas filas trae el archivo en vez de creer lo que el archivo dice de
    sí mismo. El prefijo de espacio de nombres es opcional: Xyga escribe <x:row> y Oxxo <row>.
    """
    try:
        import re as _re
        import zipfile
        with zipfile.ZipFile(_io.BytesIO(datos)) as z:
            hojas = [n for n in z.namelist()
                     if _re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
            if len(hojas) != 1:
                # Con varias hojas habría que resolver los rels para saber cuál es la del
                # contrato. No se adivina: se devuelve None y el lector no afirma nada.
                return None
            xml = z.read(hojas[0])
        return sum(1 for m in _re.finditer(rb'<(?:\w+:)?row[ >][^>]*?\br="(\d+)"', xml)
                   if int(m.group(1)) >= desde_fila)
    except Exception:
        return None


def verificar_layout(ws, contrato: Contrato) -> list:
    """Compara el encabezado real contra el contrato y SE DETIENE si difiere.

    Es la única defensa contra el mes en que el proveedor inserte una columna: sin esta
    comprobación la lectura por posición seguiría funcionando sin quejarse y guardaría
    litros donde van pesos. Devuelve el encabezado leído para que el importador lo
    archive en `importaciones_proveedor.encabezado_leido`.
    """
    leido = leer_encabezado(ws, contrato.fila_encabezado, contrato.n_columnas)
    fallas = []
    # El ancho se comprueba contra la HOJA y no solo contra el encabezado leído: las seis
    # columnas fantasma de Oxxo se llaman None, así que un archivo al que le faltaran las
    # últimas columnas se rellenaría con None al ajustar y pasaría inadvertido.
    # OJO: `ws.max_column` NO mide la hoja, repite el <dimension> que escribió el exportador
    # —se comprobó inyectando una celda real en la columna 27 con la dimensión intacta: no se
    # detectó y el dato desapareció—. Sirve para cazar un archivo con menos columnas, que es
    # el caso frecuente, pero no es una medición. Por eso el ALTO se resuelve aparte, con
    # reset_dimensions() más el conteo del XML crudo.
    if ws.max_column is not None and ws.max_column != contrato.n_columnas:
        fallas.append(f"la hoja tiene {ws.max_column} columnas y el contrato dice "
                      f"{contrato.n_columnas}")
    fallas += diferencias_encabezado(leido, contrato.encabezado)
    if fallas:
        raise LayoutInesperado(
            f"el layout de {contrato.clave} cambió en la hoja '{contrato.hoja}', "
            f"fila {contrato.fila_encabezado}:\n  - " + "\n  - ".join(fallas)
        )
    # Con la dimensión ya usada para comprobar el ancho, se descarta: a partir de aquí
    # `iter_rows` recorre hasta la última fila que EXISTE, no hasta la que el archivo dice.
    # Sin esto, un <dimension> corto recortaba la lectura en silencio y la corrida se
    # guardaba como completa, con sus litros y su importe convertidos en la cifra del mes.
    try:
        ws.reset_dimensions()
    except AttributeError:
        pass          # hoja no read_only: iter_rows ya recorre todo
    return leido


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSIÓN DE CELDAS  ·  la convención de `fila_cruda`
# ─────────────────────────────────────────────────────────────────────────────

def _ajustar(fila, n: int) -> list:
    """Deja la fila con exactamente `n` posiciones.

    openpyxl en modo read_only puede recortar las celdas vacías del final, así que una
    fila corta se rellena con None (que es lo que esas celdas contienen de todos modos).
    Una fila MÁS LARGA que el contrato no se recorta en silencio: se conserva entera para
    que la comprobación de ancho la vea.
    """
    fila = list(fila)
    if len(fila) < n:
        fila += [None] * (n - len(fila))
    return fila


def _texto(v):
    """Una celda -> texto, con la convención fija de `fila_cruda`.

    datetime -> isoformat con microsegundos SIEMPRE (aunque sean cero), para que dos
    corridas del mismo archivo produzcan la misma cadena y el hash sea reproducible.
    Celda vacía y cadena vacía -> None: openpyxl devuelve '' y no None en 'Contingencia'
    (325 filas), 'Centro de Costos' (326) y en tres filas de conductor, y guardar '' como
    si fuera un valor obligaría a todo lector posterior a conocer esa peculiaridad.
    """
    if v is None:
        return None
    if isinstance(v, datetime):          # antes que date: datetime hereda de date
        return v.isoformat(timespec="microseconds")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    return s if s != "" else None


def a_texto(fila) -> list:
    """La fila entera convertida a `fila_cruda`."""
    return [_texto(v) for v in fila]


def _numero(txt, campo: str, avisos: list):
    """Texto -> float, avisando si NO se pudo. Un `except: return 0.0` silencioso es
    exactamente cómo desaparecen litros facturados sin que ningún total se queje."""
    if txt is None:
        return None
    limpio = txt.replace(",", "").replace("$", "").strip()
    if limpio == "":
        return None
    try:
        return float(limpio)
    except ValueError:
        avisos.append(f"{campo} no es un número: {txt!r}")
        return None


def _norm_tarjeta(txt) -> str | None:
    """Solo recorta y sube a mayúsculas. JAMÁS quita los ceros a la izquierda ni convierte
    a entero: las 52 tarjetas de Xyga miden cinco caracteres y empiezan en '0' ('00140'),
    y int('00140') destruiría la identidad de un proveedor entero, en silencio."""
    if txt is None:
        return None
    t = txt.strip().upper()
    return t or None


def _norm_producto(txt) -> str | None:
    """Solo mayúsculas y espacios colapsados. NO colapsa 'DieselAutomotriz' ni 'Mobil
    Sinergy Diesel Nuevo' en 'DIESEL': eso sería interpretar, y vive en E3. Lo único que
    arregla es que 'DIESEL' (308 filas) y 'Diesel' (1) sean el mismo texto; agrupar por
    el literal inventaría un combustible fantasma de 143.7 L y $3,878.41."""
    if txt is None:
        return None
    t = re.sub(r"\s+", " ", txt).strip().upper()
    return t or None


# ─────────────────────────────────────────────────────────────────────────────
# CANONICALIZACIÓN Y HUELLA DE FILA
# ─────────────────────────────────────────────────────────────────────────────

_RE_ISO_FRACCION = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+$")


def canon_celda(txt) -> str:
    """La forma comparable de una celda: sin fracción de segundo y sin ruido de espacios.

    Las dos podas tienen una causa medida y ninguna es cosmética:

    · LA FRACCIÓN DE SEGUNDO. 125 filas de Oxxo traen microsegundos que openpyxl deriva
      del serial flotante del XML (46234.965024919 -> .153000). Ese resto no es un dato
      del ticket, es aritmética de coma flotante, y una reexportación del mismo mes puede
      entregarlo distinto. Con él dentro del hash, reimportar el archivo de siempre
      parecería una corrección masiva del proveedor.
    · LOS ESPACIOS INTERNOS. Xyga rellena 'Departmento' con '0' y treinta y nueve
      espacios. Si el proveedor un día recorta ese relleno, o corrige la errata de un
      encabezado y de paso reescribe el exportador, las 313 filas cambiarían de huella
      de golpe y la bandeja de revisión nacería con 313 pendientes falsos.

    Lo que se pierde al podar está declarado y es recuperable: `fila_cruda` conserva el
    texto exacto, con sus microsegundos y su relleno. Esto es un DETECTOR DE CAMBIOS
    entre importaciones, no un sello anti-manipulación.
    """
    if txt is None:
        return ""
    t = _RE_ISO_FRACCION.sub(r"\1", txt)
    return re.sub(r"\s+", " ", t).strip()


def canon_fila(celdas) -> list[str]:
    """`fila_cruda` en su forma comparable."""
    return [canon_celda(c) for c in celdas]


def sha_fila(celdas) -> str:
    """Huella de una fila. El separador \\x1f (unit separator) no aparece en ningún dato
    de estos archivos, así que ninguna combinación de celdas puede fingir el corte entre
    dos columnas."""
    return hashlib.sha256("\x1f".join(canon_fila(celdas)).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# LA FECHA DE XYGA  ·  la trampa más cara del archivo
# ─────────────────────────────────────────────────────────────────────────────

# 'a. m.' / 'p. m.' con puntos y espacios, como los escribe el exportador en español.
# Se acepta cualquier variante (con o sin puntos, con o sin espacios, en mayúsculas)
# porque el sufijo es lo primero que cambia cuando alguien toca el exportador.
_RE_MERIDIANO = re.compile(r"\s*([AaPp])\s*\.?\s*[Mm]\s*\.?\s*$")

# Espacios que se ven como un espacio pero no lo son, y que ningún strptime reconoce:
# duro (U+00A0), fino (U+202F) y de cifra (U+2007). Un exportador que cambie de librería
# los mete sin avisar y la fecha entera dejaría de parsear.
_ESPACIOS_RAROS = str.maketrans({" ": " ", " ": " ", " ": " "})


def parsear_fecha_xyga(txt) -> tuple:
    """'01/07/2026 02:51:18 a. m.' -> datetime(2026, 7, 1, 2, 51, 18).

    POR QUÉ ESTA FUNCIÓN EXISTE: `scripts/diagnostico.py` resuelve esta misma fecha con
    `str(v).split(" ")[0]`, que le sirve porque solo necesita el día. Copiarlo aquí
    dejaría las 313 cargas de Xyga a las 00:00:00 y el error sería INVISIBLE: los litros
    y los pesos seguirían cuadrando al centavo mientras la ventana de ±6 h con la que E3
    empareja despachos se queda sin la hora que necesita.

    El meridiano se traduce a 'AM'/'PM' ANTES de strptime porque '%p' entiende el
    meridiano de la localización activa (la C, aquí), no el español con puntos. Y la
    conversión de 12 horas la hace strptime, no una resta a mano: '12:00:00 a. m.' es la
    medianoche (hora 0) y '12:00:00 p. m.' el mediodía (hora 12), que es justo donde una
    fórmula casera se equivoca.
    """
    if txt is None:
        return None
    s = str(txt).translate(_ESPACIOS_RAROS).strip()
    if not s:
        return None

    m = _RE_MERIDIANO.search(s)
    if m:
        base = _RE_MERIDIANO.sub("", s).strip()
        s = f"{base} {'AM' if m.group(1).upper() == 'A' else 'PM'}"
        formatos = ("%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p")
    else:
        # Sin meridiano solo puede ser reloj de 24 h; se admite por si el proveedor
        # cambia de formato, en vez de devolver None y perder la hora sin decirlo.
        formatos = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")

    for f in formatos:
        try:
            # El último formato de la lista, '%d/%m/%Y', NO trae hora: strptime devuelve la
            # medianoche. Ese cero es una hora INVENTADA, no leída, y quien la reciba tiene
            # derecho a saberlo: E3 empareja despachos con una ventana de ±6 h y con las 313
            # cargas del mes a las 00:00 emparejaría contra una hora que nadie registró.
            return datetime.strptime(s, f), ("%H" in f or "%I" in f)
        except ValueError:
            continue
    return None, False


# ─────────────────────────────────────────────────────────────────────────────
# EL RESULTADO DE UNA LECTURA
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Lectura:
    """Todo lo que se sacó de una hoja, incluido lo que NO se pudo usar.

    `omitidas` no es una lista de descartes: es la prueba de que no hubo descartes. El
    importador copia su tamaño en `n_omitidas` y su contenido en `motivos_omision`, y la
    verificación exige `n_filas_leidas == nuevas + repetidas + corregidas + omitidas`.
    """

    clave: str
    hoja: str
    encabezado: list = field(default_factory=list)
    filas: list = field(default_factory=list)
    omitidas: dict = field(default_factory=dict)   # fila_num -> motivo
    impresion_txt: str | None = None               # lo que el proveedor dice de sí mismo

    @property
    def n_filas_leidas(self) -> int:
        return len(self.filas) + len(self.omitidas)

    @property
    def litros_total(self) -> float:
        return sum(f["litros"] or 0.0 for f in self.filas)

    @property
    def importe_total(self) -> float:
        return sum(f["importe"] or 0.0 for f in self.filas)

    @property
    def periodo(self) -> tuple:
        """(desde, hasta) derivado de las FILAS, jamás del nombre del archivo: el de Xyga
        se llama '..._06_08_2026' y no contiene una sola carga de agosto."""
        ms = [f["momento_local"] for f in self.filas if f["momento_local"]]
        return (min(ms), max(ms)) if ms else (None, None)

    @property
    def avisos(self) -> dict:
        """fila_num -> avisos, solo de las filas que traen alguno."""
        return {f["fila_num"]: f["avisos"] for f in self.filas if f["avisos"]}


def _centinela(valor, sha_de_la_fila: str, campo: str, avisos: list) -> str:
    """Ninguna de las dos piezas de la llave natural puede quedar vacía, pero una fila sin
    ellas TAMPOCO se descarta: se le pone un centinela que la hace única y se deja escrito
    que es un centinela. Hoy no ocurre en ninguna de las 639 filas; el día que ocurra, la
    carga entra, se ve en la bandeja y nadie tiene que reabrir el Excel para entender qué
    pasó.

    EL CENTINELA SALE DEL CONTENIDO, NO DEL NÚMERO DE FILA. Antes era `@fila:<n>`, y eso
    metía la POSICIÓN dentro de la llave natural: la misma carga vista en dos exportaciones
    con rangos solapados —el caso que la prueba 6 llama 'idempotencia por solape parcial'—
    caía en la fila 100 en una y en la 101 en la otra, producía dos llaves, dos INSERT y dos
    filas vigentes con el MISMO sha256_fila. Se reprodujo: 290 L contados dos veces. Con la
    huella del contenido, la misma carga da el mismo centinela caiga donde caiga.
    """
    if valor:
        return valor
    avisos.append(f"{campo} vacío en el archivo; se usó un centinela derivado del contenido "
                  f"de la fila para no perderla")
    return f"@sha:{sha_de_la_fila[:16]}"


def _vacia(fila) -> bool:
    """Una fila está vacía cuando NINGUNA celda trae nada. Es el único motivo de omisión
    que existe: `diagnostico.py` además saltaba las filas sin ticket con un `continue`, y
    esa regla, aplicada a la ingesta, tiraría una carga real mal capturada sin dejar
    rastro de que existió."""
    return not any(v not in (None, "") for v in fila)


# ─────────────────────────────────────────────────────────────────────────────
# LECTOR DE OXXO  ·  Despachos.xlsx · hoja 'Reporte' · datos desde la fila 7
# ─────────────────────────────────────────────────────────────────────────────

# Posiciones del contrato. Se nombran para que se lea qué se está tomando, pero lo que
# manda es el número: la comprobación de layout ya garantizó que la columna 13 son litros.
(O_CLIENTE, O_GRUPO, O_TRANSACCION, O_FECHA, O_TARJETA, O_PLACAS, O_ECO, O_DESCRIPCION,
 O_ESTACION, O_ESTACION_NOMBRE, O_BOMBA, O_PRODUCTO, O_PESOS, O_LITROS, O_CONTINGENCIA,
 O_FACTURACION, O_CENTRO_COSTOS, O_VIN, O_EMPLEADO, O_CONDUCTOR) = range(20)


def leer_oxxo_verbatim(ws) -> Lectura:
    """Las 26 columnas de Despachos.xlsx, verbatim y tipadas."""
    contrato = CONTRATOS["OXXO"]
    lec = Lectura(clave=contrato.clave, hoja=contrato.hoja,
                  encabezado=verificar_layout(ws, contrato),
                  impresion_txt=leer_impresion(ws, contrato.fila_impresion))

    for fila_num, cruda in enumerate(
            ws.iter_rows(min_row=contrato.fila_datos, values_only=True),
            start=contrato.fila_datos):
        cruda = _ajustar(cruda, contrato.n_columnas)
        if _vacia(cruda):
            lec.omitidas[fila_num] = "fila completamente vacía en el archivo"
            continue

        avisos = []
        if len(cruda) != contrato.n_columnas:
            avisos.append(f"la fila trae {len(cruda)} celdas y el contrato dice "
                          f"{contrato.n_columnas}")
        t = a_texto(cruda)

        # Oxxo entrega `datetime` real (326/326, todas con tzinfo=None) y no texto, así
        # que no hay nada que parsear. Se TRUNCA al segundo porque la fracción viene del
        # serial flotante del XML, no del reloj de la bomba, y toda comparación con la
        # fecha de facturación —que sí llega al segundo— quedaría desviada por ella.
        crudo_fecha = cruda[O_FECHA]
        momento = (crudo_fecha.replace(microsecond=0)
                   if isinstance(crudo_fecha, datetime) else None)
        if momento is None:
            avisos.append(f"'Fecha Histórica' ilegible: {t[O_FECHA]!r}")

        crudo_fact = cruda[O_FACTURACION]
        facturacion = (crudo_fact.replace(microsecond=0)
                       if isinstance(crudo_fact, datetime) else None)
        # Se calcula pero NO se juzga: hay 27 filas donde la facturación es ANTERIOR al
        # despacho y una a +2446 min (la venta en contingencia). Un `assert facturacion >=
        # momento` abortaría la importación de un mes entero por datos que son ciertos.
        desfase = (round((facturacion - momento).total_seconds() / 60.0, 2)
                   if momento and facturacion else None)

        # La huella del contenido se calcula una sola vez: identifica la fila y además
        # alimenta el centinela de la llave natural cuando el proveedor deja un hueco.
        _sha_t = sha_fila(t)
        lec.filas.append({
            "fila_num": fila_num,
            "fila_cruda": t,
            "sha256_fila": _sha_t,
            # llave natural
            "estacion_txt": _centinela(t[O_ESTACION], _sha_t, "No. Estación", avisos),
            "folio_txt": _centinela(t[O_TRANSACCION], _sha_t, "No. Transacción", avisos),
            # estación y bomba
            "estacion_nombre_txt": t[O_ESTACION_NOMBRE],
            "bomba_txt": t[O_BOMBA],
            # tiempo
            "fecha_txt": t[O_FECHA],
            "momento_local": momento,
            "fecha_operacion": momento.date() if momento else None,
            "momento_facturacion": facturacion,
            "desfase_facturacion_min": desfase,
            # identidad del activo
            "tarjeta_txt": t[O_TARJETA],
            "tarjeta_norm": _norm_tarjeta(t[O_TARJETA]),
            "eco_txt": t[O_ECO],
            "eco_norm": norm_eco(t[O_ECO]) or None,
            "placa_txt": t[O_PLACAS],
            "placa_norm": norm_placa(t[O_PLACAS]) or None,
            "vin_txt": t[O_VIN],
            "descripcion_txt": t[O_DESCRIPCION],
            # persona
            "empleado_txt": t[O_EMPLEADO],
            "conductor_txt": t[O_CONDUCTOR],
            # dinero y litros
            "litros_txt": t[O_LITROS],
            "litros": _numero(t[O_LITROS], "Consumo en Litros", avisos),
            "importe_txt": t[O_PESOS],
            "importe": _numero(t[O_PESOS], "Consumo en Pesos", avisos),
            # Oxxo no desglosa impuestos ni precio unitario. Quedan en None y ese None
            # ES el dato; rellenar precio con importe/litros fabricaría una cifra falsa
            # con apariencia de dato del proveedor.
            "precio_txt": None, "precio": None,
            "subtotal_txt": None, "subtotal": None,
            "iva_txt": None, "iva": None,
            "ieps_txt": None, "ieps": None,
            # producto
            "producto_txt": t[O_PRODUCTO],
            "producto_norm": _norm_producto(t[O_PRODUCTO]),
            # factura y extras ascendidos
            "factura_txt": None,
            "folio_qr_txt": None,
            "contingencia_txt": t[O_CONTINGENCIA],
            "avisos": avisos,
        })
    return lec


# ─────────────────────────────────────────────────────────────────────────────
# LECTOR DE XYGA  ·  hoja 'worksheet' · datos desde la fila 6
# ─────────────────────────────────────────────────────────────────────────────

(X_FECHA, X_TICKET, X_FOLIO_QR, X_DESC, X_COMENTARIO, X_REFERENCIA, X_DEPARTAMENTO,
 X_TARJETA, X_DESCRIPCION, X_PLACAS, X_ECO, X_OPERADOR, X_ESTACION, X_ESTACION_NOMBRE,
 X_BOMBA, X_LITROS, X_PRODUCTO, X_KMS, X_KMS_LT, X_PRECIO, X_FACTURA, X_SUBTOTAL,
 X_IVA, X_IEPS, X_TOTAL) = range(25)


def leer_xyga_verbatim(ws) -> Lectura:
    """Las 25 columnas del reporte de consumos, verbatim y tipadas.

    Todo llega como TEXTO en este archivo, incluidos los números y la fecha. Kms y Kms/Lt
    se conservan dentro de `fila_cruda` y NO se ascienden a campo tipado: son basura
    demostrada ('123' en 280 filas, '0' en 32, '15' en 1) y no tener campo es lo que
    impide que alguien los sume por accidente en un GROUP BY.
    """
    contrato = CONTRATOS["XYGA"]
    lec = Lectura(clave=contrato.clave, hoja=contrato.hoja,
                  encabezado=verificar_layout(ws, contrato),
                  impresion_txt=leer_impresion(ws, contrato.fila_impresion))

    for fila_num, cruda in enumerate(
            ws.iter_rows(min_row=contrato.fila_datos, values_only=True),
            start=contrato.fila_datos):
        cruda = _ajustar(cruda, contrato.n_columnas)
        if _vacia(cruda):
            lec.omitidas[fila_num] = "fila completamente vacía en el archivo"
            continue

        avisos = []
        if len(cruda) != contrato.n_columnas:
            avisos.append(f"la fila trae {len(cruda)} celdas y el contrato dice "
                          f"{contrato.n_columnas}")
        t = a_texto(cruda)

        momento, con_hora = parsear_fecha_xyga(t[X_FECHA])
        if momento is None:
            avisos.append(f"'Fecha' ilegible: {t[X_FECHA]!r}")
        elif not con_hora:
            # La fila se guarda igual —la fecha es real—, pero queda escrito que las 00:00
            # las puso strptime y no el proveedor.
            avisos.append(f"'Fecha' sin hora: {t[X_FECHA]!r}; se guarda a las 00:00, que "
                          f"NO es la hora del ticket")

        # La huella del contenido se calcula una sola vez: identifica la fila y además
        # alimenta el centinela de la llave natural cuando el proveedor deja un hueco.
        _sha_t = sha_fila(t)
        lec.filas.append({
            "fila_num": fila_num,
            "fila_cruda": t,
            "sha256_fila": _sha_t,
            # llave natural
            "estacion_txt": _centinela(t[X_ESTACION], _sha_t, "No.Estacion", avisos),
            "folio_txt": _centinela(t[X_TICKET], _sha_t, "No.Ticket", avisos),
            # estación y bomba
            "estacion_nombre_txt": t[X_ESTACION_NOMBRE],
            "bomba_txt": t[X_BOMBA],
            # tiempo. Xyga no emite fecha de facturación: no hay desfase que medir, y por
            # eso el huso de sus estaciones solo podrá deducirse de otra evidencia.
            "fecha_txt": t[X_FECHA],
            "momento_local": momento,
            "fecha_operacion": momento.date() if momento else None,
            "momento_facturacion": None,
            "desfase_facturacion_min": None,
            # identidad del activo
            "tarjeta_txt": t[X_TARJETA],
            "tarjeta_norm": _norm_tarjeta(t[X_TARJETA]),
            "eco_txt": t[X_ECO],
            "eco_norm": norm_eco(t[X_ECO]) or None,
            "placa_txt": t[X_PLACAS],
            "placa_norm": norm_placa(t[X_PLACAS]) or None,
            "vin_txt": None,
            "descripcion_txt": t[X_DESCRIPCION],
            # persona: 'No. Operador' llega vacío en las 313 filas y no es un nombre sino
            # un número, así que Xyga sencillamente no aporta persona.
            "empleado_txt": None,
            "conductor_txt": None,
            # dinero y litros
            "litros_txt": t[X_LITROS],
            "litros": _numero(t[X_LITROS], "Lts", avisos),
            # 'Total' y no 'Subtotal': es el importe CON impuestos, el único que cuadra
            # contra Lts x Precio y el que se compara con el consumo en pesos de Oxxo.
            "importe_txt": t[X_TOTAL],
            "importe": _numero(t[X_TOTAL], "Total", avisos),
            "precio_txt": t[X_PRECIO],
            "precio": _numero(t[X_PRECIO], "Precio", avisos),
            "subtotal_txt": t[X_SUBTOTAL],
            "subtotal": _numero(t[X_SUBTOTAL], "Subtotal", avisos),
            "iva_txt": t[X_IVA],
            "iva": _numero(t[X_IVA], "IVA", avisos),
            "ieps_txt": t[X_IEPS],
            "ieps": _numero(t[X_IEPS], "IEPS", avisos),
            # producto
            "producto_txt": t[X_PRODUCTO],
            "producto_norm": _norm_producto(t[X_PRODUCTO]),
            # factura y extras ascendidos
            "factura_txt": t[X_FACTURA],
            "folio_qr_txt": t[X_FOLIO_QR],
            "contingencia_txt": None,
            "avisos": avisos,
        })
    return lec


LECTORES = {"OXXO": leer_oxxo_verbatim, "XYGA": leer_xyga_verbatim}


def leer_verbatim(wb, clave: str, datos: bytes | None = None) -> Lectura:
    """Lee el libro completo de un proveedor por su clave. La hoja sale del contrato, no
    de `wb.active`: ambos archivos traen una sola hoja hoy, y el día que traigan dos, leer
    'la activa' es una lotería."""
    clave = clave.upper()
    if clave not in CONTRATOS:
        raise LayoutInesperado(f"proveedor desconocido: {clave!r}; "
                               f"hay contrato para {sorted(CONTRATOS)}")
    contrato = CONTRATOS[clave]
    if contrato.hoja not in wb.sheetnames:
        raise LayoutInesperado(
            f"el archivo de {clave} no trae la hoja '{contrato.hoja}'; "
            f"trae {wb.sheetnames}")
    lec = LECTORES[clave](wb[contrato.hoja])

    # La afirmación positiva: cuántas filas trae el archivo según su propio XML, que no pasa
    # por la dimensión. Si no coincide con lo leído, algo se quedó fuera y la corrida NO es
    # completa; detenerse es lo único honesto, porque el resto de contadores cuadran entre sí
    # aunque falten filas.
    if datos is not None:
        esperadas = contar_filas_xml(datos, contrato.fila_datos)
        leidas = len(lec.filas) + len(lec.omitidas)
        if esperadas is not None and esperadas != leidas:
            raise LayoutInesperado(
                f"el archivo de {clave} trae {esperadas} filas de datos según su XML pero se "
                f"leyeron {leidas}. No se importa nada: una corrida a la que le faltan filas "
                f"se guardaría como completa y sus litros pasarían por el total del mes.")
    return lec
