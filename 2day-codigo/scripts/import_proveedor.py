"""E2 · Guarda UN archivo de proveedor, fila por fila, tal como llegó. Idempotente.

Este script no interpreta el mes: lo ARCHIVA. Cada renglón del Excel entra a
`cargas_proveedor` con sus 26/25 celdas verbatim y, al lado, lo que se dedujo de ellas
—a qué activo pertenece, si es diésel, en qué instante absoluto ocurrió—, sin que lo
segundo pueda pisar lo primero. No calcula rendimiento, no empareja con órdenes de
despacho (eso es E3) y no escribe una sola fila en `viajes`, `ordenes_despacho`,
`facturas`, `solicitudes_recarga` ni `evidencias_recarga`.

LA MECÁNICA POR FILA, que es lo que hace que reimportar sea seguro. La llave natural es
(proveedor, estación, folio) entre las filas VIGENTES:

  no existe                     -> INSERT.                                  n_nuevas++
  existe, mismo sha256_fila     -> no se toca NADA.                         n_repetidas++
  existe, sha256_fila distinto  -> es una CORRECCIÓN del proveedor: la fila
                                   vieja NO se sobrescribe, pasa a
                                   vigente=False y entra una revisión nueva
                                   con sustituye_a_id y estado_revision
                                   ='pendiente', para que una persona la vea. n_corregidas++

Nada se pierde nunca: el importe viejo y el nuevo quedan los dos consultables. El INSERT
final lleva `ON CONFLICT (proveedor_id, estacion_txt, folio_txt) WHERE vigente DO NOTHING`
como última red —con el predicado REPETIDO, que sin él Postgres no reconoce el índice
parcial y la ingesta falla entera—, pero quien decide es el importador: si esa red llega a
actuar es que hubo un duplicado que este código no previó, y se reporta a gritos.

LO QUE ESTE SCRIPT NO HACE, A PROPÓSITO:
  · NO escribe en `unidades` ni en `remolques`. E2 observa y anota; corregir el catálogo
    es de E1 y pasa por la bandeja de propuestas.
  · NO vincula tarjetas ni empleados a activos ni a personas. Los da de alta con el
    vínculo en NULL y, cuando el archivo contradice un vínculo ya guardado, enciende
    `revisar` y escribe por qué. Avisar no es corregir.
  · NO le pasa `tarjeta_id` a `resolver_activo` cuando el económico ya resolvió. La
    cascada de catalogo.py devuelve la tarjeta ANTES de mirar el económico y con
    confianza 'alta', así que un vínculo desactualizado —una reasignación normal de
    operación— le ganaría a un económico correcto y mandaría litros al activo equivocado,
    en silencio. La tarjeta solo se intenta si el económico y la placa fallaron Y alguien
    marcó `vinculo_confirmado`.

Uso:
    python -m scripts.import_proveedor --proveedor OXXO --archivo RUTA --dry-run
    python -m scripts.import_proveedor --proveedor XYGA --archivo RUTA
    python -m scripts.import_proveedor --proveedor OXXO --archivo RUTA --forzar

  --dry-run  lee, resuelve y enseña el plan completo SIN escribir una sola carga. Deja
             constancia de que se miró: una `importaciones_proveedor` con estado
             'simulada' y vigente=False (no ocupa la llave del archivo).
  --forzar   sirve para dos cosas y las dos son "sé lo que hago": volver a leer un archivo
             que ya está guardado, y confirmar una corrida donde las correcciones del
             proveedor superan el 10% de las filas.
"""

import argparse
import base64
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# La consola de Windows es cp1252 y revienta con los acentos de los motivos ('cuarentena:
# el económico no está en el catálogo'), que es justo lo que una persona tiene que leer.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: E402

from sqlalchemy import String, inspect, select, text, update  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.catalogo import norm_eco, resolver_activo  # noqa: E402
from app.config import _tz  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.ingesta import (  # noqa: E402
    CONTRATOS,
    LayoutInesperado,
    abrir_datos,
    diferencias_encabezado,
    leer_archivo,
    leer_verbatim,
)
from app.models import (  # noqa: E402
    CargaProveedor,
    EmpleadoProveedor,
    EstacionProveedor,
    ImportacionProveedor,
    Proveedor,
    TarjetaCombustible,
    TipoUnidad,
    Unidad,
)

DESCARGAS = os.path.expanduser("~/Downloads")

# Nombre habitual del archivo de cada proveedor, solo para no teclear la ruta entera. El
# de XYGA se llama '.xls' y por dentro es xlsx: la extensión miente y el nombre se conserva
# tal cual porque es una pista real de cómo llegó.
ARCHIVO_POR_DEFECTO = {
    "OXXO": "Despachos.xlsx",
    "XYGA": "REPORTE+DE+CONSUMOS_06_08_2026.xlsx.xls",
}

# Si el proveedor "corrige" más de esta fracción de las filas, la corrida se detiene ANTES
# de escribir. Un retoque cosmético del exportador (un decimal de más, un relleno de
# espacios distinto) cambiaría cientos de huellas de golpe y la bandeja de revisión
# nacería con 313 pendientes falsos que nadie va a revisar una por una.
TOPE_CORRECCIONES = 0.10

# Dos cargas de la MISMA tarjeta, con LOS MISMOS LITROS, dentro de esta ventana. No es un
# duplicado —se marca, jamás bloquea—: es la forma que tiene un cobro doble. Calibrado
# contra el mes de julio: a 5 minutos da 0 pares y a 15 exactamente 1, la ráfaga real del
# T203 del 14/07 (dos veces 150.00 L con 9 minutos de diferencia), que es legítima.
VENTANA_CASI_DUPLICADO_MIN = 15

# Marcas de gasolina en el texto del producto. El orden importa: 'DIESEL' se comprueba
# primero porque 'Mobil Sinergy Diesel Nuevo' también es diésel.
MARCAS_GASOLINA = ("OCTANOS", "MAGNA", "PREMIUM", "GASOLINA")

COLUMNAS_CARGA = set(CargaProveedor.__table__.columns.keys())


class Aborta(Exception):
    """La corrida se detiene sin escribir. El mensaje explica qué hacer, no qué falló."""


# ─────────────────────────────────────────────────────────────────────────────
# GUARDAS DE ANCHO  ·  una fila entra recortada antes que no entrar
# ─────────────────────────────────────────────────────────────────────────────

def _anchos(tabla) -> dict:
    """{columna: longitud} de los VARCHAR de una tabla, leídos del propio modelo.

    Se derivan del esquema en vez de teclearlos aquí para que el día que alguien ensanche
    una columna la guarda lo siga sola. Todo lo medido cabe hoy; esto es para el mes en
    que el proveedor escriba una descripción más larga.
    """
    return {c.name: c.type.length for c in tabla.columns
            if isinstance(c.type, String) and c.type.length}


ANCHOS_CARGA = _anchos(CargaProveedor.__table__)


