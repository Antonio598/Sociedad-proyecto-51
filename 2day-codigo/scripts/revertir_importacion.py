"""E2 · Deshace UNA corrida de importación. Dos caminos, y el blando es el de casa.

Existe desde el primer día, antes de que haga falta. Una reversión que se escribe la noche
que algo salió mal se escribe con prisa y sobre datos que ya duelen.

LOS DOS CAMINOS

  ANULACIÓN BLANDA (lo que hace sin banderas)
      `vigente = False` en la corrida y en sus filas, más anulada_en / anulada_por /
      motivo_anulacion. NO BORRA NADA: las 326 filas siguen ahí, con sus litros, su
      `fila_cruda` y el archivo original en `archivo_b64`, y se pueden consultar mañana.
      Lo único que cambia es que dejan de contar y LIBERAN LAS LLAVES: los dos índices
      únicos son parciales `WHERE vigente`, así que tras anular se puede volver a importar
      el mismo archivo. Es el camino para todo lo que ya se miró, se compartió o se cobró.

  BORRADO DURO (--duro)
      `DELETE` de las filas y de la corrida. Limpio mientras E2 sea hoja del grafo —el
      `ondelete="SET NULL"` de `sustituye_a_id` deja que un solo DELETE se lleve las filas
      sin trabarse contra sus propias autorreferencias—, y por eso mismo se comprueba EN
      CADA CORRIDA que siga siéndolo: el día que E3 apunte a `cargas_proveedor`, este
      camino se cierra solo en vez de arrastrar datos de otra etapa.
      Es para la corrida virgen: el archivo equivocado que se acaba de importar y que nadie
      ha visto. Se lleva por delante `archivo_b64`, o sea la copia archivada del Excel.

CUÁNDO SE NIEGA A BORRAR EN DURO
  · Si filas de OTRA corrida sustituyen a filas de esta. Borrarlas dejaría a las nuevas sin
    a qué apuntar (la FK las pondría en NULL, en silencio) y la cadena de revisiones —qué
    corrigió qué— se rompería sin que nada lo denuncie. No lo levanta ni --forzar: primero
    se revierte la corrida más nueva, o se usa el camino blando, que conserva todo.
  · Si alguna tabla ajena a E2 apunta a `cargas_proveedor` o a `importaciones_proveedor`.
  · Si una persona ya revisó correcciones de esta corrida (eso sí lo levanta --forzar, que
    es la diferencia entre destruir datos y destruir trabajo de alguien).

LAS PREDECESORAS VUELVEN. Si esta corrida jubiló filas de una corrida anterior, retirarla
las devuelve a `vigente = True`: antes de la corrida estaban vivas y deshacerla tiene que
dejar la tabla como estaba. Dos excepciones, y las dos se imprimen fila por fila en vez de
resolverse en silencio: no vuelve la predecesora cuya llave natural ya ocupa otra fila viva
—restaurarla reventaría contra `ux_cargas_llave` y además sería falso—, ni la que pertenece
a una corrida anulada, porque revivirla haría contar filas que alguien retiró a propósito.

Uso:
    python -m scripts.revertir_importacion                      (lista las corridas)
    python -m scripts.revertir_importacion 7 --dry-run          (no escribe NADA)
    python -m scripts.revertir_importacion 7 --motivo "archivo de junio, no de julio"
    python -m scripts.revertir_importacion 7 --duro --guardar-copia ~/Downloads
"""

import argparse
import base64
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# La consola de Windows es cp1252 y revienta con los acentos de los motivos de anulación,
# que es justo lo que este script imprime para que una persona confirme qué está deshaciendo.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import delete, inspect, select, text, update  # noqa: E402

from app.config import _tz  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    CargaProveedor,
    ImportacionProveedor,
    Proveedor,
    Usuario,
)
# Se REUSAN del importador, no se copian. `Aborta` para que detenerse signifique lo mismo en
# los dos scripts, y `_recalcular_contadores` porque los contadores de estaciones, tarjetas y
# empleados se derivan de las cargas vigentes: una segunda versión de esa consulta acabaría
# contando distinto que la que los escribió, y nadie sabría cuál miente.
from scripts.import_proveedor import Aborta, _recalcular_contadores  # noqa: E402

