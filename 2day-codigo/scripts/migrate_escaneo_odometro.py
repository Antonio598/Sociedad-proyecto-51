"""Cierra en la BASE la puerta que `scripts/import_escaneos.py` cierra en el código:
un período de una unidad, UNA fila.

Crea UN índice único parcial y nada más. Ni un ALTER, ni una columna, ni un dato tocado.
Revertir es un DROP INDEX, y el script lo imprime literal al final.

  CREATE UNIQUE INDEX ux_escaneos_odometro ON escaneos_motor
      (unidad_id, round(odometro_total::numeric, 2))
      WHERE odometro_total IS NOT NULL AND km > 0

POR QUÉ ESE ÍNDICE Y NO OTRO
`EscaneoMotor.archivo` ya es unique, pero el nombre del archivo no identifica el período: el
mismo PDF guardado dos veces con nombres distintos pasa esa puerta sin despeinarse, y así
entraron T203 (ids 66/67) y T228 (ids 85/86). En T228 el nombre del segundo dice 12.06 y su
período cierra el 01.06 — el nombre miente, así que no hay heurística de nombres que valga.
Lo que SÍ identifica el período es `odometro_total`: el contador de por vida del motor,
estrictamente creciente dentro de una unidad. Un escaneo con km > 0 no puede dejarlo donde
estaba, así que dos lecturas de la misma unidad que cierran en el mismo kilómetro son la
misma lectura.

POR QUÉ ES PARCIAL, Y QUÉ TOLERA CADA MITAD DEL PREDICADO
  · `odometro_total IS NOT NULL` — la columna es nullable. Hoy los 43 escaneos lo traen,
    pero un formato futuro podría no darlo, y un escaneo sin odómetro no debe quedar
    bloqueado por otro sin odómetro: en un UNIQUE ordinario dos NULL ya se consideran
    distintos, pero el predicado lo deja explícito y saca esas filas del índice.
  · `km > 0` — ESTE es el que de verdad hace falta, y no es cosmético. La regla se apoya en
    que el odómetro avanza; con km = 0 la unidad NO se movió y dos extracciones distintas de
    una unidad parada SÍ pueden cerrar en el mismo kilómetro sin ser la misma. Sin este
    predicado, la restricción rechazaría lecturas legítimas. Hoy no hay ninguna fila con
    km <= 0; el predicado está para que el día que la haya no reviente la importación.

POR QUÉ REDONDEA A 2 DECIMALES
Es exactamente lo que hacen las otras dos defensas: `app/rendimiento.py::_serie()` al leer y
`import_escaneos.llave_odometro()` al importar. Dos lecturas del mismo período no pueden
diferir en centímetros, y que las tres entiendan lo mismo por «igual» evita el peor de los
casos: que el importador acepte algo que la base rechace después con un error críptico.
`round(numeric, int)` y el cast de double precision a numeric son IMMUTABLE, así que la
expresión se puede indexar.

POR QUÉ NO VA EN `__table_args__`
Un `UniqueConstraint` no admite predicado, igual que en `scripts/migrate_e2.py` con
`ux_cargas_llave` y en `migrate_e3.py` con sus tres. Los índices parciales NO únicos sí se
declaran en el modelo; los únicos se crean aquí con SQL crudo.

ESTA MIGRACIÓN SE NIEGA A CORRER SI HAY REPETIDOS
Postgres rechazaría el CREATE con un error propio y a medio camino. En vez de eso, el script
LOS BUSCA PRIMERO y, si los hay, los lista con nombre y apellido, no toca nada y sale con
código 1 diciendo qué hacer. Hoy la base tiene 2 pares, así que hasta resolverlos esta
migración no se aplica — a propósito:

    python -m scripts.escaneos_duplicados          (los lista; no borra nada)
    python -m scripts.escaneos_duplicados --eliminar   (los borra, tras confirmar)

IDEMPOTENTE, Y SE NOTA: la segunda corrida dice «ya existía» en vez de imprimir OK pase lo
que pase, que es lo único que distingue la primera corrida de la segunda.

Uso:
    python -m scripts.migrate_escaneo_odometro
"""

import sys
from pathlib import Path

# La consola de Windows es cp1252 y revienta con los acentos ('índice', 'odómetro').
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