def _recortar(valores: dict, anchos: dict, avisos: list) -> dict:
    """Recorta lo que no quepa y lo DICE. Nunca se descarta la fila entera.

    Un texto demasiado largo levantaría un DataError a media corrida y tumbaría el mes
    completo por una celda. Aquí la carga entra, el recorte queda escrito en
    `discrepancias` y el valor íntegro sigue en `fila_cruda`, que es el original.
    """
    for campo, largo in anchos.items():
        v = valores.get(campo)
        if isinstance(v, str) and len(v) > largo:
            avisos.append(f"{campo} medía {len(v)} caracteres y la columna admite {largo}: "
                          f"se guardó recortado (el texto completo está en fila_cruda)")
            valores[campo] = v[:largo]
    return valores


def _corto(txt, largo: int):
    """Recorte silencioso para los campos de los catálogos del proveedor (nombre de la
    estación, nota de revisión): ahí el original vive en la carga, no se pierde nada."""
    return txt[:largo] if isinstance(txt, str) and len(txt) > largo else txt


# ─────────────────────────────────────────────────────────────────────────────
# LAS DERIVACIONES  ·  la única inferencia de E2, explícita y rederivable
# ─────────────────────────────────────────────────────────────────────────────

def clasificar_combustible(producto_norm) -> str | None:
    """'diesel' | 'gasolina' | 'otro'. Deliberadamente tosca.

    NO colapsa las cuatro etiquetas de diésel de OXXO en una sola —eso sería interpretar y
    vive en la tabla de equivalencias de E3—: solo contesta la pregunta de la que depende
    `fuera_de_flota`, que es "¿esto es diésel de la flota o es otra cosa?".
    """
    if not producto_norm:
        return None
    if "DIESEL" in producto_norm:
        return "diesel"
    if any(m in producto_norm for m in MARCAS_GASOLINA):
        return "gasolina"
    return "otro"


def _destino(unidad_id, remolque_id, tipos) -> str | None:
    """'motor' | 'termo' | 'indeterminado'. Proyección del TIPO de activo, no un cálculo.

    'indeterminado' no es un fallo del importador: en un CAMION el termo va pegado y
    comparte el económico del motor, así que el archivo NO dice a cuál de los dos fue el
    litro. Son 191 cargas y 15,629 L del mes; un booleano `es_termo` mentiría sobre ellos.
    """
    if remolque_id:
        return "termo"
    if unidad_id:
        return "motor" if tipos.get(unidad_id) == TipoUnidad.TRACTO else "indeterminado"
    return None


def _en_zona(momento, zona, avisos):
    """(zona_aplicada, momento_ref). La zona es DECLARADA, jamás adivinada.

    Se guarda cuál se usó para que `momento_ref` sea una derivación auditable y no una
    interpretación con apariencia de dato: el día que alguien determine la zona real de una
    estación, un UPDATE la recalcula sin tocar un solo campo verbatim.
    """
    if momento is None or not zona:
        return None, None
    try:
        return zona, momento.replace(tzinfo=ZoneInfo(zona))
    except (ZoneInfoNotFoundError, KeyError):
        avisos.append(f"la zona horaria declarada '{zona}' no existe en este sistema: "
                      f"momento_ref queda sin derivar y se recalcula después con un UPDATE")
        return None, None


def _motivo(resuelto, via, fuera, vals) -> str:
    """La regla que se aplicó, escrita en castellano. Es lo que lee una persona en la
    bandeja, y lo que hace auditable la única inferencia de esta etapa."""
    if fuera:
        return f"fuera de flota: producto {vals.get('producto_txt')}"
    if resuelto.ok:
        return f"resuelta por {via} (confianza {resuelto.confianza})"
    if vals.get("eco_norm"):
        return f"cuarentena: el económico {vals['eco_norm']} no está en el catálogo"
    if vals.get("placa_norm"):
        return (f"cuarentena: sin económico, y la placa {vals['placa_norm']} tampoco está "
                f"en el catálogo")
    return "cuarentena: la fila no trae económico ni placa que resuelvan"


# ─────────────────────────────────────────────────────────────────────────────
# RESOLUCIÓN CONTRA EL CATÁLOGO
# ─────────────────────────────────────────────────────────────────────────────

def _resolver(db, cache, eco_txt, placa_txt, eco_norm, placa_norm, tarjeta):
    """Resuelve el activo de una fila. EL ORDEN ES LA REGLA, no una preferencia.

    Primero económico + placa. La tarjeta SOLO se intenta si eso falló y además alguien
    marcó `vinculo_confirmado`: `resolver_activo` devuelve la tarjeta antes de mirar el
    económico y con confianza 'alta', así que un vínculo viejo le ganaría a un económico
    correcto sin que nada fallara. Y no compra cobertura: hoy la tarjeta rescata 0 filas,
    porque las 4 cargas sin económico usan tres tarjetas que jamás aparecen con uno.

    El caché existe porque las 639 filas del mes repiten unas pocas decenas de pares
    (económico, placa): sin él son más de mil consultas para responder siempre lo mismo.
    `resolver_activo` es una función pura de (eco, placa, tarjeta) dentro de una corrida, y
    la lista de discrepancias se COPIA en cada uso, nunca se muta la del caché.
    """
    llave = (eco_norm, placa_norm, None)
    if llave not in cache:
        cache[llave] = resolver_activo(db, eco=eco_txt, placa=placa_txt)
    r = cache[llave]
    if r.ok or tarjeta is None or not tarjeta.vinculo_confirmado:
        return r, None
    llave_t = (eco_norm, placa_norm, tarjeta.id)
    if llave_t not in cache:
        cache[llave_t] = resolver_activo(db, eco=eco_txt, placa=placa_txt,
                                         tarjeta_id=tarjeta.id)
    rt = cache[llave_t]
    if not rt.ok:
        return r, None
    return rt, (f"el económico y la placa no resolvieron; se usó el vínculo confirmado de "
                f"la tarjeta {tarjeta.numero_norm}")


# ─────────────────────────────────────────────────────────────────────────────
# CONTRATO DE LECTURA  ·  el archivo contra lo que la BASE dice esperar
# ─────────────────────────────────────────────────────────────────────────────

def _verificar_contra_la_base(prov, contrato, lectura) -> None:
    """El lector ya comparó el encabezado contra el contrato del CÓDIGO. Aquí se compara
    contra el contrato guardado en la BASE, que es la copia auditable y la que una persona
    puede corregir sin tocar código. Si los dos contratos discrepan entre sí, también se
    detiene: significaría que la base describe una lectura distinta de la que se hizo.
    """
    fallas = []
    for campo, esperado, real in (
        ("hoja", prov.hoja, contrato.hoja),
        ("fila_encabezado", prov.fila_encabezado, contrato.fila_encabezado),
        ("fila_datos", prov.fila_datos, contrato.fila_datos),
        ("n_columnas", prov.n_columnas, contrato.n_columnas),
    ):
        if esperado != real:
            fallas.append(f"la base dice {campo}={esperado!r} y el lector usa {real!r}")
    if prov.n_columnas != len(lectura.encabezado):
        fallas.append(f"la base espera {prov.n_columnas} columnas y se leyeron "
                      f"{len(lectura.encabezado)}")
    if prov.encabezado_esperado:
        fallas += diferencias_encabezado(lectura.encabezado, list(prov.encabezado_esperado))
    if fallas:
        raise Aborta(
            "el archivo no coincide con el contrato guardado en `proveedores`:\n  - "
            + "\n  - ".join(fallas)
            + "\n  Si el proveedor cambió el layout de verdad, actualiza la fila de "
              "`proveedores` (encabezado_esperado / n_columnas) y el contrato de "
              "app/ingesta.py EN LOS DOS SITIOS, y vuelve a correr.")


