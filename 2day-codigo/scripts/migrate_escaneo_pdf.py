"""El escaneo se queda con su PDF: columnas nuevas y la carpeta donde vive.

Idempotente (ADD COLUMN IF NOT EXISTS). Agrega a `escaneos_motor`:
  - pdf      nombre del fichero bajo media_dir/escaneos (esc<id>.pdf)
  - pdf_en   cuándo se guardó

No toca ni un dato existente. `archivo` —el nombre con el que llegó el reporte, que es la
primera puerta de idempotencia— se queda exactamente como está.

Con `--desde "ruta\\a\\la\\carpeta"` además ENGANCHA los PDF que ya estén importados: busca
cada `archivo` dentro de esa carpeta y copia el fichero. No importa nada nuevo ni cambia
ninguna fila más allá de `pdf`/`pdf_en`; para importar lecturas que faltan está la carga
desde la pantalla de Combustible, que es donde una persona ve lo que va a entrar.
"""
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

from app.config import settings
from app.db import SessionLocal, engine
from app.models import EscaneoMotor

with engine.begin() as conn:
    for col, tipo in (("pdf", "VARCHAR(120)"), ("pdf_en", "TIMESTAMPTZ")):
        conn.execute(text(f"ALTER TABLE escaneos_motor ADD COLUMN IF NOT EXISTS {col} {tipo}"))
        print(f"OK  escaneos_motor.{col}")

destino = settings.media_dir / "escaneos"
destino.mkdir(parents=True, exist_ok=True)
print(f"OK  carpeta {destino}")

desde = None
if "--desde" in sys.argv:
    desde = Path(sys.argv[sys.argv.index("--desde") + 1])

with SessionLocal() as s:
    filas = s.execute(select(EscaneoMotor)).scalars().all()
    con, sin = sum(1 for e in filas if e.pdf), sum(1 for e in filas if not e.pdf)
    print(f"\n{len(filas)} escaneos · {con} con PDF · {sin} sin él")

    if desde is None:
        print("\n(sin --desde no se engancha ningún fichero)")
        raise SystemExit

    if not desde.is_dir():
        print(f"\nNo existe la carpeta {desde}")
        raise SystemExit(1)

    # Un índice por NOMBRE. Si el mismo nombre aparece en dos subcarpetas se deja fuera:
    # enganchar el PDF equivocado a una lectura sería peor que dejarla sin PDF.
    porn: dict[str, list[Path]] = {}
    for p in desde.rglob("*.pdf"):
        if "__MACOSX" not in str(p):
            porn.setdefault(p.name, []).append(p)
    ambiguos = {n for n, v in porn.items() if len(v) > 1}
    print(f"{sum(len(v) for v in porn.values())} PDF en {desde}"
          + (f" · {len(ambiguos)} nombres repetidos, se omiten" if ambiguos else ""))

    puestos = faltan = 0
    for e in filas:
        if e.pdf or e.archivo in ambiguos:
            continue
        origen = (porn.get(e.archivo) or [None])[0]
        if origen is None or not origen.is_file() or origen.stat().st_size == 0:
            faltan += 1
            continue
        nombre = f"esc{e.id}.pdf"
        shutil.copyfile(origen, destino / nombre)
        e.pdf, e.pdf_en = nombre, datetime.now(timezone.utc)
        puestos += 1
    s.commit()
    print(f"\n{puestos} PDF enganchados · {faltan} sin fichero en esa carpeta")

print("Migración del PDF de escaneo completa.")