MOTIVO_POR_DEFECTO = "sin motivo declarado (scripts.revertir_importacion)"

TABLAS_E2 = ("proveedores", "importaciones_proveedor", "cargas_proveedor")


# ─────────────────────────────────────────────────────────────────────────────
# LO QUE HAY QUE SABER ANTES DE TOCAR NADA
# ─────────────────────────────────────────────────────────────────────────────

def _exigir_tablas(db):
    insp = inspect(db.get_bind())
    faltan = [t for t in TABLAS_E2 if not insp.has_table(t)]
    if faltan:
        raise Aborta(f"faltan las tablas de E2 ({', '.join(faltan)}): no hay nada que "
                     f"revertir. Corre primero:  python -m scripts.migrate_e2")


def listar(db):
    """El historial de corridas. Es lo que contesta '¿cuál era el id?' sin abrir psql."""
    filas = db.execute(text("""
        SELECT i.id, p.clave, i.archivo, i.estado, i.vigente, i.n_filas_leidas,
               i.importado_en,
               (SELECT count(*) FROM cargas_proveedor c
                 WHERE c.importacion_id = i.id) AS cargas,
               (SELECT count(*) FROM cargas_proveedor c
                 WHERE c.importacion_id = i.id AND c.vigente) AS vigentes
          FROM importaciones_proveedor i
          JOIN proveedores p ON p.id = i.proveedor_id
         ORDER BY i.id""")).mappings().all()
    if not filas:
        print("No hay ninguna corrida registrada en `importaciones_proveedor`.")
        print("  Se importa con:  python -m scripts.import_proveedor --proveedor OXXO "
              "--archivo RUTA")
        return
    print(f"{'id':>4}  {'prov':<5} {'estado':<9} {'vig':<4} {'leídas':>7} {'cargas':>7} "
          f"{'vigentes':>9}  {'importada':<17} archivo")
    for f in filas:
        cuando = f["importado_en"].astimezone(_tz())
        print(f"{f['id']:>4}  {f['clave']:<5} {f['estado']:<9} "
              f"{'sí' if f['vigente'] else 'no':<4} {f['n_filas_leidas']:>7} "
              f"{f['cargas']:>7} {f['vigentes']:>9}  {cuando:%d/%m/%Y %H:%M}    "
              f"{f['archivo'][:40]}")


def _panorama(db, imp_id) -> dict:
    """Todo lo que hay que saber de las filas de una corrida, en una sola consulta.

    Con la tabla vacía devuelve ceros, no una excepción: revertir una corrida que no
    escribió ninguna carga (una simulación, por ejemplo) es un caso normal, no un error.
    """
    return dict(db.execute(text("""
        SELECT count(*)                                              AS n,
               count(*) FILTER (WHERE vigente)                       AS vigentes,
               COALESCE(sum(litros)  FILTER (WHERE vigente), 0)      AS litros,
               COALESCE(sum(importe) FILTER (WHERE vigente), 0)      AS importe,
               count(*) FILTER (WHERE revision > 1)                  AS correcciones,
               count(*) FILTER (WHERE estado_revision = 'pendiente') AS pendientes,
               count(*) FILTER (WHERE revisada_por_id IS NOT NULL)   AS revisadas
          FROM cargas_proveedor
         WHERE importacion_id = :i"""), {"i": imp_id}).mappings().one())


