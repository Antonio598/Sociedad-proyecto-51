"""Verifica la segunda puerta del importador de escaneos: el ODÓMETRO DE CIERRE.

QUÉ SE ESTÁ PROBANDO
`EscaneoMotor.archivo` es unique, así que el importador nunca mete dos veces el mismo
NOMBRE. Eso no dice nada del mismo PDF guardado con dos nombres distintos, y por ahí
entraron dos períodos repetidos: T203 (ids 66 y 67) y T228 (ids 85 y 86), idénticos campo
por campo. En T228 el nombre del segundo archivo dice 12.06 y su período cierra el 01.06:
el nombre miente respecto al contenido, así que ninguna heurística sobre el nombre lo
habría atrapado. `import_escaneos.llave_odometro()` cierra esa puerta.

LA REGLA, Y POR QUÉ ES SÓLIDA
`odometro_total` es el contador de por vida del motor, estrictamente creciente dentro de una
unidad: un escaneo con km > 0 no puede dejarlo donde estaba. Dos lecturas de la misma unidad
que cierran en el mismo kilómetro SON la misma lectura. La prueba 1 mide justamente eso
contra los 43 escaneos reales: la regla colisiona en 2 pares y ni uno más.

LA TRANSACCIÓN DE ENSAYO
La base es la VIVA del cliente. Las pruebas 5, 6 y 7 corren el importador DE VERDAD —el
mismo `import_escaneos.main()`, sin trucos— dentro de una transacción que se deshace al
salir. `join_transaction_mode="create_savepoint"` es lo que permite que el importador haga
sus propios `commit()` sin cerrar la transacción de fuera: el commit libera un SAVEPOINT y
el rollback externo sigue pudiendo deshacerlo todo. La prueba 8 lo comprueba al final,
leyendo la base otra vez: 43 filas y las 4 conocidas intactas.

TRES ESTADOS Y NUNCA OTRO. PASA (se ejecutó y cuadró), FALLA (se ejecutó y no cuadró, con el
esperado al lado del obtenido) y OMITIDA (no se pudo ejecutar, y se dice por qué). Una
omitida jamás se cuenta como buena. Termina en sys.exit(1) si algo falla.

Uso:
    python -m scripts.verificar_escaneo_duplicados
"""

import shutil
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path

# La consola de Windows es cp1252 y revienta con los acentos en cuanto se redirige la salida.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import engine
from app.models import EscaneoMotor
from scripts import import_escaneos as imp

# Los dos pares que ya están en la base, y que son la razón de ser de todo esto.
PARES_CONOCIDOS = [("T203", 66, 67), ("T228", 85, 86)]
TOTAL_ESPERADO = 43


class Omitir(Exception):
    """No se pudo ejecutar la prueba. Se dice por qué y NO se cuenta como buena."""


class Prueba:
    def __init__(self, n: int, titulo: str):
        self.n, self.titulo = n, titulo
        self.estado, self.detalle = "PASA", []

    def cuadra(self, que: str, esperado, obtenido):
        if esperado == obtenido:
            self.detalle.append(f"    ok  {que}: {obtenido}")
        else:
            self.estado = "FALLA"
            self.detalle.append(f"    NO  {que}: se esperaba {esperado!r} y se obtuvo {obtenido!r}")

    def nota(self, texto: str):
        self.detalle.append(f"    ·   {texto}")


RESULTADOS: list[Prueba] = []


@contextmanager
def registrar(n: int, titulo: str):
    p = Prueba(n, titulo)
    RESULTADOS.append(p)
    try:
        yield p
    except Omitir as e:
        p.estado, p.detalle = "OMITIDA", [f"    ··  {e}"]
    except Exception as e:
        p.estado = "FALLA"
        p.detalle.append(f"    NO  reventó: {type(e).__name__}: {e}")
    print(f"[{p.estado:7}] {n}. {titulo}")
    for d in p.detalle:
        print(d)


@contextmanager
def ensayo():
    """Una sesión cuya escritura se DESHACE al salir. La base queda como estaba."""
    conn = engine.connect()
    tx = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint",
                 autoflush=False, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        tx.rollback()
        conn.close()


def importar_callado(carpeta: Path, db) -> tuple[dict, str]:
    """Corre el importador real y devuelve (recuento, lo que imprimió)."""
    buf = StringIO()
    with redirect_stdout(buf):
        r = imp.main(carpeta, session=db)
    return r, buf.getvalue()


def carpeta_de(nombre_pdf: str) -> Path:
    """La carpeta donde vive ese PDF, o una omisión si no está."""
    raiz = imp.RAIZ_DEFECTO
    if not raiz.exists():
        raise Omitir(f"no existe la carpeta de PDF ({raiz}): sin los archivos reales esta "
                     f"prueba no se puede ejecutar")
    for p in raiz.rglob(nombre_pdf):
        return p.parent
    raise Omitir(f"no se encontró «{nombre_pdf}» bajo {raiz}")


