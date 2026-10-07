"""E3 · Llena el LIBRO MAYOR `asientos_consumo` desde `cargas_proveedor`. Idempotente.

EL HECHO QUE ESTE SCRIPT ESCRIBE, Y NINGÚN OTRO: un litro entró a UN activo, de UN tipo,
exactamente UNA vez. Nada nace aquí. Cada columna del libro se RECALCULA desde la línea que
la produjo, así que esto es una PROYECCIÓN y se regenera con UPSERT sobre la llave de origen
—jamás con DELETE+INSERT—. Un DELETE+INSERT rompería tres cosas a la vez: los ids del libro,
los emparejamientos que E3 haya sellado y la garantía de que una línea produce un asiento y
solo uno.

LO QUE HACE, EN UNA SOLA SENTENCIA
Un `INSERT ... SELECT ... ON CONFLICT (carga_id) WHERE carga_id IS NOT NULL DO UPDATE`. El
predicado se REPITE en el ON CONFLICT a propósito: `ux_asientos_carga` es un índice PARCIAL y
sin repetirlo Postgres contesta "no unique or exclusion constraint matching the ON CONFLICT
specification" y la corrida falla entera. Es el mismo gotcha que E2 ya pagó con
`ux_cargas_llave`.

TRES REGLAS QUE NO SE PUEDEN ROMPER, y las tres están escritas en el código de abajo:

  (a) LA FUENTE NO ES `WHERE c.vigente` A SECAS. Es
      `WHERE c.vigente OR EXISTS (SELECT 1 FROM asientos_consumo a WHERE a.carga_id = c.id)`.
      Si el origen JUBILA una línea corregida y la fuente fuera solo `c.vigente`, esa línea
      saldría del SELECT y su asiento nunca se enteraría: se quedaría `vigente=true` para
      siempre y el mes contaría 640 filas donde hay 639. Con esta fuente, la línea jubilada
      sigue entrando, su asiento pasa a `vigente=false` y la revisión nueva entra como asiento
      propio. Nadie borra nada y el libro conserva lo que decía antes.

  (b) LITROS E IMPORTE SALEN DEL TEXTO VERBATIM (`litros_txt::numeric`,
      `importe_txt::numeric`), JAMÁS de las columnas Float del origen. Medido: sum(litros)
      sobre Float da 118541.02100000005 y sum(litros_txt::numeric) da 118541.021 exacto; en
      importe, 3218987.9900000026 contra 3218987.99. El libro es lo que E6 concilia contra el
      CFDI y el estándar que fijó E2 es 'al centavo', no 'con tolerancia'.

  (c) EL `DO UPDATE` ENUMERA COLUMNAS Y NO MENCIONA JAMÁS `evento_id`, `contable` NI `motivo`.
      Esas tres son propiedad del emparejador (E3): son la CONCLUSIÓN de que dos filas
      describen la misma recarga física. Por eso regenerar el libro no borra emparejamientos, y
      por eso este script puede correrse cuantas veces haga falta sin pedirle permiso a nadie.

POR QUÉ UNA REGENERACIÓN SIN CAMBIOS TOCA CERO FILAS
`origen_huella` es el sha256 de EXACTAMENTE los once campos que el asiento copia de su línea.
El `DO UPDATE` lleva `WHERE asientos_consumo.origen_huella IS DISTINCT FROM
EXCLUDED.origen_huella`, así que una fila idéntica ni siquiera cambia de `proyectado_en`. Eso
convierte "es idempotente" en algo que se MIDE en escrituras y no en conteos.

LOS TRES DESTINOS DE UN LITRO, Y POR QUÉ LOS TRES ENTRAN AL LIBRO
  · `atribucion='activo'`      596 filas · el litro tiene dueño y E4 lo va a usar.
  · `atribucion='cuarentena'`   39 filas · flota REAL sin activo en el catálogo todavía
    (4,455.761 L, 5 económicos). No se pueden atribuir, pero tampoco desaparecer: entran con
    los dos activos en NULL y quedan como cola de trabajo en `ix_asientos_pendientes`. El día
    que alguien resuelva el catálogo, atribuirlas es un UPDATE sobre asientos que YA EXISTEN
    —nunca un INSERT tardío, que es justo el camino por el que un libro mayor se duplica—.
  · `atribucion='fuera_flota'`   4 filas · gasolina '87 OCTANOS' de camionetas, 227.130 L y
    $5,341.91. ENTRAN. Sin ellas el libro sumaría 118,313.891 L contra una factura de
    118,541.021 L y jamás cuadraría al centavo contra el CFDI, que es para lo que E6 lo va a
    usar: un libro que no reproduce el documento no es un libro. No contaminan el km/L porque
    su `unidad_id` es NULL (E4 agrupa por activo y un NULL no une) y no se pueden sumar al
    diésel por descuido porque llevan `combustible='gasolina'` en columna propia.

`atribucion` contesta UNA sola pregunta: "¿por qué este litro no tiene activo?" —'todavía no
lo sabemos' o 'no es flota'—. Por eso una línea CON activo resuelto es `atribucion='activo'`
aunque su producto sea gasolina: la separación del combustible la hace `combustible`, no
`atribucion`. Hoy no hay ninguna así (las 4 de gasolina son las 4 sin económico) y el día que
alguien dé de alta las camionetas la habrá; el informe lo dice en voz alta cuando ocurra.

LO QUE ESTE SCRIPT NO HACE, A PROPÓSITO:
  · NO EMPAREJA nada. `evento_id` queda NULL en las 639. Está medido por qué sería inútil hoy:
    las cargas del proveedor son de julio, las órdenes que existían eran del 1 al 14 de agosto,
    ese lote era de prueba y se borró (hoy 0 órdenes, 0 solicitudes, 0 facturas). Lo que se
    entrega es la PUERTA —`evento_id` + `contable` + `motivo` + `ux_asientos_evento`—, cableada
    y vacía.
  · NO calcula rendimiento ni km/L. Eso es E4.
  · NO ESCRIBE UNA SOLA FILA en `cargas_proveedor`, `viajes`, `unidades` ni `remolques`. El
    libro es una proyección de solo lectura sobre su origen.
  · NO CORRIGE EL CATÁLOGO ni aplica propuestas.
  · NO DESCARTA NADA EN SILENCIO. Una línea que no se pueda proyectar detiene la corrida ANTES
    de escribir, nombrándola: saltársela dejaría un libro que no cuadra contra la factura y
    nadie lo notaría, porque los totales seguirían pareciendo plausibles.

Uso:
    python -m scripts.proyectar_consumo --dry-run
    python -m scripts.proyectar_consumo
    python -m scripts.proyectar_consumo --forzar

  --dry-run  hace TODO el trabajo de verdad —la misma sentencia, contra las mismas filas— y
             deshace la transacción al terminar. No deja ni un testigo: a diferencia de la
             ingesta de E2, aquí no hay nada que registrar (una proyección no es un documento
             que llegó, es una vista recalculable).
  --forzar   sirve para UNA sola cosa: seguir adelante cuando quedan EVENTOS HUÉRFANOS (un
             asiento descontado cuyo hermano contable ya no está vivo). Es una regla ENTRE
             filas que ningún CHECK puede expresar, y sin ella el libro descontaría litros que
             nadie está contando.
"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# La consola de Windows es cp1252 y revienta con los acentos de este informe ('atribución',
# 'cuarentena', 'económico'), que es justo lo que una persona tiene que leer.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect, text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
# Se REUSA `Aborta` del importador en vez de definir otra: detenerse tiene que significar lo
# mismo en los tres scripts de la familia (import, revertir, proyectar), y `revertir_importacion`
# ya sentó ese precedente.
from scripts.import_proveedor import Aborta  # noqa: E402

TABLA = "asientos_consumo"
SECUENCIA = "consumo_evento_seq"
INDICES_EXIGIDOS = ("ux_asientos_carga", "ux_asientos_orden", "ux_asientos_evento")


# ─────────────────────────────────────────────────────────────────────────────
# LA HUELLA DEL ORIGEN
#
# sha256 de EXACTAMENTE los once campos que el asiento COPIA de su línea. Ni uno más (una
# columna que no se proyecta produciría falsos positivos y reescribiría el libro entero por
# nada) ni uno menos (deriva silenciosa: el origen cambia y el libro sigue diciendo lo viejo).
# Por eso NO entran `revision` ni `estado_resolucion`: no se proyectan.
#
# ⚠ DOS DETALLES DE IMPLEMENTACIÓN QUE NO SON COSMÉTICOS
#
# (1) NO SE PUEDE USAR concat_ws A SECAS. `concat_ws` OMITE los argumentos NULL, así que una
#     fila con `unidad_id` NULL y otra con `remolque_id` NULL producirían la MISMA cadena
#     corrida y la huella dejaría de discriminar justo en las 43 filas sin activo — las únicas
#     donde más falta hace. Va `coalesce(x, '')` campo por campo y chr(31) como separador,
#     igual que `CargaProveedor.sha256_fila`.
#
# (2) LAS FECHAS NO SE CASTEAN CON `::text`. `timestamptz::text` depende del `TimeZone` de la
#     sesión y `timestamp::text`/`date::text` del `DateStyle`: la misma fila daría huellas
#     distintas según quién corra el script, y la siguiente corrida "actualizaría" las 639
#     filas sin que hubiera cambiado un solo dato. Van con `to_char` y un formato FIJO, y el
#     `momento_ref` normalizado a UTC. El formato no lleva dos puntos a propósito: `text()` de
#     SQLAlchemy interpretaría `:MI` y `:SS` como parámetros de enlace.
# ─────────────────────────────────────────────────────────────────────────────

SQL_HUELLA = """encode(sha256(convert_to(
                   coalesce(c.vigente::text, '')                     || chr(31)
                || coalesce(c.fuera_de_flota::text, '')              || chr(31)
                || coalesce(c.unidad_id::text, '')                   || chr(31)
                || coalesce(c.remolque_id::text, '')                 || chr(31)
                || coalesce(c.destino, '')                           || chr(31)
                || coalesce(c.combustible, '')                       || chr(31)
                || coalesce(c.litros_txt, '')                        || chr(31)
                || coalesce(c.importe_txt, '')                       || chr(31)
                || coalesce(to_char(c.momento_local, 'YYYYMMDDHH24MISSUS'), '')  || chr(31)
                || coalesce(to_char(c.momento_ref AT TIME ZONE 'UTC',
                                    'YYYYMMDDHH24MISSUS'), '')       || chr(31)
                || coalesce(to_char(c.fecha_operacion, 'YYYYMMDD'), '')
                , 'UTF8')), 'hex')"""

# LA FUENTE. El `OR EXISTS` es la regla (a) del encabezado y no es un adorno: sin él, el
# espejo de `vigente` sería decorativo y una corrección de E2 dejaría 640 asientos vigentes.
#
# ⚠ EL PARÉNTESIS ENVUELVE EL `OR` ENTERO Y NO SOBRA: las guardas de abajo pegan
# `AND (condición)` detrás de esto, y `AND` liga más fuerte que `OR`. Sin el paréntesis, la
# comprobación se aplicaría solo a la mitad jubilada de la fuente y las líneas vigentes
# defectuosas pasarían de largo.
SQL_DESDE = """
      FROM cargas_proveedor c
     WHERE (c.vigente
            OR EXISTS (SELECT 1 FROM asientos_consumo a WHERE a.carga_id = c.id))"""

# LA ATRIBUCIÓN. El orden de las ramas ES la regla: `atribucion` contesta "¿por qué este litro
# no tiene activo?", así que tener activo gana siempre. `(a IS NOT NULL) <> (b IS NOT NULL)` es
# un XOR real —el mismo que exige `ck_asientos_atribucion`—, no un "al menos uno".
SQL_ATRIBUCION = """CASE
            WHEN (c.unidad_id IS NOT NULL) <> (c.remolque_id IS NOT NULL) THEN 'activo'
            WHEN c.fuera_de_flota THEN 'fuera_flota'
            ELSE 'cuarentena' END"""

SQL_FUENTE = f"""
    SELECT c.id                            AS carga_id,
           c.vigente                       AS vigente,
           c.unidad_id                     AS unidad_id,
           c.remolque_id                   AS remolque_id,
           c.destino                       AS destino,
           c.combustible                   AS combustible,
           c.litros_txt::numeric           AS litros,
           c.importe_txt::numeric          AS importe,
           c.momento_local                 AS momento_local,
           c.momento_ref                   AS momento_ref,
           c.fecha_operacion               AS fecha_operacion,
           {SQL_ATRIBUCION}                AS atribucion,
           {SQL_HUELLA}                    AS origen_huella
    {SQL_DESDE}"""

# LA SENTENCIA. Una sola, y con RETURNING para poder DECIR qué pasó fila por fila en vez de
# suponerlo. `xmax = 0` distingue el INSERT del UPDATE dentro de un ON CONFLICT; se contrasta
# después contra una resta de conteos, porque un informe que se cree a sí mismo no vale nada.
SQL_UPSERT = f"""
WITH fuente AS ({SQL_FUENTE})
INSERT INTO asientos_consumo (
        origen, carga_id, orden_id, origen_huella,
        unidad_id, remolque_id, atribucion, destino, combustible,
        litros, importe, momento_local, momento_ref, fecha_operacion,
        vigente, contable, proyectado_en)
