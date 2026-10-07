"""Vincula los hologramas del maestro con los activos del catálogo.

QUÉ ES UN HOLOGRAMA AQUÍ: la etiqueta pegada a la unidad que el operador lee con el
teléfono. Sustituye a teclear el número económico, así que la vinculación tiene que ser
exacta: un error aquí no se nota al capturar, se nota meses después cuando los litros
aparecen en la unidad equivocada.

LA REGLA QUE MANDA — un holograma NO puede apuntar a dos activos. El maestro entregado
trae dos códigos repetidos en dos tractos distintos cada uno. Esas filas se guardan con
`estado='conflicto'` y SIN activo. Vincular a la primera coincidencia habría sido peor
que no vincular: escanear resolvería, en silencio, a la unidad equivocada.

Al revés SÍ se permite: un activo con dos etiquetas es normal en un remolque refrigerado
(una para el motor y otra para el termo), que es la misma regla que ya rige la captura.

Reejecutar es seguro: se reconoce el archivo por su huella, y las etiquetas ya vinculadas
no se tocan. Las que estaban en 'sin_activo' se reintentan por si el activo ya se dio de alta.

Uso:
    python -m scripts.import_hologramas --dry-run      (no escribe NADA)
    python -m scripts.import_hologramas                (vincula)
"""

import hashlib
import io as _io
import os
import re
import sys
from collections import Counter, defaultdict

import openpyxl
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.catalogo import norm_eco, norm_etiqueta, resolver_activo
from datetime import datetime, timezone

from app.config import _tz
from app.db import SessionLocal
from app.models import EtiquetaActivo, ImportacionEtiquetas, Remolque, Unidad

DEF = os.path.join(os.path.expanduser("~/Downloads"), "HOLOGRAMA.xlsx")


def leer(path):
    """Hoja1, encabezado en la fila 3: B = UNIDAD (económico), C = HOLOGRAMA (UID)."""
    with open(path, "rb") as fh:
        datos = fh.read()
    wb = openpyxl.load_workbook(_io.BytesIO(datos), data_only=True, read_only=True)
    filas = []
    for i, r in enumerate(wb["Hoja1"].iter_rows(min_row=4, values_only=True), start=4):
        eco = str(r[1] or "").strip() if len(r) > 1 else ""
        cod = norm_etiqueta(r[2]) if len(r) > 2 else ""
        if eco or cod:
            filas.append({"fila": i, "eco": eco, "codigo": cod})
    wb.close()
    return filas, hashlib.sha256(datos).hexdigest()


