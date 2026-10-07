"""E3 · Verificador. Ejecuta las 20 pruebas obligatorias del plan y da un VEREDICTO.

EL PROBLEMA QUE RESUELVE
El libro mayor puede cuadrar al centavo y estar mal de todas las formas que importan: con dos
asientos para la misma línea, con la huella colapsada en las 43 filas sin activo, con el
espejo de `vigente` decorativo, o con la puerta del emparejador cableada al aire. Ninguno de
esos cuatro errores mueve un total. Este script comprueba las veinte cosas que el plan declaró
OBLIGATORIAS, cada una con su cifra esperada al lado de la obtenida, y termina con
`sys.exit(1)` si alguna no cuadra.

LA REGLA QUE LO GOBIERNA, HEREDADA DE `scripts/verificar_e2.py`: UN VERIFICADOR QUE APRUEBA LO
QUE NO PROBÓ ES PEOR QUE NO TENERLO. Cada prueba acaba en uno de tres estados y nunca en otro:

  PASA     se ejecutó entera y todo cuadró.
  FALLA    se ejecutó y algo no cuadró; se dice qué, con el esperado y el obtenido.
  OMITIDA  NO se pudo ejecutar, y se dice POR QUÉ. Jamás se cuenta como buena.

CÓMO SE PRUEBA LO QUE NECESITA UN LIBRO ESCRITO  ·  LA TRANSACCIÓN DE ENSAYO
Diecisiete de las veinte miran `asientos_consumo` DESPUÉS de proyectar, y esperar a que
alguien corra la proyección de verdad dejaría el verificador inservible justo cuando más falta
hace: ANTES de correrla. Así que este script abre una transacción, ejecuta dentro la
proyección real —el mismo `scripts.proyectar_consumo.proyectar`, sin trucos ni copias
simplificadas— mira el resultado y DESHACE la transacción entera. La base queda exactamente
como estaba: cero asientos escritos, cero filas de `cargas_proveedor` tocadas.

Y funciona igual ANTES y DESPUÉS de la proyección de verdad: si el libro ya está escrito, la
proyección de ensayo reporta 0 insertados y 0 actualizados y las pruebas miran los datos
reales. Cada prueba dice de dónde salió el número que miró.

LAS PRUEBAS QUE ROMPEN COSAS A PROPÓSITO (7, 8, 9, 10, 11, 19, 20) escriben de verdad dentro
de la transacción de ensayo —insertan asientos ilegales, sellan eventos, jubilan cargas,
anulan corridas y hasta ejecutan el DROP de reversión, que en Postgres es transaccional— y
todo eso se deshace. Que el dato sea efímero no debilita la prueba: se está comprobando el
comportamiento de las RESTRICCIONES, no la durabilidad de Postgres.

Uso (desde backend/, con el venv):
    python -m scripts.verificar_e3
    python -m scripts.verificar_e3 --sin-e2      (no relanza el verificador de E2: prueba 18)
    python -m scripts.verificar_e3 --solo 7 8 9
"""

import argparse
import contextlib
import io as _io
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# La consola de Windows es cp1252 y este informe está lleno de acentos ('atribución',
# 'huérfanos', 'económico') y de cajas '─' que no existen en cp1252.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.exc import DBAPIError, OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import AsientoConsumo, CargaProveedor  # noqa: E402
from scripts.import_proveedor import Aborta  # noqa: E402
# SE IMPORTAN, NO SE COPIAN. `SQL_HUELLA` en particular: una segunda copia de esa expresión
# acabaría discrepando de la que escribió las filas, y entonces la prueba 13 mediría la
# diferencia entre dos versiones del verificador en vez de la deriva del libro.
from scripts.proyectar_consumo import SQL_HUELLA, proyectar  # noqa: E402
from scripts.revertir_importacion import revertir, tablas_que_apuntan  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]

TABLA = "asientos_consumo"
SECUENCIA = "consumo_evento_seq"

# El DROP que revierte la etapa entera. Es el mismo que imprime `scripts/migrate_e3.py`; la
# prueba 20 lo ejecuta DENTRO de una transacción que se deshace, porque en Postgres el DDL
# también es transaccional y suponer que la reversión está limpia no es comprobarlo.
DROP_E3 = (f"DROP TABLE {TABLA}", f"DROP SEQUENCE {SECUENCIA}")


# ─────────────────────────────────────────────────────────────────────────────
# LAS CIFRAS QUE EL PLAN FIJÓ
#
# No son "lo que salió al probar": son lo que el arquitecto midió sobre las 639 cargas reales
# ANTES de escribir una línea de este código. Si mañana el código deja de reproducirlas, el que
# cambió es el código. NINGUNA lleva tolerancia: `litros` e `importe` son Numeric y la
# diferencia exigida contra el proveedor es 0 EXACTO, no 'menor a un centavo' — que es
# precisamente la razón por la que la etapa rompió la racha de Float del proyecto.
# ─────────────────────────────────────────────────────────────────────────────

D = Decimal

ESPERADO = {
    # 1 · cuadre contra el proveedor
    "filas": 639, "litros": D("118541.021"), "importe": D("3218987.99"),
    # 3 · partición (atribucion, destino) -> (asientos, litros, importe)
    "particion": {
        ("activo", "motor"):         (281, D("83030.178"), D("2257472.53")),
        ("activo", "indeterminado"): (191, D("15629.756"), D("423387.69")),
        ("activo", "termo"):         (124, D("15198.196"), D("412406.27")),
        ("cuarentena", None):        (39,  D("4455.761"),  D("120379.59")),
        ("fuera_flota", None):       (4,   D("227.130"),   D("5341.91")),
    },
    # 4 · lo que verá E4
    "e4_litros": D("113858.130"), "e4_unidades": 34, "e4_remolques": 23,
    # 5 · separación de combustible
    "diesel": (635, D("118313.891")), "gasolina": (4, D("227.130")),
    # 10 · la cuarentena resuelta: el económico 531702 son 10 cargas y 1,177.570 L
    "eco_prueba": "531702", "eco_cargas": 10, "eco_litros": D("1177.570"),
    "cuarentena_antes": D("4455.761"), "cuarentena_despues": D("3278.191"),
    # 16 · densidad de la ventana, medida sobre las 596 cargas con activo
    "hueco_min_min": D("2.00"), "pares_5": 46, "pares_10": 118, "pares_15": 134,
    "pares_6h": 162, "empates": 0, "indeterminado_con_hermano": 157,
    "indeterminados": 191,
    # 18 · lo que no se puede haber roto
    "enums": 6, "revision_max": 1,
}

# 9 · los nueve CHECK que tienen que morder, con el nombre exacto que debe rechazar cada caso.
# Que el rechazo llegue del CHECK ESPERADO y no de otro cualquiera es la mitad de la prueba:
# si (f) lo rechazara `ck_asientos_atribucion` en vez de `ck_asientos_remolque_termo`, el
# segundo podría estar mal escrito y nadie se enteraría.
CHECKS = (
    ("a", "carga_id Y orden_id a la vez",              "ck_asientos_origen"),
    ("b", "ni carga_id ni orden_id",                   "ck_asientos_origen"),
    ("c", "unidad_id y remolque_id a la vez",          "ck_asientos_atribucion"),
    ("d", "atribucion='activo' con destino NULL",      "ck_asientos_atribucion"),
    ("e", "atribucion='cuarentena' con unidad_id",     "ck_asientos_atribucion"),
    ("f", "remolque_id con destino='motor'",           "ck_asientos_remolque_termo"),
    ("g", "litros = 0",                                "ck_asientos_litros"),
    ("h", "contable=false sin evento_id",              "ck_asientos_no_contable_con_evento"),
    ("i", "contable=false sin motivo",                 "ck_asientos_motivo"),
)


# ─────────────────────────────────────────────────────────────────────────────
# EL MARCO  ·  tres estados y ni uno más   (calcado de scripts/verificar_e2.py)
# ─────────────────────────────────────────────────────────────────────────────

class Omitir(Exception):
    """La prueba no se puede ejecutar. El mensaje ES el motivo, y se imprime tal cual."""


def _fmt(v):
    """Un valor como lo va a leer una persona, no como lo escribe Python."""
    if isinstance(v, Decimal):
        # `,f` respeta la escala del Decimal tal cual: 118541.021 sale con sus tres decimales
        # y 3218987.99 con sus dos. Formatearlo como float devolvería la deriva binaria que
        # Numeric acaba de quitar, que es justo lo que esta etapa fue a evitar.
        return f"{v:,f}"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, (list, tuple, set)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


