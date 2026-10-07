"""Deja el catálogo con lo que dicen los Excel. Borra lo que sobra. Destructivo y deliberado.

Decisión del usuario (22-sep-2026): «unidades, remolques y ecos así como etiquetas deben de
ser de los excel; toda unidad extra o datos extra debe ser borrado».

LAS TRES FUENTES, y ninguna manda sola:
    FLOTILLA 2DAY 2026 MEX.xlsx   el maestro de la flota
    PLACAS.xlsx                   las placas
    HOLOGRAMA.xlsx                los códigos NFC

Un activo cuenta como «de los Excel» si aparece en CUALQUIERA de los tres, no sólo en
FLOTILLA. Medirlo contra FLOTILLA a secas habría marcado 14 unidades y 29 remolques para
borrar, y de esas 4 unidades y los 29 remolques SÍ vienen de un Excel —el de placas o el de
hologramas—. Uno de esos cuatro es T301, que además tiene 2 escaneos de motor y 11 cargas del
proveedor: borrarlo habría destruido datos reales por una prueba mal planteada.

LO QUE SE BORRA, y es lo único: las unidades que no aparecen en NINGUNO de los tres Excel.
Hoy son 10, todas con `activo=False`, sin escaneos, sin cargas, sin etiquetas. Con ellas se
van sus alias (1 cada una).

LO QUE NO SE BORRA, aunque a primera vista lo pareciera:
    · los 89 remolques   → los 29 que faltan en FLOTILLA están en PLACAS o en HOLOGRAMA
    · las 109 etiquetas  → las 109 tienen su código en HOLOGRAMA.xlsx
    · los 2 alias con `origen='manual'` → su texto SÍ está en los Excel («T147» en los tres,
      «32BG2M» en FLOTILLA). El origen dice cómo se capturaron, no si el dato es real.

NADA QUE ESTÉ EN USO SE BORRA. Se comprueba contra escaneos_motor, cargas_proveedor y
asientos_consumo antes de tocar nada, y si algo a borrar estuviera en uso, el script se
detiene sin escribir.

Uso:
    python -m scripts.depurar_contra_excel --dry-run     (no borra NADA)
    python -m scripts.depurar_contra_excel --borrar
"""
import sys
import warnings
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

import openpyxl
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import AliasEco, EtiquetaActivo, Remolque, Unidad

DESCARGAS = Path.home() / "Downloads"
EXCELS = {
    "FLOTILLA": DESCARGAS / "FLOTILLA 2DAY 2026 MEX.xlsx",
    "PLACAS": DESCARGAS / "PLACAS.xlsx",
    "HOLOGRAMA": DESCARGAS / "HOLOGRAMA.xlsx",
}

borrar = "--borrar" in sys.argv
if not borrar and "--dry-run" not in sys.argv:
    print(__doc__)
    raise SystemExit(2)


def norm(v):
    """Un texto comparable: mayúsculas, sin espacios. None si queda vacío."""
    if v is None:
        return None
    s = "".join(str(v).strip().upper().split())
    return s or None


def suelto(v):
    """El mismo texto SIN separadores: «D-01» y «D01» son la misma cosa, y «70-0F-E2» es
    «700FE2».

    Hace falta porque `alias_eco` existe justamente para guardar esas variantes: el Excel
    escribe «D-01» y el alias guarda «D01» para que cualquiera de las dos resuelva. Comparar
    con guiones marcaba 14 alias como «no está en ningún Excel» cuando eran exactamente el
    mismo valor, y borrarlos habría quitado la capacidad de resolver que el alias aporta.
    """
    s = norm(v)
    if not s:
        return None
    return "".join(c for c in s if c.isalnum()) or None


def textos(path: Path) -> set:
    """Todo texto de todas las hojas. Bruto a propósito: para DECIDIR UN BORRADO conviene
    equivocarse por conservar de más, no por borrar de más."""
    out = set()
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    for ws in wb.worksheets:
        for fila in ws.iter_rows(values_only=True):
            for celda in fila:
                t = norm(celda)
                if t:
                    out.add(t)
    wb.close()
    return out


faltan = [n for n, p in EXCELS.items() if not p.exists()]
if faltan:
    raise SystemExit(f"NO SE ENCUENTRAN los Excel {faltan}. Sin ellos no hay con qué "
                     f"comparar, así que no se borra nada.")

fuentes = {n: textos(p) for n, p in EXCELS.items()}
universo = set().union(*fuentes.values())
# El mismo universo sin separadores, para que «D01» encuentre a «D-01».
universo_suelto = {suelto(t) for t in universo} - {None}
print("── los Excel " + "─" * 56)
for n, p in EXCELS.items():
    print(f"  {n:10s} {len(fuentes[n]):>5} textos   {p.name}")
