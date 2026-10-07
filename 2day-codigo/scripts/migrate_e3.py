"""E3 · Libro mayor Consumo: crea `asientos_consumo`, su secuencia y sus índices.

Solo AGREGA. Ni un solo ALTER: ninguna tabla existente se toca, ninguna columna se añade a
lo que ya está, ningún dato se modifica. Revertir E3 es un DROP de UNA tabla y UNA secuencia
— el script lo imprime literal al final.

QUÉ HACE, EN TRES PASOS
  1. Crea `asientos_consumo` con `checkfirst=True`. La tabla trae ya sus 10 CHECK y sus 5
     índices parciales NO únicos, que SQLAlchemy sí sabe declarar en `__table_args__`.
  2. Crea la SEQUENCE `consumo_evento_seq` con SQL crudo. Es el espacio de ids del HECHO
     FÍSICO (la recarga real), y es un objeto REAL de la base y no un número inventado por el
     código: por eso cae con el DROP de reversión y por eso se crea aquí y no en el poblador.
  3. Crea con SQL crudo los TRES ÍNDICES ÚNICOS PARCIALES que SQLAlchemy no puede declarar
     como `UniqueConstraint` (un UniqueConstraint no admite predicado), exactamente como
     `scripts/migrate_e2.py` hizo con `ux_cargas_llave`. Son las DOS garantías centrales de
     la etapa, y son distintas entre sí:
       · `ux_asientos_carga` / `ux_asientos_orden` → IDEMPOTENCIA POR LÍNEA DE ORIGEN: una
         línea produce un asiento y solo uno, POR SIEMPRE. Reimportar julio entero o correr
         el poblador diez veces no puede crear un segundo asiento.
       · `ux_asientos_evento` → ANTI-DOBLE-CONTEO ENTRE FUENTES: un hecho físico, como mucho
         UN asiento contable vivo. Es la puerta del emparejador, cableada y vacía.

POR QUÉ DOS ÍNDICES SEPARADOS Y NO UNO SOBRE (origen, carga_id, orden_id)
En un UNIQUE ordinario dos filas con NULL en `orden_id` se consideran DISTINTAS, así que ese
índice no impediría absolutamente nada. Postgres 16.14 (el de esta base) admite
NULLS NOT DISTINCT, pero apoyar la garantía central de la etapa en una cláusula de la 15+ es
una dependencia gratuita.

POR QUÉ NINGÚN VOCABULARIO ES UN Enum NATIVO
`origen`, `atribucion`, `destino` y `combustible` son String con CHECK. La prueba 1 de
`scripts/verificar_e2.py` cuenta `SELECT count(*) FROM pg_type WHERE typtype = 'e'` = 6
justamente para detectar basura tras una reversión. Un Enum nuevo dejaría un tipo huérfano y
ensuciaría una reversión que hoy es limpia. Este script imprime ese conteo ANTES y DESPUÉS, y
las dos veces debe decir 6.

IDEMPOTENTE, Y SE NOTA: la segunda corrida no reporta un solo cambio. Cada paso dice si CREÓ
algo o si YA ESTABA, en vez de imprimir "OK" pase lo que pase — que es lo único que hace
distinguible la primera corrida de la segunda, que es lo que esta migración tiene que
demostrar.

Uso:
    python -m scripts.migrate_e3
"""

import sys
from pathlib import Path

# La consola de Windows es cp1252 y revienta con los acentos que este script imprime
# ('índice', 'secuencia', 'atribución').
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app.db import engine
from app.models import AsientoConsumo

# La secuencia del hecho físico. No lleva OWNED BY a propósito: no pertenece a una columna
# (`evento_id` no es serial ni tiene default), la sirve el emparejador con nextval() cuando
# DECIDE que dos filas son la misma recarga. Sin OWNED BY hay que soltarla explícitamente en la
# reversión, y por eso el DROP que se imprime al final la nombra.
SECUENCIA = "consumo_evento_seq"

# Los tres índices que el ORM no puede expresar. El predicado de cada uno está medido:
INDICES_PARCIALES = (
    (
        "ux_asientos_carga",
        # LA llave de idempotencia. OJO: NO lleva `AND vigente`, a diferencia de
        # `ux_cargas_llave` de E2, y la diferencia es deliberada y correcta. La identidad del
        # asiento es el ID de la línea (que nunca se reutiliza), no la llave natural del archivo
        # (que sí se libera al anular una corrida). Con `vigente` en el predicado, jubilar un
        # asiento liberaría su carga_id y la siguiente regeneración insertaría un SEGUNDO
        # asiento para la misma línea: exactamente el doble conteo que hay que impedir.
        "CREATE UNIQUE INDEX ux_asientos_carga ON asientos_consumo "
        "(carga_id) WHERE carga_id IS NOT NULL",
    ),
    (
        "ux_asientos_orden",
        # Lo mismo para la segunda fuente. Hoy no hay una sola orden en la base; el índice
        # existe desde el primer día porque añadirlo después obligaría a rehacer el CHECK de
        # origen y esta migración.
        "CREATE UNIQUE INDEX ux_asientos_orden ON asientos_consumo "
        "(orden_id) WHERE orden_id IS NOT NULL",
    ),
    (
        "ux_asientos_evento",
        # LA restricción anti-doble-conteo ENTRE FUENTES: un hecho físico, como mucho UN asiento
        # contable vivo. El predicado incluye `vigente` para que un asiento jubilado no le
        # bloquee el evento a su propia revisión, y `contable` para que el hermano descontado
        # pueda CONSERVAR su evento_id — que es justo lo que lo hace auditable.
        "CREATE UNIQUE INDEX ux_asientos_evento ON asientos_consumo "
        "(evento_id) WHERE evento_id IS NOT NULL AND vigente AND contable",
    ),
)


