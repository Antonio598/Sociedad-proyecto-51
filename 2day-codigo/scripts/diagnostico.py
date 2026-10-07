"""E0 · Diagnostico de SOLO LECTURA. No crea tablas, no escribe, no migra.

Cruza los archivos de los dos proveedores y el maestro de placas contra la base viva
e imprime el plan. Su trabajo es doble:

  1. DAR LA FOTO: cuanto hay, que resuelve y que no.
  2. VALIDAR LOS LECTORES: reproduce cifras ya verificadas a mano. Si un total no
     cuadra, el lector de ese archivo esta mal y NO se puede confiar en la ingesta
     posterior. Por eso el script termina con un veredicto, no con una tabla bonita.

Uso (desde backend/, venv activado):
    python -m scripts.diagnostico
    python -m scripts.diagnostico --placas RUTA --oxxo RUTA --xyga RUTA
"""

import io as _io
import os
import re
import sys
from collections import defaultdict

import openpyxl
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Remolque, Unidad

# ── rutas por defecto (las del equipo del dueno) ────────────────────────────
DESCARGAS = os.path.expanduser("~/Downloads")
DEF_PLACAS = os.path.join(DESCARGAS, "PLACAS.xlsx")
DEF_OXXO = os.path.join(DESCARGAS, "Despachos.xlsx")
DEF_XYGA = os.path.join(DESCARGAS, "REPORTE+DE+CONSUMOS_06_08_2026.xlsx.xls")

# ── cifras ya verificadas a mano; el script debe reproducirlas ──────────────
ESPERADO = {
    "oxxo_filas": 326, "oxxo_litros": 57910.09, "oxxo_importe": 1578329.30,
    "xyga_filas": 313, "xyga_litros": 60630.93, "xyga_importe": 1640658.69,
}

norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def abrir(path):
    """Abre el xlsx. Se lee a memoria a proposito: uno de los archivos del proveedor
    viene con extension .xls aunque por dentro es xlsx, y openpyxl lo rechaza por el
    nombre. Pasandole el contenido, no mira la extension."""
    with open(path, "rb") as fh:
        return openpyxl.load_workbook(_io.BytesIO(fh.read()), data_only=True, read_only=True)


def leer_oxxo(path):
    """Despachos.xlsx · hoja 'Reporte' · encabezado en la fila 6."""
    wb = abrir(path)
    filas = []
    for r in wb["Reporte"].iter_rows(min_row=7, values_only=True):
        if not any(r):
            continue
        f = r[3]
        filas.append({
            "prov": "OXXO", "trans": str(r[2] or "").strip(), "tarjeta": str(r[4] or "").strip(),
            "eco": norm(r[6]), "placa": norm(r[5]), "litros": num(r[13]), "importe": num(r[12]),
            "fecha": f.date() if hasattr(f, "date") else None, "momento": f,
            "factura": "", "conductor": str(r[19] or "").strip(),
        })
    wb.close()
    return filas


def leer_xyga(path):
    """REPORTE DE CONSUMOS · hoja 'worksheet' · encabezado en la fila 5."""
    from datetime import datetime
    wb = abrir(path)
    filas = []
    for r in wb["worksheet"].iter_rows(min_row=6, values_only=True):
        if not any(r):
            continue
        # sin ticket no es una fila de datos (pies de pagina, totales)
        if not r[1]:
            continue
        try:
            fecha = datetime.strptime(str(r[0]).split(" ")[0], "%d/%m/%Y").date()
        except (ValueError, IndexError):
            fecha = None
        filas.append({
            "prov": "XYGA", "trans": str(r[1] or "").strip(), "tarjeta": str(r[7] or "").strip(),
            "eco": norm(r[10]), "placa": norm(r[9]), "litros": num(r[15]), "importe": num(r[24]),
            "fecha": fecha, "momento": r[0], "factura": str(r[20] or "").strip(),
            "conductor": "", "km_reportado": num(r[17]),
        })
    wb.close()
    return filas


def leer_placas(path):
    wb = abrir(path)
    uni, rem = {}, {}
    for r in wb["Hoja1"].iter_rows(min_row=3, values_only=True):
        if r[2] and r[3]:
            uni[norm(r[2])] = norm(r[3])
        if len(r) > 6 and r[5] and r[6]:
            rem[norm(r[5])] = norm(r[6])
    wb.close()
    return uni, rem


def h(t):
    print("\n" + "=" * 78)
    print(t)
    print("-" * 78)


def cerca(a, b, tol=0.02):
    return abs(a - b) <= tol


