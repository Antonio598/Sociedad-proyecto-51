"""`operadores.numero` pasa de entero a texto: el NÚMERO DE EMPLEADO de la empresa.

POR QUÉ NO ES UN CAMPO NUEVO. El número de empleado alfanumérico ya existe y ya está en esta
base: el Excel de la gasolinera lo trae en cada carga y el importador lo guarda en
`empleados_proveedor.numero` — CFRUIT056, CFRUIT054, CFRUIT051… Son 34 personas y ninguna está
enlazada con su ficha del padrón. Adoptarlo en vez de inventar un formato nuevo hace que la
persona sea LA MISMA en la plataforma y en el consumo de diésel, que es lo único que permite
colgarle el gasto a alguien sin adivinar.

QUÉ CAMBIA DE SEMÁNTICA, y por qué importa. Hoy `numero` es INTEGER UNIQUE: 1000 y 01000 no
pueden coexistir porque son el mismo entero. En texto sí pueden, y serían dos personas
distintas para el sistema y la misma para quien las captura. Por eso el código que escribe
NORMALIZA antes de guardar (mayúsculas, sin espacios) y esta migración comprueba que la
conversión no crea colisiones antes de tocar nada.

LOS 139 NÚMEROS ACTUALES SE CONSERVAN TAL CUAL, como texto: '56', '500', '3010'. No se les
pone prefijo ni se les rellena con ceros. Inventarles un formato sería cambiarle el número de
empleado a 139 personas por comodidad del programa.

LOS 126 SIN NÚMERO SE QUEDAN EN NULO. Es el 48% del padrón y carga el 93% de los viajes.
Inventarles uno no es una migración, es una decisión de recursos humanos.

REVERSIBLE mientras todos los valores sigan siendo dígitos:
    ALTER TABLE operadores ALTER COLUMN numero TYPE INTEGER USING numero::integer

Idempotente: se puede correr las veces que haga falta.
"""
import sys
from pathlib import Path

# La consola de Windows va en cp1252 y se come los acentos del informe.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine

LARGO = 30


def tipo_actual(conn) -> str:
    return conn.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'operadores' AND column_name = 'numero'")).scalar()


with engine.begin() as conn:
    antes = tipo_actual(conn)
    print(f"  tipo actual de operadores.numero: {antes}")

    if antes and antes.lower() in ("character varying", "text"):
        print("  ya es texto: no hay nada que hacer.")
    else:
        # ── antes de tocar nada, la foto de lo que hay ──────────────────────
        filas = conn.execute(text(
            "SELECT id, numero FROM operadores WHERE numero IS NOT NULL ORDER BY numero")).all()
        print(f"  {len(filas)} operadores con número · "
              f"{conn.execute(text('SELECT count(*) FROM operadores')).scalar()} en total")

        respaldo = Path(__file__).resolve().parent / "numero_empleado_respaldo.csv"
        respaldo.write_text(
            "id,numero\n" + "\n".join(f"{i},{n}" for i, n in filas), encoding="utf-8")
        print(f"  respaldo de los valores actuales -> {respaldo.name}")

        # ── ¿la conversión a texto crearía colisiones? ──────────────────────
        # Con enteros no puede haberlas; se comprueba igual porque el UNIQUE sobrevive al
        # cambio de tipo y una colisión ahí aborta la migración a media transacción.
        choques = conn.execute(text(
            "SELECT numero::text, count(*) FROM operadores WHERE numero IS NOT NULL "
            "GROUP BY numero::text HAVING count(*) > 1")).all()
        if choques:
            print(f"  ABORTA: {len(choques)} valor(es) colisionarían como texto: {choques}")
            raise SystemExit(1)

        conn.execute(text(
            f"ALTER TABLE operadores ALTER COLUMN numero TYPE VARCHAR({LARGO}) "
            "USING numero::text"))
        print(f"  convertida a VARCHAR({LARGO}) conservando los valores")

    # ── el UNIQUE tiene que seguir ahí: es lo que impide dos fichas iguales ─
    idx = conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'operadores' "
        "AND indexdef ILIKE '%UNIQUE%' AND indexdef ILIKE '%numero%'")).all()
    print(f"  índices únicos sobre numero: {[i[0] for i in idx] or 'NINGUNO <== revisar'}")

    print(f"  tipo final: {tipo_actual(conn)}")
    muestra = conn.execute(text(
        "SELECT numero FROM operadores WHERE numero IS NOT NULL LIMIT 6")).scalars().all()
    print(f"  muestra: {muestra}")