# ─────────────────────────────────────────────────────────────────────────────
# CATÁLOGOS DEL PROVEEDOR  ·  estaciones, tarjetas y empleados observados
# ─────────────────────────────────────────────────────────────────────────────

def _catalogos(db, prov, filas, cache, avisos_corrida):
    """Da de alta lo que el archivo enseña, SIN vincularlo a nada de la flota.

    Devuelve (estaciones, tarjetas, empleados, altas) indexados por su llave natural. Las
    tres tablas nacen con el vínculo al activo o a la persona en NULL: lo llena alguien de
    carne y hueso desde el panel. Lo único que este código decide es encender `revisar`
    cuando el archivo contradice un vínculo ya guardado.
    """
    estaciones = {e.codigo: e for e in db.execute(
        select(EstacionProveedor).where(EstacionProveedor.proveedor_id == prov.id)).scalars()}
    tarjetas = {t.numero_norm: t for t in db.execute(
        select(TarjetaCombustible).where(TarjetaCombustible.proveedor_id == prov.id)).scalars()}
    empleados = {e.numero: e for e in db.execute(
        select(EmpleadoProveedor).where(EmpleadoProveedor.proveedor_id == prov.id)).scalars()}
    altas = Counter()

    # Qué económico y qué descripción imprime el proveedor junto a cada tarjeta. Es
    # OBSERVACIÓN acumulada del archivo, la materia prima de la propuesta que verá una persona.
    ecos_por_tarjeta = defaultdict(Counter)
    desc_por_tarjeta = {}

    for f in filas:
        v = f["_vals"]

        cod = v["estacion_txt"]
        est = estaciones.get(cod)
        if est is None:
            # `nombre` es Text: la longitud la decide el proveedor y no hay nada que recortar.
            est = EstacionProveedor(proveedor_id=prov.id, codigo=cod,
                                    nombre=v.get("estacion_nombre_txt"))
            db.add(est)
            estaciones[cod] = est
            altas["estaciones"] += 1
        elif not est.nombre and v.get("estacion_nombre_txt"):
            # Solo se rellena lo que estaba vacío: el nombre guardado no se pisa con el del
            # mes nuevo, porque una gasolinera que cambia de rótulo sigue siendo la misma.
            est.nombre = v["estacion_nombre_txt"]

        num = v.get("tarjeta_norm")
        if num:
            tar = tarjetas.get(num)
            if tar is None:
                tar = TarjetaCombustible(proveedor_id=prov.id, numero_txt=v["tarjeta_txt"],
                                         numero_norm=num)
                db.add(tar)
                tarjetas[num] = tar
                altas["tarjetas"] += 1
            if v.get("eco_norm"):
                ecos_por_tarjeta[num][v["eco_norm"]] += 1
            if v.get("descripcion_txt") and num not in desc_por_tarjeta:
                desc_por_tarjeta[num] = v["descripcion_txt"]

        emp = v.get("empleado_txt")
        if emp:
            e = empleados.get(emp)
            if e is None:
                e = EmpleadoProveedor(proveedor_id=prov.id, numero=emp,
                                      nombre_proveedor=v.get("conductor_txt"))
                db.add(e)
                empleados[emp] = e
                altas["empleados"] += 1
            elif not e.nombre_proveedor and v.get("conductor_txt"):
                e.nombre_proveedor = v["conductor_txt"]
            elif (e.nombre_proveedor and v.get("conductor_txt")
                  and e.nombre_proveedor != v["conductor_txt"]):
                # No se sobrescribe: el número de empleado es la identidad estable y el
                # nombre es como lo escribió el capturista ese día. Se avisa y ya.
                avisos_corrida.add(f"el empleado {emp} está guardado como "
                                   f"'{e.nombre_proveedor}' y en este archivo aparece como "
                                   f"'{v['conductor_txt']}'")

    for num, obs in ecos_por_tarjeta.items():
        tar = tarjetas[num]
        principal = obs.most_common(1)[0][0]
        notas = []
        if len(obs) > 1:
            notas.append("aparece con " + ", ".join(f"{e} ({n})" for e, n in obs.most_common()))
        if not tar.eco_observado:
            tar.eco_observado = _corto(principal, 24)
        elif norm_eco(tar.eco_observado) not in obs:
            notas.append(f"antes se observó {tar.eco_observado} y ahora {principal}")
        if tar.unidad_id or tar.remolque_id:
            r = cache.get((principal, None, None))
            if r is None:
                r = resolver_activo(db, eco=principal)
                cache[(principal, None, None)] = r
            if r.ok and (r.unidad_id, r.remolque_id) != (tar.unidad_id, tar.remolque_id):
                notas.append(f"el vínculo guardado apunta a otro activo que el económico "
                             f"{principal}")
        if notas:
            # `revisar` avisa, no corrige. Cambiar el vínculo aquí es exactamente lo que
            # hacía el importador viejo y por lo que hubo litros en la unidad equivocada.
            tar.revisar = True
            tar.nota_revision = _corto("; ".join(notas), 300)
        if not tar.descripcion_proveedor and desc_por_tarjeta.get(num):
            tar.descripcion_proveedor = desc_por_tarjeta[num]

    # Los ids tienen que existir ANTES de insertar las cargas, que los llevan como FK.
    db.flush()
    return estaciones, tarjetas, empleados, altas


