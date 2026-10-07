"""E2 · Ingesta verbatim: crea las SEIS tablas y siembra el contrato de lectura.

Solo AGREGA. Ni un solo ALTER: ninguna tabla existente se toca, ninguna columna se
añade a lo que ya está, ningún dato se modifica. Revertir E2 es un DROP de seis tablas
y nada más — el script lo imprime literal al final, en el orden que respeta las FK.

QUÉ HACE, EN TRES PASOS
  1. Crea las seis tablas con `checkfirst=True`, EN ORDEN DE DEPENDENCIA: `proveedores`
     primero porque las otras cinco le apuntan, `cargas_proveedor` al final porque le
     apunta a las cinco.
  2. Crea con SQL crudo los DOS ÍNDICES ÚNICOS PARCIALES que SQLAlchemy no puede
     declarar como `UniqueConstraint` (un UniqueConstraint no admite predicado). Son las
     dos promesas que sin `WHERE vigente` se pelean entre sí: "reimportar no duplica" y
     "una corrida se puede anular y volver a correr".
  3. SIEMBRA las filas de OXXO y XYGA con su contrato de lectura completo —hoja, filas,
     ancho y el encabezado literal CON SUS ERRATAS—, que es lo que permite detectar que
     el proveedor cambió el formato. Sin esa copia en la base, el mes en que OXXO inserte
     una columna la ingesta leería todo corrido (placas en la columna de litros) y los
     totales cuadrarían igual de bien.

POR QUÉ EL CONTRATO NO SE TECLEA AQUÍ
Las constantes salen de `app.ingesta.CONTRATOS`, las MISMAS que usa el lector para
comprobar el encabezado. Copiarlas a mano crearía dos verdades que se separan el día que
alguien corrija una: el lector aceptaría un archivo que la base considera cambiado, o al
revés. Se escriben una vez y la base guarda la copia auditable.

IDEMPOTENTE, Y SE NOTA: la segunda corrida no reporta un solo cambio. Cada paso dice si
creó algo o si ya estaba, en vez de imprimir "OK" pase lo que pase. Si un proveedor ya
sembrado trae un contrato distinto al del código, este script NO lo pisa: lo denuncia
nombrando cada campo divergente e imprime el UPDATE, porque cambiar un contrato de
lectura es una decisión, no un efecto secundario de correr una migración.

Uso:
    python -m scripts.migrate_e2
"""

import sys
from pathlib import Path

# La consola de Windows es cp1252 y revienta con los acentos de los nombres de columna
# ('Fecha Histórica', 'No. Económico'), que es justo lo que este script imprime.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, select, text

from app.db import SessionLocal, engine
from app.ingesta import CONTRATOS
from app.models import (
    CargaProveedor,
    EmpleadoProveedor,
    EstacionProveedor,
    ImportacionProveedor,
    Proveedor,
    TarjetaCombustible,
)

# El orden IMPORTA: es el de las claves foráneas. `proveedores` es la raíz del espacio de
# llaves y `cargas_proveedor` la hoja que apunta a todas las demás.
TABLAS = (
    Proveedor,
    EstacionProveedor,
    TarjetaCombustible,
    EmpleadoProveedor,
    ImportacionProveedor,
    CargaProveedor,
)

# Los dos índices que el ORM no puede expresar. El predicado `WHERE vigente` es
# deliberado y no equivale a `WHERE reemplazada_por_id IS NULL`: ese otro predicado sirve
# para las correcciones pero NO libera las llaves al anular una corrida completa —las
# filas anuladas seguirían ocupándolas— y entonces reimportar el mismo archivo fallaría.
# El booleano `vigente` cubre los dos casos con un solo UPDATE.
INDICES_PARCIALES = (
    (
        "ux_cargas_llave",
        "CREATE UNIQUE INDEX ux_cargas_llave ON cargas_proveedor "
        "(proveedor_id, estacion_txt, folio_txt) WHERE vigente",
    ),
    (
        "ux_importaciones_prov_sha256",
        "CREATE UNIQUE INDEX ux_importaciones_prov_sha256 ON importaciones_proveedor "
        "(sha256) WHERE vigente",
    ),
)

# Los campos del contrato que viven en `proveedores`. Se listan una sola vez para que
# sembrar y comparar usen exactamente el mismo conjunto: si mañana el contrato crece una
# pieza, no puede quedarse fuera de la comparación y volverse invisible.
CAMPOS_CONTRATO = (
    "nombre",
    "hoja",
    "fila_encabezado",
    "fila_datos",
    "n_columnas",
    "formato_fecha",
    "regimen_factura",
    "cliente_texto",
    "grupo_texto",
    "encabezado_esperado",
)


def _valores(contrato) -> dict:
    """Traduce un `Contrato` de app.ingesta a las columnas de `proveedores`.

    `encabezado` se llama `encabezado_esperado` en la tabla porque ahí es el patrón
    contra el que se compara lo leído; el resto conserva el nombre. `zona_horaria` NO
    aparece: la declara una persona y el modelo ya la nace en 'America/Mexico_City'.
    Adivinarla aquí sería exactamente la interpretación que E2 no hace.
    """
    return {
        "nombre": contrato.nombre,
        "hoja": contrato.hoja,
        "fila_encabezado": contrato.fila_encabezado,
        "fila_datos": contrato.fila_datos,
        "n_columnas": contrato.n_columnas,
        "formato_fecha": contrato.formato_fecha,
        "regimen_factura": contrato.regimen_factura,
        "cliente_texto": contrato.cliente_texto,
        "grupo_texto": contrato.grupo_texto,
        "encabezado_esperado": list(contrato.encabezado),
    }