class Prueba:
    """Una de las veinte. Acumula sus líneas y su estado; imprime al terminar."""

    def __init__(self, numero: int, titulo: str):
        self.numero = numero
        self.titulo = titulo
        self.lineas: list[str] = []
        self.fallas: list[str] = []
        self.omitida: str | None = None

    def dice(self, txt: str = ""):
        self.lineas.append(f"     {txt}" if txt else "")

    def fuente(self, txt: str):
        """De dónde salió el dato que se miró. Va SIEMPRE: un número sin origen no se puede
        discutir."""
        self.lineas.append(f"     fuente: {txt}")

    def cuadra(self, etiqueta: str, esperado, obtenido) -> bool:
        """Compara e imprime SIEMPRE las dos cifras, cuadren o no. Que el esperado sea visible
        incluso cuando la prueba pasa es lo que permite auditar el verificador.

        SIN TOLERANCIA, a diferencia de verificar_e2: aquí las magnitudes son Numeric y la
        igualdad es exacta por construcción. Admitir 'un centavo de diferencia' anularía la
        razón por la que esta etapa dejó de usar Float."""
        ok = esperado == obtenido
        self.lineas.append(f"     {etiqueta:<48} esperado {_fmt(esperado):>16} · "
                           f"obtenido {_fmt(obtenido):>16}  {'OK' if ok else '<-- NO CUADRA'}")
        if not ok:
            self.fallas.append(f"{etiqueta}: se esperaba {_fmt(esperado)} y se obtuvo "
                               f"{_fmt(obtenido)}")
        return ok

    def exige(self, etiqueta: str, condicion: bool, detalle: str = "") -> bool:
        """Para lo que no es una cifra: un plan de ejecución, una excepción, un rechazo."""
        self.lineas.append(f"     {etiqueta:<48} {'OK' if condicion else '<-- NO CUMPLE'}"
                           + (f"   {detalle}" if detalle else ""))
        if not condicion:
            self.fallas.append(f"{etiqueta}" + (f" ({detalle})" if detalle else ""))
        return condicion

    def falla(self, motivo: str):
        self.fallas.append(motivo)
        self.lineas.append(f"     {motivo}")

    @property
    def estado(self) -> str:
        # UNA FALLA MANDA SOBRE UNA OMISIÓN: llamar 'omitida' a una prueba que ya falló
        # escondería el defecto detrás de un "no se pudo probar".
        if self.fallas:
            return "FALLA"
        return "OMITIDA" if self.omitida else "PASA"

    def imprimir(self):
        print("\n" + "=" * 78)
        print(f"{self.numero:>2} · {self.titulo}")
        print("-" * 78)
        for l in self.lineas:
            print(l)
        if self.omitida:
            print(f"     {'OMITIDA' if not self.fallas else 'ADEMÁS QUEDÓ SIN TERMINAR'}: "
                  f"{self.omitida}")
        print(f"     -> {self.estado}")


def correr(num, titulo, fn, *args) -> Prueba:
    """Ejecuta una prueba sin que pueda tumbar la corrida.

    Una excepción inesperada NO es una omisión: es un fallo. Tragarla como 'no se pudo probar'
    convertiría cualquier error de este archivo en un aprobado.
    """
    p = Prueba(num, titulo)
    try:
        fn(p, *args)
    except Omitir as e:
        p.omitida = str(e)
    except Exception as e:            # noqa: BLE001 -- deliberado: nada aborta el informe
        p.falla(f"la prueba reventó: {type(e).__name__}: {e}")
    p.imprimir()
    return p


# ─────────────────────────────────────────────────────────────────────────────
# EL BANCO DE ENSAYO  ·  una transacción viva a la vez, siempre deshecha
# ─────────────────────────────────────────────────────────────────────────────

class Banco:
    """Gestiona la transacción de ensayo. NUNCA hay dos abiertas a la vez.

    Dos transacciones simultáneas escribirían las mismas llaves de `ux_asientos_carga` y la
    segunda se quedaría esperando a que la primera termine —que no termina, porque la primera
    espera a que acabe la prueba—. El bloqueo sería un cuelgue sin mensaje, así que abrir una
    cierra la anterior por construcción.
    """

    def __init__(self, activo: bool = True):
        self.activo = activo
        self._db = self._conn = self._tx = None
        # QUÉ transacción está abierta, no solo si hay una. Sin esto, `principal()` reutilizaba
        # la transacción de trabajo que dejó la prueba 11 —con su revisión y sus 640 asientos—
        # y la 12 contaba 640 huellas donde el plan dice 639. Es el mismo control que
        # `verificar_e2.Banco` hace con `_contenido`.
        self._modo = None
        self.informe = None

    def _abrir(self, proyectar_ahora: bool, modo: str):
        self.cerrar()
        insp = inspect(engine)
        if not insp.has_table(TABLA):
            raise Omitir(f"no existe la tabla `{TABLA}`; corre primero "
                         f"python -m scripts.migrate_e3")
        self._conn = engine.connect()
        self._tx = self._conn.begin()
        # `create_savepoint` es lo que permite que `proyectar` haga su propio commit sin cerrar
        # la transacción externa: el commit libera un SAVEPOINT y el rollback de fuera sigue
        # pudiendo deshacerlo todo.
        self._db = Session(bind=self._conn, join_transaction_mode="create_savepoint",
                           autoflush=False, expire_on_commit=False)
        try:
            if proyectar_ahora:
                self.informe = self.proyectar()
        except BaseException:
            self.cerrar()
            raise
        self._modo = modo
        return self._db

    def proyectar(self, **kw):
        """La proyección DE VERDAD dentro del ensayo. Su salida se silencia porque aquí se
        miran sus efectos, no su informe, y 60 líneas por corrida taparían las pruebas."""
        with contextlib.redirect_stdout(_io.StringIO()):
            return proyectar(self._db, **kw)

    def principal(self):
        """La transacción con el libro ya proyectado y SIN tocar. Se reabre si una prueba la
        cerró o si la anterior fue una de trabajo, que la habría dejado sucia."""
        if self._db is not None and self._modo == "principal":
            return self._db
        return self._abrir(True, "principal")

    def limpio(self, proyectado=True):
        """Una transacción recién abierta, para las pruebas que escriben. Cada una empieza de
        cero para que su cifra esperada sea la del plan y no 'la del plan más lo que dejó la
        prueba anterior'."""
        return self._abrir(proyectado, "limpio")

    def cerrar(self):
        if self._db is not None:
            self._db.close()
        if self._tx is not None:
            self._tx.rollback()        # AQUÍ se deshace todo lo que se ensayó
        if self._conn is not None:
            self._conn.close()
        self._db = self._conn = self._tx = None
        self._modo = None


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES DE LAS PRUEBAS QUE ROMPEN COSAS
# ─────────────────────────────────────────────────────────────────────────────

def _restriccion(e) -> str:
    """El nombre de la restricción que rechazó una escritura.

    Se lee del diagnóstico de psycopg y no del texto del mensaje: el texto cambia con la
    versión y con el idioma del servidor, y una prueba que dependa de él dejaría de probar
    nada el día que alguien levante Postgres en otra locale.
    """
    diag = getattr(getattr(e, "orig", None), "diag", None)
    nombre = getattr(diag, "constraint_name", None)
    if nombre:
        return nombre
    return " ".join(str(e).split())[:120]


@contextlib.contextmanager
def _deshecho(db):
    """Un SAVEPOINT que SIEMPRE se deshace, salga bien o mal.

    ⚠ NO se puede usar `with _deshecho(db):` a secas para esto, y es un error que no se ve:
    ese context manager hace RELEASE del savepoint cuando el bloque termina BIEN, o sea que
    conserva lo escrito. Las pruebas que ensucian una huella, sellan un evento o descuentan un
    asiento a propósito lo hacen para comprobar que la consulta los DETECTA, y si el destrozo
    sobrevive al bloque, las pruebas siguientes de la misma transacción miran una base
    contaminada y fallan por algo que nadie rompió. Se detectó así: las pruebas 13, 14 y 15
    fallaban en cadena por lo que ensuciaba la 13.
    """
    sp = db.begin_nested()
    try:
        yield sp
    finally:
        if sp.is_active:
            sp.rollback()


def _rechaza(db, fn):
    """(fue_rechazado, nombre_de_la_restriccion_o_mensaje).

    Cada intento va en su propio SAVEPOINT que se deshace pase lo que pase: una sentencia que
    falla aborta la transacción entera en Postgres, así que sin esto el primer CHECK que
    mordiera dejaría inservibles a los ocho siguientes y la prueba mediría una sola cosa
    creyendo medir nueve. Y si la escritura ilegal ENTRA —o sea, si la prueba falla— también
    hay que deshacerla, o la fila ilegal envenena las pruebas de después.
    """
    sp = db.begin_nested()
    try:
        fn()
    except DBAPIError as e:
        if sp.is_active:
            sp.rollback()
        return True, _restriccion(e)
    if sp.is_active:
        sp.rollback()
    return False, "la escritura ENTRÓ: la base aceptó algo que tenía que rechazar"


def _fila_valida(carga_id: int) -> dict:
    """Un asiento legal mínimo. Cada prueba de CHECK rompe UNA cosa de esta fila, para que lo
    que la base rechace sea exactamente lo que se quería probar y no un descuido de al lado."""
    return dict(
        origen="proveedor", carga_id=carga_id, orden_id=None,
        origen_huella="9" * 64,
        unidad_id=None, remolque_id=None, atribucion="cuarentena", destino=None,
        combustible="diesel",
        litros=Decimal("100.000"), importe=Decimal("1000.00"),
        momento_local=datetime(2026, 7, 1, 12, 0, 0),
        momento_ref=datetime(2026, 7, 1, 18, 0, 0, tzinfo=timezone.utc),
        fecha_operacion=date(2026, 7, 1),
        vigente=True, evento_id=None, contable=True, motivo=None)


def _insertar(db, valores: dict):
    db.execute(pg_insert(AsientoConsumo.__table__).values(**valores))


def _libre(db) -> int:
    """Un `carga_id` real cuyo asiento se retira dentro del ensayo, para que las pruebas que
    insertan a mano no choquen contra `ux_asientos_carga` antes de llegar al CHECK que quieren
    probar. Funciona igual con el libro vacío y con el libro ya escrito."""
    cid = db.execute(text("SELECT id FROM cargas_proveedor WHERE vigente ORDER BY id "
                          "LIMIT 1")).scalar()
    if cid is None:
        raise Omitir("no hay ninguna carga vigente en `cargas_proveedor`")
    db.execute(text(f"DELETE FROM {TABLA} WHERE carga_id = :c"), {"c": cid})
    return cid


