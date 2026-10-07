"""E1 · Lee el maestro de placas y PROPONE cambios al catálogo. Declarativo y no destructivo.

Este script NUNCA escribe sobre `unidades` ni `remolques`. Genera filas de
`propuestas_catalogo` que una persona aprueba después desde el panel. Es la misma
disciplina que ya tiene la máquina de estados, aplicada al catálogo: un importador que
escribe directo es exactamente lo que produjo los duplicados que se quieren evitar.

QUÉ PUEDE PROPONER, y cuándo:
  registrar_alias  · el económico del maestro difiere del de la base PERO la placa
                     coincide -> es el par viejo/nuevo (53113 <-> 400917). Es un ALIAS,
                     NO un alta. Confundirlos es lo que duplicaba la flota.
  actualizar_placa · el activo resuelve pero el maestro trae otra placa. Se PROPONE
                     ponerla en `placas_nuevas` conservando la actual: no se pisa nada.
  crear            · no coincide ni por económico ni por placa.
  reactivar        · existe dado de baja y el maestro lo lista.
  conflicto        · el económico casa con un activo y la placa con OTRO. No se decide
                     solo: lo resuelve una persona.

Nunca propone `desactivar`: se verificó que hay remolques que queman diésel y no
aparecen en el maestro, así que "no está en el archivo" no significa "no existe".

Uso:
    python -m scripts.import_placas --dry-run          (no escribe NADA)
    python -m scripts.import_placas                    (guarda las propuestas)
"""

import hashlib
import io as _io
import os
import sys
from collections import Counter

# La consola de Windows es cp1252 y reventaba con los acentos. Se corrige la SALIDA, que
# es el problema real: los motivos se guardan en la base y los lee una persona en pantalla.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import openpyxl
from sqlalchemy import select

from app.catalogo import norm_eco, norm_placa
from app.config import _tz
from app.db import SessionLocal
from app.models import AliasEco, ImportacionPlacas, PropuestaCatalogo, Remolque, Unidad

DEF_PLACAS = os.path.join(os.path.expanduser("~/Downloads"), "PLACAS.xlsx")


def leer(path):
    """Hoja1, encabezado en la fila 2. C/D = unidad+placa · F/G = remolque+placa.
    Son DOS listas independientes de distinto largo, no una tabla de dos columnas."""
    with open(path, "rb") as fh:
        datos = fh.read()
    wb = openpyxl.load_workbook(_io.BytesIO(datos), data_only=True, read_only=True)
    uni, rem = [], []
    for r in wb["Hoja1"].iter_rows(min_row=3, values_only=True):
        if len(r) > 3 and r[2] and r[3]:
            uni.append((norm_eco(r[2]), norm_placa(r[3])))
        if len(r) > 6 and r[5] and r[6]:
            rem.append((norm_eco(r[5]), norm_placa(r[6])))
    wb.close()
    return uni, rem, hashlib.sha256(datos).hexdigest()


def _alias(db):
    """texto_norm -> (unidad_id, remolque_id). Un solo viaje a la base."""
    return {a.texto_norm: (a.unidad_id, a.remolque_id)
            for a in db.execute(select(AliasEco)).scalars()}


def consumo_por_clave(paths):
    """{clave_normalizada: (n_cargas, litros)} desde los archivos de los proveedores.

    Se indexa por económico Y por placa porque hay cargas que solo traen uno de los dos.
    Es evidencia de que el activo existe y opera, independiente del maestro del cliente.
    """
    from scripts.diagnostico import leer_oxxo, leer_xyga
    acc = {}
    for path, lector in paths:
        if not os.path.exists(path):
            continue
        for c in lector(path):
            for k in (norm_eco(c.get("eco")), norm_placa(c.get("placa"))):
                if k:
                    n, l = acc.get(k, (0, 0.0))
                    acc[k] = (n + 1, l + c.get("litros", 0.0))
    return acc


def _evidencia(consumo, eco, placa):
    if not consumo:
        return ""
    n = l = 0
    for k in (eco, placa):
        if k in consumo:
            n += consumo[k][0]
            l += consumo[k][1]
    if not n:
        return "; sin consumo registrado en el periodo"
    return f"; YA OPERA: {n} cargas y {l:,.0f} L sin activo en el catálogo"


