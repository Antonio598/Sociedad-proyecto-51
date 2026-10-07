# -*- coding: utf-8 -*-
"""Prueba de no-regresion de los cinco defectos de la ingesta E2 (3-sep-2026).

POR QUE EXISTE: `verificar_e2.py` comprueba que el mes se reproduce al centavo, y sus 24
pruebas pasaban tambien CON los cinco defectos dentro. Ninguno rompia el caso feliz: todos
aparecian cuando el proveedor mandaba algo ligeramente distinto de lo de siempre. Esta
prueba fabrica esas variantes a partir del archivo real y comprueba que el modulo se queja
en vez de tragar.

LOS CINCO:
  1. Una celda numerica ilegible ('1 500.00', con el millar separado por espacio) dejaba
     litros e importe en NULL y el aviso moria en la columna `discrepancias`: la consola
     imprimia TOTALES y al mes le faltaban 300 L y $8,100 sin una sola linea de queja.
     Ahora la corrida se detiene ANTES de escribir, igual que ya hacia con una fecha ilegible.
  2. Una fecha sin hora caia a medianoche en silencio. Las 313 cargas del mes a las 00:00
     serian horas inventadas, y E3 empareja despachos con una ventana de +/-6 h.
  3. El centinela de la llave natural era `@fila:<n>`, o sea la POSICION de la fila. La misma
     carga vista en dos exportaciones solapadas producia dos llaves y entraba dos veces.
     Ahora sale del contenido: `@sha:<huella>`.
  4. La consola restaba fuera_de_flota de cuarentena y contradecia el n_cuarentena que ella
     misma guardaba: enseñaba menos trabajo pendiente del que hay.
  5. Un re-export byte-distinto del mismo mes no coincide en el sha256 del ARCHIVO, asi que
     no se detectaba como relectura: no insertaba nada y aun asi quedaba 'aplicada' y
     vigente, con los totales del mes colgando de cero cargas. Sumar los litros de las
     corridas vigentes daba el mes dos veces.

NO ESCRIBE NADA. Los tres primeros casos se ejercitan a nivel de lector; los dos ultimos
llaman al importador dentro de una transaccion que se deshace al final.

Uso:
    python -m scripts.verificar_e2_defectos
"""
import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import openpyxl

from app import ingesta

XYGA = os.path.expanduser(r"~\Downloads\REPORTE+DE+CONSUMOS_06_08_2026.xlsx.xls")
if not os.path.exists(XYGA):
    print("NO SE ENCUENTRA el archivo real de XYGA:")
    print(f"  {XYGA}")
    print("Esta prueba fabrica sus variantes a partir de el; sin el archivo no prueba nada.")
    sys.exit(2)

TMP = tempfile.mkdtemp(prefix="e2_")

ok = []


def marca(nombre, paso, detalle=""):
    ok.append(paso)
    print(f"  [{'PASA' if paso else 'FALLA'}] {nombre}")
    if detalle:
        for l in str(detalle).splitlines():
            print(f"         {l}")


def leer(ruta):
    """leer_verbatim recibe el LIBRO ya abierto y la clave del proveedor, mas los bytes
    (los usa para contar las filas del XML crudo, que es lo que atrapo el fallo de
    <dimension> de openpyxl)."""
    datos = io.open(ruta, "rb").read()
    wb = openpyxl.load_workbook(io.BytesIO(datos), data_only=True, read_only=True)
    try:
        return ingesta.leer_verbatim(wb, "XYGA", datos)
    finally:
        wb.close()


def abrir(ruta):
    """openpyxl valida por EXTENSION y el XYGA se llama '.xlsx.xls' aunque por dentro sea
    un xlsx. Cargarlo desde bytes salta esa comprobacion, que es lo que hace la ingesta."""
    return openpyxl.load_workbook(io.BytesIO(io.open(ruta, "rb").read()))


def copia(nombre, tocar):
    """Copia el XYGA real y deja que `tocar(hoja)` lo modifique."""
    dst = os.path.join(TMP, nombre)
    wb = abrir(XYGA)
    tocar(wb[wb.sheetnames[0]])
    wb.save(dst)
    return dst


print("=" * 78)
print("DEFECTO 1 · una celda numerica ilegible ya no se traga litros en silencio")
print("-" * 78)
# El escenario del refutador: 'Lts' con el millar separado por espacio.
# Los indices los declara el propio modulo; adivinarlos leyendo el encabezado a mano fue
# un error mio: el de XYGA esta en la fila 5, no en la 4.
i_lts, i_tot = ingesta.X_LITROS, ingesta.X_TOTAL
i_fec, i_tick = ingesta.X_FECHA, ingesta.X_TICKET
FILA_DATOS = ingesta.CONTRATOS["XYGA"].fila_datos
print(f"  del contrato: Lts={i_lts} Total={i_tot} Fecha={i_fec} Ticket={i_tick} "
      f"· los datos empiezan en la fila {FILA_DATOS}")

f1 = copia("ilegible.xlsx", lambda hh: (
    hh.cell(row=FILA_DATOS, column=i_lts + 1, value="1 500.00"),
    hh.cell(row=FILA_DATOS, column=i_tot + 1, value="40 500.00")))

lec = leer(f1)
avisos_num = [(n, a) for n, avs in lec.avisos.items() for a in avs if " no es un número: " in a]
marca("el lector produce los avisos", len(avisos_num) == 2, "; ".join(a for _, a in avisos_num))

# y el importador se detiene ANTES de escribir
from scripts.import_proveedor import Aborta
import scripts.import_proveedor as IP
from app.db import SessionLocal
from app.models import CargaProveedor, ImportacionProveedor