SELECT 'proveedor',
       f.carga_id,
       NULL,
       f.origen_huella,
       -- Los dos activos y el `destino` SOLO viajan cuando hay atribución. Un litro no
       -- atribuible que arrastrara activo por descuido lo rechazaría `ck_asientos_atribucion`,
       -- pero es mejor no intentarlo: la regla se escribe una vez, aquí.
       CASE WHEN f.atribucion = 'activo' THEN f.unidad_id   END,
       CASE WHEN f.atribucion = 'activo' THEN f.remolque_id END,
       f.atribucion,
       CASE WHEN f.atribucion = 'activo' THEN f.destino     END,
       f.combustible,
       f.litros, f.importe,
       f.momento_local, f.momento_ref, f.fecha_operacion,
       f.vigente,
       -- `contable` va SOLO en el INSERT y JAMÁS en el DO UPDATE de abajo, y esa asimetría es
       -- toda la regla: un asiento NACE contable —un litro cuenta mientras nadie demuestre que
       -- lo cuenta otro—, y a partir de ahí la columna es propiedad del emparejador. Hay que
       -- darla explícitamente porque su `default=True` del modelo es de Python, no del
       -- servidor: un INSERT en SQL crudo no lo ve y la columna es NOT NULL.
       TRUE,
       now()
  FROM fuente f
    ON CONFLICT (carga_id) WHERE carga_id IS NOT NULL
    DO UPDATE SET
        origen_huella   = EXCLUDED.origen_huella,
        unidad_id       = EXCLUDED.unidad_id,
        remolque_id     = EXCLUDED.remolque_id,
        atribucion      = EXCLUDED.atribucion,
        destino         = EXCLUDED.destino,
        combustible     = EXCLUDED.combustible,
        litros          = EXCLUDED.litros,
        importe         = EXCLUDED.importe,
        momento_local   = EXCLUDED.momento_local,
        momento_ref     = EXCLUDED.momento_ref,
        fecha_operacion = EXCLUDED.fecha_operacion,
        vigente         = EXCLUDED.vigente,
        proyectado_en   = now()
        -- NI `evento_id`, NI `contable`, NI `motivo`: son la conclusión del emparejador y
        -- regenerar el libro no puede borrarla. Tampoco `origen`, `carga_id` ni `orden_id`,
        -- que son la identidad de la fila y no cambian nunca.
     WHERE asientos_consumo.origen_huella IS DISTINCT FROM EXCLUDED.origen_huella