def analizar(db, filas, entidad, consumo=None):
    """Compara cada fila del maestro contra el catálogo y devuelve las propuestas.

    `consumo` es opcional: si se le pasa, cada alta declara si el activo YA quema
    diésel. Es la diferencia entre \"29 altas por revisar\" y \"5 urgentes, 24 sin prisa\"."""
    al = _alias(db)
    modelo = Unidad if entidad == "unidad" else Remolque
    objetos = {o.id: o for o in db.execute(select(modelo)).scalars()}
    idx = 0 if entidad == "unidad" else 1          # posición en la tupla del alias
    props = []

    for eco, placa in filas:
        por_eco = al.get(eco)
        por_placa = al.get(placa) if placa else None
        id_eco = por_eco[idx] if por_eco else None
        id_placa = por_placa[idx] if por_placa else None

        # ── conflicto: el económico apunta a uno y la placa a otro ──────────
        if id_eco and id_placa and id_eco != id_placa:
            a, b = objetos.get(id_eco), objetos.get(id_placa)
            props.append(dict(
                accion="conflicto", entidad=entidad, entidad_id=id_eco,
                eco_texto=eco, placa_texto=placa,
                valor_actual=f"eco->{getattr(a, 'clave', None) or getattr(a, 'eco', '?')}",
                valor_propuesto=f"placa->{getattr(b, 'clave', None) or getattr(b, 'eco', '?')}",
                motivo="el económico y la placa apuntan a activos distintos"))
            continue

        obj_id = id_eco or id_placa
        if obj_id is None:
            props.append(dict(
                accion="crear", entidad=entidad, entidad_id=None,
                eco_texto=eco, placa_texto=placa, valor_actual=None,
                valor_propuesto=f"{eco} / {placa}",
                motivo="no coincide ni por económico ni por placa" + _evidencia(consumo, eco, placa)))
            continue

        obj = objetos.get(obj_id)
        if obj is None:
            continue

        # ── alias: la placa lo identifica, pero ese económico no está registrado ──
        if id_eco is None and id_placa is not None:
            actual = getattr(obj, "clave", None) or getattr(obj, "eco", "?")
            props.append(dict(
                accion="registrar_alias", entidad=entidad, entidad_id=obj_id,
                eco_texto=eco, placa_texto=placa, valor_actual=str(actual),
                valor_propuesto=eco,
                motivo="misma placa y económico distinto: es el par viejo/nuevo, NO un alta"))

        # ── placa: el maestro trae una que no está registrada ───────────────
        if placa:
            conocidas = {norm_placa(p) for p in (obj.placa, obj.placas_nuevas) if p}
            if placa not in conocidas:
                props.append(dict(
                    accion="actualizar_placa", entidad=entidad, entidad_id=obj_id,
                    eco_texto=eco, placa_texto=placa,
                    valor_actual=" / ".join(sorted(conocidas)) or "(sin placa)",
                    valor_propuesto=placa,
                    motivo="el maestro trae una placa que no está registrada; se propone "
                           "como placa nueva SIN pisar la actual"))

        # ── reactivar ───────────────────────────────────────────────────────
        if getattr(obj, "activo", True) is False:
            props.append(dict(
                accion="reactivar", entidad=entidad, entidad_id=obj_id,
                eco_texto=eco, placa_texto=placa, valor_actual="inactivo",
                valor_propuesto="activo", motivo="está dado de baja y el maestro lo lista"))
    return props


def main(path, dry):
    if not os.path.exists(path):
        print(f"NO SE ENCUENTRA: {path}")
        sys.exit(1)
    uni, rem, sha = leer(path)
    db = SessionLocal()

    ya = db.execute(select(ImportacionPlacas).where(
        ImportacionPlacas.sha256 == sha)).scalar_one_or_none()
    if ya is not None and not dry:
        # en hora de la flota, no UTC: el panel la muestra en local y ambos deben coincidir
        cuando = ya.importado_en.astimezone(_tz())
        print(f"Este archivo ya se importó el {cuando:%d/%m/%Y %H:%M} "
              f"(importacion id={ya.id}). No se duplican propuestas.")
        db.close()
        return

    dl = os.path.expanduser("~/Downloads")
    from scripts.diagnostico import leer_oxxo, leer_xyga
    consumo = consumo_por_clave([
        (os.path.join(dl, "Despachos.xlsx"), leer_oxxo),
        (os.path.join(dl, "REPORTE+DE+CONSUMOS_06_08_2026.xlsx.xls"), leer_xyga)])

    props = (analizar(db, uni, "unidad", consumo)
             + analizar(db, rem, "remolque", consumo))

    print("=" * 78)
    print(f"MAESTRO DE PLACAS · {len(uni)} unidades / {len(rem)} remolques")
    print(f"huella del archivo: {sha[:16]}…")
    print("-" * 78)
    conteo = Counter(p["accion"] for p in props)
    if consumo:
        print(f"  (evidencia de consumo cargada: {len(consumo)} claves de proveedor)")
    if not props:
        print("  Sin diferencias: el catálogo ya coincide con el maestro.")
    for accion in ("crear", "registrar_alias", "actualizar_placa", "reactivar", "conflicto"):
        n = conteo.get(accion, 0)
        if n:
            print(f"  {accion:<18} {n:>4}")

    for accion in ("conflicto", "crear", "registrar_alias", "actualizar_placa", "reactivar"):
        grupo = [p for p in props if p["accion"] == accion]
        if not grupo:
            continue
        if accion == "crear" and consumo:
            grupo.sort(key=lambda p: -sum(consumo.get(k, (0, 0.0))[1]
                                          for k in (p["eco_texto"], p["placa_texto"])))
        print(f"\n  ── {accion.upper()} ({len(grupo)}) ─────────────────────────")
        for p in grupo[:40]:
            print(f"     {p['entidad']:<8} {p['eco_texto']:<10} placa={p['placa_texto'] or '—':<10} "
                  f"actual={(p['valor_actual'] or '—')[:22]:<24} -> {p['valor_propuesto']}")
            if "YA OPERA" in (p["motivo"] or ""):
                print(f"                 ^ {p['motivo'].split('; ')[-1]}")
        if len(grupo) > 40:
            print(f"     … y {len(grupo) - 40} más")

    if dry:
        print("\n" + "=" * 78)
        print("SIMULACIÓN: no se escribió nada, ni propuestas ni catálogo.")
        db.close()
        return

    imp = ImportacionPlacas(archivo=os.path.basename(path), sha256=sha,
                            n_unidades=len(uni), n_remolques=len(rem))
    db.add(imp)
    db.flush()
    for p in props:
        db.add(PropuestaCatalogo(importacion_id=imp.id, estado="pendiente", **p))
    db.commit()
    print("\n" + "=" * 78)
    print(f"Guardadas {len(props)} PROPUESTAS (importacion id={imp.id}).")
    print("El catálogo NO se tocó: quedan pendientes de que una persona las apruebe.")
    db.close()


if __name__ == "__main__":
    a = sys.argv
    ruta = next((x for x in a[1:] if not x.startswith("--")), DEF_PLACAS)
    main(ruta, "--dry-run" in a)