print(f"  {len(universo)} textos distintos en total")

s = SessionLocal()

# Qué está EN USO por lo que se conserva. Se pregunta a la base, no se supone.
with engine.connect() as c:
    uni_uso, rem_uso = set(), set()
    for tabla in ("escaneos_motor", "cargas_proveedor", "asientos_consumo"):
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t"), {"t": tabla})}
        if "unidad_id" in cols:
            uni_uso |= {r[0] for r in c.execute(text(
                f"SELECT DISTINCT unidad_id FROM {tabla} WHERE unidad_id IS NOT NULL"))}
        if "remolque_id" in cols:
            rem_uso |= {r[0] for r in c.execute(text(
                f"SELECT DISTINCT remolque_id FROM {tabla} WHERE remolque_id IS NOT NULL"))}


def en_excel(*valores) -> bool:
    """¿Alguno de estos textos está en algún Excel, con o sin separadores?"""
    return any(norm(v) in universo or suelto(v) in universo_suelto
               for v in valores if v)


unis = s.execute(select(Unidad).order_by(Unidad.clave)).scalars().all()
rems = s.execute(select(Remolque).order_by(Remolque.eco)).scalars().all()
etis = s.execute(select(EtiquetaActivo)).scalars().all()
alias = s.execute(select(AliasEco)).scalars().all()

u_fuera = [u for u in unis if not en_excel(u.clave, u.placa, u.placas_nuevas, u.serie)]
r_fuera = [r for r in rems if not en_excel(r.eco, r.eco_nuevo, r.placa,
                                           r.placas_nuevas, r.serie)]
e_fuera = [e for e in etis if not en_excel(e.codigo)]
a_fuera = [a for a in alias if not en_excel(a.texto_norm)]

print("\n── lo que SOBRA (no aparece en ningún Excel) " + "─" * 25)
print(f"  unidades : {len(u_fuera):>3} de {len(unis)}")
for u in u_fuera:
    print(f"      {u.clave:8s} activo={str(u.activo):5s} "
          f"{'EN USO — NO SE TOCA' if u.id in uni_uso else 'sin datos'}")
print(f"  remolques: {len(r_fuera):>3} de {len(rems)}")
for r in r_fuera:
    print(f"      {r.eco or '—':10s} {'EN USO — NO SE TOCA' if r.id in rem_uso else ''}")
print(f"  etiquetas: {len(e_fuera):>3} de {len(etis)}")
print(f"  alias    : {len(a_fuera):>3} de {len(alias)}")

# Nada en uso se borra: si algo lo estuviera, el script para.
chocan = ([u.clave for u in u_fuera if u.id in uni_uso]
          + [r.eco for r in r_fuera if r.id in rem_uso])
if chocan:
    raise SystemExit(
        f"\nSE DETIENE SIN BORRAR: {chocan} no están en los Excel pero SÍ los usan escaneos "
        f"o cargas del proveedor. Borrarlos destruiría datos reales. Decide qué hacer con "
        f"ellos —¿falta actualizar el Excel?— antes de volver a correr esto.")

arrastre = [a for a in alias
            if a.unidad_id in {u.id for u in u_fuera}
            or a.remolque_id in {r.id for r in r_fuera}]
print(f"\n  se van además {len(arrastre)} alias que apuntan a lo borrado")
total = len(u_fuera) + len(r_fuera) + len(e_fuera) + len({a.id for a in a_fuera + arrastre})
print(f"  TOTAL A BORRAR: {total} filas")

if not borrar:
    print("\nENSAYO: no se tocó nada. Para hacerlo de verdad:  --borrar")
    raise SystemExit

with engine.begin() as c:
    for a in {a.id for a in a_fuera + arrastre}:
        c.execute(text("DELETE FROM alias_eco WHERE id = :i"), {"i": a})
    for e in e_fuera:
        c.execute(text("DELETE FROM etiquetas_activo WHERE id = :i"), {"i": e.id})
    for r in r_fuera:
        c.execute(text("DELETE FROM remolques WHERE id = :i"), {"i": r.id})
    for u in u_fuera:
        c.execute(text("DELETE FROM unidades WHERE id = :i"), {"i": u.id})
print(f"\n  borradas {len(u_fuera)} unidades, {len(r_fuera)} remolques, "
      f"{len(e_fuera)} etiquetas y {len({a.id for a in a_fuera + arrastre})} alias")

s.close()
with engine.connect() as c:
    for t in ("unidades", "remolques", "alias_eco", "etiquetas_activo",
              "escaneos_motor", "cargas_proveedor"):
        print(f"  {c.execute(text(f'SELECT count(*) FROM {t}')).scalar_one():>6}  {t}")