def clasificar(db, filas):
    """Decide el destino de cada fila SIN escribir. Devuelve la lista enriquecida.

    La agrupación se hace por ACTIVO RESUELTO, nunca por el texto del económico: el mismo
    remolque llega escrito como 53113 o como 400917 y comparar textos fabricaría un
    conflicto donde no lo hay. Es la razón de ser de `alias_eco`.
    """
    # 1 · resolver cada fila primero: todo lo demás se decide sobre activos, no sobre textos
    for f in filas:
        f["motivo"] = ""
        f["candidatos"] = None
        f["unidad_id"] = f["remolque_id"] = None
        f["valida"] = bool(f["codigo"] and f["eco"]
                           and re.fullmatch(r"[0-9A-F]{8,32}", f["codigo"]))
        if f["valida"]:
            r = resolver_activo(db, eco=f["eco"])
            f["unidad_id"], f["remolque_id"] = r.unidad_id, r.remolque_id
            f["activo"] = (r.unidad_id, r.remolque_id) if r.ok else None
        else:
            f["activo"] = None

    # 2 · un código que apunta a DOS activos distintos, y su simétrico: un activo con DOS
    #     códigos distintos. Los dos casos son la misma duda —no se sabe qué sticker está
    #     pegado dónde— así que ninguno resuelve hasta que una persona lo declare.
    por_codigo, por_activo = {}, {}
    for f in filas:
        if not f["valida"] or f["activo"] is None:
            continue
        por_codigo.setdefault(f["codigo"], set()).add(f["activo"])
        por_activo.setdefault(f["activo"], set()).add(f["codigo"])

    ambiguos = {c for c, a in por_codigo.items() if len(a) > 1}
    repetidos = {a for a, c in por_activo.items() if len(c) > 1}

    def nombre(act):
        uid, rid = act
        o = db.get(Unidad, uid) if uid else db.get(Remolque, rid)
        return (getattr(o, "clave", None) or getattr(o, "eco", None) or "?") if o else "?"

    for f in filas:
        if not f["codigo"] or not f["eco"]:
            f["estado"], f["motivo"] = "conflicto", "la fila viene incompleta"
            continue
        if not f["valida"]:
            f["estado"] = "conflicto"
            f["motivo"] = f"el código '{f['codigo']}' no parece un UID válido"
            continue
        if f["activo"] is None:
            f["estado"] = "sin_activo"
            f["motivo"] = (f"el económico {f['eco']} no existe en el catálogo; se guarda y se "
                           "vinculará al reejecutar, en cuanto el activo se dé de alta")
            continue

        if f["codigo"] in ambiguos:
            otros = sorted(nombre(a) for a in por_codigo[f["codigo"]])
            f["estado"] = "conflicto"
            f["candidatos"] = otros
            f["motivo"] = (f"el maestro pega este mismo holograma en {len(otros)} activos "
                           f"distintos ({', '.join(otros)}); una persona debe decir cuál lo lleva")
            f["unidad_id"] = f["remolque_id"] = None
            continue

        if f["activo"] in repetidos:
            cods = sorted(por_activo[f["activo"]])
            f["estado"] = "duplicada"
            f["candidatos"] = cods
            # NO se da por bueno con la excusa del termo: `usa_combustible` es cierto en la
            # gran mayoría de los remolques, así que no distingue nada. Mientras nadie declare
            # qué sticker va en el motor y cuál en el termo, no se sabe cuál está pegado dónde.
            f["motivo"] = (f"el maestro da {len(cods)} hologramas distintos al mismo activo "
                           f"({nombre(f['activo'])}); si son el del motor y el del termo hay "
                           "que declarar cuál es cuál, y si no, uno de los dos está mal")
            f["unidad_id"] = f["remolque_id"] = None
            continue

        f["estado"] = "vinculada"
    return filas


def aplicar(db, filas):
    """Escribe las etiquetas.

    Nada se pisa en silencio. Si el maestro contradice un vínculo ya establecido, NO se
    cambia solo —mover litros de activo es una decisión de una persona— pero tampoco se
    calla: se devuelve como discrepancia para que aparezca en el informe. Antes esto se
    contaba como "sin cambio", que es justo lo que hace invisible una corrección.
    """
    ya = {e.codigo: e for e in db.execute(select(EtiquetaActivo)).scalars()}
    nuevas = act = intactas = 0
    discrepan = []
    vistos = set()
    for f in filas:
        if not f["codigo"] or f["codigo"] in vistos:
            continue
        # UN código es UNA etiqueta, aunque el maestro lo repita en varias filas.
        vistos.add(f["codigo"])
        e = ya.get(f["codigo"])
        if e is None:
            db.add(EtiquetaActivo(
                codigo=f["codigo"], tipo="holograma", estado=f["estado"],
                unidad_id=f["unidad_id"], remolque_id=f["remolque_id"],
                # en un conflicto guarda los económicos candidatos; en cualquier otro caso,
                # el económico de su fila. Los códigos hermanos de una duplicada se deducen
                # del económico, que es el mismo activo.
                eco_texto=(", ".join(f["candidatos"]) if f["estado"] == "conflicto"
                           and f.get("candidatos") else f["eco"]),
                nota=f["motivo"] or None, origen="holograma"))
            nuevas += 1
            continue

        if e.estado == "vinculada":
            mismo = (e.unidad_id, e.remolque_id) == (f["unidad_id"], f["remolque_id"])
            if f["estado"] == "vinculada" and mismo:
                intactas += 1
            else:
                discrepan.append({
                    "codigo": f["codigo"], "guardado": e.eco_texto, "maestro": f["eco"],
                    "estado_maestro": f["estado"]})
            continue

        if f["estado"] == "vinculada":
            e.estado, e.unidad_id, e.remolque_id = "vinculada", f["unidad_id"], f["remolque_id"]
            e.nota, e.eco_texto = f["motivo"] or None, f["eco"]
            e.actualizada_en = datetime.now(timezone.utc)
            act += 1
        else:
            # sigue sin resolver: se refresca el motivo, que puede haber cambiado
            if (e.nota or "") != (f["motivo"] or ""):
                e.nota = f["motivo"] or None
                e.eco_texto = (", ".join(f["candidatos"]) if f["estado"] == "conflicto"
                               and f.get("candidatos") else f["eco"])
                e.actualizada_en = datetime.now(timezone.utc)
            intactas += 1
    return nuevas, act, intactas, discrepan


