"""E2 · Verificador. Ejecuta las 24 pruebas obligatorias del plan y da un VEREDICTO.

EL PROBLEMA QUE RESUELVE
`scripts/diagnostico.py` demostró que los LECTORES reproducen las cifras verificadas a
mano. Eso ya no basta: E2 además escribe, y una ingesta puede cuadrar al centavo mientras
tira la hora de Xyga, deduplica una ráfaga legítima o guarda '140' donde el proveedor
escribió '00140'. Ninguno de esos tres errores mueve un total. Este script comprueba las
veinticuatro cosas que el plan declaró OBLIGATORIAS, cada una con su cifra esperada al
lado de la obtenida, y termina con `sys.exit(1)` si alguna no cuadra.

LA REGLA QUE LO GOBIERNA: UN VERIFICADOR QUE APRUEBA LO QUE NO PROBÓ ES PEOR QUE NO
TENERLO. Cada prueba acaba en uno de tres estados y nunca en otro:

  PASA     se ejecutó entera y todo cuadró.
  FALLA    se ejecutó y algo no cuadró; se dice qué, con el esperado y el obtenido.
  OMITIDA  NO se pudo ejecutar, y se dice POR QUÉ. Jamás se cuenta como buena.

El veredicto distingue las tres: una corrida con omitidas puede salir con éxito, pero
dice cuántas quedaron sin probar y cuáles. Nada se da por bueno en silencio.

CÓMO SE PRUEBA LO QUE NECESITA DATOS GUARDADOS  ·  LA TRANSACCIÓN DE ENSAYO
Veinte de las veinticuatro pruebas miran la base DESPUÉS de una ingesta, y esperar a que
alguien corra la importación de verdad dejaría el verificador inservible justo cuando más
falta hace: antes de correrla. Así que este script abre una transacción, ejecuta dentro
la ingesta real —el mismo `scripts.import_proveedor.importar`, sin trucos— mira el
resultado y DESHACE la transacción entera. La base queda exactamente como estaba: cero
cargas escritas, cero catálogos, cero contadores. Es el mismo recurso que el plan pide
literalmente en su prueba 19 ("inyectar en una sesión con rollback").

Que el dato sea efímero no debilita la prueba: se está comprobando el comportamiento del
importador, no la durabilidad de Postgres. Lo que sí cambia es la honestidad de la
etiqueta, así que cada prueba dice de dónde salió el dato que miró, y el encabezado dice
si hubo ensayo o si había cargas guardadas de verdad. Con `--sin-ensayo` no se abre
ninguna transacción y esas pruebas salen OMITIDAS, que es exactamente lo que son.

Uso (desde backend/, con el venv):
    python -m scripts.verificar_e2
    python -m scripts.verificar_e2 --sin-ensayo
    python -m scripts.verificar_e2 --oxxo RUTA --xyga RUTA
"""

import argparse
import base64
import contextlib
import hashlib
import io as _io
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# La consola de Windows es cp1252 y los motivos que este script imprime llevan acentos
# ('cuarentena: el económico no está en el catálogo'). Sin esto, la prueba 24 la fallaría
# el propio verificador.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import openpyxl  # noqa: E402
from sqlalchemy import inspect, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.catalogo import resolver_activo  # noqa: E402
from app.db import engine  # noqa: E402
from app.ingesta import (  # noqa: E402
    CONTRATOS,
    O_PESOS,
    X_FECHA,
    X_TICKET,
    LayoutInesperado,
    abrir_datos,
    leer_archivo,
    leer_verbatim,
)
from app.models import (  # noqa: E402
    CargaProveedor,
    ImportacionProveedor,
    TarjetaCombustible,
)
from scripts.import_proveedor import _casi_duplicados, _resolver, importar  # noqa: E402

DESCARGAS = os.path.expanduser("~/Downloads")
RAIZ = Path(__file__).resolve().parents[1]

TABLAS_E2 = ("proveedores", "estaciones_proveedor", "tarjetas_combustible",
             "empleados_proveedor", "importaciones_proveedor", "cargas_proveedor")

# El DROP que revierte la etapa entera, en el orden que respeta las FK. Es el mismo que
# imprime scripts/migrate_e2.py; la prueba 1 lo ejecuta DENTRO de una transacción que se
# deshace, porque en Postgres el DDL también es transaccional.
# El libro mayor de E3 apunta a cargas_proveedor, así que la reversión tiene ORDEN: primero
# se cae la etapa de arriba. Se pone aquí y no en el DROP que imprime migrate_e2 porque es una
# propiedad del momento —la que E2 comprueba en cada corrida con tablas_que_apuntan()—, no del
# diseño de E2. El día que E3 no exista, este primer DROP no encuentra nada y no estorba.
DROP_E2 = ("DROP TABLE IF EXISTS asientos_consumo; "
           "DROP TABLE cargas_proveedor, importaciones_proveedor, empleados_proveedor, "
           "tarjetas_combustible, estaciones_proveedor, proveedores")


# ─────────────────────────────────────────────────────────────────────────────
# LAS CIFRAS QUE EL PLAN FIJÓ
#
# No son "lo que salió al probar": son lo que el arquitecto midió a mano sobre los dos
# archivos reales ANTES de escribir una línea de código. Por eso van aquí arriba y no
# incrustadas en cada prueba: si el día de mañana el código deja de reproducirlas, el que
# cambió es el código. La única cifra con tolerancia es la suma de litros por destino,
# porque sumar 639 floats en otro orden mueve el segundo decimal y eso no es un defecto.
# ─────────────────────────────────────────────────────────────────────────────

ESPERADO = {
    # 2 · reproducción al centavo
    "OXXO": {"filas": 326, "litros": 57910.09, "importe": 1578329.30},
    "XYGA": {"filas": 313, "litros": 60630.93, "importe": 1640658.69},
    "total_filas": 639, "total_litros": 118541.02, "total_importe": 3218987.99,
    # huella del archivo tal como llegó, verificada por el arquitecto
    "sha_OXXO": "9ba67d24aab0", "bytes_OXXO": 49255,
    "sha_XYGA": "40931cdf5b6c", "bytes_XYGA": 47034,
    # 1 · los enum de tablas anteriores a E1; un séptimo significaría que alguien coló un
    # Enum en E2 y que el DROP dejaría basura detrás
    "enums": 6,
    # 8 · la ráfaga real del T203 el 14/07 en P07196-B, bomba 8
    "rafaga": {"39852550": 370.14, "39852640": 237.68, "39852740": 92.47,
               "39852770": 150.00, "39852830": 150.00},
    "rafaga_litros": 1000.29,
    # 9 · casi-duplicados: a 5 minutos ninguno, a 15 solo la ráfaga legítima
    "casi_5": 0, "casi_15": 1,
    # 10 · la hora de Xyga
    "xyga_medianoche": 0, "xyga_horas": 24, "xyga_12am": 7, "xyga_12pm": 24,
    # 11 · las dos fechas de Oxxo, TRUNCANDO microsegundos
    "oxxo_distintas": 220, "oxxo_iguales": 106, "oxxo_facturacion_antes": 27,
    "oxxo_mas_de_una_hora": 26, "oxxo_desfase_min": -147.77, "oxxo_desfase_max": 2446.35,
    "oxxo_peor": {"folio": "8280926", "eco": "T227", "estacion": "E09215",
                  "contingencia": "224727615"},
    # 12 · partición de estados (unidad + remolque + cuarentena + fuera de flota = 639)
    "particion": {"OXXO": (300, 20, 6, 0), "XYGA": (172, 104, 33, 4)},
    "ecos_cuarentena": ["401001", "401002", "401004", "531702", "531705"],
    # 13 · destino
    "destino": {"motor": (281, 83030.19), "indeterminado": (191, 15629.78),
                "termo": (124, 15198.19)},
    # 14 · separación de bandejas
    "cuarentena_diesel": 39, "fuera_flota": 4, "fuera_litros": 227.13,
    "fuera_importe": 5341.91, "fuera_tarjetas": ["00130", "00131", "00244"],
    # 15 · tipos y ceros a la izquierda
    "tarjetas": {"OXXO": 45, "XYGA": 52}, "estaciones_no_numericas": 40,
    # 16 · anchos del verbatim
    "ancho": {"OXXO": 26, "XYGA": 25}, "vin_largos": {3, 17, 18},
    # 22 · los Kms de Xyga, basura que vive dentro de fila_cruda y no tiene columna
    "kms": {"123": 280, "0": 32, "15": 1},
}

# Los tres scripts NUEVOS de la etapa. La prueba 24 comprueba que los tres sobreviven a la
# consola cp1252; app/ingesta.py no está porque es un módulo y no imprime nada.
SCRIPTS_NUEVOS = ("migrate_e2.py", "import_proveedor.py", "verificar_e2.py")


# ─────────────────────────────────────────────────────────────────────────────
# EL MARCO  ·  tres estados y ni uno más
# ─────────────────────────────────────────────────────────────────────────────

class Omitir(Exception):
    """La prueba no se puede ejecutar. El mensaje ES el motivo, y se imprime tal cual."""


def _fmt(v):
    """Un valor como lo va a leer una persona, no como lo escribe Python."""
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, (list, tuple, set)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