# ── 1 ───────────────────────────────────────────────────────────────────────
with registrar(1, "La regla NO tiene falsos positivos sobre los 43 escaneos reales") as p:
    with engine.connect() as c:
        filas = c.execute(text(
            "SELECT e.id, e.unidad_id, u.clave, e.odometro_total, e.km "
            "FROM escaneos_motor e JOIN unidades u ON u.id = e.unidad_id "
            "ORDER BY e.id")).all()
    p.cuadra("escaneos en la base", TOTAL_ESPERADO, len(filas))
    grupos: dict = {}
    for f in filas:
        k = imp.llave_odometro(f.unidad_id, f.odometro_total, f.km)
        if k is not None:
            grupos.setdefault(k, []).append((f.clave, f.id))
    choques = sorted((v for v in grupos.values() if len(v) > 1), key=lambda v: v[0][0])
    p.cuadra("pares que la regla marca como repetidos", 2, len(choques))
    p.cuadra("y son exactamente los conocidos",
             PARES_CONOCIDOS,
             [(g[0][0], g[0][1], g[1][1]) for g in choques])
    p.nota(f"{len(filas)} escaneos → {len(grupos)} períodos distintos: "
           f"{len(filas) - len(grupos)} filas sobran")

# ── 2, 3, 4 ─────────────────────────────────────────────────────────────────
with registrar(2, "Sin odómetro no hay llave (la columna es nullable)") as p:
    p.cuadra("llave_odometro(1, None, 500)", None, imp.llave_odometro(1, None, 500))

with registrar(3, "Con km <= 0 no hay llave: parada, la unidad SÍ puede repetir odómetro") as p:
    p.cuadra("llave_odometro(1, 1000.0, 0)", None, imp.llave_odometro(1, 1000.0, 0))
    p.cuadra("llave_odometro(1, 1000.0, -5)", None, imp.llave_odometro(1, 1000.0, -5))
    p.nota("es el único caso en que dos lecturas distintas cierran en el mismo kilómetro "
           "sin ser la misma, y por eso queda fuera de la regla")

with registrar(4, "Redondea a 2 decimales, igual que rendimiento._serie()") as p:
    p.cuadra("misma unidad, centímetros de diferencia → misma llave",
             imp.llave_odometro(7, 142916.4300001, 100), imp.llave_odometro(7, 142916.43, 100))
    p.cuadra("unidades distintas, mismo odómetro → llaves distintas",
             False, imp.llave_odometro(7, 142916.43, 100) == imp.llave_odometro(8, 142916.43, 100))
    p.cuadra("un kilómetro de diferencia → llaves distintas",
             False, imp.llave_odometro(7, 142916.43, 100) == imp.llave_odometro(7, 142917.43, 100))

# ── 5 ───────────────────────────────────────────────────────────────────────
with registrar(5, "Sobre la carpeta REAL: cero rechazos indebidos y lo nuevo sí entra") as p:
    # Esta prueba destapó algo que no se sabía: la carpeta tiene 45 PDF y la base 43. Los dos
    # que faltan son de T147 (05.06 y 10.06), períodos distintos entre sí y del que ya está,
    # y nunca se importaron. Aquí sirven de prueba en vivo de que la puerta 2 NO estorba: dos
    # lecturas legítimas de una unidad QUE YA TIENE ESCANEO pasan sin que nadie las toque.
    carpeta = imp.RAIZ_DEFECTO
    if not carpeta.exists():
        raise Omitir(f"no existe {carpeta}")
    with ensayo() as db:
        antes = {a for (a,) in db.execute(select(EscaneoMotor.archivo))}
        r, _ = importar_callado(carpeta, db)
        entraron = sorted({a for (a,) in db.execute(select(EscaneoMotor.archivo))} - antes)
        p.cuadra("saltados por nombre (todo lo que ya estaba)", TOTAL_ESPERADO, r["saltados"])
        p.cuadra("rechazados por odómetro", 0, len(r["repetidos"]))
        p.cuadra("entraron los pendientes de T147",
                 ["T147 05.06.2026.pdf", "T147 10.06.2026.pdf"], entraron)
        p.cuadra("total tras la corrida", TOTAL_ESPERADO + 2, r["total"])
        p.nota("los 43 ya guardados los atrapa la puerta 1; los 2 nuevos cierran en odómetros "
               "distintos (832,107.80 y 834,411.56) y la puerta 2 los deja pasar")
        p.nota("OJO: esos 2 PDF siguen SIN importar en la base real — este ensayo se deshace")