def jubiladas_por(db, imp_id):
    """Las filas de corridas ANTERIORES que esta jubiló, con las dos razones para no revivirlas.

    `ocupada_por` trae el id de la fila viva que hoy tiene esa misma llave natural sin ser de
    esta corrida. NULL = la llave queda libre en cuanto esta corrida se retire y la
    predecesora puede volver a `vigente`. Con id = restaurarla violaría `ux_cargas_llave` y
    además sería mentira: esa llave ya tiene dueña viva y más nueva.

    `corrida_viva` dice si la corrida DE LA PREDECESORA sigue vigente. Si está anulada, la
    predecesora no vuelve: revivirla haría contar una fila de una corrida que alguien retiró
    a propósito, y anular dejaría de significar lo que dice.
    """
    return db.execute(text("""
        SELECT DISTINCT p.id, p.estacion_txt, p.folio_txt, p.revision,
               p.importacion_id, i.vigente AS corrida_viva,
               (SELECT min(o.id) FROM cargas_proveedor o
                 WHERE o.vigente
                   AND o.proveedor_id  = p.proveedor_id
                   AND o.estacion_txt  = p.estacion_txt
                   AND o.folio_txt     = p.folio_txt
                   AND o.importacion_id <> :i) AS ocupada_por
          FROM cargas_proveedor p
          JOIN cargas_proveedor c ON c.sustituye_a_id = p.id
          JOIN importaciones_proveedor i ON i.id = p.importacion_id
         WHERE c.importacion_id = :i AND NOT p.vigente
         ORDER BY p.id"""), {"i": imp_id}).mappings().all()


def dependientes_de(db, imp_id):
    """Filas de OTRAS corridas que sustituyen a filas de esta. Es el veto del borrado duro."""
    return db.execute(text("""
        SELECT c.importacion_id AS corrida, count(*) AS n
          FROM cargas_proveedor c
          JOIN cargas_proveedor v ON c.sustituye_a_id = v.id
         WHERE v.importacion_id = :i AND c.importacion_id <> :i
         GROUP BY c.importacion_id
         ORDER BY c.importacion_id"""), {"i": imp_id}).mappings().all()


def tablas_que_apuntan(db):
    """Tablas AJENAS a E2 con una FK hacia sus dos tablas borrables.

    Hoy no hay ninguna y por eso el borrado duro es un DELETE limpio. Se comprueba en cada
    corrida porque esa es una propiedad del momento, no del diseño: en cuanto E3 guarde a qué
    orden de despacho pertenece una carga, borrar en duro dejaría de ser asunto de E2.
    """
    return [r[0] for r in db.execute(text("""
        SELECT DISTINCT c.conrelid::regclass::text
          FROM pg_constraint c
         WHERE c.contype = 'f'
           AND c.confrelid IN ('cargas_proveedor'::regclass,
                               'importaciones_proveedor'::regclass)
           AND c.conrelid NOT IN ('cargas_proveedor'::regclass,
                                  'importaciones_proveedor'::regclass)
         ORDER BY 1"""))]


# ─────────────────────────────────────────────────────────────────────────────
# LO QUE VE LA PERSONA ANTES DE DECIDIR
# ─────────────────────────────────────────────────────────────────────────────

def _cabecera(db, imp, prov, pan):
    print("=" * 78)
    print(f"CORRIDA {imp.id} · {prov.clave} · {imp.archivo}")
    print(f"huella del archivo: {imp.sha256[:16]}… · "
          f"{imp.archivo_bytes or 0:,} bytes")
    # `length()` en SQL y no `len(imp.archivo_b64)`: la columna es deferred justamente para
    # que nadie arrastre 64 KB de base64 sin querer, y aquí solo hace falta saber si está.
    b64 = db.scalar(text("SELECT length(archivo_b64) FROM importaciones_proveedor "
                         "WHERE id = :i"), {"i": imp.id})
    copia = f"sí, {b64:,} caracteres b64" if b64 else "no"
    print(f"importada el {imp.importado_en.astimezone(_tz()):%d/%m/%Y %H:%M} · "
          f"estado '{imp.estado}' · {'vigente' if imp.vigente else 'NO vigente'} · "
          f"copia archivada del Excel: {copia}")
    if imp.periodo_desde:
        print(f"periodo {imp.periodo_desde:%d/%m/%Y %H:%M} .. "
              f"{imp.periodo_hasta:%d/%m/%Y %H:%M} · {imp.n_filas_leidas} filas leídas")
    if imp.anulada_en:
        print(f"YA ANULADA el {imp.anulada_en.astimezone(_tz()):%d/%m/%Y %H:%M}: "
              f"{imp.motivo_anulacion or '(sin motivo)'}")
    print("-" * 78)
    print("  SUS FILAS EN cargas_proveedor")
    print(f"     guardadas            {pan['n']:>6}")
    print(f"     vigentes hoy         {pan['vigentes']:>6}   "
          f"{pan['litros']:,.2f} L · ${pan['importe']:,.2f}")
    print(f"     correcciones (rev>1) {pan['correcciones']:>6}   "
          f"{pan['pendientes']} sin revisar · {pan['revisadas']} ya revisadas por alguien")