def _recalcular_contadores(db, prov_id):
    """Los contadores de estaciones, tarjetas y empleados se RECALCULAN desde las cargas
    vigentes, no se incrementan.

    Sumar de a poco es lo que hace que reimportar un archivo duplique los contadores sin
    duplicar una sola carga: el total dejaría de cuadrar con la tabla que lo produce.
    Recalcular es idempotente por construcción y cuesta tres UPDATE.
    """
    db.execute(update(EstacionProveedor).where(
        EstacionProveedor.proveedor_id == prov_id).values(
        n_cargas=0, primera_carga=None, ultima_carga=None, desfase_mediano_min=None))
    db.execute(update(TarjetaCombustible).where(
        TarjetaCombustible.proveedor_id == prov_id).values(
        n_cargas=0, primera_vista=None, ultima_vista=None))
    db.execute(update(EmpleadoProveedor).where(
        EmpleadoProveedor.proveedor_id == prov_id).values(n_cargas=0, litros=0))

    db.execute(text("""
        UPDATE estaciones_proveedor e
           SET n_cargas = a.n, primera_carga = a.pri, ultima_carga = a.ult,
               desfase_mediano_min = a.med
          FROM (SELECT estacion_id, count(*) AS n,
                       min(momento_local) AS pri, max(momento_local) AS ult,
                       percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY desfase_facturacion_min) AS med
                  FROM cargas_proveedor
                 WHERE vigente AND proveedor_id = :p AND estacion_id IS NOT NULL
                 GROUP BY estacion_id) a
         WHERE e.id = a.estacion_id"""), {"p": prov_id})
    db.execute(text("""
        UPDATE tarjetas_combustible t
           SET n_cargas = a.n, primera_vista = a.pri, ultima_vista = a.ult
          FROM (SELECT tarjeta_id, count(*) AS n,
                       min(momento_local) AS pri, max(momento_local) AS ult
                  FROM cargas_proveedor
                 WHERE vigente AND proveedor_id = :p AND tarjeta_id IS NOT NULL
                 GROUP BY tarjeta_id) a
         WHERE t.id = a.tarjeta_id"""), {"p": prov_id})
    db.execute(text("""
        UPDATE empleados_proveedor e
           SET n_cargas = a.n, litros = COALESCE(a.l, 0)
          FROM (SELECT empleado_id, count(*) AS n, sum(litros) AS l
                  FROM cargas_proveedor
                 WHERE vigente AND proveedor_id = :p AND empleado_id IS NOT NULL
                 GROUP BY empleado_id) a
         WHERE e.id = a.empleado_id"""), {"p": prov_id})


# ─────────────────────────────────────────────────────────────────────────────
# CASI-DUPLICADOS  ·  se marcan, jamás bloquean
# ─────────────────────────────────────────────────────────────────────────────

def _casi_duplicados(filas, minutos=VENTANA_CASI_DUPLICADO_MIN):
    """Pares de cargas de la misma tarjeta, MISMOS LITROS, dentro de la ventana.

    Los litros iguales son la mitad importante de la regla: sin ellos, la ráfaga normal de
    un camión que llena en dos tomas produce 135 pares y el aviso se vuelve ruido. Con
    ellos, el mes de julio entero da UN par —dos veces 150.00 L del T203 con 9 minutos de
    diferencia— que además es legítimo. Por eso esto MARCA y nunca detiene nada.
    """
    pares = []
    por_tarjeta = defaultdict(list)
    for f in filas:
        v = f["_vals"]
        if v.get("tarjeta_norm") and v.get("momento_local"):
            por_tarjeta[v["tarjeta_norm"]].append(f)
    for num, grupo in por_tarjeta.items():
        grupo.sort(key=lambda f: f["_vals"]["momento_local"])
        for i, a in enumerate(grupo):
            for b in grupo[i + 1:]:
                d = (b["_vals"]["momento_local"] - a["_vals"]["momento_local"]).total_seconds() / 60
                if d > minutos:
                    break
                if a["_vals"].get("litros") is not None and a["_vals"]["litros"] == b["_vals"]["litros"]:
                    pares.append((num, a, b, round(d, 1)))
    return pares


# ─────────────────────────────────────────────────────────────────────────────
# LA CORRIDA
# ─────────────────────────────────────────────────────────────────────────────