# ─────────────────────────────────────────────────────────────────────────────
# LAS VEINTE PRUEBAS
# ─────────────────────────────────────────────────────────────────────────────

def p01_cuadre(p, banco):
    """El libro reproduce el archivo del proveedor. La diferencia exigida es 0 EXACTO."""
    db = banco.principal()
    a_n, a_l, a_i = db.execute(text(
        f"SELECT count(*), COALESCE(sum(litros), 0), COALESCE(sum(importe), 0) "
        f"FROM {TABLA} WHERE vigente")).one()
    p.cuadra("libro · asientos vigentes", ESPERADO["filas"], a_n)
    p.cuadra("libro · litros", ESPERADO["litros"], a_l)
    p.cuadra("libro · importe", ESPERADO["importe"], a_i)
    c_n, c_l, c_i = db.execute(text(
        "SELECT count(*), COALESCE(sum(litros_txt::numeric), 0), "
        "COALESCE(sum(importe_txt::numeric), 0) FROM cargas_proveedor WHERE vigente")).one()
    p.cuadra("origen · cargas vigentes", ESPERADO["filas"], c_n)
    p.cuadra("origen · sum(litros_txt::numeric)", ESPERADO["litros"], c_l)
    p.cuadra("origen · sum(importe_txt::numeric)", ESPERADO["importe"], c_i)
    p.cuadra("DIFERENCIA en litros (exigida 0 EXACTO)", D("0"), a_l - c_l)
    p.cuadra("DIFERENCIA en importe (exigida 0 EXACTO)", D("0.00"), a_i - c_i)
    # La columna Float del origen es ELLA la derivación con pérdida: se enseña la cifra para
    # que quede escrito por qué el libro se puebla desde el texto verbatim.
    f_l, f_i = db.execute(text(
        "SELECT sum(litros), sum(importe) FROM cargas_proveedor WHERE vigente")).one()
    p.dice(f"y sobre las columnas Float del origen la misma suma da {f_l!r} y {f_i!r}: "
           f"por eso litros e importe son Numeric y se pueblan desde litros_txt/importe_txt")
    p.fuente("transacción de ensayo (la proyección corrió y se deshizo)")


def p02_biyeccion(p, banco):
    """Una línea, un asiento. Ni una carga sin asiento ni un asiento sin carga."""
    db = banco.principal()
    n, d = db.execute(text(
        f"SELECT count(*), count(DISTINCT carga_id) FROM {TABLA} "
        f"WHERE origen = 'proveedor'")).one()
    p.cuadra("asientos de origen 'proveedor'", ESPERADO["filas"], n)
    p.cuadra("carga_id distintos entre ellos", ESPERADO["filas"], d)
    p.cuadra("cargas vigentes SIN asiento", 0, db.execute(text(
        f"SELECT count(*) FROM cargas_proveedor c WHERE c.vigente AND NOT EXISTS "
        f"(SELECT 1 FROM {TABLA} a WHERE a.carga_id = c.id)")).scalar())
    p.cuadra("asientos SIN carga que los explique", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} a WHERE a.origen = 'proveedor' AND NOT EXISTS "
        f"(SELECT 1 FROM cargas_proveedor c WHERE c.id = a.carga_id)")).scalar())
    p.cuadra("asientos sin ninguna línea de origen (litros inventados)", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE carga_id IS NULL AND orden_id IS NULL")).scalar())
    p.fuente("transacción de ensayo")


def p03_particion(p, banco):
    """Las cinco cifras exactas de (atribución, destino). Suman 639 y 118,541.021 L."""
    db = banco.principal()
    obtenido = {(a, d): (n, li, im) for a, d, n, li, im in db.execute(text(
        f"SELECT atribucion, destino, count(*), sum(litros), COALESCE(sum(importe), 0) "
        f"FROM {TABLA} WHERE vigente GROUP BY 1, 2")).all()}
    tn = 0
    tl = D("0")
    for llave, esperado in ESPERADO["particion"].items():
        real = obtenido.get(llave, (0, D("0"), D("0")))
        etiqueta = f"{llave[0]} / {llave[1] or 'sin destino'}"
        p.cuadra(f"{etiqueta} · asientos", esperado[0], real[0])
        p.cuadra(f"{etiqueta} · litros", esperado[1], real[1])
        p.cuadra(f"{etiqueta} · importe", esperado[2], real[2])
        tn += real[0]
        tl += real[1]
    p.cuadra("las cinco suman los asientos del mes", ESPERADO["filas"], tn)
    p.cuadra("las cinco suman los litros del mes", ESPERADO["litros"], tl)
    p.cuadra("combinaciones (atribucion, destino) distintas", 5, len(obtenido))
    p.dice("'atribucion' y 'destino' son EJES INDEPENDIENTES: por eso las 4 de gasolina y las "
           "39 de cuarentena, que las dos van sin activo, no caen en el mismo cajón")
    p.fuente("transacción de ensayo")


def p04_lo_que_vera_e4(p, banco):
    """El filtro que E4 va a escribir, y lo que le devuelve."""
    db = banco.principal()
    n, li, u, r = db.execute(text(
        f"SELECT count(*), COALESCE(sum(litros), 0), count(DISTINCT unidad_id), "
        f"count(DISTINCT remolque_id) FROM {TABLA} "
        f"WHERE vigente AND contable AND atribucion = 'activo'")).one()
    p.cuadra("asientos atribuidos", 596, n)
    p.cuadra("litros atribuidos", ESPERADO["e4_litros"], li)
    p.cuadra("unidades distintas", ESPERADO["e4_unidades"], u)
    p.cuadra("remolques distintos", ESPERADO["e4_remolques"], r)
    p.cuadra("y ninguno tiene los dos activos a la vez", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE unidad_id IS NOT NULL "
        f"AND remolque_id IS NOT NULL")).scalar())
    p.cuadra("ni un remolque con destino distinto de 'termo'", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE remolque_id IS NOT NULL "
        f"AND destino <> 'termo'")).scalar())
    ind = db.execute(text(
        f"SELECT count(*), COALESCE(sum(litros), 0) FROM {TABLA} "
        f"WHERE vigente AND contable AND destino = 'indeterminado'")).one()
    p.dice(f"de esos, {ind[0]} asientos y {ind[1]} L son 'indeterminado': camiones donde el "
           f"termo comparte económico con el motor. E4 tiene que declarar qué hace con ellos "
           f"— sumarlos al motor infla el rendimiento e ignorarlos pierde el 13.2% del diésel, "
           f"y el número sale igual de plausible en los dos casos")
    p.fuente("transacción de ensayo")


def p05_combustible(p, banco):
    """El diésel y la gasolina no se pueden sumar por descuido."""
    db = banco.principal()
    obtenido = {c: (n, li) for c, n, li in db.execute(text(
        f"SELECT combustible, count(*), sum(litros) FROM {TABLA} WHERE vigente "
        f"GROUP BY 1")).all()}
    for clave in ("diesel", "gasolina"):
        real = obtenido.get(clave, (0, D("0")))
        p.cuadra(f"{clave} · asientos", ESPERADO[clave][0], real[0])
        p.cuadra(f"{clave} · litros", ESPERADO[clave][1], real[1])
    p.cuadra("vocabularios distintos de combustible", 2, len(obtenido))
    n_ok = db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE vigente AND combustible = 'gasolina' "
        f"AND atribucion = 'fuera_flota' AND destino IS NULL AND unidad_id IS NULL "
        f"AND remolque_id IS NULL")).scalar()
    p.cuadra("las 4 de gasolina: fuera_flota, sin destino y sin activo (4 de 4)", 4, n_ok)
    p.dice("con unidad_id NULL no pueden contaminar el km/L (E4 agrupa por activo y un NULL "
           "no une) y con combustible='gasolina' no se pueden sumar al diésel sin escribirlo")
    p.fuente("transacción de ensayo")


def p06_idempotencia(p, banco):
    """Idempotencia MEDIDA EN ESCRITURAS, no en conteos. Tres corridas seguidas."""
    db = banco.limpio(proyectado=False)
    informes = []
    sellos = []
    for _ in range(3):
        informes.append(banco.proyectar())
        sellos.append({i: t for i, t in db.execute(text(
            f"SELECT id, proyectado_en FROM {TABLA} ORDER BY id")).all()})
    for i, inf in enumerate(informes, start=1):
        p.dice(f"corrida {i}: {inf['insertados']} insertados · {inf['actualizados']} "
               f"actualizados · {inf['sin_cambios']} sin cambios")
    for i in (2, 3):
        p.cuadra(f"corrida {i} · insertados", 0, informes[i - 1]["insertados"])
        p.cuadra(f"corrida {i} · actualizados", 0, informes[i - 1]["actualizados"])
        p.cuadra(f"corrida {i} · sin cambios", ESPERADO["filas"],
                 informes[i - 1]["sin_cambios"])
    for i in (1, 2, 3):
        p.cuadra(f"corrida {i} · asientos en la tabla", ESPERADO["filas"],
                 informes[i - 1]["despues"]["filas"])
    # LO QUE DE VERDAD PRUEBA LA IDEMPOTENCIA: que ni una fila cambiara de añada. Un
    # `DO UPDATE` sin el `WHERE ... IS DISTINCT FROM` daría los mismos conteos y reescribiría
    # las 639 filas en cada corrida.
    p.cuadra("filas que cambiaron de proyectado_en entre la 1ª y la 3ª", 0,
             sum(1 for i, t in sellos[2].items() if sellos[0].get(i) != t))
    p.dice("un DO UPDATE sin `WHERE origen_huella IS DISTINCT FROM EXCLUDED.origen_huella` "
           "daría estos mismos conteos y reescribiría las 639 filas en cada corrida")
    p.fuente("transacción de ensayo: tres proyecciones reales, todas deshechas")