def _resumen_predecesoras(jub):
    """Reparte las predecesoras en las que vuelven y las que no, y DICE por qué no."""
    libres = [j for j in jub if j["ocupada_por"] is None and j["corrida_viva"]]
    ocupadas = [j for j in jub if j["ocupada_por"] is not None]
    de_anulada = [j for j in jub if j["ocupada_por"] is None and not j["corrida_viva"]]
    if not jub:
        return libres, ocupadas
    print(f"     jubiló {len(jub)} fila(s) de corridas anteriores: {len(libres)} "
          f"vuelven a vigente, {len(jub) - len(libres)} no")
    for j in ocupadas[:10]:
        print(f"       fila {j['id']} (estación {j['estacion_txt']}, folio "
              f"{j['folio_txt']}) NO vuelve: la llave ya la ocupa la fila "
              f"{j['ocupada_por']}, viva y más nueva")
    for j in de_anulada[:10]:
        print(f"       fila {j['id']} (estación {j['estacion_txt']}, folio "
              f"{j['folio_txt']}) NO vuelve: su corrida {j['importacion_id']} está anulada")
    return libres, ocupadas


# ─────────────────────────────────────────────────────────────────────────────
# LOS DOS CAMINOS
# ─────────────────────────────────────────────────────────────────────────────

def _guardar_copia(db, imp, destino):
    """Escribe en disco el Excel archivado y comprueba su huella antes de que el DELETE se
    lo lleve. Si el sha no coincide, la copia no sirve como prueba y no se sigue."""
    b64 = db.scalar(select(ImportacionProveedor.archivo_b64).where(
        ImportacionProveedor.id == imp.id))
    if not b64:
        print(f"  AVISO: la corrida {imp.id} no tiene copia archivada del Excel "
              f"(archivo_b64 vacío): no hay nada que guardar.")
        return None
    datos = base64.b64decode(b64)
    sha = hashlib.sha256(datos).hexdigest()
    if sha != imp.sha256:
        raise Aborta(f"la copia archivada NO coincide con su huella (guardada "
                     f"{imp.sha256[:16]}…, calculada {sha[:16]}…). Alguien tocó la fila: "
                     f"no se borra nada hasta entender qué pasó.")
    ruta = Path(os.path.expanduser(destino))
    if ruta.is_dir():
        ruta = ruta / imp.archivo
    ruta.write_bytes(datos)
    print(f"  copia guardada en {ruta} · {len(datos):,} bytes · sha {sha[:16]}… correcto")
    return ruta


def _restaurar(db, libres):
    """Devuelve a `vigente` las predecesoras cuya llave quedó libre. SIEMPRE después de
    retirar las filas de esta corrida: hacerlo antes pondría dos filas vivas con la misma
    llave natural y el índice único parcial rechazaría el UPDATE."""
    if not libres:
        return 0
    ids = [j["id"] for j in libres]
    db.execute(update(CargaProveedor).where(CargaProveedor.id.in_(ids)).values(vigente=True))
    return len(ids)