def importar(db, clave, ruta, dry=False, forzar=False, usuario_id=None):
    """Lee un archivo y lo guarda. Devuelve la `ImportacionProveedor` registrada.

    `db` se recibe en vez de abrirlo aquí para que la verificación pueda correr la ingesta
    entera dentro de una transacción y deshacerla.
    """
    clave = clave.upper()
    contrato = CONTRATOS.get(clave)
    if contrato is None:
        raise Aborta(f"no hay contrato de lectura para {clave!r}; los que hay son "
                     f"{sorted(CONTRATOS)}")

    # Se comprueba antes de leer el archivo para que "faltan las tablas" no llegue como un
    # traceback de Postgres a mitad de la corrida, sino como la única frase que hace falta.
    inspector = inspect(db.get_bind())
    faltan = [t for t in ("proveedores", "estaciones_proveedor", "tarjetas_combustible",
                          "empleados_proveedor", "importaciones_proveedor",
                          "cargas_proveedor") if not inspector.has_table(t)]
    if faltan:
        raise Aborta(f"faltan las tablas de E2 ({', '.join(faltan)}). Corre primero:  "
                     f"python -m scripts.migrate_e2")

    datos, sha = leer_archivo(ruta)
    prov = db.execute(select(Proveedor).where(
        Proveedor.clave == clave)).scalar_one_or_none()
    if prov is None:
        raise Aborta(f"el proveedor {clave} no está dado de alta en `proveedores`. "
                     f"Corre primero:  python -m scripts.migrate_e2")

    # ── (a) el archivo ya se procesó ────────────────────────────────────────
    ya = db.execute(select(ImportacionProveedor).where(
        ImportacionProveedor.sha256 == sha,
        ImportacionProveedor.vigente.is_(True))).scalars().first()
    if ya is not None and not forzar:
        cuando = ya.importado_en.astimezone(_tz())   # hora de la flota, no UTC
        print(f"Este archivo ya se importó el {cuando:%d/%m/%Y %H:%M} "
              f"(importacion id={ya.id}, {ya.n_filas_leidas} filas). No se vuelve a leer.")
        print("  Para leerlo otra vez de todos modos: --forzar")
        print("  Para reemplazar esa corrida: anúlala primero "
              f"(python -m scripts.revertir_importacion {ya.id}) y vuelve a importar.")
        return None

    # ── (b) el layout, contra el código y contra la base ────────────────────
    wb = abrir_datos(datos)
    try:
        # los bytes van para que el lector compruebe el número de filas contra el XML
        lec = leer_verbatim(wb, clave, datos)  # levanta LayoutInesperado si el archivo cambió
    finally:
        wb.close()
    _verificar_contra_la_base(prov, contrato, lec)

    # ── preparación de cada fila: verbatim + lo que quepa en cada columna ───
    sobras = set()
    for f in lec.filas:
        vals = {k: v for k, v in f.items() if k in COLUMNAS_CARGA}
        sobras |= set(f) - COLUMNAS_CARGA - {"avisos"}
        avisos = list(f["avisos"])
        f["_vals"] = _recortar(vals, ANCHOS_CARGA, avisos)
        f["_avisos"] = avisos
    if sobras:
        # No es fatal: significa que app/ingesta.py entrega algo que la tabla no tiene, y
        # eso se guarda igual dentro de fila_cruda. Pero hay que verlo.
        print(f"  AVISO: la lectura entrega campos que `cargas_proveedor` no tiene y que "
              f"solo quedarán en fila_cruda: {sorted(sobras)}")

    # `momento_local` es NOT NULL a propósito (es el reloj del ticket). Una fecha ilegible
    # no se puede guardar como NULL ni inventar, así que la corrida se detiene ANTES de
    # escribir y dice exactamente qué renglones abrir: es un cambio de formato, no un dato malo.
    sin_fecha = [f for f in lec.filas if f["_vals"].get("momento_local") is None]
    if sin_fecha:
        detalle = ", ".join(f"fila {f['fila_num']} ({f['_vals'].get('fecha_txt')!r})"
                            for f in sin_fecha[:8])
        raise Aborta(f"{len(sin_fecha)} filas traen una fecha que no se pudo leer y "
                     f"`momento_local` no admite nulos: {detalle}. "
                     f"Revisa el formato de fecha del proveedor antes de importar.")

    # Simetría con la fecha, y por la misma razón. `_numero` avisa cuando una celda TENÍA
    # contenido y no se pudo convertir, pero ese aviso moría en la columna `discrepancias`:
    # la corrida terminaba bien, la consola imprimía TOTALES y al mes le faltaban litros. Se
    # reprodujo con un separador de millares con espacio ('1 500.00'): 300 L y $8,100
    # desaparecidos sin una sola línea de queja. Una celda ilegible es un cambio de formato
    # del proveedor, igual que una fecha ilegible, y se trata igual: se para antes de escribir.
    #
    # Una celda VACÍA no entra aquí: `_numero` devuelve None sin aviso, y eso es un dato
    # ausente de verdad, no una lectura fallida.
    # Se filtra por el TEXTO que emite `_numero` y no por una lista de nombres de columna:
    # los dos proveedores llaman distinto a lo mismo ('Lts' y 'Consumo en Litros', 'Total' y
    # 'Consumo en Pesos') y una lista escrita a mano se queda corta en cuanto entre un tercero.
    ilegibles = [(f["fila_num"], a) for f in lec.filas for a in f["avisos"]
                 if " no es un número: " in a]
    if ilegibles:
        detalle = "; ".join(f"fila {n}: {a}" for n, a in ilegibles[:8])
        raise Aborta(
            f"{len(ilegibles)} celdas numéricas no se pudieron leer y sus litros o importes "
            f"quedarían fuera del total sin que nada lo dijera: {detalle}"
            + (f" … y {len(ilegibles) - 8} más" if len(ilegibles) > 8 else "")
            + ". Revisa el formato numérico del proveedor antes de importar.")

    # ── clasificación previa: qué haría cada fila, ANTES de escribir nada ───
    vigentes = {(e, fo): (i, s, r) for i, e, fo, s, r in db.execute(select(
        CargaProveedor.id, CargaProveedor.estacion_txt, CargaProveedor.folio_txt,
        CargaProveedor.sha256_fila, CargaProveedor.revision).where(
        CargaProveedor.proveedor_id == prov.id,
        CargaProveedor.vigente.is_(True)))}

    vistas = {}
    for f in lec.filas:
        v = f["_vals"]
        llave = (v["estacion_txt"], v["folio_txt"])
        if llave in vistas:
            # Dos renglones del MISMO archivo con la misma llave natural. No ha pasado
            # nunca (639 llaves para 639 filas) y no se resuelve solo: uno de los dos se
            # quedaría fuera y el total dejaría de cuadrar.
            f["_accion"], f["_vieja"] = "conflicto", vistas[llave]
            continue
        vistas[llave] = f["fila_num"]
        previa = vigentes.get(llave)
        if previa is None:
            f["_accion"], f["_vieja"] = "nueva", None
        elif previa[1] == v["sha256_fila"]:
            f["_accion"], f["_vieja"] = "repetida", previa
        else:
            f["_accion"], f["_vieja"] = "corregida", previa

    conteo = Counter(f["_accion"] for f in lec.filas)
    n_filas = len(lec.filas)

    if conteo["conflicto"]:
        chocan = [f for f in lec.filas if f["_accion"] == "conflicto"]
        raise Aborta(
            f"{len(chocan)} filas del archivo repiten la llave natural (estación, folio) de "
            f"otra fila del MISMO archivo: "
            + ", ".join(f"fila {f['fila_num']} choca con la {f['_vieja']}" for f in chocan[:8])
            + ". Guardarlas perdería una de las dos, así que no se escribe nada.")

    # ── (d) freno por corrección masiva ─────────────────────────────────────
    if conteo["corregida"] > TOPE_CORRECCIONES * n_filas and not forzar:
        raise Aborta(
            f"el proveedor cambió {conteo['corregida']} de {n_filas} filas "
            f"({conteo['corregida'] / n_filas:.0%}), por encima del "
            f"{TOPE_CORRECCIONES:.0%} que se admite sin preguntar. Suele ser un retoque del "
            f"exportador (un decimal, un relleno de espacios), no correcciones reales, y "
            f"dejaría la bandeja con cientos de pendientes.\n"
            f"  Míralo primero con --dry-run; si de verdad son correcciones, repite con --forzar.")

    # Una reejecución forzada sobre una corrida que sigue vigente solo puede ser una
    # RELECTURA: si además aportara filas, habría dos corridas vivas del mismo archivo y
    # ninguna sabría cuál manda. Se comprueba aquí, antes de escribir.
    relectura = ya is not None
    ya_id = ya.id if relectura else None
    if relectura and (conteo["nueva"] or conteo["corregida"]):
        raise Aborta(
            f"la corrida {ya_id} de este mismo archivo sigue vigente y esta relectura "
            f"aportaría {conteo['nueva']} filas nuevas y {conteo['corregida']} correcciones. "
            f"Anula la corrida {ya_id} primero "
            f"(python -m scripts.revertir_importacion {ya_id}) y vuelve a importar.")

    # Una simulación —el --dry-run y la relectura, que no escriben— NACE con vigente=False.
    # No es cosmética: el índice `ux_importaciones_prov_sha256 ON (sha256) WHERE vigente`
    # rechazaría la fila en el flush de abajo (la corrida original sigue vigente) y, al
    # revés, un --dry-run que quedara vigente impediría después la importación de verdad.
    # `relectura` se detecta por el sha256 del ARCHIVO, así que un re-export byte-distinto
    # del mismo mes no la dispara: las 639 filas se clasifican como 'repetida', no se
    # inserta nada, y aun así quedaba una corrida 'aplicada' y vigente con los totales del
    # mes colgando de cero cargas. Sumar `litros_total` de las corridas vigentes daba el mes
    # dos veces. La regla que el módulo declara —una corrida que no escribe nace
    # vigente=False— ahora también cubre este caso, mirando lo que de verdad se va a
    # escribir en vez de sólo cómo se llegó hasta aquí.
    nada_que_escribir = (conteo["nueva"] == 0 and conteo["corregida"] == 0)
    simulacion = dry or relectura or nada_que_escribir
    prov_id, prov_zona = prov.id, prov.zona_horaria

    # ── la corrida, ya con id, para que las cargas puedan apuntarle ─────────
    desde, hasta = lec.periodo
    imp = ImportacionProveedor(
        proveedor_id=prov_id, archivo=_corto(os.path.basename(ruta), 300),
        archivo_bytes=len(datos), sha256=sha,
        # El archivo entero solo se archiva cuando la corrida guarda algo. Una simulación
        # no tiene nada que demostrar y no vale 64 KB.
        archivo_b64=None if simulacion else base64.b64encode(datos).decode("ascii"),
        hoja_leida=lec.hoja, encabezado_leido=lec.encabezado,
        impresion_txt=_corto(lec.impresion_txt, 120),
        periodo_desde=desde, periodo_hasta=hasta,
        n_filas_leidas=lec.n_filas_leidas,
        n_omitidas=len(lec.omitidas),
        motivos_omision={str(k): v for k, v in lec.omitidas.items()} or None,
        litros_total=round(lec.litros_total, 2), importe_total=round(lec.importe_total, 2),
        estado="simulada" if simulacion else "aplicada",
        vigente=not simulacion, por_id=usuario_id)
    db.add(imp)
    db.flush()

    cache = {}
    avisos_corrida = set()
    estaciones, tarjetas, empleados, altas = _catalogos(
        db, prov, lec.filas, cache, avisos_corrida)
    tipos = {u.id: u.tipo for u in db.execute(select(Unidad)).scalars()}

    # Los casi-duplicados se anotan en las DOS filas del par: quien abra cualquiera de las
    # dos tiene que enterarse, no solo quien abra la segunda.
    parecidas = _casi_duplicados(lec.filas)
    for num, a, b, minutos in parecidas:
        for uno, otro in ((a, b), (b, a)):
            uno["_avisos"].append(
                f"casi-duplicado: la tarjeta {num} despachó los mismos "
                f"{uno['_vals']['litros']:.2f} L en el folio {otro['_vals']['folio_txt']} "
                f"con {minutos:g} minutos de diferencia")

    ahora = datetime.now(timezone.utc)
    tabla = CargaProveedor.__table__
    estados = Counter()
    destinos = Counter()
    litros_destino = Counter()
    vias = Counter()
    cuarentena_por_eco = Counter()
    litros_cuarentena = Counter()
    litros_fuera = Counter()
    correcciones = []
    conflictos_del_indice = []

    for f in lec.filas:
        v, avisos = f["_vals"], f["_avisos"]
        est = estaciones[v["estacion_txt"]]
        tar = tarjetas.get(v.get("tarjeta_norm")) if v.get("tarjeta_norm") else None
        emp = empleados.get(v.get("empleado_txt")) if v.get("empleado_txt") else None

        v["proveedor_id"] = prov_id
        v["importacion_id"] = imp.id
        v["estacion_id"] = est.id
        v["tarjeta_id"] = tar.id if tar is not None else None
        v["empleado_id"] = emp.id if emp is not None else None

        # La zona de la estación manda sobre la del proveedor: es el dato más específico
        # que alguien haya declarado. Hoy nace NULL en todas y gana la del proveedor.
        v["zona_aplicada"], v["momento_ref"] = _en_zona(
            v["momento_local"], est.zona_horaria or prov_zona, avisos)

        comb = clasificar_combustible(v.get("producto_norm"))
        v["combustible"] = comb
        # `fuera_de_flota` sale del PRODUCTO, no de que falte el económico. Hoy coinciden
        # (las 4 de gasolina son las 4 sin económico), pero hay 39 filas de diésel sin
        # resolver que SÍ son flota real por dar de alta y no deben caer en el mismo cajón.
        v["fuera_de_flota"] = comb == "gasolina"
        if comb == "otro":
            avisos.append(f"producto no reconocido como diésel ni gasolina: "
                          f"{v.get('producto_txt')!r}")

        r, nota_tarjeta = _resolver(db, cache, v.get("eco_txt"), v.get("placa_txt"),
                                   v.get("eco_norm"), v.get("placa_norm"), tar)
        via = r.via
        # Compensación de un defecto conocido de catalogo.py: su rama de placa llegó a
        # devolver via='eco' con confianza 'media', y entonces "cuántas cargas resolvieron
        # solo por placa" contestaba 0 mirando el campo equivocado. E2 no parchea
        # catalogo.py (es aditivo): reetiqueta aquí y deja constancia. Con el código de hoy
        # esta rama no se activa nunca; se queda porque el día que se reintroduzca el
        # defecto, la carga seguirá diciendo la verdad.
        if via == "eco" and r.confianza == "media":
            via = "placa"
            avisos.append("catalogo.py etiquetó como 'eco' una resolución que fue por placa")
        if nota_tarjeta:
            avisos.append(nota_tarjeta)

        v["unidad_id"], v["remolque_id"] = r.unidad_id, r.remolque_id
        v["destino"] = _destino(r.unidad_id, r.remolque_id, tipos)
        v["estado_resolucion"] = "resuelta" if r.ok else "cuarentena"
        v["resuelto_via"] = via if r.ok else "ninguna"
        v["resuelto_confianza"] = r.confianza
        v["resuelto_en"] = ahora
        v["motivo_estado"] = _corto(_motivo(r, via, v["fuera_de_flota"], v), 160)
        disc = list(r.discrepancias or []) + avisos
        v["discrepancias"] = disc or None

        estados[v["estado_resolucion"]] += 1
        # La BANDEJA DE TRABAJO se cuenta con su predicado real. Restar `fuera_de_flota` de
        # `cuarentena` solo funciona mientras las 4 cargas de gasolina tampoco traigan
        # económico; en cuanto alguien dé de alta las camionetas habrá filas resueltas y
        # fuera de flota, y la resta empezaría a devolver una cifra menor que la real.
        if v["estado_resolucion"] == "cuarentena" and not v.get("fuera_de_flota"):
            estados["bandeja"] += 1
        if v["fuera_de_flota"]:
            estados["fuera_de_flota"] += 1
            litros_fuera[v.get("producto_norm") or "(sin producto)"] += v.get("litros") or 0.0
        vias[v["resuelto_via"]] += 1
        if v["destino"]:
            destinos[v["destino"]] += 1
            litros_destino[v["destino"]] += v.get("litros") or 0.0
        if v["estado_resolucion"] == "cuarentena" and not v["fuera_de_flota"]:
            cuarentena_por_eco[v.get("eco_norm") or "(sin económico)"] += 1
            litros_cuarentena[v.get("eco_norm") or "(sin económico)"] += v.get("litros") or 0.0

        if f["_accion"] == "repetida":
            continue

        v["vigente"] = True
        v["revision"] = 1
        if f["_accion"] == "corregida":
            vieja_id, vieja_sha, vieja_rev = f["_vieja"]
            # La vieja se jubila ANTES de insertar: dos filas vigentes con la misma llave
            # violarían el índice parcial, y además el orden es el que hace que nunca haya
            # un instante con dos versiones vivas.
            db.execute(update(tabla).where(tabla.c.id == vieja_id).values(vigente=False))
            v["revision"] = vieja_rev + 1
            v["sustituye_a_id"] = vieja_id
            # La corrección NO se da por buena: entra vigente pero marcada, para que una
            # persona compare las dos versiones y decida.
            v["estado_revision"] = "pendiente"
            correcciones.append((f["fila_num"], v["estacion_txt"], v["folio_txt"],
                                 vieja_sha[:12], v["sha256_fila"][:12]))

        stmt = (pg_insert(tabla).values(**v)
                .on_conflict_do_nothing(
                    index_elements=["proveedor_id", "estacion_txt", "folio_txt"],
                    # El predicado se REPITE aquí a propósito: con un índice parcial,
                    # omitirlo hace que Postgres conteste "no unique or exclusion constraint
                    # matching the ON CONFLICT specification" y la corrida falle entera.
                    index_where=text("vigente"))
                .returning(tabla.c.id))
        nuevo_id = db.execute(stmt).scalar()
        if nuevo_id is None:
            # La red de seguridad actuó, o sea que el índice vio un duplicado que la
            # clasificación de arriba no vio. Es un fallo de este código, no del archivo.
            conflictos_del_indice.append(f["fila_num"])
        else:
            vigentes[(v["estacion_txt"], v["folio_txt"])] = (
                nuevo_id, v["sha256_fila"], v["revision"])

    if conflictos_del_indice:
        raise Aborta(
            f"el índice único rechazó {len(conflictos_del_indice)} filas que este importador "
            f"daba por nuevas (filas {conflictos_del_indice[:8]}). No se escribe nada: "
            f"hay un duplicado que la llave natural no está viendo.")

    imp.n_nuevas = conteo["nueva"]
    imp.n_repetidas = conteo["repetida"]
    imp.n_corregidas = conteo["corregida"]
    imp.n_resueltas = estados["resuelta"]
    # `n_cuarentena` es la BANDEJA DE TRABAJO: lo que no resolvió y sí es flota. Las de
    # gasolina se cuentan aparte porque no hay nada que dar de alta en ellas.
    imp.n_cuarentena = estados["bandeja"]
    imp.n_fuera_flota = estados["fuera_de_flota"]

    # Que no se pierda una fila deja de ser una promesa y pasa a ser una resta.
    cuadre = imp.n_nuevas + imp.n_repetidas + imp.n_corregidas + imp.n_omitidas
    if cuadre != imp.n_filas_leidas:
        raise Aborta(f"no cuadran las filas: se leyeron {imp.n_filas_leidas} y se "
                     f"clasificaron {cuadre} ({imp.n_nuevas} nuevas + {imp.n_repetidas} "
                     f"repetidas + {imp.n_corregidas} corregidas + {imp.n_omitidas} omitidas)")

    # Se recalculan también en una simulación: son parte del camino que hay que probar, y
    # en ese caso se deshacen tres líneas más abajo junto con todo lo demás.
    _recalcular_contadores(db, prov_id)

    _imprimir(prov, imp, lec, ruta, sha, altas, estados, destinos, litros_destino, vias,
              cuarentena_por_eco, litros_cuarentena, litros_fuera, correcciones, parecidas,
              tarjetas, avisos_corrida, dry, relectura)

    if simulacion:
        # Se deshace TODO —cargas, catálogos, contadores— y se deja solo el testigo de que
        # alguien miró este archivo y no escribió nada.
        motivo = ("simulación: se leyó el archivo completo y no se escribió ninguna carga"
                  if dry else
                  f"relectura forzada del archivo de la corrida {ya_id}: "
                  f"{conteo['repetida']} filas ya estaban guardadas y no se tocó nada")
        db.rollback()
        testigo = ImportacionProveedor(
            proveedor_id=prov_id, archivo=_corto(os.path.basename(ruta), 300),
            archivo_bytes=len(datos), sha256=sha, hoja_leida=lec.hoja,
            encabezado_leido=lec.encabezado, impresion_txt=_corto(lec.impresion_txt, 120),
            periodo_desde=desde, periodo_hasta=hasta,
            n_filas_leidas=lec.n_filas_leidas, n_omitidas=len(lec.omitidas),
            # El testigo tiene que decir lo MISMO que la consola. Sin estas dos, el registro
            # persistente de una simulación afirmaba 0 altas donde el plan eran 326, y quien
            # lo leyera después concluiría que el archivo ya estaba importado.
            n_nuevas=conteo['nueva'], n_corregidas=conteo['corregida'],
            motivos_omision={str(k): v for k, v in lec.omitidas.items()} or None,
            litros_total=round(lec.litros_total, 2),
            importe_total=round(lec.importe_total, 2),
            n_repetidas=conteo["repetida"], n_resueltas=estados["resuelta"],
            n_cuarentena=estados["bandeja"],
            n_fuera_flota=estados["fuera_de_flota"],
            estado="simulada", vigente=False, por_id=usuario_id,
            nota=_corto(motivo, 400))
        db.add(testigo)
        db.commit()
        return testigo

    db.commit()
    return imp