class Prueba:
    """Una de las veinticuatro. Acumula sus líneas y su estado; imprime al terminar."""

    def __init__(self, numero: int, titulo: str):
        self.numero = numero
        self.titulo = titulo
        self.lineas: list[str] = []
        self.fallas: list[str] = []
        self.omitida: str | None = None

    # ── lo que la prueba cuenta ──────────────────────────────────────────────
    def dice(self, txt: str = ""):
        self.lineas.append(f"     {txt}" if txt else "")

    def fuente(self, txt: str):
        """De dónde salió el dato que se miró. Va SIEMPRE, porque un número sin origen no
        se puede discutir."""
        self.lineas.append(f"     fuente: {txt}")

    # ── las comprobaciones ───────────────────────────────────────────────────
    def cuadra(self, etiqueta: str, esperado, obtenido, tol: float | None = None) -> bool:
        """Compara e imprime SIEMPRE las dos cifras, cuadren o no. Que el esperado sea
        visible incluso cuando la prueba pasa es lo que permite auditar el verificador."""
        if tol is not None and isinstance(esperado, (int, float)):
            ok = abs(float(obtenido) - float(esperado)) <= tol
        else:
            ok = esperado == obtenido
        marca = "OK" if ok else "<-- NO CUADRA"
        self.lineas.append(f"     {etiqueta:<44} esperado {_fmt(esperado):>14} · "
                           f"obtenido {_fmt(obtenido):>14}  {marca}")
        if not ok:
            self.fallas.append(f"{etiqueta}: se esperaba {_fmt(esperado)} y se obtuvo "
                               f"{_fmt(obtenido)}")
        return ok

    def exige(self, etiqueta: str, condicion: bool, detalle: str = "") -> bool:
        """Para lo que no es una cifra: un plan de ejecución, una excepción, un tipo."""
        self.lineas.append(f"     {etiqueta:<44} {'OK' if condicion else '<-- NO CUMPLE'}"
                           + (f"   {detalle}" if detalle else ""))
        if not condicion:
            self.fallas.append(f"{etiqueta}" + (f" ({detalle})" if detalle else ""))
        return condicion

    def falla(self, motivo: str):
        self.fallas.append(motivo)
        self.lineas.append(f"     {motivo}")

    # ── el estado ────────────────────────────────────────────────────────────
    @property
    def estado(self) -> str:
        # UNA FALLA MANDA SOBRE UNA OMISIÓN. Varias pruebas tienen dos mitades —una contra
        # el archivo y otra contra la base—; si la primera no cuadra y la segunda no se
        # puede correr, llamarla 'omitida' escondería el defecto detrás de un "no se pudo
        # probar", que es exactamente lo que este verificador no debe hacer.
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

    Una excepción inesperada NO es una omisión: es un fallo, y se cuenta como tal. Tragarla
    como 'no se pudo probar' convertiría cualquier error de este archivo en un aprobado.
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
# EL CONTEXTO  ·  los archivos, leídos UNA vez
# ─────────────────────────────────────────────────────────────────────────────

class Contexto:
    """Los dos archivos y su lectura verbatim, compartidos por todas las pruebas.

    Se leen una sola vez: doce pruebas los necesitan y releerlos doce veces no probaría
    nada nuevo, solo tardaría doce veces más.
    """

    def __init__(self, rutas: dict, tmp: Path):
        self.rutas = rutas
        self.tmp = tmp
        self.datos: dict = {}
        self.lecturas: dict = {}
        self.error_lectura: dict = {}
        for clave, ruta in rutas.items():
            if not os.path.exists(ruta):
                self.error_lectura[clave] = f"no se encuentra el archivo: {ruta}"
                continue
            try:
                datos, sha = leer_archivo(ruta)
                wb = abrir_datos(datos)
                try:
                    self.lecturas[clave] = leer_verbatim(wb, clave)
                finally:
                    wb.close()
                self.datos[clave] = (datos, sha)
            except Exception as e:                     # noqa: BLE001
                self.error_lectura[clave] = f"{type(e).__name__}: {e}"
        self.tablas = self._tablas()

    @staticmethod
    def _tablas() -> set:
        try:
            return set(inspect(engine).get_table_names())
        except Exception:                              # noqa: BLE001
            return set()

    def lectura(self, clave: str):
        """La lectura de un archivo, o el motivo por el que no la hay."""
        if clave in self.lecturas:
            return self.lecturas[clave]
        raise Omitir(f"{clave}: {self.error_lectura.get(clave, 'sin lectura')}")

    def ambas(self) -> dict:
        return {c: self.lectura(c) for c in ("OXXO", "XYGA")}

    def exige_tablas(self):
        faltan = [t for t in TABLAS_E2 if t not in self.tablas]
        if faltan:
            raise Omitir(f"faltan las tablas de E2 ({', '.join(faltan)}); corre primero "
                         f"python -m scripts.migrate_e2")


# ─────────────────────────────────────────────────────────────────────────────
# EL BANCO DE ENSAYO  ·  una transacción viva a la vez, siempre deshecha
# ─────────────────────────────────────────────────────────────────────────────

def _ingerir(db, ctx, clave):
    """Corre la ingesta DE VERDAD dentro de la transacción de ensayo.

    Se llama a `scripts.import_proveedor.importar` sin adaptaciones: probar una copia
    simplificada del importador no probaría el importador. Su salida se silencia porque
    aquí lo que se mira son sus efectos, no su informe, y 300 líneas por archivo taparían
    el resultado de las pruebas.

    Si el archivo YA está guardado de verdad, `importar` devuelve None y dice por qué; eso
    no es un fallo: significa que la prueba va a mirar datos reales en vez de ensayados.
    """
    ctx.lectura(clave)                    # si el archivo no se puede leer, omite aquí
    with contextlib.redirect_stdout(_io.StringIO()):
        importar(db, clave, ctx.rutas[clave])


class Banco:
    """Gestiona la transacción de ensayo. NUNCA hay dos abiertas a la vez.

    Dos transacciones simultáneas escribirían las mismas llaves de `ux_cargas_llave` y la
    segunda se quedaría esperando a que la primera termine — que no termina, porque la
    primera espera a que acabe la prueba. El bloqueo sería un cuelgue sin mensaje, así que
    abrir una cierra la anterior por construcción.
    """

    def __init__(self, ctx: Contexto, activo: bool = True):
        self.ctx = ctx
        self.activo = activo
        self._db = self._conn = self._tx = None
        self._contenido = None

    def _abrir(self, claves):
        self.cerrar()
        if not self.activo:
            raise Omitir("modo --sin-ensayo: no se abre ninguna transacción de ensayo, así "
                         "que no hay cargas que mirar")
        self.ctx.exige_tablas()
        self._conn = engine.connect()
        self._tx = self._conn.begin()
        # `create_savepoint` es lo que permite que `importar` haga su propio commit sin
        # cerrar la transacción externa: el commit libera un SAVEPOINT y el rollback de
        # fuera sigue pudiendo deshacerlo todo.
        self._db = Session(bind=self._conn, join_transaction_mode="create_savepoint",
                           autoflush=False, expire_on_commit=False)
        try:
            for clave in claves:
                _ingerir(self._db, self.ctx, clave)
        except BaseException:
            self.cerrar()
            raise
        self._contenido = tuple(claves)
        return self._db

    def principal(self):
        """La transacción con los DOS archivos dentro. Se reabre si una prueba la cerró."""
        if self._db is not None and self._contenido == ("OXXO", "XYGA"):
            return self._db
        return self._abrir(("OXXO", "XYGA"))

    def limpio(self, *claves):
        """Una transacción recién abierta, para las pruebas que escriben. Cada una empieza
        de cero para que su cifra esperada sea la del plan y no 'la del plan más lo que
        dejó la prueba anterior'."""
        return self._abrir(claves)

    def cerrar(self):
        if self._db is not None:
            self._db.close()
        if self._tx is not None:
            self._tx.rollback()          # AQUÍ se deshace todo lo que se ensayó
        if self._conn is not None:
            self._conn.close()
        self._db = self._conn = self._tx = None
        self._contenido = None

    @property
    def ensayado(self) -> bool:
        return self._contenido is not None


# ─────────────────────────────────────────────────────────────────────────────
# ARCHIVOS SINTÉTICOS  ·  para las pruebas 6, 7 y 17
#
# Reconstruir el xlsx celda por celda es FIEL: lo medí antes de apoyarme en ello y las 326
# filas de Oxxo y las 313 de Xyga conservan su `sha256_fila` exacto tras el viaje de ida y
# vuelta, incluidas las fechas que openpyxl guarda como serial flotante. Si no lo fuera,
# la prueba 7 no podría aislar UNA corrección: la reescritura las cambiaría todas.
# ─────────────────────────────────────────────────────────────────────────────

def _hoja_nativa(datos: bytes, contrato) -> list:
    """Todas las filas de la hoja con sus valores nativos, cabeceras incluidas."""
    wb = openpyxl.load_workbook(_io.BytesIO(datos), data_only=True, read_only=True)
    try:
        return [list(r) for r in wb[contrato.hoja].iter_rows(values_only=True)]
    finally:
        wb.close()