def anular(db, imp, libres, motivo, por_id):
    """Camino blando. Nada se borra: dejan de contar y las llaves quedan libres."""
    n = db.execute(update(CargaProveedor).where(
        CargaProveedor.importacion_id == imp.id,
        CargaProveedor.vigente.is_(True)).values(vigente=False)).rowcount
    imp.vigente = False
    imp.estado = "anulada"
    imp.anulada_en = datetime.now(timezone.utc)
    imp.anulada_por_id = por_id
    imp.motivo_anulacion = motivo[:300]
    revividas = _restaurar(db, libres)
    _recalcular_contadores(db, imp.proveedor_id)
    db.commit()

    print("\n" + "=" * 78)
    print(f"ANULADA la corrida {imp.id}. No se borró una sola fila.")
    print(f"  {n} carga(s) pasaron a vigente=false y siguen consultables con sus litros, "
          f"su fila_cruda y el archivo original.")
    if revividas:
        print(f"  {revividas} fila(s) de corridas anteriores volvieron a vigente: antes de "
              f"esta corrida estaban vivas.")
    print(f"  La llave del archivo quedó libre: se puede volver a importar "
          f"(sha {imp.sha256[:16]}…).")
    print("\nPara deshacer la anulación —solo funciona si nadie ocupó esas llaves "
          "mientras tanto:")
    if revividas:
        print(f"  UPDATE cargas_proveedor SET vigente = false WHERE id IN "
              f"(SELECT sustituye_a_id FROM cargas_proveedor "
              f"WHERE importacion_id = {imp.id} AND sustituye_a_id IS NOT NULL);")
    print(f"  UPDATE cargas_proveedor SET vigente = true WHERE importacion_id = {imp.id};")
    print(f"  UPDATE importaciones_proveedor SET vigente = true, estado = 'aplicada', "
          f"anulada_en = NULL, anulada_por_id = NULL, motivo_anulacion = NULL "
          f"WHERE id = {imp.id};")


def borrar(db, imp, prov, libres):
    """Camino duro. Se lleva las filas y la corrida; no hay UPDATE que lo deshaga."""
    # Se copian ANTES de borrar: después de `db.delete(imp)` estos valores son lo único que
    # queda de la corrida, y el mensaje final los necesita para decir cómo rehacerla.
    ident, prov_id, sha, archivo = imp.id, imp.proveedor_id, imp.sha256, imp.archivo
    # Un solo DELETE se lleva las filas aunque se sustituyan entre ellas: el
    # `ondelete="SET NULL"` de `sustituye_a_id` evita tener que borrarlas en orden topológico.
    n = db.execute(delete(CargaProveedor).where(
        CargaProveedor.importacion_id == ident).execution_options(
        synchronize_session=False)).rowcount
    revividas = _restaurar(db, libres)
    db.delete(imp)
    _recalcular_contadores(db, prov_id)
    db.commit()

    print("\n" + "=" * 78)
    print(f"BORRADA la corrida {ident}: {n} carga(s) y su registro de importación.")
    if revividas:
        print(f"  {revividas} fila(s) de corridas anteriores volvieron a vigente.")
    print("  Las estaciones, tarjetas y empleados que dio de alta NO se borran: pueden "
          "llevar vínculos que decidió una persona.")
    print("     Los que se hayan quedado sin cargas se ven con:")
    print(f"     SELECT numero_norm, n_cargas FROM tarjetas_combustible "
          f"WHERE proveedor_id = {prov_id} AND n_cargas = 0;")
    print("\nEsto no se deshace con un UPDATE. Se rehace volviendo a importar el archivo:")
    print(f"  python -m scripts.import_proveedor --proveedor {prov.clave} "
          f"--archivo <ruta de {archivo}>")
    print(f"  (misma huella {sha[:16]}…: la lectura es reproducible y las filas vuelven "
          f"idénticas, con ids nuevos)")