def main(p_placas, p_oxxo, p_xyga):
    fallos = []

    for etiqueta, ruta in (("PLACAS", p_placas), ("Oxxo", p_oxxo), ("Xyga", p_xyga)):
        if not os.path.exists(ruta):
            print(f"NO SE ENCUENTRA el archivo de {etiqueta}: {ruta}")
            sys.exit(1)

    oxxo = leer_oxxo(p_oxxo)
    xyga = leer_xyga(p_xyga)
    todas = oxxo + xyga
    xl_uni, xl_rem = leer_placas(p_placas)

    # ── 1 · totales, contra lo verificado ───────────────────────────────────
    h("1 · TOTALES  (deben coincidir con lo verificado a mano)")
    for prov, filas, k in (("Oxxo Gas", oxxo, "oxxo"), ("Xyga", xyga, "xyga")):
        nf, li, im = len(filas), sum(f["litros"] for f in filas), sum(f["importe"] for f in filas)
        ok_n = nf == ESPERADO[f"{k}_filas"]
        ok_l = cerca(li, ESPERADO[f"{k}_litros"], 0.5)
        ok_i = cerca(im, ESPERADO[f"{k}_importe"], 1.0)
        print(f"  {prov:<10} {nf:>4} filas {'OK' if ok_n else '<-- ESPERADO ' + str(ESPERADO[f'{k}_filas'])}"
              f"   {li:>11,.2f} L {'OK' if ok_l else '<-- ESPERADO ' + format(ESPERADO[f'{k}_litros'], ',.2f')}"
              f"   ${im:>13,.2f} {'OK' if ok_i else '<-- ESPERADO ' + format(ESPERADO[f'{k}_importe'], ',.2f')}")
        if not (ok_n and ok_l and ok_i):
            fallos.append(f"los totales de {prov} no cuadran: el lector de ese archivo esta mal")
    print(f"  {'TOTAL':<10} {len(todas):>4} filas   "
          f"{sum(f['litros'] for f in todas):>11,.2f} L   ${sum(f['importe'] for f in todas):>13,.2f}")

    # ── 2 · regimen de factura ──────────────────────────────────────────────
    h("2 · REGIMEN DE FACTURA  (quien emite consolidada: es DATO, no constante)")
    for prov, filas in (("Oxxo Gas", oxxo), ("Xyga", xyga)):
        fac = {f["factura"] for f in filas if f["factura"]}
        if not fac:
            print(f"  {prov:<10} sin columna de folio -> el reporte ES el documento de liquidacion")
        elif len(fac) == 1:
            print(f"  {prov:<10} UN folio para todo el mes: {list(fac)[0]} -> consolidada mensual")
        else:
            print(f"  {prov:<10} {len(fac)} folios distintos -> CFDI por carga")

    # ── 3 · la tarjeta como llave ───────────────────────────────────────────
    h("3 · TARJETA <-> ECONOMICO  (la llave de union del rediseno)")
    for prov, filas in (("Oxxo Gas", oxxo), ("Xyga", xyga)):
        m = defaultdict(set)
        for f in filas:
            if f["tarjeta"]:
                m[f["tarjeta"]].add(f["eco"])
        multi = {t: e for t, e in m.items() if len(e) > 1}
        print(f"  {prov:<10} {len(m):>3} tarjetas   con mas de un economico: {len(multi)}"
              + ("  <-- ROMPE el supuesto" if multi else "  (1:1 exacto)"))
        if multi:
            fallos.append(f"en {prov} una tarjeta apunta a varios economicos")
    ajenas = [f for f in todas if not f["eco"]]
    print(f"  cargas SIN economico (solo tarjeta): {len(ajenas)}"
          f"  -> {sum(f['litros'] for f in ajenas):,.0f} L  ${sum(f['importe'] for f in ajenas):,.2f}")
    print("     son dinero real: entran a la factura, NO al rendimiento")

    # ── 4 · resolucion contra el catalogo ───────────────────────────────────
    h("4 · RESOLUCION CONTRA EL CATALOGO DE LA BASE")
    db = SessionLocal()
    UNI = {norm(u.clave) for u in db.query(Unidad).all()}
    REM = set()
    for r in db.query(Remolque).all():
        for k in (r.eco, r.eco_nuevo):
            if k:
                REM.add(norm(k))
    ecos = defaultdict(lambda: {"n": 0, "l": 0.0, "i": 0.0})
    for f in todas:
        if not f["eco"]:
            continue
        d = ecos[f["eco"]]
        d["n"] += 1
        d["l"] += f["litros"]
        d["i"] += f["importe"]
    es_uni = [e for e in ecos if e in UNI]
    es_rem = [e for e in ecos if e in REM and e not in UNI]
    sin = [e for e in ecos if e not in UNI and e not in REM]
    lt_rem = sum(ecos[e]["l"] for e in es_rem)
    lt_tot = sum(d["l"] for d in ecos.values())
    print(f"  economicos distintos ....... {len(ecos)}")
    print(f"    resuelven a UNIDAD ....... {len(es_uni)}")
    print(f"    resuelven a REMOLQUE ..... {len(es_rem)}   = {lt_rem:,.0f} L "
          f"({lt_rem / lt_tot * 100:.1f}% del mes)  <- diesel de TERMO ya identificable")
    print(f"    NO resuelven ............. {len(sin)}")
    for e in sorted(sin, key=lambda x: -ecos[x]["i"]):
        d = ecos[e]
        en_placas = "si" if (e in xl_uni or e in xl_rem) else "NO"
        print(f"       {e:<10} {d['n']:>3} cargas  {d['l']:>8,.0f} L  ${d['i']:>11,.2f}"
              f"   ¿en PLACAS.xlsx? {en_placas}")
    if sin:
        print("     -> quedan EN CUARENTENA: no se descartan, van a 'altas pendientes'")
        # Casi todos los que no resuelven son remolques que faltan por dar de alta, o sea
        # TERMO tambien. Decir solo el 12.8% subestimaria el problema: el termo real del
        # mes es la suma, y la diferencia es exactamente lo que destapan las altas.
        en_placas_rem = [e for e in sin if e in xl_rem]
        lt_sin = sum(ecos[e]["l"] for e in en_placas_rem)
        if lt_sin:
            tot = lt_rem + lt_sin
            print(f"\n    De esos, {len(en_placas_rem)} son REMOLQUES del maestro que faltan en la base:")
            print(f"      termo ya identificable ... {lt_rem:>9,.0f} L  ({lt_rem / lt_tot * 100:.1f}%)")
            print(f"      termo aun sin atribuir ... {lt_sin:>9,.0f} L  ({lt_sin / lt_tot * 100:.1f}%)")
            print(f"      TERMO REAL DEL MES ....... {tot:>9,.0f} L  ({tot / lt_tot * 100:.1f}%)")
            print("      -> dar de alta esos remolques cierra la diferencia por completo")

    # ── 5 · por que la ventana horaria ──────────────────────────────────────
    h("5 · ¿POR QUE EMPAREJAR POR HORA Y NO POR DIA?")
    for prov, filas in (("Oxxo Gas", oxxo), ("Xyga", xyga)):
        pares = defaultdict(int)
        for f in filas:
            if f["eco"] and f["fecha"]:
                pares[(f["eco"], f["fecha"])] += 1
        multi = sum(1 for v in pares.values() if v > 1)
        pct = multi / len(pares) * 100 if pares else 0
        print(f"  {prov:<10} {multi} de {len(pares)} pares (economico, dia) con MAS de una carga"
              f"  = {pct:.0f}%")
    print("     -> emparejar por dia cruzaria atribuciones. Se usa ventana de +-6 h.")

    # ── 6 · placas maestro vs base ──────────────────────────────────────────
    h("6 · PLACAS.xlsx CONTRA LA BASE")
    rem_bd = {}
    for r in db.query(Remolque).all():
        for k in (r.eco, r.eco_nuevo):
            if k:
                rem_bd[norm(k)] = r
    falta_u = [k for k in xl_uni if k not in UNI]
    falta_r = [k for k in xl_rem if k not in rem_bd]
    print(f"  Excel: {len(xl_uni)} unidades / {len(xl_rem)} remolques")
    print(f"  faltan en la base: {len(falta_u)} unidades {sorted(falta_u)}")
    print(f"                     {len(falta_r)} remolques")
    consumen = [e for e in falta_r if e in ecos]
    if consumen:
        li = sum(ecos[e]["l"] for e in consumen)
        im = sum(ecos[e]["i"] for e in consumen)
        print(f"    de esos, {len(consumen)} YA CONSUMEN: {li:,.0f} L  ${im:,.2f}  {sorted(consumen)}")
    db.close()

    # ── veredicto ───────────────────────────────────────────────────────────
    h("VEREDICTO")
    if fallos:
        print("  NO SE PUEDE CONTINUAR. Se encontraron problemas de lectura:")
        for f in fallos:
            print(f"    · {f}")
        print("\n  Corregir el lector antes de pasar a E2 (la ingesta).")
        sys.exit(1)
    print("  Los lectores reproducen las cifras verificadas: se puede confiar en ellos.")
    print("  Nada se escribio: este script es de solo lectura.")
    print("\n  Siguiente: E1 (catalogo por propuestas) y E2 (ingesta de los reportes).")


if __name__ == "__main__":
    a = sys.argv
    g = lambda k, d: a[a.index(k) + 1] if k in a and len(a) > a.index(k) + 1 else d
    main(g("--placas", DEF_PLACAS), g("--oxxo", DEF_OXXO), g("--xyga", DEF_XYGA))