# ─────────────────────────────────────────────────────────────────────────────
# LO QUE VE LA PERSONA
# ─────────────────────────────────────────────────────────────────────────────

def _imprimir(prov, imp, lec, ruta, sha, altas, estados, destinos, litros_destino, vias,
              cuarentena_por_eco, litros_cuarentena, litros_fuera, correcciones, parecidas,
              tarjetas, avisos_corrida, dry, relectura):
    print("=" * 78)
    print(f"{prov.clave} · {os.path.basename(ruta)}")
    print(f"huella del archivo: {sha[:16]}… · {imp.archivo_bytes:,} bytes")
    print(f"hoja '{lec.hoja}' · encabezado fila {prov.fila_encabezado} · "
          f"{prov.n_columnas} columnas · layout conforme")
    if imp.periodo_desde:
        print(f"periodo leído: {imp.periodo_desde:%d/%m/%Y %H:%M} .. "
              f"{imp.periodo_hasta:%d/%m/%Y %H:%M}"
          + (f" · impresión del proveedor: {imp.impresion_txt}" if imp.impresion_txt else ""))
    print("-" * 78)
    print(f"  FILAS        {imp.n_filas_leidas} leídas = {imp.n_nuevas} nuevas + "
          f"{imp.n_repetidas} repetidas + {imp.n_corregidas} corregidas + "
          f"{imp.n_omitidas} omitidas")
    print(f"  TOTALES      {imp.litros_total:,.2f} L · ${imp.importe_total:,.2f}")
    # `bandeja` es el predicado REAL (cuarentena Y NO fuera de flota) y es lo que se guarda
    # en imp.n_cuarentena. La resta que había aquí solo coincidía mientras toda fila fuera de
    # flota estuviera además en cuarentena; en cuanto una no lo estaba, la consola enseñaba
    # menos trabajo pendiente del que hay y las tres cifras dejaban de sumar el archivo.
    print(f"  RESOLUCIÓN   {estados['resuelta']} resueltas · "
          f"{estados['bandeja']} en cuarentena · "
          f"{estados['fuera_de_flota']} fuera de flota"
          + (f"   (vías: " + " · ".join(f"{v} {n}" for v, n in vias.most_common()) + ")"))
    if destinos:
        print("  DESTINO      " + " · ".join(
            f"{d} {n} ({litros_destino[d]:,.2f} L)"
            for d, n in sorted(destinos.items(), key=lambda x: -x[1])))
    print(f"  CATÁLOGO     estaciones +{altas['estaciones']} · tarjetas +{altas['tarjetas']} "
          f"· empleados +{altas['empleados']}   (altas de este archivo; sin vincular a "
          f"ningún activo)")

    # Los avisos de fila existían y no los leía nadie: `Lectura.avisos` no tenía un solo
    # consumidor en todo el backend y `_imprimir` solo miraba `avisos_corrida`. Una lectura
    # que dudó de una celda tiene que decirlo en la misma pantalla donde dice los totales.
    if lec.avisos:
        n_filas = len(lec.avisos)
        n_avisos = sum(len(v) for v in lec.avisos.values())
        print(f"\n  ── AVISOS DE LECTURA ({n_avisos} en {n_filas} filas) ───────────")
        print("     la fila SE GUARDA; esto es lo que el lector no pudo dar por seguro")
        for num, avs in list(lec.avisos.items())[:15]:
            for a in avs:
                print(f"     fila {num}: {a}")
        if n_filas > 15:
            print(f"     … y {n_filas - 15} filas más con avisos")
        # Una fecha sin hora en TODO el archivo no es un aviso suelto: es un cambio de
        # formato del proveedor que deja el mes entero a medianoche.
        sin_hora = sum(1 for avs in lec.avisos.values()
                       for a in avs if a.startswith("'Fecha' sin hora"))
        if sin_hora:
            pct = 100.0 * sin_hora / max(lec.n_filas_leidas, 1)
            print(f"\n     ATENCIÓN: {sin_hora} de {lec.n_filas_leidas} filas ({pct:.0f}%) "
                  f"vienen SIN HORA y se guardan a las 00:00.")
            print("     Esa hora la puso el lector, no el proveedor. E3 empareja con una "
                  "ventana de ±6 h.")

    if lec.omitidas:
        print(f"\n  ── OMITIDAS ({len(lec.omitidas)}) ───────────────────────────")
        for num, motivo in list(lec.omitidas.items())[:20]:
            print(f"     fila {num}: {motivo}")

    if correcciones:
        print(f"\n  ── CORRECCIONES DEL PROVEEDOR ({len(correcciones)}) ─────────")
        print("     la versión vieja NO se borra: pasa a vigente=False y la nueva queda "
              "'pendiente'")
        for fila, est, folio, sha_v, sha_n in correcciones[:20]:
            print(f"     fila {fila:<5} estación {est:<10} folio {folio:<12} "
                  f"{sha_v}… -> {sha_n}…")
        if len(correcciones) > 20:
            print(f"     … y {len(correcciones) - 20} más")

    if cuarentena_por_eco:
        print(f"\n  ── CUARENTENA ({sum(cuarentena_por_eco.values())} filas, "
              f"{sum(litros_cuarentena.values()):,.2f} L) ─────────")
        print("     flota real por dar de alta: la carga SE GUARDA, solo no sabe a qué "
              "activo pertenece")
        for eco, n in sorted(cuarentena_por_eco.items(), key=lambda x: -litros_cuarentena[x[0]]):
            print(f"     {eco:<16} {n:>4} cargas · {litros_cuarentena[eco]:>10,.2f} L")

    if estados["fuera_de_flota"]:
        print(f"\n  ── FUERA DE FLOTA ({estados['fuera_de_flota']}) ─────────────")
        print("     no es cuarentena: el producto no es diésel, así que no hay activo que "
              "dar de alta")
        for prod, litros in litros_fuera.most_common():
            print(f"     {prod:<16} {litros:>10,.2f} L")

    if parecidas:
        print(f"\n  ── CASI-DUPLICADOS ({len(parecidas)}) ────────────────────")
        print(f"     misma tarjeta y MISMOS litros dentro de {VENTANA_CASI_DUPLICADO_MIN} "
              f"minutos. Se marcan en `discrepancias` de las dos filas y ninguna se descarta:")
        for num, uno, otro, minutos in parecidas[:20]:
            print(f"     tarjeta {num:<12} folios {uno['_vals']['folio_txt']} y "
                  f"{otro['_vals']['folio_txt']} · {uno['_vals']['litros']:,.2f} L · "
                  f"{minutos:g} min de diferencia")

    revisar = [t for t in tarjetas.values() if t.revisar]
    if revisar:
        print(f"\n  ── TARJETAS POR REVISAR ({len(revisar)}) ────────────────────")
        for t in revisar[:20]:
            print(f"     {t.numero_norm:<12} {t.nota_revision}")

    if avisos_corrida:
        print(f"\n  ── AVISOS ({len(avisos_corrida)}) ───────────────────────────")
        for a in sorted(avisos_corrida)[:20]:
            print(f"     {a}")

    print("\n" + "=" * 78)
    if dry:
        print("SIMULACIÓN: no se escribió ninguna carga, ni catálogo, ni contador.")
        print("Queda registrada la corrida con estado='simulada' y vigente=False, para que "
              "conste que se miró y no ocupe la llave del archivo.")
    elif relectura:
        print("RELECTURA: el archivo ya estaba íntegramente guardado y no se tocó nada.")
        print("Queda registrada con estado='simulada' y vigente=False.")
    else:
        print(f"Guardadas {imp.n_nuevas + imp.n_corregidas} cargas "
              f"(importacion id={imp.id}). El catálogo de la flota NO se tocó.")
        print("Revertir esta corrida:")
        print(f"  python -m scripts.revertir_importacion {imp.id}")
        print(f"  -- o, mientras ese script no exista:")
        print(f"  DELETE FROM cargas_proveedor WHERE importacion_id={imp.id};")
        print(f"  DELETE FROM importaciones_proveedor WHERE id={imp.id};")