def _enums(conn) -> int:
    """El canario de la reversión limpia: cuántos tipos ENUM nativos hay. Debe valer 6 antes y
    después de esta migración, y seguir valiendo 6 después del DROP."""
    return conn.execute(
        text("SELECT count(*) FROM pg_type WHERE typtype = 'e'")).scalar_one()


with engine.connect() as conn:
    enums_antes = _enums(conn)
print(f"pg_type typtype='e' ANTES : {enums_antes}   (debe ser 6)")

# ── 1. la tabla ─────────────────────────────────────────────────────────────
# Una sola tabla y ningún orden de dependencia que respetar: `asientos_consumo` es una hoja
# que apunta a cuatro tablas ya existentes (cargas_proveedor, ordenes_despacho, unidades,
# remolques) y nadie le apunta a ella.
insp = inspect(engine)
ya = insp.has_table(AsientoConsumo.__tablename__)
AsientoConsumo.__table__.create(engine, checkfirst=True)
creadas = 0
if ya:
    print(f"=      tabla {AsientoConsumo.__tablename__} ya existía")
else:
    creadas = 1
    print(f"NUEVA  tabla {AsientoConsumo.__tablename__} "
          f"(con sus 10 CHECK y sus 5 índices parciales no únicos)")

with engine.begin() as conn:
    # ── 2. la secuencia del hecho físico ────────────────────────────────────
    # Se comprueba contra pg_class y no con `CREATE SEQUENCE IF NOT EXISTS` a secas para poder
    # DECIR si se creó: un "OK" incondicional haría indistinguible la primera corrida de la
    # segunda. Y NO se toca si ya existe — reiniciarla mezclaría eventos viejos con nuevos.
    existe = conn.execute(
        text("SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = :n"),
        {"n": SECUENCIA},
    ).first()
    if existe:
        ultimo = conn.execute(
            text(f"SELECT last_value, is_called FROM {SECUENCIA}")).first()
        servidos = ultimo[0] if ultimo[1] else 0
        print(f"=      secuencia {SECUENCIA} ya existía "
              f"({servidos} evento(s) servido(s); NO se reinicia)")
    else:
        conn.execute(text(f"CREATE SEQUENCE {SECUENCIA}"))
        print(f"NUEVA  secuencia {SECUENCIA} (ids del hecho físico; hoy 0 servidos)")

    # ── 3. los tres índices únicos PARCIALES ────────────────────────────────
    nuevos = 0
    for nombre, ddl in INDICES_PARCIALES:
        existe = conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
                 "AND indexname = :n"),
            {"n": nombre},
        ).first()
        if existe:
            print(f"=      índice {nombre} ya existía")
        else:
            conn.execute(text(ddl))
            nuevos += 1
            print(f"NUEVA  índice único parcial {nombre}")

# ── informe ─────────────────────────────────────────────────────────────────
with engine.connect() as conn:
    enums_despues = _enums(conn)
    filas = conn.execute(
        text("SELECT count(*) FROM asientos_consumo")).scalar_one()
    # Lo que la tabla tiene de verdad, leído de la base y no de lo que este script creyó hacer.
    n_checks = conn.execute(text(
        "SELECT count(*) FROM pg_constraint "
        "WHERE contype = 'c' AND conrelid = 'asientos_consumo'::regclass")).scalar_one()
    n_indices = conn.execute(text(
        "SELECT count(*) FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'asientos_consumo'")).scalar_one()

print(f"pg_type typtype='e' DESPUÉS: {enums_despues}   (debe seguir en 6)")
if enums_antes != enums_despues or enums_despues != 6:
    print("\n¡ATENCIÓN!  el número de ENUM nativos cambió o no es 6. Esta etapa no crea "
          "ninguno: `origen`, `atribucion`, `destino` y `combustible` son String con CHECK.")

print(f"\n{creadas} tabla(s) y {nuevos} índice(s) único(s) creados en esta corrida.")
print(f"Estado de asientos_consumo: {filas} fila(s) · {n_checks} CHECK · "
      f"{n_indices} índice(s) (incluida la PK).")
if filas == 0:
    print("La tabla nace VACÍA a propósito: poblarla es trabajo del proyector, no de la "
          "migración.")

print("\nMigración E3 completa. Revertir la etapa entera:")
print("  DROP TABLE asientos_consumo;")
print(f"  DROP SEQUENCE {SECUENCIA};")
print("  (los cinco índices parciales, los tres únicos y los diez CHECK caen con la tabla;")
print("   la secuencia va aparte porque no tiene OWNED BY: no pertenece a ninguna columna.")
print("   No hay un solo ALTER que deshacer, porque no se hizo ninguno.)")
print("  Comprobación de que la reversión quedó limpia — antes y después debe dar 6:")
print("  SELECT count(*) FROM pg_type WHERE typtype = 'e';")