def _sql(valor) -> str:
    """Literal SQL, no repr de Python. El `None` de Python es `NULL` en SQL y `'X'` con
    comilla simple: imprimir un UPDATE que no se puede pegar en psql sería peor que no
    imprimirlo, porque parece que sí se puede."""
    if valor is None:
        return "NULL"
    if isinstance(valor, bool):
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, int):
        return str(valor)
    return "'" + str(valor).replace("'", "''") + "'"


def _resumen(clave: str, valores: dict) -> str:
    """Una línea que deja ver el contrato sin volcar 26 nombres de columna."""
    return (f"{clave}: hoja '{valores['hoja']}' · encabezado fila "
            f"{valores['fila_encabezado']} · datos desde {valores['fila_datos']} · "
            f"{valores['n_columnas']} columnas")


# ── 1. las seis tablas ──────────────────────────────────────────────────────
insp = inspect(engine)
creadas = 0
for modelo in TABLAS:
    ya = insp.has_table(modelo.__tablename__)
    modelo.__table__.create(engine, checkfirst=True)
    if ya:
        print(f"=   tabla {modelo.__tablename__} ya existía")
    else:
        creadas += 1
        print(f"NUEVA  tabla {modelo.__tablename__}")

# ── 2. los dos índices únicos PARCIALES ─────────────────────────────────────
# Van con SQL crudo y no con `IF NOT EXISTS` a secas para poder DECIR cuál se creó: un
# "OK" incondicional haría indistinguible la primera corrida de la segunda, que es
# justamente lo que esta migración tiene que demostrar.
with engine.begin() as conn:
    for nombre, ddl in INDICES_PARCIALES:
        existe = conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
                 "AND indexname = :n"),
            {"n": nombre},
        ).first()
        if existe:
            print(f"=   índice {nombre} ya existía")
        else:
            conn.execute(text(ddl))
            print(f"NUEVA  índice único parcial {nombre}")

# ── 3. la semilla de los dos proveedores ────────────────────────────────────
with SessionLocal() as db:
    for clave, contrato in CONTRATOS.items():
        valores = _valores(contrato)
        fila = db.scalar(select(Proveedor).where(Proveedor.clave == clave))
        if fila is None:
            db.add(Proveedor(clave=clave, **valores))
            db.commit()
            print(f"NUEVA  proveedor {_resumen(clave, valores)}")
            continue

        # Existe: NO se pisa. Se compara campo por campo y se denuncia lo que difiera.
        difieren = [c for c in CAMPOS_CONTRATO
                    if getattr(fila, c) != valores[c]]
        if not difieren:
            print(f"=   proveedor {_resumen(clave, valores)} (sin cambios)")
            continue

        print(f"\n¡ATENCIÓN!  el proveedor {clave} ya está sembrado con OTRO contrato "
              "de lectura.")
        print("            NO se sobrescribe: cambiar un contrato es una decisión, no "
              "un efecto de correr esta migración.")
        for campo in difieren:
            print(f"            {campo}:")
            print(f"              en la base : {getattr(fila, campo)!r}")
            print(f"              en el código: {valores[campo]!r}")
        # El UPDATE se imprime COMPLETO y solo con los campos que difieren: quien lo
        # ejecute tiene que poder leer exactamente qué cambia, no reconstruirlo a mano.
        asigna = ", ".join(f"{c} = {_sql(valores[c])}" for c in difieren
                           if c != "encabezado_esperado")
        print("            Para adoptar el del código (app/ingesta.py CONTRATOS):")
        if asigna:
            print(f"              UPDATE proveedores SET {asigna} "
                  f"WHERE clave = '{clave}';")
        if "encabezado_esperado" in difieren:
            print("              -- el encabezado cambió: NO lo actualices sin mirar el "
                  "archivo. Que el proveedor")
            print("              -- reordene o renombre una columna es justo lo que este "
                  "campo existe para detectar.")
        print()

    total = db.query(Proveedor).count()

print(f"\n{creadas} tabla(s) creadas en esta corrida · {total} proveedor(es) sembrados")
print("\nMigración E2 completa. Revertir la etapa entera:")
# ORDEN DE REVERSION: desde E3, el libro mayor apunta a cargas_proveedor con una FK RESTRICT,
# asi que la etapa de arriba se cae primero. El IF EXISTS lo deja inocuo si E3 no esta aplicada.
print("  DROP TABLE IF EXISTS asientos_consumo;   -- si E3 esta aplicada, va primero")
print("  DROP TABLE cargas_proveedor, importaciones_proveedor, empleados_proveedor,"
      " tarjetas_combustible, estaciones_proveedor, proveedores;")
print("  (los dos índices únicos parciales caen con sus tablas; no hay ALTER que"
      " deshacer porque no se hizo ninguno)")
print("  Comprobación de que la reversión quedó limpia — antes y después debe dar 6:")
print("  SELECT count(*) FROM pg_type WHERE typtype = 'e';")