def p07_doble_conteo_regeneracion(p, banco):
    """Un segundo asiento para una línea ya proyectada tiene que ser IMPOSIBLE."""
    db = banco.principal()
    cid = db.execute(text(f"SELECT carga_id FROM {TABLA} WHERE carga_id IS NOT NULL "
                          f"ORDER BY id LIMIT 1")).scalar()
    if cid is None:
        raise Omitir("el libro no tiene ningún asiento de proveedor que duplicar")
    p.dice(f"se intenta insertar A MANO un segundo asiento para la carga {cid}, que ya tiene "
           f"el suyo")
    rechazado, quien = _rechaza(db, lambda: _insertar(db, _fila_valida(cid)))
    p.exige("la BASE lo rechaza (no basta con que el código no lo intente)", rechazado, quien)
    p.cuadra("y lo rechaza ux_asientos_carga", "ux_asientos_carga", quien)
    p.cuadra("el libro sigue con los mismos asientos", ESPERADO["filas"], db.execute(text(
        f"SELECT count(*) FROM {TABLA}")).scalar())
    # El predicado del índice NO lleva `AND vigente`, y eso es lo que impide que jubilar un
    # asiento libere su carga_id y la siguiente regeneración inserte un SEGUNDO asiento.
    definicion = db.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ux_asientos_carga'")).scalar()
    p.exige("ux_asientos_carga NO lleva `vigente` en su predicado",
            definicion is not None and "vigente" not in definicion, definicion or "no existe")
    with _deshecho(db):
        db.execute(text(f"UPDATE {TABLA} SET vigente = false WHERE carga_id = :c"), {"c": cid})
        rechazado2, quien2 = _rechaza(db, lambda: _insertar(db, _fila_valida(cid)))
        p.exige("y con el asiento JUBILADO sigue rechazando el duplicado", rechazado2, quien2)
    p.fuente("transacción de ensayo: dos INSERT ilegales, los dos deshechos")


def p08_doble_conteo_entre_fuentes(p, banco):
    """La puerta del emparejador: un hecho físico, como mucho UN asiento contable vivo."""
    db = banco.principal()
    a, b = [r[0] for r in db.execute(text(
        f"SELECT id FROM {TABLA} WHERE vigente AND contable ORDER BY id LIMIT 2")).all()]
    with _deshecho(db):
        evento = db.execute(text(f"SELECT nextval('{SECUENCIA}')")).scalar()
        p.dice(f"se declara el hecho físico {evento} (nextval de {SECUENCIA}) y se sella en el "
               f"asiento {a}")
        db.execute(text(f"UPDATE {TABLA} SET evento_id = :e WHERE id = :i"),
                   {"e": evento, "i": a})
        rechazado, quien = _rechaza(db, lambda: db.execute(
            text(f"UPDATE {TABLA} SET evento_id = :e WHERE id = :i"), {"e": evento, "i": b}))
        p.exige(f"sellar el MISMO evento en el asiento {b}, también contable, se rechaza",
                rechazado, quien)
        p.cuadra("y lo rechaza ux_asientos_evento", "ux_asientos_evento", quien)

        # Y ahora la otra mitad: descontar uno de los dos SÍ tiene que pasar. Ese par de
        # resultados ES la demostración de que la puerta está cableada y no tapiada.
        try:
            db.execute(text(
                f"UPDATE {TABLA} SET evento_id = :e, contable = false, motivo = :m "
                f"WHERE id = :i"),
                {"e": evento, "i": b,
                 "m": "duplica la carga del mismo hecho físico (prueba del verificador)"})
            p.exige("descontar ese segundo asiento (contable=false + motivo) SÍ pasa", True)
        except DBAPIError as e:
            p.exige("descontar ese segundo asiento (contable=false + motivo) SÍ pasa", False,
                    _restriccion(e))
        vivos = db.execute(text(
            f"SELECT count(*) FROM {TABLA} WHERE evento_id = :e AND vigente AND contable"),
            {"e": evento}).scalar()
        p.cuadra("asientos CONTABLES vivos para ese hecho físico", 1, vivos)
        p.cuadra("y los dos lados del par siguen consultables", 2, db.execute(text(
            f"SELECT count(*) FROM {TABLA} WHERE evento_id = :e"), {"e": evento}).scalar())
        p.dice("el hermano descontado CONSERVA su evento_id: es justo lo que lo hace "
               "auditable, y por eso el predicado del índice lleva `AND contable`")
    p.dice(f"OJO, LO ÚNICO QUE ESTE VERIFICADOR NO DESHACE: `nextval` NO es transaccional en "
           f"Postgres, así que {SECUENCIA} se queda un número más adelante aunque el ensayo "
           f"se deshaga. Es inocuo —un evento_id es un identificador opaco y los huecos son "
           f"normales— pero hay que decirlo en vez de afirmar que no se tocó nada.")
    p.fuente("transacción de ensayo: se selló un evento de verdad y se deshizo")


def p09_los_check_muerden(p, banco):
    """Nueve intentos ilegales, nueve rechazos, y cada uno del CHECK que le toca."""
    db = banco.principal()
    cid = _libre(db)
    uid = db.execute(text("SELECT id FROM unidades ORDER BY id LIMIT 1")).scalar()
    rid = db.execute(text("SELECT id FROM remolques ORDER BY id LIMIT 1")).scalar()
    if uid is None or rid is None:
        raise Omitir("hacen falta al menos una unidad y un remolque en el catálogo para "
                     "provocar los CHECK de atribución")
    oid = db.execute(text("SELECT id FROM ordenes_despacho ORDER BY id LIMIT 1")).scalar()

    def variante(clave):
        v = _fila_valida(cid)
        if clave == "a":
            # Se usa un orden_id INVENTADO a propósito: en Postgres los CHECK se evalúan al
            # formar la tupla y las FK son disparadores posteriores, así que el rechazo tiene
            # que venir de ck_asientos_origen aunque la orden no exista. Si algún día hubiera
            # órdenes de verdad, se usa la primera y la prueba dice lo mismo.
            v.update(orden_id=oid or 999999999)
        elif clave == "b":
            v.update(carga_id=None, orden_id=None)
        elif clave == "c":
            v.update(atribucion="activo", destino="termo", unidad_id=uid, remolque_id=rid)
        elif clave == "d":
            v.update(atribucion="activo", destino=None, unidad_id=uid)
        elif clave == "e":
            v.update(atribucion="cuarentena", unidad_id=uid)
        elif clave == "f":
            v.update(atribucion="activo", destino="motor", remolque_id=rid)
        elif clave == "g":
            v.update(litros=Decimal("0"))
        elif clave == "h":
            v.update(contable=False, evento_id=None, motivo="con motivo pero sin evento")
        elif clave == "i":
            v.update(contable=False, evento_id=1, motivo=None)
        return v

    for clave, descripcion, esperado in CHECKS:
        rechazado, quien = _rechaza(db, lambda c=clave: _insertar(db, variante(c)))
        p.exige(f"({clave}) {descripcion}", rechazado, "" if rechazado else quien)
        if rechazado:
            p.cuadra(f"   rechazado por", esperado, quien)
    p.dice("cada intento va en su propio SAVEPOINT: sin eso, el primer CHECK que mordiera "
           "abortaría la transacción y los ocho siguientes no probarían nada")
    p.fuente("transacción de ensayo: nueve INSERT ilegales, los nueve deshechos")


def p10_cuarentena_resuelta(p, banco):
    """ATRIBUIR ES UN UPDATE, JAMÁS UN INSERT. Es LA prueba del enfoque proyección."""
    db = banco.limpio()
    eco = ESPERADO["eco_prueba"]
    antes_n, antes_l = db.execute(text(
        f"SELECT count(*), sum(litros) FROM {TABLA} WHERE vigente")).one()
    cuar_antes = db.execute(text(
        f"SELECT COALESCE(sum(litros), 0) FROM {TABLA} WHERE vigente "
        f"AND atribucion = 'cuarentena'")).scalar()
    p.cuadra("litros sin dueño ANTES", ESPERADO["cuarentena_antes"], cuar_antes)

    uid = db.execute(text("SELECT id FROM unidades ORDER BY id LIMIT 1")).scalar()
    tocadas = db.execute(text(
        "UPDATE cargas_proveedor SET unidad_id = :u, remolque_id = NULL, destino = 'motor', "
        "estado_resolucion = 'resuelta' WHERE vigente AND eco_norm = :e"),
        {"u": uid, "e": eco}).rowcount
    p.cuadra(f"cargas del económico {eco} a las que se les asigna activo",
             ESPERADO["eco_cargas"], tocadas)

    inf = banco.proyectar()
    p.cuadra("asientos NUEVOS (un INSERT tardío es como se duplica un libro)", 0,
             inf["insertados"])
    p.cuadra("asientos ACTUALIZADOS", ESPERADO["eco_cargas"], inf["actualizados"])
    despues_n, despues_l = db.execute(text(
        f"SELECT count(*), sum(litros) FROM {TABLA} WHERE vigente")).one()
    p.cuadra("asientos vigentes (no se movió el total)", antes_n, despues_n)
    p.cuadra("litros vigentes (no se movió el total)", antes_l, despues_l)
    p.cuadra("litros vigentes contra el plan", ESPERADO["litros"], despues_l)
    cuar_despues = db.execute(text(
        f"SELECT COALESCE(sum(litros), 0) FROM {TABLA} WHERE vigente "
        f"AND atribucion = 'cuarentena'")).scalar()
    p.cuadra("litros sin dueño DESPUÉS", ESPERADO["cuarentena_despues"], cuar_despues)
    p.cuadra("y la bajada es exactamente lo del económico", ESPERADO["eco_litros"],
             cuar_antes - cuar_despues)
    p.fuente("transacción de ensayo: se resolvió el catálogo de mentira y se deshizo")