def revertir(db, ident, duro=False, dry=False, motivo=MOTIVO_POR_DEFECTO,
             por_id=None, forzar=False, guardar_copia=None):
    """Enseña qué haría y, salvo --dry-run, lo hace. Devuelve el código de salida."""
    _exigir_tablas(db)

    imp = db.get(ImportacionProveedor, ident)
    if imp is None:
        print(f"No existe la corrida {ident} en `importaciones_proveedor`.")
        print("  Corre el script sin argumentos para ver las que hay.")
        return 1
    prov = db.get(Proveedor, imp.proveedor_id)
    prov_id = imp.proveedor_id
    if por_id is not None and db.get(Usuario, por_id) is None:
        raise Aborta(f"no existe el usuario {por_id}: `anulada_por_id` apunta a "
                     f"`usuarios.id` y una anulación sin autor comprobable no vale de nada.")

    pan = _panorama(db, imp.id)
    jub = jubiladas_por(db, imp.id)
    deps = dependientes_de(db, imp.id)

    # Anular lo que ya no cuenta no es un error, pero tampoco es una anulación: se dice y no
    # se escribe. Se decide ANTES de imprimir el plan para no describir un UPDATE que no va a
    # ocurrir, que es la manera más fácil de que un --dry-run mienta.
    sin_efecto = None
    if not duro and imp.estado == "anulada":
        cuando = imp.anulada_en.astimezone(_tz()) if imp.anulada_en else None
        sin_efecto = ("ya está anulada"
                      + (f" desde el {cuando:%d/%m/%Y %H:%M}" if cuando else "")
                      + f" · {imp.motivo_anulacion or '(sin motivo)'}")
    elif not duro and imp.estado == "simulada":
        sin_efecto = ("es una SIMULACIÓN: nació con vigente=false y no escribió ninguna "
                      "carga, así que no hay nada que dejar de contar")

    _cabecera(db, imp, prov, pan)
    libres, _ = _resumen_predecesoras(jub)
    if deps:
        print(f"     LA REFERENCIAN {sum(d['n'] for d in deps)} fila(s) de otras corridas: "
              + ", ".join(f"corrida {d['corrida']} ({d['n']})" for d in deps))

    # ── el plan, antes de escribir ──────────────────────────────────────────
    print("-" * 78)
    if sin_efecto:
        print(f"  NADA QUE ANULAR · {sin_efecto}")
        print("     no se escribe nada. Para quitarla también del historial:  --duro")
    elif duro:
        print("  BORRADO DURO · lo que se haría")
        print(f"     DELETE de {pan['n']} carga(s) de cargas_proveedor")
        print(f"     DELETE de la importación {imp.id} (con su copia del Excel)")
    else:
        print("  ANULACIÓN BLANDA · lo que se haría")
        print(f"     UPDATE de {pan['vigentes']} carga(s) vigente -> false "
              f"(las {pan['n']} siguen guardadas)")
        print(f"     UPDATE de la importación {imp.id}: vigente=false, estado='anulada', "
              f"anulada_en, anulada_por_id={por_id}, motivo")
        print(f"     motivo: {motivo}")
    if not sin_efecto:
        if libres:
            print(f"     UPDATE de {len(libres)} predecesora(s) vigente -> true")
        print(f"     y se recalculan los contadores de estaciones, tarjetas y empleados de "
              f"{prov.clave} desde las cargas que queden vivas")

    # ── las negativas del camino duro ───────────────────────────────────────
    if duro:
        if deps:
            raise Aborta(
                f"{sum(d['n'] for d in deps)} fila(s) de otras corridas "
                f"({', '.join(str(d['corrida']) for d in deps)}) sustituyen a filas de "
                f"esta. Borrarlas les dejaría `sustituye_a_id` en NULL —la FK lo hace sola y "
                f"sin avisar— y se perdería qué corrigió qué.\n"
                f"  Usa la anulación blanda (sin --duro), que conserva la cadena entera, o "
                f"revierte primero la corrida más nueva.")
        ajenas = tablas_que_apuntan(db)
        if ajenas:
            raise Aborta(
                f"ya hay tablas fuera de E2 que apuntan a estas dos ({', '.join(ajenas)}). "
                f"El borrado duro está pensado para cuando E2 es hoja del grafo y dejó de "
                f"serlo.\n  Usa la anulación blanda y revisa qué guardó "
                f"{ajenas[0]} sobre estas cargas antes de borrar nada.")
        if pan["revisadas"] and not forzar:
            raise Aborta(
                f"{pan['revisadas']} corrección(es) de esta corrida ya las revisó una "
                f"persona: borrarlas tira ese trabajo, no solo los datos.\n"
                f"  Anúlala (sin --duro) para conservarlo, o repite con --forzar si de "
                f"verdad quieres borrar.")
    # Una corrida ya anulada o meramente simulada se queda como está: repetir la anulación
    # solo serviría para pisar el motivo y la fecha originales, que son lo que explica por
    # qué se retiró.
    if sin_efecto:
        print("\n" + "=" * 78)
        print(f"NO SE ESCRIBIÓ NADA: la corrida {imp.id} no tiene nada que anular.")
        print("  Para quitarla también del historial:  --duro")
        return 0

    if dry:
        print("\n" + "=" * 78)
        print("SIMULACIÓN: no se escribió nada. Ni una fila cambió de estado.")
        if duro and db.scalar(text("SELECT archivo_b64 IS NOT NULL FROM "
                                   "importaciones_proveedor WHERE id = :i"), {"i": imp.id}):
            print("  El borrado real se llevará la copia archivada del Excel. Para "
                  "conservarla:  --guardar-copia <carpeta o archivo>")
        # A propósito NO se hace rollback: hasta aquí solo hubo SELECT, no hay nada que
        # deshacer, y quien llame a esta función desde dentro de su propia transacción —la
        # verificación de E2 corre así— perdería su trabajo sin haber pedido nada.
        return 0

    if duro and guardar_copia:
        _guardar_copia(db, imp, guardar_copia)

    if duro:
        borrar(db, imp, prov, libres)
    else:
        anular(db, imp, libres, motivo, por_id)

    vivas = dict(db.execute(text("""
        SELECT count(*) AS n, COALESCE(sum(litros), 0) AS l FROM cargas_proveedor
         WHERE vigente AND proveedor_id = :p"""), {"p": imp.proveedor_id}).mappings().one())
    print(f"\nQuedan {vivas['n']} carga(s) vigentes de {prov.clave} · "
          f"{vivas['l']:,.2f} L")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="E2 · Revierte una corrida de importación de proveedor.",
        epilog="Sin --duro anula (no borra nada) y libera las llaves para reimportar.")
    ap.add_argument("corrida", nargs="?", type=int,
                    help="id de importaciones_proveedor; sin él, lista las corridas")
    ap.add_argument("--duro", action="store_true",
                    help="DELETE de las filas y de la corrida, en vez de anularlas")
    ap.add_argument("--dry-run", dest="dry", action="store_true",
                    help="enseña exactamente qué haría y cuántas filas toca, sin escribir")
    ap.add_argument("--motivo", default=MOTIVO_POR_DEFECTO,
                    help="por qué se anula; lo lee una persona meses después")
    ap.add_argument("--por", type=int, default=None,
                    help="usuarios.id de quien anula (anulada_por_id)")
    ap.add_argument("--forzar", action="store_true",
                    help="borrar en duro aunque alguien ya haya revisado sus correcciones")
    ap.add_argument("--guardar-copia", dest="guardar_copia", default=None,
                    help="carpeta o archivo donde dejar el Excel archivado antes de borrarlo")
    a = ap.parse_args()

    with SessionLocal() as db:
        try:
            if a.corrida is None:
                _exigir_tablas(db)
                listar(db)
                return 0
            return revertir(db, a.corrida, duro=a.duro, dry=a.dry, motivo=a.motivo,
                            por_id=a.por, forzar=a.forzar, guardar_copia=a.guardar_copia)
        except Aborta as e:
            db.rollback()
            print("\n" + "=" * 78)
            print(f"SE DETUVO SIN TOCAR NADA · corrida {a.corrida}")
            print(f"  {e}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