INDICE = "ux_escaneos_odometro"

# El predicado y la expresión están explicados arriba. Se escriben UNA vez y se reutilizan
# para buscar repetidos, para que lo que busca el script y lo que exige el índice no puedan
# separarse nunca.
LLAVE = "unidad_id, round(odometro_total::numeric, 2)"
PREDICADO = "odometro_total IS NOT NULL AND km > 0"

DDL = (f"CREATE UNIQUE INDEX {INDICE} ON escaneos_motor ({LLAVE}) "
       f"WHERE {PREDICADO}")

SQL_CHOQUES = f"""
SELECT u.clave,
       round(e.odometro_total::numeric, 2) AS odometro,
       count(*)                            AS n,
       string_agg(e.id::text,  ', ' ORDER BY e.id) AS ids,
       string_agg(e.archivo,   ' | ' ORDER BY e.id) AS archivos,
       string_agg(e.periodo_fin::text, ' | ' ORDER BY e.id) AS cierres
  FROM escaneos_motor e
  JOIN unidades u ON u.id = e.unidad_id
 WHERE {PREDICADO}
 GROUP BY {LLAVE.replace('unidad_id', 'e.unidad_id')}, u.clave
HAVING count(*) > 1
 ORDER BY u.clave
"""


def existe_indice(conn) -> bool:
    return conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"),
        {"n": INDICE},
    ).first() is not None


with engine.connect() as conn:
    filas = conn.execute(text("SELECT count(*) FROM escaneos_motor")).scalar_one()
    fuera = conn.execute(text(
        f"SELECT count(*) FROM escaneos_motor WHERE NOT ({PREDICADO})")).scalar_one()
    choques = conn.execute(text(SQL_CHOQUES)).all()
    ya_estaba = existe_indice(conn)

print(f"escaneos_motor: {filas} fila(s); {filas - fuera} entran en el índice y "
      f"{fuera} quedan fuera por el predicado.")

# ── la puerta: si hay repetidos, no se toca nada ────────────────────────────
if choques:
    sobrantes = sum(c.n - 1 for c in choques)
    print(f"\nNO SE APLICÓ NADA. Hay {len(choques)} período(s) repetido(s) en la base "
          f"({sobrantes} fila(s) de más),")
    print("y un índice único no se puede crear encima de ellos:\n")
    for c in choques:
        print(f"  {c.clave}  cierra en {c.odometro:,.2f} km  ·  {c.n} filas  (ids {c.ids})")
        print(f"      archivos: {c.archivos}")
        print(f"      periodo_fin de cada una: {c.cierres}")
    print("\nQué hacer, en este orden:")
    print("  1. python -m scripts.escaneos_duplicados            → los lista campo por campo")
    print("  2. python -m scripts.escaneos_duplicados --eliminar → borra los sobrantes")
    print("  3. python -m scripts.migrate_escaneo_odometro       → vuelve a correr esto")
    print("\nNada se ha modificado. La base está exactamente como estaba.")
    sys.exit(1)

# ── crear ───────────────────────────────────────────────────────────────────
if ya_estaba:
    print(f"\n=      índice {INDICE} YA EXISTÍA. No se hizo nada.")
else:
    with engine.begin() as conn:
        conn.execute(text(DDL))
    print(f"\nNUEVO  índice único parcial {INDICE}")
    print(f"       {LLAVE}  WHERE {PREDICADO}")

with engine.connect() as conn:
    definicion = conn.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname = :n"),
        {"n": INDICE},
    ).scalar_one_or_none()
    n_indices = conn.execute(text(
        "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
        "AND tablename='escaneos_motor'")).scalar_one()

print(f"\nTal como quedó en la base:\n  {definicion}")
print(f"escaneos_motor tiene ahora {n_indices} índice(s), incluida la PK.")
print("\nDesde aquí, un INSERT que repita período en una unidad que se movió falla con")
print(f"  duplicate key value violates unique constraint \"{INDICE}\"")
print("El importador lo atrapa ANTES y lo explica en castellano; esto es la red de abajo,")
print("por si algún día escribe en la tabla alguien que no sea `scripts/import_escaneos.py`.")
print("\nRevertir:")
print(f"  DROP INDEX {INDICE};")