def main(path, dry):
    if not os.path.exists(path):
        print(f"NO SE ENCUENTRA: {path}")
        sys.exit(1)
    filas, sha = leer(path)
    db = SessionLocal()

    prev = db.execute(select(ImportacionEtiquetas).where(
        ImportacionEtiquetas.sha256 == sha)).scalar_one_or_none()

    filas = clasificar(db, filas)
    c = Counter(f["estado"] for f in filas)

    print("=" * 78)
    print(f"MAESTRO DE HOLOGRAMAS · {len(filas)} filas · huella {sha[:16]}…")
    if prev is not None:
        cuando = prev.importado_en.astimezone(_tz())
        print(f"(este archivo ya se procesó el {cuando:%d/%m/%Y %H:%M}; se revisa de nuevo "
              "por si hay activos dados de alta desde entonces)")
    print("-" * 78)
    for est, et in (("vinculada", "vinculadas a un activo"),
                    ("conflicto", "EN CONFLICTO: un holograma en varios activos"),
                    ("duplicada", "DUPLICADAS: varios hologramas en un activo"),
                    ("sin_activo", "sin activo en el catálogo")):
        print(f"  {c.get(est, 0):>4}  {et}")

    for est, tit in (("conflicto", "CONFLICTOS · nadie las vincula hasta que se decidan"),
                     ("duplicada", "DUPLICADAS · tampoco se vinculan hasta que se declare"),
                     ("sin_activo", "SIN ACTIVO · esperan el alta en el catálogo")):
        g = [f for f in filas if f["estado"] == est]
        if not g:
            continue
        print(f"\n  ── {tit} ({len(g)}) ──")
        for f in g:
            print(f"     fila {f['fila']:>4}  {f['eco']:<9} {f['codigo']:<16} {f['motivo']}")

    if dry:
        print("\n" + "=" * 78)
        print("SIMULACIÓN: no se escribió nada.")
        db.close()
        return

    imp = prev
    if imp is None:
        imp = ImportacionEtiquetas(
            archivo=os.path.basename(path), sha256=sha, n_filas=len(filas),
            n_vinculadas=c.get("vinculada", 0), n_conflicto=c.get("conflicto", 0),
            n_sin_activo=c.get("sin_activo", 0))
        db.add(imp)
        db.flush()
    nuevas, act, intactas, discrepan = aplicar(db, filas)
    db.commit()
    print("\n" + "=" * 78)
    print(f"{nuevas} etiquetas nuevas · {act} actualizadas · {intactas} sin cambio")
    if discrepan:
        print(f"\n  {len(discrepan)} DISCREPAN con lo ya vinculado y NO se pisaron:")
        for d in discrepan:
            print(f"     {d['codigo']:<16} guardado={d['guardado']:<12} maestro dice "
                  f"{d['maestro']} ({d['estado_maestro']})")
        print("  Mover litros de un activo a otro es una decisión de una persona: revísalas "
              "en el panel.")
    print("Las que están en conflicto NO se vincularon a propósito: escanearlas no "
          "resuelve hasta que una persona decida.")
    db.close()


if __name__ == "__main__":
    a = sys.argv
    ruta = next((x for x in a[1:] if not x.startswith("--")), DEF)
    main(ruta, "--dry-run" in a)