RETURNING id, carga_id, (xmax = 0) AS insertado"""


# ─────────────────────────────────────────────────────────────────────────────
# LO QUE TIENE QUE EXISTIR ANTES DE PROYECTAR
# ─────────────────────────────────────────────────────────────────────────────

def _exigir_objetos(db):
    """La tabla, la secuencia y los TRES índices únicos parciales.

    Se comprueban por separado y no solo la tabla: los tres índices son las DOS garantías
    centrales de la etapa, y una tabla creada a mano sin ellos aceptaría alegremente dos
    asientos para la misma línea. Un libro sin `ux_asientos_carga` no es un libro, es una lista.
    """
    insp = inspect(db.get_bind())
    if not insp.has_table(TABLA):
        raise Aborta(f"no existe la tabla `{TABLA}`. Corre primero:  "
                     f"python -m scripts.migrate_e3")
    faltan = [n for n in INDICES_EXIGIDOS if not db.execute(text(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"),
        {"n": n}).first()]
    if faltan:
        raise Aborta(
            f"faltan los índices únicos parciales que impiden el doble conteo "
            f"({', '.join(faltan)}). Sin ellos, proyectar dos veces duplicaría litros y nada "
            f"lo impediría. Corre primero:  python -m scripts.migrate_e3")
    if not db.execute(text("SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = :n"),
                      {"n": SECUENCIA}).first():
        raise Aborta(f"falta la secuencia `{SECUENCIA}`, que es el espacio de ids del hecho "
                     f"físico. Corre primero:  python -m scripts.migrate_e3")


# ─────────────────────────────────────────────────────────────────────────────
# LO QUE NO SE PUEDE PROYECTAR  ·  se dice y se detiene, nunca se salta
#
# Cada comprobación de aquí corresponde a un CHECK de la tabla o a un NOT NULL. Podría dejarse
# que Postgres las rechace, pero entonces el mensaje sería "null value in column ... violates
# not-null constraint" sin decir QUÉ FILA del Excel lo causó ni por qué. Aquí se nombran las
# líneas y se explica qué significa cada caso, que es lo que una persona necesita para
# arreglarlo.
#
# Y NO SE SALTAN. Proyectar 638 de 639 dejaría un libro que no cuadra contra la factura por
# 300 L, con todas sus cifras igual de plausibles. El proyecto prefiere el fallo ruidoso.
# ─────────────────────────────────────────────────────────────────────────────

# (a) Lo que se comprueba SIN castear. El orden importa: si `litros_txt` no fuera numérico, un
#     `litros_txt::numeric` en la misma consulta reventaría con un error de Postgres en vez de
#     con la frase que explica qué pasó.
GUARDAS_TEXTO = (
    ("c.momento_local IS NULL",
     "sin `momento_local`: el libro no puede tener un litro sin el reloj del ticket"),
    ("c.momento_ref IS NULL",
     "sin `momento_ref`: un asiento sin instante comparable no se podría emparejar JAMÁS "
     "contra una orden, o sea un agujero permanente en la garantía anti-doble-conteo"),
    ("c.fecha_operacion IS NULL",
     "sin `fecha_operacion`: es la llave del periodo contable y el eje de los rollups"),
    ("c.combustible IS NULL OR c.combustible NOT IN ('diesel','gasolina','otro')",
     "`combustible` vacío o fuera de vocabulario: sin él, dar de alta las camionetas metería "
     "gasolina en el consumo de diésel sin que nadie lo escribiera a propósito"),
    ("c.litros_txt IS NULL OR c.litros_txt !~ '^[0-9]+([.][0-9]+)?$'",
     "`litros_txt` vacío o no numérico: los litros salen del texto verbatim, no del Float"),
    ("c.importe_txt IS NULL OR c.importe_txt !~ '^-?[0-9]+([.][0-9]+)?$'",
     "`importe_txt` vacío o no numérico: el dinero del proveedor es obligatorio "
     "(ck_asientos_importe_proveedor)"),
    ("c.unidad_id IS NOT NULL AND c.remolque_id IS NOT NULL",
     "unidad Y remolque a la vez: un litro entra a UN activo, no a dos "
     "(ck_asientos_atribucion)"),
    ("(c.unidad_id IS NOT NULL) <> (c.remolque_id IS NOT NULL) AND c.destino IS NULL",
     "activo resuelto pero sin `destino`: no se puede decir de quién es el litro y callar a "
     "qué tanque entró (ck_asientos_atribucion)"),
    ("c.destino IS NOT NULL AND c.destino NOT IN ('motor','termo','indeterminado')",
     "`destino` fuera de vocabulario (ck_asientos_destino_vocab)"),
    ("c.remolque_id IS NOT NULL AND c.destino <> 'termo'",
     "remolque con destino distinto de 'termo': un remolque no tiene motor "
     "(ck_asientos_remolque_termo)"),
)

# (b) Lo que solo se puede comprobar casteando, y solo después de que (a) pasara.
#     Los dos redondeos son el peligro invisible de esta etapa: `Numeric(12,3)` y
#     `Numeric(14,2)` REDONDEAN EN SILENCIO lo que no cabe. Medido hoy: máximo 3 decimales en
#     litros y 2 en importe, así que no redondea nada; el día que el proveedor mande un decimal
#     más, la corrida se detiene en vez de mover el total sin decirlo.
GUARDAS_NUMERO = (
    ("c.litros_txt::numeric <= 0",
     "litros no positivos: un litro negativo es una devolución disfrazada de carga y un cero "
     "no es una recarga (ck_asientos_litros)"),
    ("c.litros_txt::numeric <> round(c.litros_txt::numeric, 3)",
     "litros con más de 3 decimales: `Numeric(12,3)` los redondearía EN SILENCIO y el libro "
     "dejaría de cuadrar al centavo contra el CFDI"),
    ("abs(c.litros_txt::numeric) >= 1000000000",
     "litros fuera del rango de `Numeric(12,3)`"),
    ("c.importe_txt::numeric < 0",
     "importe negativo (ck_asientos_importe)"),
    ("c.importe_txt::numeric <> round(c.importe_txt::numeric, 2)",
     "importe con más de 2 decimales: `Numeric(14,2)` lo redondearía EN SILENCIO"),
    ("abs(c.importe_txt::numeric) >= 1000000000000",
     "importe fuera del rango de `Numeric(14,2)`"),
)


def _guardas(db, guardas, etapa: str):
    """Corre un bloque de guardas y ABORTA nombrando las líneas ofensoras."""
    fallos = []
    for condicion, explicacion in guardas:
        filas = db.execute(text(
            f"SELECT c.id, c.estacion_txt, c.folio_txt, c.fila_num, c.importacion_id"
            f"{SQL_DESDE} AND ({condicion}) ORDER BY c.id LIMIT 6")).all()
        if not filas:
            continue
        n = db.execute(text(
            f"SELECT count(*){SQL_DESDE} AND ({condicion})")).scalar_one()
        muestra = ", ".join(
            f"carga {i} (corrida {imp}, fila {fn}, estación {est}, folio {fol})"
            for i, est, fol, fn, imp in filas)
        fallos.append(f"{n} línea(s) {explicacion}\n        {muestra}"
                      + (" …" if n > len(filas) else ""))
    if fallos:
        raise Aborta(
            f"hay líneas del proveedor que NO se pueden proyectar ({etapa}). No se escribe "
            f"nada: proyectar el resto dejaría un libro que no cuadra contra la factura y "
            f"cuyas cifras parecerían igual de plausibles.\n      - "
            + "\n      - ".join(fallos)
            + "\n  Arregla el origen (o su lectura en E2) y vuelve a correr esto. El libro es "
              "una proyección: se regenera entero cuando el origen esté bien.")


# ─────────────────────────────────────────────────────────────────────────────
# LA CORRIDA
# ─────────────────────────────────────────────────────────────────────────────

def _radiografia(db) -> dict:
    """El estado del libro en una sola consulta. Se toma antes y después para que 'no se tocó
    nada' sea una resta y no una promesa."""
    return dict(db.execute(text(f"""
        SELECT count(*)                                        AS filas,
               count(*) FILTER (WHERE vigente)                 AS vigentes,
               count(*) FILTER (WHERE NOT vigente)             AS jubilados,
               count(*) FILTER (WHERE evento_id IS NOT NULL)   AS con_evento,
               count(*) FILTER (WHERE NOT contable)            AS no_contables,
               count(*) FILTER (WHERE origen = 'orden')        AS de_orden,
               COALESCE(max(proyectado_en)::text, '(nunca)')   AS ultima
          FROM {TABLA}""")).mappings().one())