def main():
    ap = argparse.ArgumentParser(
        description="E2 · Ingesta verbatim de un archivo de proveedor.",
        epilog="Sin --dry-run escribe en cargas_proveedor. Nunca toca el catálogo de la flota.")
    ap.add_argument("--proveedor", required=True, help=f"clave: {' | '.join(sorted(CONTRATOS))}")
    ap.add_argument("--archivo", help="ruta del Excel (por defecto, el habitual en ~/Downloads)")
    ap.add_argument("--dry-run", dest="dry", action="store_true",
                    help="lee y enseña el plan completo sin escribir ninguna carga")
    ap.add_argument("--forzar", action="store_true",
                    help="releer un archivo ya guardado, o aceptar una corrección masiva")
    a = ap.parse_args()

    clave = a.proveedor.upper()
    if clave not in CONTRATOS:
        print(f"Proveedor desconocido: {a.proveedor!r}. Hay contrato para "
              f"{sorted(CONTRATOS)}.")
        return 2
    ruta = a.archivo or os.path.join(DESCARGAS, ARCHIVO_POR_DEFECTO[clave])
    if not os.path.exists(ruta):
        print(f"NO SE ENCUENTRA: {ruta}")
        return 2

    with SessionLocal() as db:
        try:
            importar(db, clave, ruta, dry=a.dry, forzar=a.forzar)
        except (Aborta, LayoutInesperado) as e:
            db.rollback()
            print("=" * 78)
            print(f"SE DETUVO SIN ESCRIBIR NADA · {clave} · {os.path.basename(ruta)}")
            print(f"  {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