def p11_correccion_de_e2(p, banco):
    """Una corrección del origen jubila su asiento y proyecta la revisión nueva. Ni una más."""
    db = banco.limpio()
    antes_l = db.execute(text(
        f"SELECT sum(litros) FROM {TABLA} WHERE vigente")).scalar()
    vieja = db.execute(text(
        "SELECT id, revision, estacion_txt, folio_txt FROM cargas_proveedor "
        "WHERE vigente ORDER BY id LIMIT 1")).one()
    asiento_viejo = db.execute(text(
        f"SELECT id FROM {TABLA} WHERE carga_id = :c"), {"c": vieja[0]}).scalar()

    # Se jubila la vieja ANTES de insertar la revisión: dos filas vigentes con la misma llave
    # natural violarían `ux_cargas_llave`, igual que en el importador de verdad.
    db.execute(text("UPDATE cargas_proveedor SET vigente = false WHERE id = :i"),
               {"i": vieja[0]})
    columnas = [c.name for c in CargaProveedor.__table__.columns if c.name != "id"]
    cambios = {"revision": "revision + 1", "vigente": "true",
               "sustituye_a_id": str(vieja[0]), "sha256_fila": "'" + "e" * 64 + "'",
               "estado_revision": "'pendiente'"}
    proyeccion = ", ".join(cambios.get(c, c) for c in columnas)
    nueva_id = db.execute(text(
        f"INSERT INTO cargas_proveedor ({', '.join(columnas)}) SELECT {proyeccion} "
        f"FROM cargas_proveedor WHERE id = :i RETURNING id"), {"i": vieja[0]}).scalar()
    p.dice(f"carga {vieja[0]} (estación {vieja[2]}, folio {vieja[3]}) jubilada; entra su "
           f"revisión 2 como carga {nueva_id}")

    inf = banco.proyectar()
    p.cuadra("asientos NUEVOS (la revisión trae asiento propio)", 1, inf["insertados"])
    p.cuadra("asientos ACTUALIZADOS (el viejo, que pasa a vigente=false)", 1,
             inf["actualizados"])
    p.cuadra("asientos VIGENTES (639, no 640)", ESPERADO["filas"], db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE vigente")).scalar())
    p.cuadra("asientos jubilados", 1, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE NOT vigente")).scalar())
    p.cuadra("asientos en total (nadie borró nada)", ESPERADO["filas"] + 1, db.execute(text(
        f"SELECT count(*) FROM {TABLA}")).scalar())
    p.cuadra("litros de los vigentes (idénticos)", antes_l, db.execute(text(
        f"SELECT sum(litros) FROM {TABLA} WHERE vigente")).scalar())
    jub = db.execute(text(
        f"SELECT id, carga_id FROM {TABLA} WHERE NOT vigente")).one()
    p.cuadra("el asiento jubilado es el de siempre", asiento_viejo, jub[0])
    p.cuadra("y sigue apuntando a SU carga vieja", vieja[0], jub[1])
    p.dice("si la fuente del proyector fuera `WHERE c.vigente` a secas, la carga jubilada "
           "saldría del SELECT, su asiento nunca se enteraría y aquí saldrían 640 vigentes")
    p.fuente("transacción de ensayo: se jubiló una carga y se insertó su revisión, deshecho")


def p12_la_huella_discrimina(p, banco):
    """639 huellas distintas para 639 filas. Es lo que delata el defecto del concat_ws."""
    db = banco.principal()
    n, d = db.execute(text(
        f"SELECT count(*), count(DISTINCT origen_huella) FROM {TABLA}")).one()
    p.cuadra("asientos", ESPERADO["filas"], n)
    p.cuadra("huellas distintas", ESPERADO["filas"], d)
    # LA MITAD IMPORTANTE: las 43 filas sin activo son donde `concat_ws` colapsaría, porque en
    # ellas unidad_id y remolque_id son los dos NULL y `concat_ws` los OMITE.
    n43, d43 = db.execute(text(
        f"SELECT count(*), count(DISTINCT origen_huella) FROM {TABLA} "
        f"WHERE unidad_id IS NULL AND remolque_id IS NULL")).one()
    p.cuadra("filas sin activo (donde concat_ws colapsaría)", 43, n43)
    p.cuadra("  y sus huellas distintas", 43, d43)
    p.exige("la expresión de la huella usa coalesce + chr(31) y NO concat_ws",
            "concat_ws" not in SQL_HUELLA and "chr(31)" in SQL_HUELLA
            and "coalesce" in SQL_HUELLA)
    # Las fechas no se castean con ::text: eso las haría depender del TimeZone y del DateStyle
    # de la sesión, y la corrida siguiente "actualizaría" las 639 filas sin cambiar un dato.
    p.exige("y las fechas van con to_char y formato fijo, no con ::text",
            "to_char" in SQL_HUELLA and "momento_ref::text" not in SQL_HUELLA)
    p.fuente("transacción de ensayo + la expresión importada del propio proyector")


def p13_desincronizados(p, banco):
    """La consulta que hay que correr tras CADA importación: 0 asientos a la deriva."""
    db = banco.principal()
    consulta = (f"SELECT count(*) FROM {TABLA} a JOIN cargas_proveedor c ON c.id = a.carga_id "
                f"WHERE a.origen_huella IS DISTINCT FROM {SQL_HUELLA}")
    p.cuadra("asientos cuya huella no coincide con su línea", 0, db.execute(
        text(consulta)).scalar())
    # Y la comprobación de que esa consulta SABE detectar la deriva: si no lo demostrara, un 0
    # solo significaría que la expresión está rota de las dos formas a la vez.
    with _deshecho(db):
        db.execute(text(f"UPDATE {TABLA} SET origen_huella = '0' || substr(origen_huella, 2) "
                        f"WHERE id = (SELECT min(id) FROM {TABLA})"))
        p.cuadra("tras ensuciar UNA huella a mano, la consulta la encuentra", 1,
                 db.execute(text(consulta)).scalar())
    p.cuadra("y tras deshacerlo vuelve a 0", 0, db.execute(text(consulta)).scalar())
    p.fuente("transacción de ensayo, con una huella ensuciada y deshecha")


def p14_eventos_huerfanos(p, banco):
    """El hueco que el esquema NO puede cerrar: es una regla entre filas, no un CHECK."""
    db = banco.principal()
    consulta = (f"SELECT count(*) FROM {TABLA} a WHERE NOT a.contable AND NOT EXISTS "
                f"(SELECT 1 FROM {TABLA} b WHERE b.evento_id = a.evento_id "
                f"AND b.contable AND b.vigente)")
    p.cuadra("asientos descontados sin hermano contable y vigente", 0,
             db.execute(text(consulta)).scalar())
    # Que dé 0 hoy es trivial (no hay ningún descontado). Lo que hay que demostrar es que la
    # consulta lo VERÍA, porque es la única red contra "descontar litros que nadie cuenta".
    with _deshecho(db):
        evento = db.execute(text(f"SELECT nextval('{SECUENCIA}')")).scalar()
        db.execute(text(f"UPDATE {TABLA} SET evento_id = :e, contable = false, motivo = :m "
                        f"WHERE id = (SELECT min(id) FROM {TABLA})"),
                   {"e": evento, "m": "descontado a propósito por el verificador"})
        p.cuadra("tras descontar uno sin hermano, la consulta lo encuentra", 1,
                 db.execute(text(consulta)).scalar())
    p.cuadra("y tras deshacerlo vuelve a 0", 0, db.execute(text(consulta)).scalar())
    p.dice("el proyector corre esta misma consulta en cada corrida y se DETIENE si no da 0: "
           "un asiento descontado sin quien lo cuente hace el mes menor que la factura")
    p.fuente("transacción de ensayo, con un descuento huérfano inyectado y deshecho")


def p15_segunda_fuente(p, banco):
    """El estado de hoy, escrito: la puerta está cableada y VACÍA."""
    db = banco.principal()
    p.cuadra("asientos de origen 'orden'", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE origen = 'orden'")).scalar())
    p.cuadra("asientos con evento_id", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE evento_id IS NOT NULL")).scalar())
    p.cuadra("asientos NO contables", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE NOT contable")).scalar())
    total, contable = db.execute(text(
        f"SELECT COALESCE(sum(litros), 0), "
        f"COALESCE(sum(litros) FILTER (WHERE contable), 0) FROM {TABLA} "
        f"WHERE vigente")).one()
    p.cuadra("sum(litros) contable == sum(litros) total", total, contable)
    # La comprobación que envejece BIEN: ningún asiento apunta a una orden de despacho. Eso es
    # el doble conteo que hay que impedir, y sigue siendo significativo cuando la aplicación
    # tenga mil solicitudes. Antes aquí se exigía que `ordenes_despacho`, `solicitudes_recarga`
    # y `facturas` estuvieran VACÍAS: era cierto el día que se escribió, pero convierte el uso
    # normal de la aplicación en un fallo del libro mayor, y una alarma falsa repetida enseña a
    # ignorar al verificador entero.
    p.cuadra("asientos que apuntan a una orden de despacho", 0, db.execute(text(
        f"SELECT count(*) FROM {TABLA} WHERE orden_id IS NOT NULL")).scalar())
    arriba = {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar()
              for t in ("ordenes_despacho", "solicitudes_recarga", "facturas")}
    p.dice("aguas arriba hay " + ", ".join(f"{n} {t}" for t, n in arriba.items())
           + "; ninguna ha entrado al libro mayor, que es lo que aquí se protege")
    p.dice("por eso esta etapa no construye el emparejador: no hay con qué emparejar, y un "
           "emparejador sin datos se calibra contra el vacío")
    p.fuente("transacción de ensayo + las tablas de la aplicación")


def p16_densidad_de_la_ventana(p, banco):
    """Deja MEDIDO lo que el emparejador va a enfrentar. Conclusión: activo + tiempo NO basta."""
    db = banco.principal()
    pares = ("WITH c AS (SELECT id, unidad_id, remolque_id, momento_ref, destino "
             f"           FROM {TABLA} WHERE vigente AND atribucion = 'activo'), "
             " p AS (SELECT abs(EXTRACT(epoch FROM (b.momento_ref - a.momento_ref))) / 60 AS dm"
             "         FROM c a JOIN c b ON b.id > a.id"
             "          AND a.unidad_id IS NOT DISTINCT FROM b.unidad_id"
             "          AND a.remolque_id IS NOT DISTINCT FROM b.remolque_id) "
             "SELECT round(min(dm)::numeric, 2), count(*) FILTER (WHERE dm < 5), "
             "       count(*) FILTER (WHERE dm < 10), count(*) FILTER (WHERE dm < 15), "
             "       count(*) FILTER (WHERE dm <= 360), count(*) FILTER (WHERE dm = 0) FROM p")
    hueco, p5, p10, p15, p6h, empates = db.execute(text(pares)).one()
    p.cuadra("hueco mínimo entre dos cargas del mismo activo (min)",
             ESPERADO["hueco_min_min"], hueco)
    p.cuadra("pares a menos de  5 minutos", ESPERADO["pares_5"], p5)
    p.cuadra("pares a menos de 10 minutos", ESPERADO["pares_10"], p10)
    p.cuadra("pares a menos de 15 minutos", ESPERADO["pares_15"], p15)
    p.cuadra("pares dentro de ±6 h (la ventana del emparejador)", ESPERADO["pares_6h"], p6h)
    p.cuadra("empates exactos de momento_ref", ESPERADO["empates"], empates)
    n_ind, con_hermano = db.execute(text(
        f"WITH c AS (SELECT id, unidad_id, momento_ref, destino FROM {TABLA} "
        f"            WHERE vigente AND unidad_id IS NOT NULL) "
        f"SELECT count(*) FILTER (WHERE destino = 'indeterminado'), "
        f"       count(*) FILTER (WHERE destino = 'indeterminado' AND EXISTS ("
        f"           SELECT 1 FROM c b WHERE b.id <> c.id AND b.unidad_id = c.unidad_id "
        f"           AND abs(EXTRACT(epoch FROM (b.momento_ref - c.momento_ref))) / 60 < 10))"
        f"  FROM c")).one()
    p.cuadra("asientos 'indeterminado'", ESPERADO["indeterminados"], n_ind)
    p.cuadra("  de ellos, con un hermano del mismo camión a <10 min",
             ESPERADO["indeterminado_con_hermano"], con_hermano)
    p.dice("CONCLUSIÓN, MEDIDA Y NO SUPUESTA: EMPAREJAR POR ACTIVO + TIEMPO NO BASTA. Con 162")
    p.dice("pares del mismo activo dentro de ±6 h y un hueco mínimo de 2 minutos, una ventana")
    p.dice("de ±6 h devuelve varios candidatos idénticos para casi cualquier orden, y en 157")
    p.dice("de los 191 'indeterminado' el hermano está a menos de 10 minutos. El emparejador")
    p.dice("necesitará litros, folio o importe además del activo y la hora.")
    p.fuente("transacción de ensayo, sobre los 596 asientos atribuidos")


def p17_indices_de_verdad(p, banco):
    """Los dos índices que E4 y la bandeja van a usar existen, y sirven para su consulta."""
    db = banco.principal()
    db.execute(text(f"ANALYZE {TABLA}"))        # sin estadísticas, el plan es ficción
    unidad = db.execute(text(
        f"SELECT unidad_id FROM {TABLA} WHERE unidad_id IS NOT NULL "
        f"GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")).scalar()

    casos = (
        ("litros por unidad en julio", "ix_asientos_unidad_momento", True,
         f"SELECT unidad_id, sum(litros) FROM {TABLA} WHERE vigente AND contable "
         f"AND unidad_id = {unidad} AND momento_ref >= TIMESTAMPTZ '2026-07-01' "
         f"AND momento_ref < TIMESTAMPTZ '2026-08-01' GROUP BY unidad_id"),
        ("bandeja de litros sin dueño", "ix_asientos_pendientes", False,
         f"SELECT atribucion, fecha_operacion, sum(litros) FROM {TABLA} "
         f"WHERE vigente AND atribucion <> 'activo' GROUP BY 1, 2"),
    )
    for etiqueta, indice, exigir_natural, consulta in casos:
        plan = "\n".join(db.execute(text("EXPLAIN " + consulta)).scalars().all())
        p.dice(f"— {etiqueta} —")
        for l in plan.splitlines():
            p.dice(f"  {l}")
        if exigir_natural:
            p.exige(f"el planificador elige {indice}", indice in plan)
            p.exige("y NO barre la tabla entera", f"Seq Scan on {TABLA}" not in plan)
        elif indice in plan:
            p.exige(f"el planificador elige {indice} por sí solo", True)
            p.exige("y NO barre la tabla entera", f"Seq Scan on {TABLA}" not in plan)
        else:
            # HONESTIDAD SOBRE LO QUE SE PUEDE PROBAR HOY. El plan dice que el Seq Scan salió
            # más barato, y a 639 filas en 8 páginas eso es CIERTO: leer la tabla entera cuesta
            # menos que el índice. Exigir aquí "sin Seq Scan" haría fallar al verificador por
            # algo que no es un defecto, y aprobar sin mirar sería peor. Lo que SÍ se puede
            # exigir —y es lo único que de verdad podría estar mal— es que el predicado parcial
            # del índice CUBRA la consulta de la bandeja: si `atribucion <> 'activo'` del
            # índice no implicara el filtro de la consulta, Postgres no podría usarlo NI
            # queriendo. Se comprueba quitándole la opción barata.
            p.dice("  el planificador prefirió el Seq Scan: a 639 filas leer la tabla entera "
                   "es de verdad más barato, y eso no es un defecto del índice")
            with _deshecho(db):
                db.execute(text("SET LOCAL enable_seqscan = off"))
                plan2 = "\n".join(db.execute(text("EXPLAIN " + consulta)).scalars().all())
                p.exige(f"el predicado de {indice} CUBRE la consulta de la bandeja",
                        indice in plan2,
                        "" if indice in plan2 else plan2.splitlines()[0])
            p.dice("  el criterio 'sin Seq Scan' solo será medible cuando la tabla tenga un "
                   "año de datos; hoy se prueba que el índice es aplicable")

    # El texto EXACTO con el que Postgres devuelve cada predicado. `atribucion` es varchar, así
    # que su comparación sale normalizada como `(atribucion)::text <> 'activo'::text`: buscar
    # aquí `atribucion <> 'activo'` no encontraría nada y la prueba fallaría por la forma de
    # imprimir del catálogo, no por el índice.
    for indice, trozo in (("ix_asientos_unidad_momento", "unidad_id IS NOT NULL"),
                          ("ix_asientos_pendientes", "(atribucion)::text <> 'activo'::text"),
                          ("ix_asientos_remolque_momento", "remolque_id IS NOT NULL"),
                          ("ix_asientos_fecha_destino", "vigente AND contable"),
                          ("ix_asientos_evento_par", "evento_id IS NOT NULL")):
        definicion = db.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": indice}).scalar()
        p.exige(f"{indice} existe y es parcial por ({trozo})",
                definicion is not None and trozo in definicion,
                definicion or "no existe")
    p.fuente("transacción de ensayo, con ANALYZE previo")


def p18_no_se_rompio_lo_que_funciona(p, banco, con_e2: bool):
    """E3 solo AGREGA. E2 tiene que dar exactamente el mismo veredicto que antes."""
    banco.cerrar()      # verificar_e2 abre sus propias transacciones y hace un DROP de prueba
    with engine.connect() as c:
        n, litros, rev = c.execute(text(
            "SELECT count(*), sum(litros_txt::numeric), max(revision) "
            "FROM cargas_proveedor WHERE vigente")).one()
        enums = c.execute(text(
            "SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar()
    p.cuadra("cargas_proveedor · vigentes", ESPERADO["filas"], n)
    p.cuadra("cargas_proveedor · litros", ESPERADO["litros"], litros)
    p.cuadra("cargas_proveedor · max(revision)", ESPERADO["revision_max"], rev)
    p.cuadra("ENUM nativos en la base (E3 no crea ninguno)", ESPERADO["enums"], enums)
    p.fuente("consulta directa a la base, fuera de todo ensayo")

    if not con_e2:
        raise Omitir("--sin-e2: no se relanzó scripts.verificar_e2, así que sus 24 pruebas "
                     "quedan sin comprobar desde aquí")
    r = subprocess.run([sys.executable, "-m", "scripts.verificar_e2"], cwd=str(RAIZ),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    salida = (r.stdout or "") + (r.stderr or "")
    veredicto = [l.strip() for l in salida.splitlines() if " pasan · " in l]
    if not veredicto:
        p.falla("verificar_e2 no imprimió su línea de veredicto; no se puede comparar")
        return
    p.dice(f"veredicto de E2: {veredicto[0]}")
    p.exige("ninguna de las 24 pruebas de E2 quedó omitida",
            " 0 omitidas " in f" {veredicto[0]} ", veredicto[0])

    fallan = _e2_fallan(salida)
    if not fallan:
        p.cuadra("código de salida de verificar_e2", 0, r.returncode)
        p.exige("ninguna de las 24 pruebas de E2 falla", True)
        p.fuente("subprocess de scripts.verificar_e2, entero")
        return

    # ── LA ÚNICA DIVERGENCIA ADMITIDA, Y HAY QUE MIRARLA DE FRENTE ──────────
    # E3 solo AGREGA, pero agregar una FK hacia `cargas_proveedor` tiene una consecuencia
    # medible: `cargas_proveedor` deja de ser borrable por sí sola. La prueba 1 de E2 ejecuta
    # de verdad su DROP de reversión y ahora Postgres lo rechaza nombrando
    # `asientos_consumo_carga_id_fkey`. NO es que el libro haya escrito en E2 —las cifras de
    # arriba lo demuestran—: es que E2 dejó de ser hoja del grafo, que es exactamente lo que
    # `revertir_importacion.tablas_que_apuntan()` ya detecta solo (prueba 19).
    #
    # Se admite ESA falla y ninguna otra: se exige que sea la prueba 1, que su motivo nombre
    # `asientos_consumo`, y —lo que de verdad importa— que la reversión SIGA SIENDO LIMPIA
    # cuando se hace en su orden: primero E3, después E2. Eso se ejecuta aquí de verdad.
    conocida = (len(fallan) == 1 and fallan[0][0] == 1
                and "asientos_consumo" in fallan[0][1])
    p.exige("la única prueba de E2 que falla es la 1 (su DROP de reversión)",
            conocida, "; ".join(f"prueba {n}" for n, _ in fallan))
    if not conocida:
        for numero, motivo in fallan:
            p.falla(f"E2 · prueba {numero}: {motivo[:150]}")
        p.fuente("subprocess de scripts.verificar_e2, entero")
        return
    p.dice("motivo: " + " ".join(fallan[0][1].split())[:150])
    p.dice("NO es que el libro escribiera en E2 —las cuatro cifras de arriba lo demuestran—: "
           "es que `cargas_proveedor` dejó de ser borrable POR SÍ SOLA, que es la misma")
    p.dice("consecuencia que `tablas_que_apuntan()` detecta sola en la prueba 19.")

    conn = engine.connect()
    tx = conn.begin()
    try:
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        for sentencia in DROP_E3:
            conn.execute(text(sentencia))
        conn.execute(text(
            "DROP TABLE cargas_proveedor, importaciones_proveedor, empleados_proveedor, "
            "tarjetas_combustible, estaciones_proveedor, proveedores"))
        p.exige("revertir EN ORDEN (primero E3, después E2) sí funciona", True)
        p.cuadra("y no deja un solo ENUM huérfano detrás", ESPERADO["enums"],
                 conn.execute(text(
                     "SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar())
    except OperationalError as e:
        p.falla(f"la reversión en orden tampoco funcionó: {str(e).splitlines()[0]}")
    finally:
        tx.rollback()
        conn.close()
    p.dice("")
    p.dice("QUÉ HAY QUE HACER CON ESTO, y no lo decide este script:")
    p.dice("  · `DROP_E2` de scripts/verificar_e2.py tiene que soltar `asientos_consumo`")
    p.dice("    ANTES de las seis tablas de E2, o llevar CASCADE. Es una línea, y es de quien")
    p.dice("    coordina la etapa: este verificador no toca código de E2.")
    p.dice("  · El orden de reversión de la base pasa a ser: E3 primero, E2 después.")
    p.fuente("subprocess de scripts.verificar_e2 + el DROP en orden, ejecutado y deshecho")


def _e2_fallan(salida: str):
    """[(numero, motivo)] de las pruebas que verificar_e2 declaró FALLA, leídas de su bloque
    'NO CUADRA'. Se lee su informe y no se reimplementa su juicio: el que decide si una prueba
    de E2 falla es E2."""
    fallan: list[list] = []
    dentro = False
    for linea in salida.splitlines():
        if linea.strip() == "NO CUADRA:":
            dentro = True
            continue
        if not dentro:
            continue
        if linea.strip().startswith("LA INGESTA NO ES CONFIABLE"):
            break
        cabecera = linea.strip().split(" · ", 1)
        if len(cabecera) == 2 and cabecera[0].isdigit():
            fallan.append([int(cabecera[0]), ""])
        elif fallan:
            # TODO lo que venga detrás de la cabecera es motivo, no solo las líneas que
            # empiezan por '·': el DETAIL y el HINT de Postgres —que son justo donde aparece
            # `asientos_consumo`— llegan sin sangrar y sin viñeta.
            fallan[-1][1] += " " + linea.strip()
    return [(n, m.strip()) for n, m in fallan]


def p19_el_guion_de_reversion(p, banco):
    """`tablas_que_apuntan()` se entera SOLA de que E3 existe, sin cambiarle una línea."""
    db = banco.limpio()
    ajenas = tablas_que_apuntan(db)
    p.cuadra("tablas ajenas que apuntan a las dos tablas de E2", [TABLA], ajenas)
    p.dice("consulta pg_constraint por contype='f' de forma genérica: pasó de devolver [] a "
           "devolver ['asientos_consumo'] sin que nadie le tocara una línea")

    imp_id = db.execute(text(
        "SELECT importacion_id FROM cargas_proveedor WHERE vigente "
        "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")).scalar()
    if imp_id is None:
        raise Omitir("no hay ninguna corrida de importación que revertir")
    n_cargas = db.execute(text(
        "SELECT count(*) FROM cargas_proveedor WHERE importacion_id = :i AND vigente"),
        {"i": imp_id}).scalar()
    asientos_antes = db.execute(text(
        f"SELECT count(*) FROM {TABLA} a JOIN cargas_proveedor c ON c.id = a.carga_id "
        f"WHERE c.importacion_id = :i"), {"i": imp_id}).scalar()

    # (a) el borrado duro tiene que NEGARSE, y nombrando la tabla
    try:
        with contextlib.redirect_stdout(_io.StringIO()):
            revertir(db, imp_id, duro=True, dry=True)
        p.falla("el borrado duro NO se negó: se llevaría por delante el libro mayor")
    except Aborta as e:
        p.exige("--duro se niega", True)
        p.exige(f"y nombra `{TABLA}` en el motivo", TABLA in str(e),
                " ".join(str(e).split())[:110] + "…")

    # (b) el camino blando sigue funcionando, y tras re-proyectar los asientos se jubilan
    with contextlib.redirect_stdout(_io.StringIO()):
        revertir(db, imp_id, duro=False, dry=False,
                 motivo="prueba 19 del verificador de E3 (dentro de una transacción deshecha)")
    p.cuadra("cargas de esa corrida que quedaron vigentes", 0, db.execute(text(
        "SELECT count(*) FROM cargas_proveedor WHERE importacion_id = :i AND vigente"),
        {"i": imp_id}).scalar())
    inf = banco.proyectar()
    p.cuadra("re-proyectar tras anular · asientos NUEVOS", 0, inf["insertados"])
    p.cuadra("re-proyectar tras anular · asientos ACTUALIZADOS", n_cargas, inf["actualizados"])
    p.cuadra("asientos de esa corrida, ahora jubilados", asientos_antes, db.execute(text(
        f"SELECT count(*) FROM {TABLA} a JOIN cargas_proveedor c ON c.id = a.carga_id "
        f"WHERE c.importacion_id = :i AND NOT a.vigente"), {"i": imp_id}).scalar())
    p.cuadra("y no se borró ni uno", ESPERADO["filas"], db.execute(text(
        f"SELECT count(*) FROM {TABLA}")).scalar())
    p.cuadra("el libro vigente encoge exactamente lo anulado",
             ESPERADO["filas"] - n_cargas, db.execute(text(
                 f"SELECT count(*) FROM {TABLA} WHERE vigente")).scalar())
    p.fuente("transacción de ensayo: se anuló una corrida de verdad y se deshizo")


def p20_reversion_limpia(p, banco):
    """Revertir E3 es un DROP. Ni un ALTER que deshacer, ni un enum huérfano detrás."""
    banco.cerrar()      # el DROP necesita el candado exclusivo para sí solo
    with engine.connect() as c:
        antes_enum = c.execute(text(
            "SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar()
    p.cuadra("ENUM nativos ANTES del DROP", ESPERADO["enums"], antes_enum)

    conn = engine.connect()
    tx = conn.begin()
    try:
        # Si el servidor está levantado con una consulta abierta sobre la tabla, el DROP
        # esperaría para siempre. Cinco segundos y se dice que no se pudo.
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        for sentencia in DROP_E3:
            conn.execute(text(sentencia))
        p.dice("ejecutado de verdad: " + "; ".join(DROP_E3))
        p.cuadra("ENUM nativos DESPUÉS del DROP", ESPERADO["enums"], conn.execute(text(
            "SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar())
        n, litros = conn.execute(text(
            "SELECT count(*), sum(litros_txt::numeric) FROM cargas_proveedor "
            "WHERE vigente")).one()
        p.cuadra("cargas_proveedor sigue intacta · filas", ESPERADO["filas"], n)
        p.cuadra("cargas_proveedor sigue intacta · litros", ESPERADO["litros"], litros)
        p.cuadra("índices de asientos_consumo que sobreviven al DROP", 0, conn.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = :t"), {"t": TABLA}).scalar())
        p.cuadra("secuencias `consumo_evento_seq` que sobreviven", 0, conn.execute(text(
            "SELECT count(*) FROM pg_class WHERE relkind = 'S' AND relname = :n"),
            {"n": SECUENCIA}).scalar())
        p.dice("la secuencia va aparte porque no tiene OWNED BY: no pertenece a ninguna "
               "columna, así que no cae con la tabla")
    except OperationalError as e:
        p.dice(f"no se pudo probar el DROP (bloqueado): {str(e).splitlines()[0]}")
        p.falla("el DROP de reversión no se pudo ejecutar")
    finally:
        tx.rollback()
        conn.close()
    p.fuente("el DROP de reversión, ejecutado dentro de una transacción deshecha")


PRUEBAS = (
    (1,  "CUADRE CONTRA EL PROVEEDOR, AL CENTAVO", p01_cuadre),
    (2,  "BIYECCIÓN CON EL ORIGEN", p02_biyeccion),
    (3,  "PARTICIÓN POR ATRIBUCIÓN Y DESTINO", p03_particion),
    (4,  "LO QUE VERÁ E4", p04_lo_que_vera_e4),
    (5,  "SEPARACIÓN DE COMBUSTIBLE", p05_combustible),
    (6,  "IDEMPOTENCIA, MEDIDA EN ESCRITURAS", p06_idempotencia),
    (7,  "DOBLE CONTEO POR REGENERACIÓN: IMPOSIBLE", p07_doble_conteo_regeneracion),
    (8,  "DOBLE CONTEO ENTRE FUENTES: IMPOSIBLE", p08_doble_conteo_entre_fuentes),
    (9,  "LOS NUEVE CHECK MUERDEN", p09_los_check_muerden),
    (10, "CUARENTENA RESUELTA: ATRIBUIR ES UN UPDATE", p10_cuarentena_resuelta),
    (11, "CORRECCIÓN DE E2: EL ESPEJO DE `vigente` NO ES DECORATIVO", p11_correccion_de_e2),
    (12, "LA HUELLA DISCRIMINA", p12_la_huella_discrimina),
    (13, "ASIENTOS DESINCRONIZADOS = 0", p13_desincronizados),
    (14, "EVENTOS HUÉRFANOS = 0", p14_eventos_huerfanos),
    (15, "ESTADO DE LA SEGUNDA FUENTE HOY", p15_segunda_fuente),
    (16, "DENSIDAD DE LA VENTANA QUE E3 VA A ENFRENTAR", p16_densidad_de_la_ventana),
    (17, "ÍNDICES DE VERDAD", p17_indices_de_verdad),
    (18, "NO SE ROMPIÓ LO QUE FUNCIONA", p18_no_se_rompio_lo_que_funciona),
    (19, "EL GUION DE REVERSIÓN SE ENTERA SOLO", p19_el_guion_de_reversion),
    (20, "REVERSIÓN LIMPIA", p20_reversion_limpia),
)


# ─────────────────────────────────────────────────────────────────────────────
# LA CORRIDA
# ─────────────────────────────────────────────────────────────────────────────

def _valor_secuencia():
    """Cuántos hechos físicos lleva servidos la secuencia. NULL = ninguno todavía."""
    try:
        with engine.connect() as c:
            return c.execute(text(
                "SELECT last_value FROM pg_sequences WHERE sequencename = :n"),
                {"n": SECUENCIA}).scalar()
    except Exception:                              # noqa: BLE001
        return None


def _encabezado():
    print("=" * 78)
    print("E3 · VERIFICACIÓN DEL LIBRO MAYOR CONSUMO")
    print("-" * 78)
    insp = inspect(engine)
    hay = insp.has_table(TABLA)
    print(f"  tabla {TABLA}: {'presente' if hay else 'NO EXISTE (corre migrate_e3)'}")
    with engine.connect() as c:
        if hay:
            n, v = c.execute(text(
                f"SELECT count(*), count(*) FILTER (WHERE vigente) FROM {TABLA}")).one()
            print(f"  asientos guardados de verdad en la base: {n} ({v} vigentes)")
        cargas = c.execute(text(
            "SELECT count(*) FROM cargas_proveedor WHERE vigente")).scalar()
        print(f"  cargas vigentes en cargas_proveedor: {cargas}")
    print("  modo ENSAYO: la proyección se ejecuta dentro de una transacción que se DESHACE")
    print("               al terminar. La base no cambia ni una fila, ni en el libro ni en")
    print("               `cargas_proveedor`.")


def _veredicto(resultados, parcial: bool) -> int:
    fallan = [p for p in resultados if p.estado == "FALLA"]
    omitidas = [p for p in resultados if p.estado == "OMITIDA"]
    pasan = [p for p in resultados if p.estado == "PASA"]

    print("\n" + "=" * 78)
    print("VEREDICTO")
    print("-" * 78)
    print(f"  {len(pasan)} pasan · {len(fallan)} fallan · {len(omitidas)} omitidas "
          f"(de {len(resultados)} corridas, de {len(PRUEBAS)} que exige el plan)")
    if parcial:
        sin_correr = sorted({n for n, _, _ in PRUEBAS} - {p.numero for p in resultados})
        print(f"  CORRIDA PARCIAL (--solo): no se ejecutaron las pruebas {sin_correr}")

    if omitidas:
        print("\n  NO SE PROBÓ (y por tanto no se da por bueno):")
        for p in omitidas:
            print(f"    {p.numero:>2} · {p.titulo}")
            print(f"         {p.omitida}")

    if fallan:
        print("\n  NO CUADRA:")
        for p in fallan:
            print(f"    {p.numero:>2} · {p.titulo}")
            for f in p.fallas[:6]:
                print(f"         · {f}")
            if len(p.fallas) > 6:
                print(f"         … y {len(p.fallas) - 6} más")
            if p.omitida:
                print(f"         · y encima quedó incompleta: {p.omitida}")
        print("\n  EL LIBRO MAYOR NO ES CONFIABLE. Corregir antes de proyectar de verdad: un")
        print("  litro contado dos veces no se nota en un total, se nota en el km/L de dentro")
        print("  de seis meses.")
        return 1

    if omitidas or parcial:
        print("\n  Todo lo que se probó cuadra, pero quedan pruebas sin ejecutar.")
        print("  El plan las declaró OBLIGATORIAS: la etapa no está verificada hasta que se")
        print("  puedan correr todas.")
        return 0

    print(f"\n  Las {len(PRUEBAS)} pruebas obligatorias del plan pasan.")
    print("  El libro reproduce el mes al centavo, una línea produce un asiento y solo uno,")
    print("  atribuir la cuarentena es un UPDATE, y la puerta del emparejador está cableada:")
    print("  declarar el mismo hecho físico en dos asientos contables es IMPOSIBLE por")
    print("  restricción de la base. Nada se escribió.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="E3 · Ejecuta las 20 pruebas obligatorias del plan.",
        epilog="No escribe nada: la proyección de ensayo corre dentro de una transacción que "
               "se deshace al terminar.")
    ap.add_argument("--sin-e2", dest="sin_e2", action="store_true",
                    help="no relanzar scripts.verificar_e2 (la prueba 18 saldrá OMITIDA)")
    ap.add_argument("--solo", type=int, nargs="+", metavar="N",
                    help="correr solo estas pruebas (para depurar el verificador)")
    a = ap.parse_args()

    seq_antes = _valor_secuencia()
    _encabezado()
    banco = Banco()
    resultados = []
    try:
        for numero, titulo, fn in PRUEBAS:
            if a.solo and numero not in a.solo:
                continue
            if numero == 18:
                resultados.append(correr(numero, titulo, fn, banco, not a.sin_e2))
            else:
                resultados.append(correr(numero, titulo, fn, banco))
    finally:
        banco.cerrar()             # el rollback pase lo que pase

    codigo = _veredicto(resultados, parcial=bool(a.solo))
    with engine.connect() as c:
        vivos = c.execute(text(f"SELECT count(*) FROM {TABLA}")).scalar() \
            if inspect(engine).has_table(TABLA) else "n/a"
        cargas = c.execute(text(
            "SELECT count(*) FROM cargas_proveedor WHERE vigente")).scalar()
        seq_final = c.execute(text(
            "SELECT last_value FROM pg_sequences WHERE sequencename = :n"),
            {"n": SECUENCIA}).scalar()
    print(f"\n  Comprobación final: asientos en la base = {vivos} · cargas vigentes = "
          f"{cargas} (los mismos que antes de correr esto).")
    if seq_antes != seq_final:
        # No es una excusa, es el dato. Se dice porque el encabezado promete que la base no
        # cambia ni una fila, y esto es exactamente lo que sí cambia.
        print(f"  {SECUENCIA} avanzó de {seq_antes} a {seq_final}: `nextval` NO es "
              f"transaccional y las pruebas 8 y 14 declaran hechos físicos de verdad.")
        print(f"  Es inocuo (un evento_id es opaco y los huecos son normales), pero es lo "
              f"ÚNICO que este verificador no deshace.")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
