"""Da de baja los activos que el maestro de placas no lista y que no queman diésel.

QUÉ HACE Y QUÉ NO. Pone `activo = False`. **Nunca borra una fila.** Un activo dado de baja
desaparece de las listas y de las asignaciones, pero conserva sus viajes, sus recargas y sus
etiquetas, así que todo lo que ya se registró sigue teniendo a qué apuntar. Revertirlo es un
UPDATE, y el script imprime exactamente cuál.

LA REGLA QUE PROTEGE: un activo con consumo registrado NO se da de baja aunque el maestro no
lo liste. Se verificó que hay remolques que facturan diésel y no aparecen en PLACAS.xlsx
(5311801 y 5311808, 1,910 L en julio): darlos de baja dejaría esos litros sin dueño. PLACAS
manda sobre la placa y el económico vigente, pero no es una lista cerrada.

Uso:
    python -m scripts.baja_fuera_placas --dry-run     (no escribe NADA)
    python -m scripts.baja_fuera_placas               (aplica)
"""

import io as _io
import os
import sys
from collections import defaultdict

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.catalogo import norm_eco, norm_placa
from app.db import SessionLocal
from app.models import EtiquetaActivo, Remolque, Unidad, Viaje
from scripts.diagnostico import leer_oxxo, leer_xyga

DL = os.path.expanduser("~/Downloads")


def leer_placas(path):
    with open(path, "rb") as fh:
        wb = openpyxl.load_workbook(_io.BytesIO(fh.read()), data_only=True, read_only=True)
    uni, rem = set(), set()
    for r in wb["Hoja1"].iter_rows(min_row=3, values_only=True):
        if len(r) > 3 and r[2]:
            uni.add(norm_eco(r[2]))
        if len(r) > 6 and r[5]:
            rem.add(norm_eco(r[5]))
    wb.close()
    return uni, rem


def consumo():
    """Litros por económico y por placa, desde los dos proveedores."""
    acc = defaultdict(lambda: [0, 0.0])
    for f, lector in ((os.path.join(DL, "Despachos.xlsx"), leer_oxxo),
                      (os.path.join(DL, "REPORTE+DE+CONSUMOS_06_08_2026.xlsx.xls"), leer_xyga)):
        if not os.path.exists(f):
            continue
        for c in lector(f):
            for k in (norm_eco(c["eco"]), norm_placa(c["placa"])):
                if k:
                    acc[k][0] += 1
                    acc[k][1] += c["litros"]
    return acc


def main(dry):
    ruta = os.path.join(DL, "PLACAS.xlsx")
    if not os.path.exists(ruta):
        print(f"NO SE ENCUENTRA: {ruta}")
        sys.exit(1)
    pl_u, pl_r = leer_placas(ruta)
    cons = consumo()
    db = SessionLocal()

    bajas, protegidos = [], []
    for u in db.query(Unidad).filter(Unidad.activo.is_(True)).all():
        if norm_eco(u.clave) in pl_u:
            continue
        claves = [norm_eco(u.clave)] + [norm_placa(p) for p in (u.placa, u.placas_nuevas) if p]
        n = sum(cons[k][0] for k in claves)
        lts = sum(cons[k][1] for k in claves)
        fila = {"obj": u, "eco": u.clave, "tipo": "unidad", "cargas": n, "litros": lts,
                "viajes": db.query(Viaje).filter(Viaje.unidad_id == u.id).count()}
        (protegidos if n else bajas).append(fila)

    for x in db.query(Remolque).filter(Remolque.activo.is_(True)).all():
        alias = {norm_eco(x.eco), norm_eco(x.eco_nuevo)} - {""}
        if alias & pl_r:
            continue
        claves = list(alias) + [norm_placa(p) for p in (x.placa, x.placas_nuevas) if p]
        n = sum(cons[k][0] for k in claves)
        lts = sum(cons[k][1] for k in claves)
        fila = {"obj": x, "eco": x.eco, "cargas": n, "litros": lts,
                "tipo": "dolly" if x.es_dolly else ("termo" if x.usa_combustible else "seco"),
                "viajes": db.query(Viaje).filter(Viaje.remolque_thermo == x.eco).count()}
        (protegidos if n else bajas).append(fila)

    etq = defaultdict(int)
    for e in db.query(EtiquetaActivo).all():
        if e.unidad_id:
            etq[("u", e.unidad_id)] += 1
        if e.remolque_id:
            etq[("r", e.remolque_id)] += 1

    print("=" * 76)
    print(f"MAESTRO DE PLACAS · {len(pl_u)} unidades / {len(pl_r)} remolques")
    print(f"Activos del catálogo que el maestro NO lista: {len(bajas) + len(protegidos)}")
    print("-" * 76)
    print(f"\n  SE DAN DE BAJA ({len(bajas)}) · sin consumo registrado")
    print(f"  {'activo':<12}{'tipo':<8}{'viajes':>7}{'etiquetas':>11}")
    for f in sorted(bajas, key=lambda f: -f["viajes"]):
        o = f["obj"]
        k = ("u", o.id) if f["tipo"] == "unidad" else ("r", o.id)
        print(f"  {f['eco']:<12}{f['tipo']:<8}{f['viajes']:>7}{etq.get(k, 0):>11}")
    tot_v = sum(f["viajes"] for f in bajas)
    print(f"  conservan {tot_v} viajes históricos y {sum(etq.get(('u', f['obj'].id) if f['tipo']=='unidad' else ('r', f['obj'].id), 0) for f in bajas)} etiquetas")

    if protegidos:
        print(f"\n  NO SE TOCAN ({len(protegidos)}) · queman diésel aunque el maestro no los liste")
        for f in protegidos:
            print(f"  {f['eco']:<12}{f['tipo']:<8}{f['cargas']:>4} cargas {f['litros']:>9,.0f} L")

    if dry:
        print("\n" + "=" * 76)
        print("SIMULACIÓN: no se escribió nada.")
        db.close()
        return

    for f in bajas:
        f["obj"].activo = False
    db.commit()
    print("\n" + "=" * 76)
    print(f"{len(bajas)} activos dados de baja. Ninguna fila se borró.")
    u = [f["eco"] for f in bajas if f["tipo"] == "unidad"]
    r = [f["eco"] for f in bajas if f["tipo"] != "unidad"]
    print("\nPara revertir:")
    if u:
        print("  UPDATE unidades  SET activo=true WHERE clave IN ("
              + ", ".join(f"'{x}'" for x in u) + ");")
    if r:
        print("  UPDATE remolques SET activo=true WHERE eco IN ("
              + ", ".join(f"'{x}'" for x in r) + ");")
    db.close()


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