# ── 6 ───────────────────────────────────────────────────────────────────────
with registrar(6, "REPRODUCCIÓN DEL DEFECTO: borrada la fila 67, el odómetro sí lo rechaza") as p:
    carpeta = carpeta_de("T203 07.06.2026.pdf")
    with ensayo() as db:
        # Se borra SOLO la copia. Así «T203 08.06.2026.pdf» deja de estar en la base por
        # nombre y la puerta 1 lo deja pasar: exactamente el escenario que creó el defecto.
        db.execute(text("DELETE FROM escaneos_motor WHERE id = 67"))
        db.commit()
        antes = db.scalar(select(func.count()).select_from(EscaneoMotor))
        p.cuadra("filas tras borrar la copia", TOTAL_ESPERADO - 1, antes)

        r, salida = importar_callado(carpeta, db)
        p.cuadra("insertados", 0, r["nuevos"])
        p.cuadra("saltados por nombre (el original, id 66)", 1, r["saltados"])
        p.cuadra("rechazados por odómetro", 1, len(r["repetidos"]))
        despues = db.scalar(select(func.count()).select_from(EscaneoMotor))
        p.cuadra("la fila NO volvió a entrar", TOTAL_ESPERADO - 1, despues)

        # Y se rechazó DICIÉNDOLO: el requisito era no fallar en silencio.
        p.cuadra("el aviso nombra el archivo que llegó",
                 True, "T203 08.06.2026.pdf" in salida)
        p.cuadra("y contra cuál chocó", True, "T203 07.06.2026.pdf" in salida)
        p.cuadra("y dice el odómetro", True, "1,076,211.00 km" in salida)

# ── 7 ───────────────────────────────────────────────────────────────────────
with registrar(7, "Dos copias en la MISMA corrida: entra una, se rechaza la otra") as p:
    origen = carpeta_de("T203 07.06.2026.pdf") / "T203 07.06.2026.pdf"
    tmp = Path(tempfile.mkdtemp(prefix="ensayo_escaneos_"))
    try:
        # Nombres nuevos los dos: ninguno está en la base, así que la puerta 1 no interviene
        # y la 2 tiene que apañárselas sola contra lo insertado en esta misma corrida.
        shutil.copy2(origen, tmp / "T203 20.09.2026.pdf")
        shutil.copy2(origen, tmp / "T203 21.09.2026.pdf")
        with ensayo() as db:
            db.execute(text("DELETE FROM escaneos_motor WHERE id IN (66, 67)"))
            db.commit()
            r, salida = importar_callado(tmp, db)
            p.cuadra("insertados (la primera copia SÍ entra)", 1, r["nuevos"])
            p.cuadra("saltados por nombre", 0, r["saltados"])
            p.cuadra("rechazados por odómetro", 1, len(r["repetidos"]))
            p.cuadra("el rechazo apunta a la copia recién insertada",
                     True, "T203 20.09.2026.pdf" in salida)
            p.nota("una lectura legítimamente nueva no se bloquea: solo se bloquea la segunda")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ── 8 ───────────────────────────────────────────────────────────────────────
with registrar(8, "La base quedó INTACTA: los ensayos se deshicieron enteros") as p:
    with engine.connect() as c:
        p.cuadra("filas en escaneos_motor", TOTAL_ESPERADO,
                 c.execute(text("SELECT count(*) FROM escaneos_motor")).scalar_one())
        vivas = c.execute(text(
            "SELECT id, odometro_total FROM escaneos_motor "
            "WHERE id IN (66, 67, 85, 86) ORDER BY id")).all()
        p.cuadra("las 4 filas de los pares conocidos siguen ahí",
                 [(66, 1076211.0), (67, 1076211.0), (85, 142916.43), (86, 142916.43)],
                 [(v.id, v.odometro_total) for v in vivas])

# ── veredicto ───────────────────────────────────────────────────────────────
fallan = [p for p in RESULTADOS if p.estado == "FALLA"]
omitidas = [p for p in RESULTADOS if p.estado == "OMITIDA"]
pasan = [p for p in RESULTADOS if p.estado == "PASA"]

print(f"\n{'─' * 78}")
print(f"VEREDICTO: {len(pasan)} pasan · {len(fallan)} fallan · {len(omitidas)} omitidas "
      f"(de {len(RESULTADOS)})")
if omitidas:
    print("  Sin ejecutar (NO se dan por buenas): " +
          ", ".join(str(p.n) for p in omitidas))
if fallan:
    print("  FALLAN: " + ", ".join(f"{p.n} ({p.titulo})" for p in fallan))
    sys.exit(1)
print("  La puerta del odómetro funciona y no rechaza nada legítimo.")
if omitidas:
    sys.exit(1)