def proyectar(db, dry=False, forzar=False) -> dict:
    """Regenera el libro desde `cargas_proveedor`. Devuelve el informe como diccionario.

    `db` se recibe en vez de abrirlo aquí para que el verificador pueda correr la proyección
    ENTERA dentro de una transacción de ensayo y deshacerla, exactamente como
    `scripts/verificar_e2.py` hace con la ingesta.
    """
    _exigir_objetos(db)
    _guardas(db, GUARDAS_TEXTO, "comprobación de textos y nulos")
    _guardas(db, GUARDAS_NUMERO, "comprobación de magnitudes")

    antes = _radiografia(db)
    n_fuente = db.execute(text(f"SELECT count(*){SQL_DESDE}")).scalar_one()

    tocados = db.execute(text(SQL_UPSERT)).all()
    insertados = sum(1 for _, _, nuevo in tocados if nuevo)
    actualizados = len(tocados) - insertados

    despues = _radiografia(db)

    # ── CUADRE 1 · que no se pierda una fila deja de ser una promesa y pasa a ser una resta
    if despues["filas"] - antes["filas"] != insertados:
        raise Aborta(
            f"no cuadran las escrituras: el RETURNING dice {insertados} insertadas y la tabla "
            f"creció en {despues['filas'] - antes['filas']}. Es un fallo de este script, no "
            f"del dato, y se deshace la transacción entera.")
    if insertados + actualizados > n_fuente:
        raise Aborta(f"se tocaron {insertados + actualizados} asientos para {n_fuente} líneas "
                     f"de origen: una línea produjo más de un asiento.")

    # ── CUADRE 2 · el libro contra el proveedor, AL CENTAVO
    # Se compara solo el origen 'proveedor': el día que existan asientos de 'orden' sumarían de
    # más contra un archivo que no los contiene, y este cuadre dejaría de significar nada.
    cuadre = dict(db.execute(text(f"""
        SELECT a.n AS a_n, a.l AS a_l, a.i AS a_i,
               c.n AS c_n, c.l AS c_l, c.i AS c_i
          FROM (SELECT count(*) AS n, COALESCE(sum(litros), 0) AS l,
                       COALESCE(sum(importe), 0) AS i
                  FROM {TABLA} WHERE vigente AND origen = 'proveedor') a
         CROSS JOIN
               (SELECT count(*) AS n, COALESCE(sum(litros_txt::numeric), 0) AS l,
                       COALESCE(sum(importe_txt::numeric), 0) AS i
                  FROM cargas_proveedor WHERE vigente) c
    """)).mappings().one())
    diferencias = [
        etiqueta for etiqueta, a, c in (
            ("cargas", cuadre["a_n"], cuadre["c_n"]),
            ("litros", cuadre["a_l"], cuadre["c_l"]),
            ("importe", cuadre["a_i"], cuadre["c_i"]))
        if a != c]
    if diferencias:
        raise Aborta(
            f"el libro NO reproduce el archivo del proveedor ({', '.join(diferencias)}): "
            f"{cuadre['a_n']} asientos / {cuadre['a_l']} L / ${cuadre['a_i']} contra "
            f"{cuadre['c_n']} cargas / {cuadre['c_l']} L / ${cuadre['c_i']}. "
            f"La diferencia exigida es 0 EXACTO, no 'menor a un centavo': el libro es lo que "
            f"E6 concilia contra el CFDI. No se escribe nada.")

    # ── CUADRE 3 · biyección con el origen
    huerfanos_asiento = db.execute(text(f"""
        SELECT count(*) FROM {TABLA} a
         WHERE a.origen = 'proveedor'
           AND NOT EXISTS (SELECT 1 FROM cargas_proveedor c WHERE c.id = a.carga_id)
    """)).scalar_one()
    sin_asiento = db.execute(text(f"""
        SELECT count(*) FROM cargas_proveedor c
         WHERE c.vigente
           AND NOT EXISTS (SELECT 1 FROM {TABLA} a WHERE a.carga_id = c.id)
    """)).scalar_one()
    if huerfanos_asiento or sin_asiento:
        raise Aborta(f"la biyección con el origen está rota: {sin_asiento} carga(s) vigentes "
                     f"sin asiento y {huerfanos_asiento} asiento(s) sin carga.")

    # ── CUADRE 4 · desincronizados: recalcular la huella y comparar
    # Después del UPSERT tiene que dar 0 por construcción. Se comprueba igual porque es la
    # consulta que hay que correr después de CADA importación y de CADA aplicación de propuestas
    # del catálogo, y aquí queda demostrado que sabe detectar la deriva.
    desincronizados = db.execute(text(f"""
        SELECT count(*) FROM {TABLA} a
          JOIN cargas_proveedor c ON c.id = a.carga_id
         WHERE a.origen_huella IS DISTINCT FROM {SQL_HUELLA}
    """)).scalar_one()
    if desincronizados:
        raise Aborta(f"{desincronizados} asiento(s) quedaron desincronizados de su línea "
                     f"después de proyectar. Es un fallo de este script.")

    # ── CUADRE 5 · eventos huérfanos (regla ENTRE filas: ningún CHECK la puede expresar)
    # Un asiento descontado cuyo hermano contable ya no está vivo son litros que nadie cuenta.
    # El esquema no puede impedirlo y por eso se mira aquí, en cada corrida.
    huerfanos_evento = db.execute(text(f"""
        SELECT count(*) FROM {TABLA} a
         WHERE NOT a.contable
           AND NOT EXISTS (SELECT 1 FROM {TABLA} b
                            WHERE b.evento_id = a.evento_id AND b.contable AND b.vigente)
    """)).scalar_one()
    if huerfanos_evento and not forzar:
        raise Aborta(
            f"{huerfanos_evento} asiento(s) descontados se quedaron sin un hermano contable y "
            f"vigente: son litros que nadie está contando, y el mes sale MENOR de lo que dice "
            f"la factura.\n"
            f"  Míralos con:\n"
            f"    SELECT id, carga_id, evento_id, motivo FROM {TABLA} a WHERE NOT a.contable "
            f"AND NOT EXISTS (SELECT 1 FROM {TABLA} b WHERE b.evento_id = a.evento_id AND "
            f"b.contable AND b.vigente);\n"
            f"  Vuelve a sellar la pareja desde la evidencia del emparejador, o repite con "
            f"--forzar si ya sabes por qué están así.")

    informe = {
        "antes": antes, "despues": despues, "n_fuente": n_fuente,
        "insertados": insertados, "actualizados": actualizados,
        "sin_cambios": n_fuente - len(tocados),
        "cuadre": cuadre, "desincronizados": desincronizados,
        "huerfanos_evento": huerfanos_evento,
        "particion": db.execute(text(f"""
            SELECT atribucion, destino, count(*) AS n,
                   sum(litros) AS litros, COALESCE(sum(importe), 0) AS importe
              FROM {TABLA} WHERE vigente
             GROUP BY atribucion, destino
             ORDER BY atribucion, destino""")).mappings().all(),
        "combustible": db.execute(text(f"""
            SELECT combustible, count(*) AS n, sum(litros) AS litros
              FROM {TABLA} WHERE vigente GROUP BY combustible
             ORDER BY combustible""")).mappings().all(),
        "para_e4": dict(db.execute(text(f"""
            SELECT count(*) AS n, COALESCE(sum(litros), 0) AS litros,
                   count(DISTINCT unidad_id) AS unidades,
                   count(DISTINCT remolque_id) AS remolques
              FROM {TABLA}
             WHERE vigente AND contable AND atribucion = 'activo'""")).mappings().one()),
        "cuarentena": db.execute(text(f"""
            SELECT COALESCE(c.eco_norm, '(sin económico)') AS eco,
                   count(*) AS n, sum(a.litros) AS litros
              FROM {TABLA} a JOIN cargas_proveedor c ON c.id = a.carga_id
             WHERE a.vigente AND a.atribucion = 'cuarentena'
             GROUP BY 1 ORDER BY 3 DESC""")).mappings().all(),
        "fuera": db.execute(text(f"""
            SELECT COALESCE(c.producto_norm, '(sin producto)') AS producto,
                   count(*) AS n, sum(a.litros) AS litros,
                   COALESCE(sum(a.importe), 0) AS importe
              FROM {TABLA} a JOIN cargas_proveedor c ON c.id = a.carga_id
             WHERE a.vigente AND a.atribucion = 'fuera_flota'
             GROUP BY 1 ORDER BY 3 DESC""")).mappings().all(),
        # El caso que hoy no existe y mañana sí: una línea de producto no-diésel que ADEMÁS
        # resolvió activo. Entra como 'activo' (tiene dueño) y solo `combustible` la separa.
        "no_diesel_con_activo": db.execute(text(f"""
            SELECT count(*) AS n, COALESCE(sum(litros), 0) AS litros
              FROM {TABLA}
             WHERE vigente AND atribucion = 'activo' AND combustible <> 'diesel'
        """)).mappings().one(),
        "jubilados_ahora": despues["jubilados"] - antes["jubilados"],
    }
    _imprimir(db, informe, dry, forzar)

    if dry:
        # Se deshace TODO. A diferencia de la ingesta de E2, aquí NO queda testigo: una
        # proyección no es un documento que llegó y del que haya que dejar constancia, es una
        # vista recalculable. Registrar la simulación solo inventaría historia.
        db.rollback()
    else:
        db.commit()
    return informe