def _escribir_hoja(filas, contrato, destino: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = contrato.hoja
    for f in filas:
        ws.append(list(f))
    wb.save(destino)
    wb.close()
    return destino


# ─────────────────────────────────────────────────────────────────────────────
# LAS VEINTICUATRO PRUEBAS
# ─────────────────────────────────────────────────────────────────────────────

def p01_migracion(p, ctx, banco):
    """Correr la migración dos veces no cambia nada, y revertirla no deja basura."""
    ctx.exige_tablas()
    banco.cerrar()      # el DROP de prueba necesita el candado exclusivo para sí solo

    # (a) idempotencia: la segunda corrida no puede reportar un solo cambio
    antes = _radiografia()
    r = subprocess.run([sys.executable, "-m", "scripts.migrate_e2"], cwd=str(RAIZ),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    salida = (r.stdout or "") + (r.stderr or "")
    nuevas = [l.strip() for l in salida.splitlines() if l.startswith("NUEVA")]
    p.cuadra("código de salida de migrate_e2", 0, r.returncode)
    p.cuadra("líneas 'NUEVA' en la segunda corrida", 0, len(nuevas))
    for l in nuevas[:6]:
        p.dice(f"  {l}")
    despues = _radiografia()
    p.exige("el esquema de las 6 tablas quedó idéntico", antes == despues,
            "" if antes == despues else _primera_diferencia(antes, despues))

    # (b) la reversión, EJECUTADA. En Postgres el DDL es transaccional, así que el DROP de
    # las seis tablas se puede hacer de verdad y deshacer: es la única forma de comprobar
    # que no deja un enum huérfano detrás en vez de suponerlo.
    with engine.connect() as c:
        antes_enum = c.execute(text(
            "SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar()
    p.cuadra("enums en la base ANTES del DROP", ESPERADO["enums"], antes_enum)

    conn = engine.connect()
    tx = conn.begin()
    try:
        # Si el servidor está levantado y tiene una consulta abierta sobre estas tablas, el
        # DROP esperaría para siempre. Cinco segundos y se dice que no se pudo.
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        conn.execute(text(DROP_E2))
        despues_enum = conn.execute(text(
            "SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar()
        p.cuadra("enums DESPUÉS del DROP de las seis tablas", ESPERADO["enums"], despues_enum)
        p.dice("el DROP se ejecutó de verdad dentro de una transacción y se deshizo")
    except OperationalError as e:
        p.dice(f"no se pudo probar el DROP (bloqueado): {str(e).splitlines()[0]}")
        p.dice("queda SIN PROBAR que la reversión no deje un enum huérfano")
        p.fallas.append("el DROP de reversión no se pudo ejecutar")
    finally:
        tx.rollback()
        conn.close()
    p.fuente("subprocess de scripts.migrate_e2 + un DROP dentro de una transacción deshecha")


def _radiografia() -> dict:
    """Columnas e índices de las seis tablas: el `\\d+` que la prueba 1 compara."""
    with engine.connect() as c:
        cols = c.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = ANY(:t) "
            "ORDER BY table_name, ordinal_position"), {"t": list(TABLAS_E2)}).all()
        idx = c.execute(text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = ANY(:t) "
            "ORDER BY tablename, indexname"), {"t": list(TABLAS_E2)}).all()
    return {"columnas": [tuple(r) for r in cols], "indices": [tuple(r) for r in idx]}


def _primera_diferencia(a, b) -> str:
    for k in a:
        fa, fb = set(map(str, a[k])), set(map(str, b[k]))
        if fa != fb:
            solo = list(fa - fb)[:1] + list(fb - fa)[:1]
            return f"{k}: {solo}"
    return ""


def p02_reproduccion(p, ctx, banco):
    """Los totales al centavo, y los mismos totales guardados en la corrida."""
    lec = ctx.ambas()
    total_l = total_i = total_f = 0.0
    for clave in ("OXXO", "XYGA"):
        e, l = ESPERADO[clave], lec[clave]
        p.cuadra(f"{clave} · filas", e["filas"], len(l.filas))
        p.cuadra(f"{clave} · litros", e["litros"], round(l.litros_total, 2), tol=0.01)
        p.cuadra(f"{clave} · importe", e["importe"], round(l.importe_total, 2), tol=0.01)
        datos, sha = ctx.datos[clave]
        p.cuadra(f"{clave} · sha256 del archivo", ESPERADO[f"sha_{clave}"], sha[:12])
        p.cuadra(f"{clave} · bytes", ESPERADO[f"bytes_{clave}"], len(datos))
        total_f += len(l.filas)
        total_l += l.litros_total
        total_i += l.importe_total
    p.cuadra("TOTAL · cargas", ESPERADO["total_filas"], int(total_f))
    p.cuadra("TOTAL · litros", ESPERADO["total_litros"], round(total_l, 2), tol=0.01)
    p.cuadra("TOTAL · importe", ESPERADO["total_importe"], round(total_i, 2), tol=0.01)
    p.fuente("los dos archivos, leídos con app.ingesta")

    # Y lo mismo GUARDADO: el plan exige poder auditar una corrida sin reabrir el Excel.
    ctx.exige_tablas()
    with engine.connect() as c:
        for clave in ("OXXO", "XYGA"):
            _, sha = ctx.datos[clave]
            fila = c.execute(text(
                "SELECT litros_total, importe_total, n_filas_leidas FROM "
                "importaciones_proveedor WHERE sha256 = :s ORDER BY id DESC LIMIT 1"),
                {"s": sha}).first()
            if fila is None:
                p.dice(f"{clave}: todavía no hay ninguna corrida guardada de este archivo, "
                       f"ni siquiera simulada; no se puede auditar sin reabrir el Excel")
                continue
            p.cuadra(f"{clave} · litros_total guardado", ESPERADO[clave]["litros"],
                     round(float(fila[0]), 2), tol=0.01)
            p.cuadra(f"{clave} · importe_total guardado", ESPERADO[clave]["importe"],
                     round(float(fila[1]), 2), tol=0.01)


def p03_cero_perdida(p, ctx, banco):
    """Ninguna fila se cae antes de contarse."""
    lec = ctx.ambas()
    for clave in ("OXXO", "XYGA"):
        l = lec[clave]
        contrato = CONTRATOS[clave]
        p.cuadra(f"{clave} · n_filas_leidas == filas + omitidas", l.n_filas_leidas,
                 len(l.filas) + len(l.omitidas))
        p.cuadra(f"{clave} · omitidas", 0, len(l.omitidas))
        for num, motivo in list(l.omitidas.items())[:5]:
            p.dice(f"  fila {num}: {motivo}")
        # El conteo INDEPENDIENTE del lector: el ancho que declara la propia hoja, sin
        # pasar por la regla de "fila vacía" del módulo que se está probando.
        wb = openpyxl.load_workbook(_io.BytesIO(ctx.datos[clave][0]), data_only=True,
                                    read_only=True)
        try:
            filas_hoja = wb[contrato.hoja].max_row - contrato.fila_datos + 1
        finally:
            wb.close()
        p.cuadra(f"{clave} · filas de la hoja contadas aparte", ESPERADO[clave]["filas"],
                 filas_hoja)
    p.fuente("los archivos; el conteo de control sale de ws.max_row, no del lector")

    db = banco.principal()
    for clave in ("OXXO", "XYGA"):
        imp = db.execute(select(ImportacionProveedor).where(
            ImportacionProveedor.sha256 == ctx.datos[clave][1],
            ImportacionProveedor.vigente.is_(True))).scalars().first()
        if imp is None:
            p.dice(f"{clave}: sin corrida vigente en el ensayo (¿el archivo ya estaba?)")
            continue
        cuadre = imp.n_nuevas + imp.n_repetidas + imp.n_corregidas + imp.n_omitidas
        p.cuadra(f"{clave} · nuevas+repetidas+corregidas+omitidas", imp.n_filas_leidas, cuadre)
        p.cuadra(f"{clave} · n_omitidas guardado", 0, imp.n_omitidas)


def p04_llave(p, ctx, banco):
    """639 llaves distintas para 639 filas, y ni un componente vacío."""
    lec = ctx.ambas()
    llaves = set()
    vacios = 0
    for clave, l in lec.items():
        for f in l.filas:
            llaves.add((clave, f["estacion_txt"], f["folio_txt"]))
            if not f["estacion_txt"] or not f["folio_txt"]:
                vacios += 1
    p.cuadra("archivo · llaves distintas", ESPERADO["total_filas"], len(llaves))
    p.cuadra("archivo · componentes de llave vacíos", 0, vacios)
    p.fuente("los dos archivos")

    db = banco.principal()
    n, d = db.execute(text(
        "SELECT count(*), count(DISTINCT (proveedor_id, estacion_txt, folio_txt)) "
        "FROM cargas_proveedor WHERE vigente")).one()
    p.cuadra("base · cargas vigentes", ESPERADO["total_filas"], n)
    p.cuadra("base · llaves distintas entre ellas", ESPERADO["total_filas"], d)
    p.cuadra("base · llaves con componente nulo o vacío", 0, db.execute(text(
        "SELECT count(*) FROM cargas_proveedor WHERE estacion_txt IS NULL OR "
        "estacion_txt = '' OR folio_txt IS NULL OR folio_txt = ''")).scalar())
    p.fuente("transacción de ensayo (la ingesta corrió y se deshizo)")


def p05_idempotencia_archivo(p, ctx, banco):
    """Reimportar el mismo archivo avisa y no toca nada; con --forzar, todo repetido."""
    db = banco.limpio("OXXO", "XYGA")
    for clave in ("OXXO", "XYGA"):
        salida = _io.StringIO()
        with contextlib.redirect_stdout(salida):
            r = importar(db, clave, ctx.rutas[clave])
        p.exige(f"{clave} · sin bandera: no reimporta", r is None,
                salida.getvalue().splitlines()[0] if salida.getvalue() else "")
        with contextlib.redirect_stdout(_io.StringIO()):
            r2 = importar(db, clave, ctx.rutas[clave], forzar=True)
        if r2 is None:
            p.falla(f"{clave}: --forzar tampoco leyó el archivo")
            continue
        p.cuadra(f"{clave} · --forzar n_repetidas", ESPERADO[clave]["filas"], r2.n_repetidas)
        p.cuadra(f"{clave} · --forzar n_nuevas", 0, r2.n_nuevas)
        p.cuadra(f"{clave} · --forzar n_corregidas", 0, r2.n_corregidas)
        p.cuadra(f"{clave} · --forzar n_omitidas", 0, r2.n_omitidas)
    p.cuadra("cargas vigentes tras las cuatro relecturas", ESPERADO["total_filas"],
             db.execute(text("SELECT count(*) FROM cargas_proveedor WHERE vigente")).scalar())
    p.fuente("transacción de ensayo: dos ingestas y cuatro relecturas, todas deshechas")


def p06_solape_parcial(p, ctx, banco):
    """Lo que el sha del archivo NO cubre: un reporte que se solapa con el anterior."""
    ctx.lectura("XYGA")
    contrato = CONTRATOS["XYGA"]
    filas = _hoja_nativa(ctx.datos["XYGA"][0], contrato)
    plantilla = list(filas[-1])
    inventadas = (("9900001", "03/08/2026 09:15:00 a. m."),
                  ("9900002", "04/08/2026 05:40:00 p. m."))
    for ticket, fecha in inventadas:
        nueva = list(plantilla)
        nueva[X_TICKET] = ticket
        nueva[X_FECHA] = fecha
        filas.append(nueva)
    ruta = _escribir_hoja(filas, contrato, ctx.tmp / "xyga_julio_mas_dos_de_agosto.xlsx")
    p.dice(f"archivo sintético: las {ESPERADO['XYGA']['filas']} filas de julio + 2 de agosto")
    p.cuadra("sha256 distinto del original", True,
             leer_archivo(ruta)[1] != ctx.datos["XYGA"][1])

    db = banco.limpio("OXXO", "XYGA")
    with contextlib.redirect_stdout(_io.StringIO()):
        imp = importar(db, "XYGA", ruta)
    if imp is None:
        raise Omitir("el importador no leyó el archivo sintético")
    p.cuadra("n_nuevas", 2, imp.n_nuevas)
    p.cuadra("n_repetidas", ESPERADO["XYGA"]["filas"], imp.n_repetidas)
    p.cuadra("n_corregidas", 0, imp.n_corregidas)
    p.cuadra("cargas vigentes de XYGA", ESPERADO["XYGA"]["filas"] + 2, db.execute(text(
        "SELECT count(*) FROM cargas_proveedor c JOIN proveedores p ON p.id = c.proveedor_id "
        "WHERE c.vigente AND p.clave = 'XYGA'")).scalar())
    p.cuadra("cargas vigentes en total", ESPERADO["total_filas"] + 2, db.execute(text(
        "SELECT count(*) FROM cargas_proveedor WHERE vigente")).scalar())
    p.dice("si saliera 628 en vez de 315, la llave natural estaría mal y un reporte de "
           "'últimos 45 días' duplicaría litros")
    p.fuente("transacción de ensayo + archivo sintético construido en un temporal")


def p07_correccion(p, ctx, banco):
    """Una corrección del proveedor no borra nada: jubila la vieja y deja las dos."""
    ctx.lectura("OXXO")
    contrato = CONTRATOS["OXXO"]
    filas = _hoja_nativa(ctx.datos["OXXO"][0], contrato)
    i = contrato.fila_datos - 1                      # la primera fila de datos, 0-indexada
    original = filas[i][O_PESOS]
    filas[i] = list(filas[i])
    filas[i][O_PESOS] = round(float(original) + 111.11, 2)
    ruta = _escribir_hoja(filas, contrato, ctx.tmp / "oxxo_con_un_importe_cambiado.xlsx")
    p.dice(f"copia de Despachos.xlsx con el importe de la fila {contrato.fila_datos} "
           f"cambiado: {float(original):,.2f} -> {filas[i][O_PESOS]:,.2f}")

    db = banco.limpio("OXXO", "XYGA")
    with contextlib.redirect_stdout(_io.StringIO()):
        imp = importar(db, "OXXO", ruta)
    if imp is None:
        raise Omitir("el importador no leyó la copia corregida")
    p.cuadra("n_corregidas", 1, imp.n_corregidas)
    p.cuadra("n_nuevas", 0, imp.n_nuevas)
    p.cuadra("n_repetidas", ESPERADO["OXXO"]["filas"] - 1, imp.n_repetidas)

    versiones = db.execute(text(
        "SELECT c.id, c.revision, c.vigente, c.importe, c.sustituye_a_id, c.estado_revision "
        "FROM cargas_proveedor c JOIN proveedores pr ON pr.id = c.proveedor_id "
        "WHERE pr.clave = 'OXXO' AND c.estacion_txt = :e AND c.folio_txt = :f "
        "ORDER BY c.revision"), {"e": _texto_celda(filas[i], contrato, "estacion"),
                                 "f": _texto_celda(filas[i], contrato, "folio")}).all()
    p.cuadra("versiones consultables de esa carga", 2, len(versiones))
    if len(versiones) == 2:
        vieja, nueva = versiones
        p.cuadra("vieja · revision", 1, vieja[1])
        p.cuadra("vieja · vigente", False, vieja[2])
        p.cuadra("vieja · conserva su importe original", round(float(original), 2),
                 round(float(vieja[3]), 2), tol=0.01)
        p.cuadra("nueva · revision", 2, nueva[1])
        p.cuadra("nueva · vigente", True, nueva[2])
        p.cuadra("nueva · sustituye_a_id apunta a la vieja", vieja[0], nueva[4])
        p.cuadra("nueva · estado_revision", "pendiente", nueva[5])
        p.cuadra("nueva · trae el importe corregido", filas[i][O_PESOS],
                 round(float(nueva[3]), 2), tol=0.01)
    p.cuadra("cargas vigentes en total", ESPERADO["total_filas"], db.execute(text(
        "SELECT count(*) FROM cargas_proveedor WHERE vigente")).scalar())
    p.fuente("transacción de ensayo + copia sintética con UNA celda cambiada")


def _texto_celda(fila, contrato, cual):
    """La estación y el folio de una fila nativa, con la misma conversión que el lector."""
    from app.ingesta import O_ESTACION, O_TRANSACCION
    v = fila[O_ESTACION if cual == "estacion" else O_TRANSACCION]
    return None if v is None else str(v)


def p08_rafaga(p, ctx, banco):
    """Las cinco cargas del T203 el 14/07 tienen que estar LAS CINCO."""
    l = ctx.lectura("OXXO")
    raf = {f["folio_txt"]: f for f in l.filas
           if f["eco_norm"] == "T203" and f["estacion_txt"] == "P07196-B"
           and f["momento_local"] and f["momento_local"].date().isoformat() == "2026-07-14"}
    p.cuadra("archivo · cargas de la ráfaga", len(ESPERADO["rafaga"]), len(raf))
    for folio, litros in sorted(ESPERADO["rafaga"].items()):
        f = raf.get(folio)
        p.cuadra(f"  folio {folio} · litros", litros,
                 round(f["litros"], 2) if f else None, tol=0.01)
    p.cuadra("archivo · litros de la ráfaga", ESPERADO["rafaga_litros"],
             round(sum(f["litros"] for f in raf.values()), 2), tol=0.01)
    p.cuadra("todas en la misma bomba", {"8"}, {f["bomba_txt"] for f in raf.values()})
    p.fuente("Despachos.xlsx")

    db = banco.principal()
    n, litros = db.execute(text(
        "SELECT count(*), COALESCE(sum(litros), 0) FROM cargas_proveedor c "
        "JOIN proveedores pr ON pr.id = c.proveedor_id WHERE c.vigente AND pr.clave = 'OXXO' "
        "AND c.eco_norm = 'T203' AND c.estacion_txt = 'P07196-B' "
        "AND c.fecha_operacion = DATE '2026-07-14'")).one()
    p.cuadra("base · cargas guardadas de la ráfaga", len(ESPERADO["rafaga"]), n)
    p.cuadra("base · litros guardados", ESPERADO["rafaga_litros"], round(float(litros), 2),
             tol=0.01)
    p.dice("si devolviera 4, la llave estaría deduplicando por contenido y se borraría "
           "dinero real")
    p.fuente("transacción de ensayo")


def p09_casi_duplicados(p, ctx, banco):
    """El detector marca la ráfaga legítima y NADA más. Calibración, no bloqueo."""
    lec = ctx.ambas()
    filas = []
    for l in lec.values():
        for f in l.filas:
            g = dict(f)
            g["_vals"] = f          # la forma que espera _casi_duplicados del importador
            filas.append(g)
    p.cuadra("filas examinadas", ESPERADO["total_filas"], len(filas))
    for minutos, esperado in ((5, ESPERADO["casi_5"]), (15, ESPERADO["casi_15"])):
        pares = _casi_duplicados(filas, minutos)
        p.cuadra(f"pares a {minutos:>2} minutos", esperado, len(pares))
        for num, a, b, d in pares[:3]:
            p.dice(f"  tarjeta {num} · folios {a['_vals']['folio_txt']} y "
                   f"{b['_vals']['folio_txt']} · {a['_vals']['litros']:,.2f} L · {d:g} min")
    p.dice("el par de 15 minutos es la ráfaga real del T203 (dos veces 150.00 L a 9 "
           "minutos): se MARCA, no se descarta")
    p.fuente("los dos archivos, con la función _casi_duplicados del propio importador")


def p10_hora_xyga(p, ctx, banco):
    """La hora de Xyga no se tira. Es el error que ningún total delataría."""
    l = ctx.lectura("XYGA")
    horas = Counter(f["momento_local"].hour for f in l.filas if f["momento_local"])
    p.cuadra("archivo · filas sin fecha legible", 0,
             sum(1 for f in l.filas if not f["momento_local"]))
    p.cuadra("archivo · cargas a las 00:00:00 exactas", ESPERADO["xyga_medianoche"],
             sum(1 for f in l.filas if f["momento_local"]
                 and f["momento_local"].strftime("%H:%M:%S") == "00:00:00"))
    p.cuadra("archivo · horas del día cubiertas", ESPERADO["xyga_horas"], len(horas))
    # 12 a. m. es medianoche y 12 p. m. es mediodía: es justo donde una conversión casera
    # de 12 horas se equivoca, y por eso se comprueban por separado.
    am = [f for f in l.filas if _es_doce(f["fecha_txt"], "a")]
    pm = [f for f in l.filas if _es_doce(f["fecha_txt"], "p")]
    p.cuadra("archivo · filas '12 ... a. m.'", ESPERADO["xyga_12am"], len(am))
    p.cuadra("  y todas caen en la hora 0", {0}, {f["momento_local"].hour for f in am})
    p.cuadra("archivo · filas '12 ... p. m.'", ESPERADO["xyga_12pm"], len(pm))
    p.cuadra("  y todas caen en la hora 12", {12}, {f["momento_local"].hour for f in pm})
    p.fuente("REPORTE+DE+CONSUMOS, leído con app.ingesta")

    db = banco.principal()
    p.cuadra("base · XYGA con momento_local a medianoche", ESPERADO["xyga_medianoche"],
             db.execute(text(
                 "SELECT count(*) FROM cargas_proveedor c JOIN proveedores p "
                 "ON p.id = c.proveedor_id WHERE c.vigente AND p.clave = 'XYGA' "
                 "AND c.momento_local::time = '00:00:00'")).scalar())
    p.fuente("transacción de ensayo")


def _es_doce(fecha_txt, meridiano) -> bool:
    if not fecha_txt:
        return False
    partes = str(fecha_txt).split(" ")
    return (len(partes) > 1 and partes[1].startswith("12:")
            and str(fecha_txt).strip().lower().endswith(f"{meridiano}. m."))


def p11_dos_fechas_oxxo(p, ctx, banco):
    """Las dos fechas de Oxxo son datos distintos, y una de ellas viaja al futuro."""
    l = ctx.lectura("OXXO")
    con = [f for f in l.filas if f["momento_facturacion"] and f["momento_local"]]
    p.cuadra("filas con las dos fechas", ESPERADO["OXXO"]["filas"], len(con))
    p.cuadra("con momento_facturacion != momento_local", ESPERADO["oxxo_distintas"],
             sum(1 for f in con if f["momento_facturacion"] != f["momento_local"]))
    p.cuadra("idénticas al segundo", ESPERADO["oxxo_iguales"],
             sum(1 for f in con if f["momento_facturacion"] == f["momento_local"]))
    p.cuadra("facturación ANTERIOR al despacho", ESPERADO["oxxo_facturacion_antes"],
             sum(1 for f in con if f["momento_facturacion"] < f["momento_local"]))
    p.cuadra("con |desfase| > 1 hora", ESPERADO["oxxo_mas_de_una_hora"],
             sum(1 for f in con if abs(f["desfase_facturacion_min"]) > 60))
    desf = [f["desfase_facturacion_min"] for f in con]
    p.cuadra("desfase mínimo (min)", ESPERADO["oxxo_desfase_min"], min(desf), tol=0.01)
    p.cuadra("desfase máximo (min)", ESPERADO["oxxo_desfase_max"], max(desf), tol=0.01)
    peor = max(con, key=lambda f: f["desfase_facturacion_min"])
    e = ESPERADO["oxxo_peor"]
    p.cuadra("la del desfase máximo · folio", e["folio"], peor["folio_txt"])
    p.cuadra("  · económico", e["eco"], peor["eco_norm"])
    p.cuadra("  · estación", e["estacion"], peor["estacion_txt"])
    p.cuadra("  · es la venta en contingencia", e["contingencia"], peor["contingencia_txt"])
    p.dice("estas 27 filas existen: un 'assert facturacion >= despacho' abortaría la "
           "importación de un mes entero por datos ciertos")
    p.fuente("Despachos.xlsx, con las fechas truncadas al segundo como manda el plan")


def p12_particion(p, ctx, banco):
    """unidad + remolque + cuarentena + fuera de flota = 639, sin solapes."""
    db = banco.principal()
    filas = db.execute(text(
        "SELECT p.clave, "
        "  count(*) FILTER (WHERE c.unidad_id IS NOT NULL), "
        "  count(*) FILTER (WHERE c.remolque_id IS NOT NULL), "
        "  count(*) FILTER (WHERE c.estado_resolucion = 'cuarentena' AND NOT c.fuera_de_flota), "
        "  count(*) FILTER (WHERE c.fuera_de_flota), count(*) "
        "FROM cargas_proveedor c JOIN proveedores p ON p.id = c.proveedor_id "
        "WHERE c.vigente GROUP BY p.clave ORDER BY p.clave")).all()
    suma = 0
    for clave, uni, rem, cuar, fuera, tot in filas:
        esperado = ESPERADO["particion"].get(clave)
        p.cuadra(f"{clave} · (unidad, remolque, cuarentena, fuera)", esperado,
                 (uni, rem, cuar, fuera))
        p.cuadra(f"{clave} · los cuatro suman sus filas", tot, uni + rem + cuar + fuera)
        suma += uni + rem + cuar + fuera
    p.cuadra("TOTAL de la partición", ESPERADO["total_filas"], suma)
    ecos = [r[0] for r in db.execute(text(
        "SELECT DISTINCT eco_norm FROM cargas_proveedor WHERE vigente AND "
        "estado_resolucion = 'cuarentena' AND NOT fuera_de_flota AND eco_norm IS NOT NULL "
        "ORDER BY eco_norm")).all()]
    p.cuadra("económicos en cuarentena", ESPERADO["ecos_cuarentena"], ecos)
    p.fuente("transacción de ensayo")


def p13_destino(p, ctx, banco):
    """'motor' | 'termo' | 'indeterminado'. Un booleano mentiría sobre el 13.2% del mes."""
    db = banco.principal()
    obtenido = {d: (n, round(float(li), 2)) for d, n, li in db.execute(text(
        "SELECT destino, count(*), COALESCE(sum(litros), 0) FROM cargas_proveedor "
        "WHERE vigente AND destino IS NOT NULL GROUP BY destino")).all()}
    for destino, (n, litros) in ESPERADO["destino"].items():
        real = obtenido.get(destino, (0, 0.0))
        p.cuadra(f"{destino} · cargas", n, real[0])
        # Tolerancia de un centavo: sumar 639 floats en otro orden mueve el segundo
        # decimal, y eso no es un defecto de la ingesta.
        p.cuadra(f"{destino} · litros", litros, real[1], tol=0.05)
    tipos = db.execute(text(
        "SELECT DISTINCT u.tipo::text FROM cargas_proveedor c JOIN unidades u "
        "ON u.id = c.unidad_id WHERE c.vigente AND c.destino = 'indeterminado'")).scalars().all()
    p.cuadra("los 'indeterminado' son todos de unidades CAMION", ["CAMION"], sorted(tipos))
    p.dice("en un CAMION el termo va pegado y comparte el económico del motor: el archivo "
           "no dice a cuál de los dos fue el litro")
    p.fuente("transacción de ensayo")


def p14_bandejas(p, ctx, banco):
    """Cuarentena y fuera-de-flota son ejes independientes, no el mismo cajón."""
    db = banco.principal()
    n_cuar, comb = db.execute(text(
        "SELECT count(*), COALESCE(string_agg(DISTINCT combustible, ','), '') "
        "FROM cargas_proveedor WHERE vigente AND estado_resolucion = 'cuarentena' "
        "AND NOT fuera_de_flota")).one()
    p.cuadra("cuarentena que NO es fuera de flota", ESPERADO["cuarentena_diesel"], n_cuar)
    p.cuadra("  y todas son diésel (flota real por dar de alta)", "diesel", comb)
    n_f, li, im, tar, prod = db.execute(text(
        "SELECT count(*), COALESCE(sum(litros), 0), COALESCE(sum(importe), 0), "
        "COALESCE(string_agg(DISTINCT tarjeta_norm, ','), ''), "
        "COALESCE(string_agg(DISTINCT producto_norm, ','), '') "
        "FROM cargas_proveedor WHERE vigente AND fuera_de_flota")).one()
    p.cuadra("fuera de flota · cargas", ESPERADO["fuera_flota"], n_f)
    p.cuadra("fuera de flota · litros", ESPERADO["fuera_litros"], round(float(li), 2), tol=0.01)
    p.cuadra("fuera de flota · importe", ESPERADO["fuera_importe"], round(float(im), 2),
             tol=0.01)
    p.cuadra("fuera de flota · tarjetas", ESPERADO["fuera_tarjetas"], sorted(tar.split(",")))
    p.cuadra("fuera de flota · producto", "87 OCTANOS", prod)
    p.dice("si las 43 cayeran en el mismo cajón, `fuera_de_flota` se habría derivado de la "
           "ausencia de económico y no del producto")
    p.fuente("transacción de ensayo")


def p15_ceros_izquierda(p, ctx, banco):
    """'00140' no es 140. Un solo cero perdido invalida la ingesta entera."""
    db = banco.principal()
    for clave, esperado in ESPERADO["tarjetas"].items():
        nums = db.execute(text(
            "SELECT t.numero_txt FROM tarjetas_combustible t JOIN proveedores p "
            "ON p.id = t.proveedor_id WHERE p.clave = :c ORDER BY t.numero_txt"),
            {"c": clave}).scalars().all()
        p.cuadra(f"{clave} · tarjetas distintas", esperado, len(nums))
        largos = sorted({len(n) for n in nums})
        if clave == "XYGA":
            p.cuadra("  todas de 5 caracteres", [5], largos)
            p.cuadra("  todas empiezan en '0'", 0,
                     sum(1 for n in nums if not n.startswith("0")))
            p.dice(f"  rango: {nums[0]} .. {nums[-1]}")
        else:
            p.cuadra("  todas de 9-10 dígitos", True,
                     all(9 <= len(n) <= 10 and n.isdigit() for n in nums))
    codigos = db.execute(text(
        "SELECT DISTINCT estacion_txt FROM cargas_proveedor WHERE vigente "
        "AND estacion_txt ~ '[^0-9]' ORDER BY estacion_txt")).scalars().all()
    p.cuadra("códigos de estación no numéricos", ESPERADO["estaciones_no_numericas"],
             len(codigos))
    for esperado in ("P07196-B", "ECO50036"):
        p.cuadra(f"  '{esperado}' completo y sin truncar", True, esperado in codigos)
    p.fuente("transacción de ensayo")


def p16_anchos(p, ctx, banco):
    """El verbatim conserva las 26/25 celdas, las fantasma y los VIN raros."""
    db = banco.principal()
    anchos = {(c, w): n for c, w, n in db.execute(text(
        "SELECT p.clave, jsonb_array_length(c.fila_cruda), count(*) FROM cargas_proveedor c "
        "JOIN proveedores p ON p.id = c.proveedor_id WHERE c.vigente "
        "GROUP BY 1, 2")).all()}
    for clave, ancho in ESPERADO["ancho"].items():
        p.cuadra(f"{clave} · filas con jsonb_array_length = {ancho}",
                 ESPERADO[clave]["filas"], anchos.get((clave, ancho), 0))
        p.cuadra(f"{clave} · anchos distintos", 1,
                 len([k for k in anchos if k[0] == clave]))
    p.cuadra("OXXO · filas con las posiciones 20..25 todas nulas", ESPERADO["OXXO"]["filas"],
             db.execute(text(
                 "SELECT count(*) FROM cargas_proveedor c JOIN proveedores p "
                 "ON p.id = c.proveedor_id WHERE c.vigente AND p.clave = 'OXXO' AND "
                 "c.fila_cruda->>20 IS NULL AND c.fila_cruda->>21 IS NULL AND "
                 "c.fila_cruda->>22 IS NULL AND c.fila_cruda->>23 IS NULL AND "
                 "c.fila_cruda->>24 IS NULL AND c.fila_cruda->>25 IS NULL")).scalar())
    largos = set(db.execute(text(
        "SELECT DISTINCT length(vin_txt) FROM cargas_proveedor WHERE vigente "
        "AND vin_txt IS NOT NULL")).scalars().all())
    p.cuadra("longitudes de VIN aceptadas", ESPERADO["vin_largos"], largos)
    p.dice("las de 3 son el literal 'XXX' y la de 18 empata porque remolques.serie "
           "arrastra la misma errata: validar el formato tiraría 6 cargas legítimas")
    p.fuente("transacción de ensayo")


def p17_guardia_layout(p, ctx, banco):
    """El día que el proveedor mueva una columna, la ingesta SE DETIENE nombrándola."""
    ctx.lectura("XYGA")
    contrato = CONTRATOS["XYGA"]
    base = _hoja_nativa(ctx.datos["XYGA"][0], contrato)
    i = contrato.fila_encabezado - 1

    # (a) el proveedor corrige la errata 'Departmento'
    filas = [list(f) for f in base]
    filas[i][6] = "Departamento"
    ruta = _escribir_hoja(filas, contrato, ctx.tmp / "xyga_errata_corregida.xlsx")
    p.exige("renombrar 'Departmento' detiene la lectura",
            *_debe_detenerse(ruta, "XYGA", "Departmento"))

    # (b) el proveedor inserta una columna: el ancho deja de ser 25 y todo se corre
    filas = [list(f) + ["extra"] for f in base]
    ruta = _escribir_hoja(filas, contrato, ctx.tmp / "xyga_con_columna_de_mas.xlsx")
    p.exige("insertar una columna detiene la lectura",
            *_debe_detenerse(ruta, "XYGA", "columnas"))

    # (c) y el contrato guardado en la BASE es el mismo que el del código: si se separan,
    # el archivo pasaría una guardia y fallaría la otra sin que nadie sepa cuál manda
    ctx.exige_tablas()
    with engine.connect() as c:
        for clave, contrato_ in CONTRATOS.items():
            fila = c.execute(text(
                "SELECT n_columnas, encabezado_esperado, hoja FROM proveedores "
                "WHERE clave = :k"), {"k": clave}).first()
            if fila is None:
                p.falla(f"{clave} no está sembrado en `proveedores`")
                continue
            p.cuadra(f"{clave} · n_columnas base == código", contrato_.n_columnas, fila[0])
            p.cuadra(f"{clave} · encabezado base == código", list(contrato_.encabezado),
                     list(fila[1]))
    p.fuente("dos copias sintéticas del archivo de XYGA + la fila de `proveedores`")


def _debe_detenerse(ruta, clave, palabra):
    """(se detuvo, el mensaje). Que la lectura falle no basta: tiene que DECIR qué cambió."""
    try:
        wb = abrir_datos(leer_archivo(ruta)[0])
        try:
            leer_verbatim(wb, clave)
        finally:
            wb.close()
    except LayoutInesperado as e:
        msg = " ".join(str(e).split())
        return palabra.lower() in msg.lower(), msg[:120] + "…"
    return False, "la lectura NO se detuvo: leyó el archivo alterado como si nada"


def p18_verbatim_punta_a_punta(p, ctx, banco):
    """20 filas al azar: del Excel archivado a `fila_cruda`, celda por celda."""
    db = banco.principal()
    # La muestra es al azar pero REPRODUCIBLE: si una fila falla, hay que poder volver a
    # sacarla. `setseed` fija el generador de Postgres, que es quien ordena aquí.
    db.execute(text("SELECT setseed(0.4207)"))
    muestras = db.execute(text(
        "SELECT c.id, c.importacion_id, c.fila_num, c.fila_cruda, pr.clave "
        "FROM cargas_proveedor c JOIN proveedores pr ON pr.id = c.proveedor_id "
        "WHERE c.vigente ORDER BY random() LIMIT 20")).all()
    p.cuadra("filas muestreadas", 20, len(muestras))
    libros, revisadas, difieren = {}, 0, []
    for _id, imp_id, fila_num, cruda, clave in muestras:
        if imp_id not in libros:
            b64, sha_guardado = db.execute(text(
                "SELECT archivo_b64, sha256 FROM importaciones_proveedor WHERE id = :i"),
                {"i": imp_id}).one()
            if not b64:
                libros[imp_id] = None
                continue
            datos = base64.b64decode(b64)
            sha = hashlib.sha256(datos).hexdigest()
            p.cuadra(f"corrida {imp_id} · sha256 de archivo_b64 == sha256 guardado",
                     sha_guardado, sha)
            p.cuadra(f"corrida {imp_id} · huella del plan", ESPERADO[f"sha_{clave}"], sha[:12])
            p.cuadra(f"corrida {imp_id} · bytes", ESPERADO[f"bytes_{clave}"], len(datos))
            wb = abrir_datos(datos)
            ws = wb[CONTRATOS[clave].hoja]
            libros[imp_id] = {n: list(r) for n, r in enumerate(
                ws.iter_rows(values_only=True), start=1)}
            wb.close()
        hoja = libros[imp_id]
        if hoja is None:
            continue
        from app.ingesta import a_texto, _ajustar
        original = a_texto(_ajustar(hoja[fila_num], CONTRATOS[clave].n_columnas))
        revisadas += 1
        if original != list(cruda):
            difieren.append((_id, fila_num))
    p.cuadra("filas comparadas celda por celda contra el Excel archivado", 20, revisadas)
    p.cuadra("diferencias", 0, len(difieren))
    if difieren:
        p.dice(f"  discrepan: {difieren[:5]}")
    if revisadas < 20:
        p.dice("las corridas sin archivo_b64 son simulaciones: una simulación no guarda el "
               "archivo porque no tiene nada que demostrar")
    p.fuente("transacción de ensayo: el Excel sale de archivo_b64, no del disco")


def p19_cascada(p, ctx, banco):
    """La cascada revive Y no misatribuye. La única misatribución silenciosa posible."""
    p.exige("`from app.models import TarjetaCombustible` ya no lanza ImportError", True,
            TarjetaCombustible.__tablename__)
    for attr in ("unidad_id", "remolque_id"):
        p.exige(f"TarjetaCombustible.{attr} existe (lo lee catalogo.py:108)",
                hasattr(TarjetaCombustible, attr))

    db = banco.principal()
    # Una carga cuyo ECONÓMICO resuelve y que además trae tarjeta: es el único caso donde
    # las dos vías pueden pelearse.
    fila = db.execute(text(
        "SELECT c.tarjeta_id, c.unidad_id, c.eco_txt, c.eco_norm, c.placa_txt, c.placa_norm "
        "FROM cargas_proveedor c WHERE c.vigente AND c.tarjeta_id IS NOT NULL "
        "AND c.unidad_id IS NOT NULL AND c.resuelto_via = 'eco' LIMIT 1")).first()
    if fila is None:
        raise Omitir("no hay ninguna carga con tarjeta y con económico que resuelva: sin "
                     "ella no se puede provocar el conflicto que esta prueba busca")
    tarjeta_id, unidad_ok, eco_txt, eco_norm, placa_txt, placa_norm = fila

    r = resolver_activo(db, tarjeta_id=tarjeta_id)
    p.exige("resolver_activo(db, tarjeta_id=<real>) devuelve un Resuelto sin reventar",
            hasattr(r, "ok"), f"via={r.via} confianza={r.confianza}")

    # El vínculo DELIBERADAMENTE equivocado: se apunta la tarjeta a otra unidad y se marca
    # como confirmado, que es el escenario de una reasignación de operación mal registrada.
    otra = db.execute(text(
        "SELECT id FROM unidades WHERE id <> :u ORDER BY id LIMIT 1"),
        {"u": unidad_ok}).scalar()
    tar = db.get(TarjetaCombustible, tarjeta_id)
    tar.unidad_id, tar.remolque_id, tar.vinculo_confirmado = otra, None, True
    db.flush()
    p.dice(f"tarjeta {tar.numero_norm}: vínculo confirmado apuntando a la unidad {otra}, "
           f"mientras el económico {eco_norm} apunta a la {unidad_ok}")
    res, nota = _resolver(db, {}, eco_txt, placa_txt, eco_norm, placa_norm, tar)
    p.cuadra("la carga sigue resolviendo al activo del ECONÓMICO", unidad_ok, res.unidad_id)
    p.cuadra("no resolvió al de la tarjeta", False, res.unidad_id == otra)
    p.cuadra("y no se apoyó en la tarjeta", None, nota)
    p.dice("catalogo.py devuelve la tarjeta ANTES de mirar el económico y con confianza "
           "'alta': el orden lo impone el importador, no la cascada")
    p.fuente("transacción de ensayo, con el vínculo falso inyectado y deshecho")


def p20_indice_parcial(p, ctx, banco):
    """Insertar dos veces la misma llave viva no duplica; tras anular, el mismo insert entra."""
    db = banco.limpio()                    # no hace falta ingerir nada: es SQL puro
    prov_id = db.execute(text(
        "SELECT id FROM proveedores ORDER BY id LIMIT 1")).scalar()
    if prov_id is None:
        raise Omitir("no hay ningún proveedor sembrado; corre python -m scripts.migrate_e2")
    imp = ImportacionProveedor(
        proveedor_id=prov_id, archivo="prueba-20-indice-parcial",
        sha256="0" * 64, estado="simulada", vigente=False,
        nota="fila de prueba del verificador; vive dentro de una transacción deshecha")
    db.add(imp)
    db.flush()

    tabla = CargaProveedor.__table__
    valores = dict(
        proveedor_id=prov_id, importacion_id=imp.id, fila_num=1,
        fila_cruda=["prueba"], sha256_fila="1" * 64, revision=1, vigente=True,
        estacion_txt="@prueba20", folio_txt="@prueba20",
        momento_local=datetime(2026, 7, 1, 12, 0, 0),
        estado_resolucion="cuarentena", fuera_de_flota=False)

    def insertar():
        # El predicado `WHERE vigente` se REPITE en la sentencia: sin él, Postgres contesta
        # "no unique or exclusion constraint matching the ON CONFLICT specification".
        return db.execute(
            pg_insert(tabla).values(**valores).on_conflict_do_nothing(
                index_elements=["proveedor_id", "estacion_txt", "folio_txt"],
                index_where=text("vigente")).returning(tabla.c.id)).scalar()

    primero = insertar()
    p.exige("el primer INSERT entra", primero is not None, f"id={primero}")
    p.cuadra("el segundo INSERT de la misma llave viva no inserta nada", None, insertar())
    db.execute(text("UPDATE cargas_proveedor SET vigente = FALSE WHERE id = :i"),
               {"i": primero})
    tercero = insertar()
    p.exige("tras anular (vigente=False), el MISMO insert entra", tercero is not None,
            f"id={tercero}")
    p.cuadra("y quedan las dos versiones consultables", 2, db.execute(text(
        "SELECT count(*) FROM cargas_proveedor WHERE estacion_txt = '@prueba20'")).scalar())
    p.dice("sin esto, 'anular una corrida y reimportar el archivo' no funcionaría")
    p.fuente("transacción de ensayo, sin ingerir ningún archivo")


def p21_e3_en_seco(p, ctx, banco):
    """La ventana de ±6 h de E3 tiene que ser un barrido de índice, no un seq scan."""
    db = banco.principal()
    # OJO: `ordenes_despacho` NO tiene unidad_id — la unidad llega por la solicitud. Se
    # deja escrito porque es exactamente lo primero con lo que E3 se va a topar.
    consulta = ("SELECT c.id, o.id FROM ordenes_despacho o "
                "JOIN solicitudes_recarga s ON s.id = o.solicitud_id "
                "JOIN cargas_proveedor c ON c.unidad_id = s.unidad_id "
                "AND c.momento_ref BETWEEN o.autorizada_en - interval '6 hours' "
                "                      AND o.autorizada_en + interval '6 hours'")
    db.execute(text("ANALYZE cargas_proveedor"))     # sin estadísticas el plan es ficción
    plan = "\n".join(db.execute(text("EXPLAIN " + consulta)).scalars().all())
    for l in plan.splitlines():
        p.dice(f"  {l}")
    p.exige("el plan usa ix_cargas_unidad_momento", "ix_cargas_unidad_momento" in plan)
    p.exige("y NO barre cargas_proveedor de punta a punta",
            "Seq Scan on cargas_proveedor" not in plan)
    definicion = db.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_cargas_unidad_momento'")).scalar()
    p.exige("el índice está ordenado (unidad_id, momento_ref) y es parcial",
            "(unidad_id, momento_ref)" in (definicion or "")
            and "unidad_id IS NOT NULL" in (definicion or ""), definicion or "no existe")
    p.dice("`ordenes_despacho` no lleva unidad_id: la unidad viene por solicitud_id, y eso "
           "es lo primero con lo que E3 se va a topar")
    p.fuente("transacción de ensayo, con ANALYZE previo")


def p22_basura_sin_donde_sumarse(p, ctx, banco):
    """Los Kms de Xyga se conservan, pero fuera del alcance de cualquier GROUP BY."""
    with engine.connect() as c:
        km = c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'cargas_proveedor' AND column_name ILIKE '%km%'")).scalars().all()
    p.cuadra("columnas de cargas_proveedor que digan 'km'", [], list(km))
    l = ctx.lectura("XYGA")
    kms = Counter(f["fila_cruda"][17] for f in l.filas)
    for valor, n in ESPERADO["kms"].items():
        p.cuadra(f"archivo · fila_cruda[17] == '{valor}'", n, kms.get(valor, 0))
    p.cuadra("valores distintos de Kms en todo el mes", len(ESPERADO["kms"]), len(kms))
    p.fuente("information_schema + el archivo de XYGA")

    db = banco.principal()
    guardados = {v: n for v, n in db.execute(text(
        "SELECT c.fila_cruda->>17, count(*) FROM cargas_proveedor c "
        "JOIN proveedores p ON p.id = c.proveedor_id "
        "WHERE c.vigente AND p.clave = 'XYGA' GROUP BY 1")).all()}
    for valor, n in ESPERADO["kms"].items():
        p.cuadra(f"base · recuperables desde fila_cruda ('{valor}')", n, guardados.get(valor, 0))
    p.dice("son basura demostrada: 261 filas dicen Kms=123 con Kms/Lt=0, o sea que el "
           "archivo se contradice a sí mismo")
    p.fuente("transacción de ensayo")


def p23_reversion(p, ctx, banco):
    """Borrar una corrida y volver a correrla da EXACTAMENTE lo mismo."""
    db = banco.limpio("OXXO")
    antes = db.execute(text(
        "SELECT count(*), COALESCE(sum(litros), 0) FROM cargas_proveedor c "
        "JOIN proveedores p ON p.id = c.proveedor_id WHERE c.vigente AND p.clave = 'OXXO'")).one()
    shas_antes = set(db.execute(text(
        "SELECT c.sha256_fila FROM cargas_proveedor c JOIN proveedores p "
        "ON p.id = c.proveedor_id WHERE c.vigente AND p.clave = 'OXXO'")).scalars().all())
    imp_id = db.execute(text(
        "SELECT c.importacion_id FROM cargas_proveedor c JOIN proveedores p "
        "ON p.id = c.proveedor_id WHERE c.vigente AND p.clave = 'OXXO' LIMIT 1")).scalar()
    if imp_id is None:
        raise Omitir("no hay ninguna corrida de OXXO que revertir")

    # La reversión dura, la del camino (a). Desde E3 hay que soltar antes los asientos que
    # cuelgan de estas cargas: la FK es RESTRICT a propósito, para que nadie se lleve el libro
    # mayor por delante sin enterarse. Es exactamente lo que `revertir_importacion.py` exige en
    # producción cuando detecta una tabla ajena, y lo que esta prueba tiene que ensayar también.
    db.execute(text(
        "DELETE FROM asientos_consumo WHERE carga_id IN "
        "(SELECT id FROM cargas_proveedor WHERE importacion_id = :i)"), {"i": imp_id})
    borradas = db.execute(text(
        "DELETE FROM cargas_proveedor WHERE importacion_id = :i"), {"i": imp_id}).rowcount
    db.execute(text("DELETE FROM importaciones_proveedor WHERE id = :i"), {"i": imp_id})
    db.commit()
    p.cuadra("cargas borradas por la reversión", antes[0], borradas)
    p.cuadra("cargas de OXXO tras revertir", 0, db.execute(text(
        "SELECT count(*) FROM cargas_proveedor c JOIN proveedores p "
        "ON p.id = c.proveedor_id WHERE p.clave = 'OXXO'")).scalar())

    with contextlib.redirect_stdout(_io.StringIO()):
        importar(db, "OXXO", ctx.rutas["OXXO"])
    despues = db.execute(text(
        "SELECT count(*), COALESCE(sum(litros), 0) FROM cargas_proveedor c "
        "JOIN proveedores p ON p.id = c.proveedor_id WHERE c.vigente AND p.clave = 'OXXO'")).one()
    shas_despues = set(db.execute(text(
        "SELECT c.sha256_fila FROM cargas_proveedor c JOIN proveedores p "
        "ON p.id = c.proveedor_id WHERE c.vigente AND p.clave = 'OXXO'")).scalars().all())
    p.cuadra("cargas tras reimportar", antes[0], despues[0])
    p.cuadra("litros tras reimportar", round(float(antes[1]), 2), round(float(despues[1]), 2),
             tol=0.01)
    p.cuadra("el CONJUNTO de sha256_fila es idéntico", True, shas_antes == shas_despues)
    p.cuadra("  (tamaño del conjunto)", len(shas_antes), len(shas_despues))
    p.dice("es la prueba de que la canonicalización del hash es estable y la corrida "
           "reproducible")
    p.fuente("transacción de ensayo: importar, borrar y volver a importar, todo deshecho")


def _en_cp1252(argumentos):
    """Corre un comando con la consola forzada a cp1252, como la de Windows."""
    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "cp1252"
    r = subprocess.run([sys.executable, *argumentos], cwd=str(RAIZ), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=entorno)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def p24_consola(p, ctx, banco):
    """Lo que estos scripts imprimen tiene que llegar entero a una consola cp1252."""
    banco.cerrar()
    for nombre in SCRIPTS_NUEVOS:
        ruta = RAIZ / "scripts" / nombre
        if not ruta.exists():
            p.falla(f"{nombre} no existe")
            continue
        texto = ruta.read_text(encoding="utf-8")
        p.exige(f"{nombre} lleva sys.stdout.reconfigure",
                'sys.stdout.reconfigure(encoding="utf-8"' in texto
                or "sys.stdout.reconfigure(encoding='utf-8'" in texto)

    # LOS DOS CONTROLES, primero, porque sin ellos las comprobaciones de abajo pasarían
    # igual en una consola que ya fuera UTF-8 y no demostrarían nada.
    #
    # El carácter elegido no es caprichoso: '─' (U+2500) es el que `import_proveedor`
    # imprime en sus separadores de sección ('── CUARENTENA ───') y NO existe en cp1252.
    # Los acentos españoles, en cambio, SÍ caben en cp1252, así que probar solo con ellos
    # no distinguiría un script blindado de uno que no lo está. Va escapado para que el
    # argumento del subproceso no dependa a su vez de la codificación de la línea de órdenes.
    caja = "'\\u2500' * 3"
    codigo_a, salida_a = _en_cp1252(["-c", f"print({caja})"])
    p.exige("control A · sin reconfigure, el separador '─' revienta en cp1252",
            codigo_a != 0 and "UnicodeEncodeError" in salida_a,
            "si esto NO falla, la consola de la prueba no es cp1252 y lo de abajo no "
            "prueba nada")
    codigo_b, salida_b = _en_cp1252([
        "-c", "import sys; sys.stdout.reconfigure(encoding='utf-8', errors='replace'); "
              f"print({caja})"])
    p.exige("control B · con reconfigure, el mismo '─' sale entero",
            codigo_b == 0 and "─" in salida_b)

    # Y ahora los scripts de verdad. NINGUNO de los tres comandos escribe una fila:
    # migrate_e2 es idempotente; el importador se detiene en la guardia de layout antes de
    # tocar la base; y --help ni se conecta. Correr aquí un `--dry-run` de verdad sí
    # escribiría (deja su testigo 'simulada') y este verificador promete no escribir nada.
    ctx.lectura("XYGA")
    contrato = CONTRATOS["XYGA"]
    filas = [list(f) for f in _hoja_nativa(ctx.datos["XYGA"][0], contrato)]
    filas[contrato.fila_encabezado - 1][6] = "Departamento"
    roto = _escribir_hoja(filas, contrato, ctx.tmp / "xyga_para_la_prueba_24.xlsx")

    antes = _contar_importaciones()
    casos = (
        ("migrate_e2", ["-m", "scripts.migrate_e2"], 0, "Migración"),
        # El importador REAL, imprimiendo un motivo acentuado y deteniéndose sin escribir.
        ("import_proveedor (se detiene por layout)",
         ["-m", "scripts.import_proveedor", "--proveedor", "XYGA", "--archivo", str(roto)],
         1, "cambió"),
        ("verificar_e2 --help", ["-m", "scripts.verificar_e2", "--help"], 0, "transacción"),
    )
    for etiqueta, argumentos, esperado, palabra in casos:
        codigo, salida = _en_cp1252(argumentos)
        p.cuadra(f"{etiqueta} · código de salida", esperado, codigo)
        p.exige(f"{etiqueta} · sin UnicodeEncodeError", "UnicodeEncodeError" not in salida)
        p.exige(f"{etiqueta} · el texto acentuado llega entero ('{palabra}')",
                palabra in salida)
    p.cuadra("y los tres juntos no escribieron una sola fila", antes, _contar_importaciones())
    p.dice("lo que se protege son los motivos que lee una persona: 'cuarentena: el "
           "económico no está en el catálogo'")
    p.fuente("cinco subprocesos con PYTHONIOENCODING=cp1252; ninguno escribe en la base")


def _contar_importaciones():
    """Cuántas corridas hay guardadas. La prueba 24 lanza el importador de verdad y tiene
    que demostrar que no dejó ni un testigo detrás."""
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM importaciones_proveedor")).scalar()


PRUEBAS = (
    (1, "MIGRACIÓN DOS VECES SEGUIDAS, Y UNA REVERSIÓN LIMPIA", p01_migracion),
    (2, "REPRODUCCIÓN AL CENTAVO", p02_reproduccion),
    (3, "CERO PÉRDIDA DE FILAS", p03_cero_perdida),
    (4, "UNICIDAD DE LA LLAVE NATURAL", p04_llave),
    (5, "IDEMPOTENCIA POR ARCHIVO", p05_idempotencia_archivo),
    (6, "IDEMPOTENCIA POR SOLAPE PARCIAL", p06_solape_parcial),
    (7, "CORRECCIÓN SIN DESTRUCCIÓN", p07_correccion),
    (8, "LA RÁFAGA REAL DEL T203 NO SE PIERDE", p08_rafaga),
    (9, "DETECTOR DE CASI-DUPLICADOS, CALIBRADO", p09_casi_duplicados),
    (10, "LA HORA DE XYGA NO SE TIRA", p10_hora_xyga),
    (11, "LAS DOS FECHAS DE OXXO SON DATOS DISTINTOS", p11_dos_fechas_oxxo),
    (12, "PARTICIÓN DE ESTADOS", p12_particion),
    (13, "DESTINO: MOTOR, TERMO E INDETERMINADO", p13_destino),
    (14, "SEPARACIÓN DE BANDEJAS", p14_bandejas),
    (15, "TIPOS Y CEROS A LA IZQUIERDA", p15_ceros_izquierda),
    (16, "ANCHOS DEL VERBATIM", p16_anchos),
    (17, "GUARDIA DE LAYOUT", p17_guardia_layout),
    (18, "VERBATIM DE PUNTA A PUNTA", p18_verbatim_punta_a_punta),
    (19, "LA CASCADA REVIVE, Y NO MISATRIBUYE", p19_cascada),
    (20, "ÍNDICE PARCIAL Y ANULACIÓN", p20_indice_parcial),
    (21, "E3 EN SECO: LA VENTANA DE ±6 H", p21_e3_en_seco),
    (22, "EL DATO BASURA NO TIENE DÓNDE SUMARSE", p22_basura_sin_donde_sumarse),
    (23, "REVERSIÓN REPRODUCIBLE", p23_reversion),
    (24, "CONSOLA DE WINDOWS (cp1252)", p24_consola),
)


# ─────────────────────────────────────────────────────────────────────────────
# LA CORRIDA
# ─────────────────────────────────────────────────────────────────────────────

def _encabezado(ctx, ensayo):
    print("=" * 78)
    print("E2 · VERIFICACIÓN DE LA INGESTA VERBATIM")
    print("-" * 78)
    for clave, ruta in ctx.rutas.items():
        if clave in ctx.lecturas:
            datos, sha = ctx.datos[clave]
            print(f"  {clave:<5} {os.path.basename(ruta)}")
            print(f"        {len(datos):,} bytes · sha256 {sha[:16]}… · "
                  f"{len(ctx.lecturas[clave].filas)} filas")
        else:
            print(f"  {clave:<5} SIN LEER: {ctx.error_lectura.get(clave)}")
    faltan = [t for t in TABLAS_E2 if t not in ctx.tablas]
    print(f"  tablas de E2: {'las seis presentes' if not faltan else 'FALTAN ' + ', '.join(faltan)}")
    if ctx.tablas and not faltan:
        with engine.connect() as c:
            vivas = c.execute(text(
                "SELECT count(*) FROM cargas_proveedor WHERE vigente")).scalar()
        print(f"  cargas guardadas de verdad en la base: {vivas}")
        if ensayo:
            print("  modo ENSAYO: la ingesta se ejecuta dentro de una transacción que se")
            print("               DESHACE al terminar. La base no cambia ni una fila.")
        else:
            print("  modo --sin-ensayo: no se abre ninguna transacción; las pruebas que")
            print("               necesitan cargas guardadas saldrán OMITIDAS.")


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
        # Con --solo el informe NO puede hablar de la etapa: habla de lo que se corrió.
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
                # Se falló Y además quedó a medias: las dos cosas hay que decirlas.
                print(f"         · y encima quedó incompleta: {p.omitida}")
        print("\n  LA INGESTA NO ES CONFIABLE. Corregir antes de correr la importación de")
        print("  verdad: una carga mal guardada no se nota en los totales.")
        return 1

    if omitidas or parcial:
        print("\n  Todo lo que se probó cuadra, pero quedan pruebas sin ejecutar.")
        print("  El plan las declaró OBLIGATORIAS: la etapa no está verificada hasta que")
        print("  se puedan correr todas.")
        return 0

    print(f"\n  Las {len(PRUEBAS)} pruebas obligatorias del plan pasan.")
    print("  La ingesta reproduce el mes al centavo, no pierde filas, no deduplica cargas")
    print("  legítimas y no misatribuye. Nada se escribió en la base.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="E2 · Ejecuta las 24 pruebas obligatorias del plan.",
        epilog="No escribe nada: la ingesta de ensayo corre dentro de una transacción "
               "que se deshace al terminar.")
    ap.add_argument("--oxxo", default=os.path.join(DESCARGAS, "Despachos.xlsx"))
    ap.add_argument("--xyga", default=os.path.join(
        DESCARGAS, "REPORTE+DE+CONSUMOS_06_08_2026.xlsx.xls"))
    ap.add_argument("--sin-ensayo", dest="sin_ensayo", action="store_true",
                    help="no abrir la transacción de ensayo; omite las pruebas que "
                         "necesitan cargas guardadas")
    ap.add_argument("--solo", type=int, nargs="+", metavar="N",
                    help="correr solo estas pruebas (para depurar el verificador)")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="verificar_e2_"))
    ctx = Contexto({"OXXO": a.oxxo, "XYGA": a.xyga}, tmp)
    banco = Banco(ctx, activo=not a.sin_ensayo)
    _encabezado(ctx, not a.sin_ensayo)

    resultados = []
    try:
        for numero, titulo, fn in PRUEBAS:
            if a.solo and numero not in a.solo:
                continue
            resultados.append(correr(numero, titulo, fn, ctx, banco))
    finally:
        banco.cerrar()             # el rollback pase lo que pase
        shutil.rmtree(tmp, ignore_errors=True)

    codigo = _veredicto(resultados, parcial=bool(a.solo))
    with engine.connect() as c:
        vivas = c.execute(text("SELECT count(*) FROM cargas_proveedor WHERE vigente")).scalar() \
            if "cargas_proveedor" in ctx.tablas else "n/a"
    print(f"\n  Comprobación final: cargas vigentes en la base = {vivas} "
          f"(las mismas que antes de correr esto).")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