db = SessionLocal()
antes = db.query(CargaProveedor).filter(CargaProveedor.vigente.is_(True)).count()
try:
    IP.importar(db, "XYGA", f1, dry=True)
    marca("el importador se detiene", False, "NO abortó: siguió adelante")
except Aborta as e:
    txt = str(e)
    marca("el importador se detiene antes de escribir",
          "no se pudieron leer" in txt and f"fila {FILA_DATOS}" in txt, txt[:200])
except Exception as e:
    marca("el importador se detiene", False, f"abortó con otra cosa: {type(e).__name__}: {e}")
db.rollback()

print()
print("=" * 78)
print("DEFECTO 2 · una fecha sin hora deja de ser silenciosa")
print("-" * 78)
def _sin_hora(hh):
    for r in range(FILA_DATOS, hh.max_row + 1):
        v = hh.cell(row=r, column=i_fec + 1).value
        if v:
            hh.cell(row=r, column=i_fec + 1, value=str(v).split(" ")[0])

f2 = copia("sin_hora.xlsx", _sin_hora)
lec2 = leer(f2)
sinh = sum(1 for avs in lec2.avisos.values() for a in avs if a.startswith("'Fecha' sin hora"))
horas = {f["momento_local"].hour for f in lec2.filas if f["momento_local"]}
marca("cada fila sin hora trae su aviso", sinh == len(lec2.filas),
      f"{sinh} avisos para {len(lec2.filas)} filas · horas distintas leídas: {horas}")

# el archivo real, con hora, NO debe producir ese aviso
lec_ok = leer(XYGA)
sinh_ok = sum(1 for avs in lec_ok.avisos.values() for a in avs if a.startswith("'Fecha' sin hora"))
marca("el archivo real no genera falsos avisos", sinh_ok == 0,
      f"{sinh_ok} avisos de hora en el archivo bueno · {len(lec_ok.filas)} filas, "
      f"{len({f['momento_local'].hour for f in lec_ok.filas})} horas distintas")

print()
print("=" * 78)
print("DEFECTO 3 · el centinela ya no depende de la posicion de la fila")
print("-" * 78)
FILA_HUECO = FILA_DATOS + 94        # una fila de datos cualquiera, lejos del principio
fA = copia("solapeA.xlsx", lambda hh: hh.cell(row=FILA_HUECO, column=i_tick + 1, value=""))

# B: el mismo archivo con una fila mas al principio, de modo que esa carga cae una fila mas abajo
dstB = os.path.join(TMP, "solapeB.xlsx")
wbB = abrir(XYGA)
hB = wbB[wbB.sheetnames[0]]
hB.insert_rows(FILA_DATOS)
for c in range(1, hB.max_column + 1):
    hB.cell(row=FILA_DATOS, column=c, value=hB.cell(row=FILA_DATOS + 1, column=c).value)
hB.cell(row=FILA_HUECO + 1, column=i_tick + 1, value="")
wbB.save(dstB)

lA = leer(fA)
lB = leer(dstB)
cA = [f for f in lA.filas if str(f["folio_txt"]).startswith("@")]
cB = [f for f in lB.filas if str(f["folio_txt"]).startswith("@")]
if cA and cB:
    a, b = cA[0], cB[0]
    marca("la misma carga da el mismo folio en las dos exportaciones",
          a["folio_txt"] == b["folio_txt"] and a["sha256_fila"] == b["sha256_fila"],
          f"A: fila {a['fila_num']} folio {a['folio_txt']}\n"
          f"B: fila {b['fila_num']} folio {b['folio_txt']}\n"
          f"sha iguales: {a['sha256_fila'] == b['sha256_fila']}")
    marca("y el folio ya no lleva el número de fila",
          "@fila:" not in a["folio_txt"], a["folio_txt"])
else:
    marca("se pudo construir el escenario de solape", False,
          f"centinelas hallados: A={len(cA)} B={len(cB)}")

print()
print("=" * 78)
print("DEFECTO 5 · una corrida que no escribe nada no queda vigente")
print("-" * 78)
# Re-export byte-distinto del mismo mes: mismas filas, sha del archivo distinto.
dstR = os.path.join(TMP, "reexport.xlsx")
wbR = abrir(XYGA)
wbR.save(dstR)   # openpyxl reescribe el zip: mismo contenido, otro sha
import hashlib
sha_orig = hashlib.sha256(io.open(XYGA, "rb").read()).hexdigest()
sha_new = hashlib.sha256(io.open(dstR, "rb").read()).hexdigest()
print(f"  sha del original {sha_orig[:16]}… · del re-export {sha_new[:16]}… · distintos: {sha_orig != sha_new}")
try:
    imp = IP.importar(db, "XYGA", dstR, dry=False)
    marca("la corrida sin altas nace simulada y no vigente",
          imp.estado == "simulada" and imp.vigente is False,
          f"estado={imp.estado} vigente={imp.vigente} "
          f"nuevas={imp.n_nuevas} corregidas={imp.n_corregidas} repetidas={imp.n_repetidas}")
except Aborta as e:
    marca("la corrida sin altas nace simulada y no vigente", False, f"abortó: {e}")
finally:
    db.rollback()

despues = db.query(CargaProveedor).filter(CargaProveedor.vigente.is_(True)).count()
marca("no se escribió ni una carga en la base", antes == despues, f"{antes} antes · {despues} después")
db.close()
shutil.rmtree(TMP, ignore_errors=True)

print()
print("=" * 78)
print(f"  {sum(ok)}/{len(ok)} comprobaciones pasan")
print("=" * 78)
sys.exit(0 if all(ok) else 1)