# ─────────────────────────────────────────────────────────────────────────────
# LO QUE VE LA PERSONA
# ─────────────────────────────────────────────────────────────────────────────

def _l(x) -> str:
    """Litros con sus tres decimales exactos. Es Decimal, no float: formatear con `%f` lo
    convertiría a binario y devolvería la deriva que Numeric acaba de quitar."""
    return f"{Decimal(x):,.3f}"


def _p(x) -> str:
    return f"${Decimal(x):,.2f}"


def _imprimir(db, inf, dry, forzar):
    a, d, cu = inf["antes"], inf["despues"], inf["cuadre"]
    print("=" * 78)
    print("E3 · LIBRO MAYOR CONSUMO · proyección desde cargas_proveedor")
    print(f"fuente: {inf['n_fuente']} línea(s) — las vigentes MÁS las jubiladas que ya tenían "
          f"asiento")
    print(f"libro antes: {a['filas']} asiento(s) ({a['vigentes']} vigentes) · "
          f"última proyección {a['ultima']}")
    print("-" * 78)
    print(f"  ESCRITURAS   {inf['insertados']} insertados · {inf['actualizados']} actualizados "
          f"· {inf['sin_cambios']} sin cambios")
    print(f"               {inf['insertados']} + {inf['actualizados']} + "
          f"{inf['sin_cambios']} = {inf['n_fuente']} líneas de origen")
    if inf["sin_cambios"] == inf["n_fuente"]:
        print("               nada cambió en el origen: NI UNA fila tocó `proyectado_en`. "
              "Eso es la idempotencia, medida en escrituras y no en conteos.")
    if inf["jubilados_ahora"]:
        print(f"               {inf['jubilados_ahora']} asiento(s) pasaron a vigente=false "
              f"porque su línea dejó de estar vigente en el origen (una corrección de E2 o "
              f"una corrida anulada). No se borró ninguno.")
    print(f"  LIBRO AHORA  {d['filas']} asiento(s) · {d['vigentes']} vigentes · "
          f"{d['jubilados']} jubilados")

    print("-" * 78)
    print("  CUADRE CONTRA EL PROVEEDOR (la diferencia exigida es 0 EXACTO, no 'un centavo')")
    print(f"     libro    {cu['a_n']:>5} asientos · {_l(cu['a_l']):>14} L · "
          f"{_p(cu['a_i']):>17}")
    print(f"     origen   {cu['c_n']:>5} cargas   · {_l(cu['c_l']):>14} L · "
          f"{_p(cu['c_i']):>17}   (sum(litros_txt::numeric) / sum(importe_txt::numeric))")
    print(f"     diferencia   {cu['a_n'] - cu['c_n']:>5}          · "
          f"{_l(Decimal(cu['a_l']) - Decimal(cu['c_l'])):>14} L · "
          f"{_p(Decimal(cu['a_i']) - Decimal(cu['c_i'])):>17}")

    print("\n  PARTICIÓN POR ATRIBUCIÓN Y DESTINO")
    print("     'atribucion' contesta UNA pregunta: ¿por qué este litro no tiene activo?")
    for f in inf["particion"]:
        print(f"     {f['atribucion']:<12} {f['destino'] or '(sin destino)':<14} "
              f"{f['n']:>4} · {_l(f['litros']):>12} L · {_p(f['importe']):>15}")

    print("\n  COMBUSTIBLE   (columna propia: sumar gasolina dentro del diésel exige "
          "escribirlo a propósito)")
    for f in inf["combustible"]:
        print(f"     {f['combustible']:<12} {f['n']:>4} · {_l(f['litros']):>12} L")
    if inf["no_diesel_con_activo"]["n"]:
        print(f"     AVISO: {inf['no_diesel_con_activo']['n']} asiento(s) con activo resuelto "
              f"NO son diésel ({_l(inf['no_diesel_con_activo']['litros'])} L). Entran como "
              f"atribucion='activo' porque tienen dueño; lo único que los separa del km/L del "
              f"diésel es la columna `combustible`. E4 tiene que filtrarla.")

    e4 = inf["para_e4"]
    print(f"\n  LO QUE VERÁ E4   WHERE vigente AND contable AND atribucion = 'activo'")
    print(f"     {e4['n']} asiento(s) · {_l(e4['litros'])} L · {e4['unidades']} unidad(es) "
          f"· {e4['remolques']} remolque(s)")

    if inf["cuarentena"]:
        total = sum(Decimal(f["litros"]) for f in inf["cuarentena"])
        print(f"\n  ── CUARENTENA ({sum(f['n'] for f in inf['cuarentena'])} asientos, "
              f"{_l(total)} L) ──────────")
        print("     flota REAL sin activo en el catálogo todavía. NO se pueden atribuir, pero "
              "tampoco desaparecer:")
        print("     entran al libro con los dos activos en NULL y quedan como cola de trabajo "
              "en ix_asientos_pendientes.")
        print("     Cuando alguien resuelva el catálogo, atribuirlas es un UPDATE de estos "
              "mismos asientos, jamás un INSERT.")
        for f in inf["cuarentena"]:
            print(f"     {f['eco']:<18} {f['n']:>4} asientos · {_l(f['litros']):>12} L")

    if inf["fuera"]:
        print(f"\n  ── FUERA DE FLOTA ({sum(f['n'] for f in inf['fuera'])}) ────────────────")
        print("     no es cuarentena: no hay activo que dar de alta. ENTRAN igual, porque sin "
              "ellas el libro")
        print("     no reproduciría la factura y E6 jamás podría conciliarlo al centavo contra "
              "el CFDI.")
        for f in inf["fuera"]:
            print(f"     {f['producto']:<18} {f['n']:>4} asientos · {_l(f['litros']):>12} L · "
                  f"{_p(f['importe']):>15}")

    # `pg_sequences.last_value` es NULL mientras nadie haya llamado a nextval(): 0 eventos
    # servidos. Se lee así y no con `SELECT last_value FROM la_secuencia` porque esa consulta
    # devuelve 1 con is_called=false y haría parecer que ya hay un evento declarado.
    servidos = db.execute(text(
        "SELECT last_value FROM pg_sequences WHERE sequencename = :n"),
        {"n": SECUENCIA}).scalar()
    print("\n  ── LA PUERTA DEL EMPAREJADOR, CABLEADA Y VACÍA ──────────────")
    print(f"     asientos con evento_id      {d['con_evento']:>5}   (el hecho físico; hoy "
          f"nadie ha declarado ninguno)")
    print(f"     asientos NO contables       {d['no_contables']:>5}   (litros que existen y "
          f"no entran al SUM)")
    print(f"     asientos de origen 'orden'  {d['de_orden']:>5}   (la segunda fuente; hoy no "
          f"hay órdenes en la base)")
    # Se dice "último id servido" y no "eventos declarados": `nextval` NO es transaccional, así
    # que la secuencia adelanta también por ensayos deshechos (el verificador declara dos por
    # corrida). Los eventos que de verdad están declarados son la línea de arriba, y los huecos
    # de la secuencia son normales y no significan nada.
    print(f"     último id servido por {SECUENCIA}: {servidos or 0}   (los huecos son "
          f"normales: nextval no se deshace con un rollback)")
    print(f"     asientos desincronizados de su línea: {inf['desincronizados']}   "
          f"(esta consulta se corre tras CADA importación y CADA aplicación de propuestas)")
    print(f"     eventos huérfanos (descontado sin hermano contable): "
          f"{inf['huerfanos_evento']}"
          + ("   ← ADMITIDOS CON --forzar" if inf["huerfanos_evento"] and forzar else ""))
    print("     ESTO NO EMPAREJA NADA, y está medido por qué: las cargas del proveedor son de")
    print("     julio y las órdenes que existían eran del 1 al 14 de agosto, de un lote de")
    print("     prueba ya borrado. Emparejar por activo + tiempo tampoco bastaría: hay 162")
    print("     pares del mismo activo dentro de ±6 h DENTRO de julio, y el hueco mínimo entre")
    print("     dos cargas del mismo activo es de 2.00 minutos.")

    print("\n" + "=" * 78)
    if dry:
        print("SIMULACIÓN: la sentencia se ejecutó de verdad contra las filas de verdad y la")
        print("transacción se deshizo entera. No se escribió NI UN asiento, y no queda testigo")
        print("de esta corrida: una proyección no es un documento que llegó, es una vista")
        print("recalculable, y registrar la simulación solo inventaría historia.")
        print("Para escribirla:  python -m scripts.proyectar_consumo")
    else:
        print(f"Libro escrito: {d['filas']} asiento(s), {d['vigentes']} vigentes. "
              f"cargas_proveedor NO se tocó.")
        print("Volver a correr esto no puede duplicar un solo litro: `ux_asientos_carga` es un")
        print("índice único sobre carga_id y Postgres rechazaría el segundo asiento.")
        print("\nDeshacer SOLO la proyección (regenerable en cualquier momento):")
        if d["con_evento"] or d["no_contables"]:
            print(f"  ATENCIÓN: {d['con_evento']} asiento(s) llevan evento_id y "
                  f"{d['no_contables']} están descontados. Un DELETE se lleva por delante esas")
            print("  conclusiones del emparejador y hay que volver a sellarlas desde su tabla "
                  "de evidencia.")
        print("  DELETE FROM asientos_consumo WHERE origen = 'proveedor';")
        print("  -- y volver a correr:  python -m scripts.proyectar_consumo")
        print("\nRevertir la etapa E3 entera (ni un ALTER que deshacer):")
        print("  DROP TABLE asientos_consumo;")
        print(f"  DROP SEQUENCE {SECUENCIA};")
        print("  SELECT count(*) FROM pg_type WHERE typtype = 'e';   -- debe seguir en 6")


def main():
    ap = argparse.ArgumentParser(
        description="E3 · Proyecta cargas_proveedor sobre el libro mayor asientos_consumo.",
        epilog="Idempotente: correrlo dos veces no puede duplicar un litro. No escribe en "
               "cargas_proveedor, viajes, unidades ni remolques.")
    ap.add_argument("--dry-run", dest="dry", action="store_true",
                    help="hace todo el trabajo y deshace la transacción; no escribe nada")
    ap.add_argument("--forzar", action="store_true",
                    help="proyectar aunque queden eventos huérfanos (un asiento descontado "
                         "sin hermano contable vivo)")
    a = ap.parse_args()

    with SessionLocal() as db:
        try:
            proyectar(db, dry=a.dry, forzar=a.forzar)
        except Aborta as e:
            db.rollback()
            print("=" * 78)
            print("SE DETUVO SIN ESCRIBIR NADA · proyección del libro mayor")
            print(f"  {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
