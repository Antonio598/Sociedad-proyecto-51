"""Corrige `usa_combustible` en los remolques a los que el proveedor SÍ les cobró diésel.

EL CASO (3-sep-2026): el remolque id=23 —eco 5300121, renumerado a 400914— está marcado
`usa_combustible=False`, es decir "caja seca, no quema nada". Y sin embargo tiene dos
asientos en el libro mayor por 288.0 L y $7,776.00, ambos con destino 'termo'. Uno de los
dos datos miente, y no es el proveedor: los asientos vienen de cargas facturadas y cargadas
verbatim, mientras que la bandera la puso una importación de catálogo.

POR QUÉ IMPORTA Y NO ES COSMÉTICO: `usa_combustible` es lo que decide si un remolque
aparece como termo recargable. Con la bandera en falso, esa caja no se puede seleccionar
para una recarga de termo desde la app, así que sus litros seguirían entrando solo por el
reporte del proveedor y nunca por una captura. La bandera estaba cerrando la puerta por la
que debía entrar el dato.

QUÉ NO HACE: no toca ningún asiento, ninguna carga ni ningún otro campo del remolque. No
inventa la serie del equipo termo, que sigue vacía —eso lo tiene que capturar una persona—.

Uso:
    python -m scripts.corregir_bandera_termo --dry-run   (no escribe NADA)
    python -m scripts.corregir_bandera_termo --aplicar
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AsientoConsumo, Remolque


def candidatos(db):
    """Remolques marcados como que no usan combustible y con litros contables atribuidos.

    El criterio es la EVIDENCIA, no una lista escrita a mano: si mañana aparece otro caso,
    este script lo encuentra solo. Se exige `vigente` y `contable` para no contar asientos
    anulados, y se excluyen los dollies, que por definición no llevan equipo de frío.
    """
    salida = []
    for r in db.execute(select(Remolque).where(
            Remolque.usa_combustible.is_(False),
            Remolque.es_dolly.is_(False))).scalars():
        filas = db.query(AsientoConsumo).filter(
            AsientoConsumo.remolque_id == r.id,
            AsientoConsumo.vigente.is_(True),
            AsientoConsumo.contable.is_(True)).all()
        if not filas:
            continue
        litros = sum(float(a.litros or 0) for a in filas)
        importe = sum(float(a.importe or 0) for a in filas)
        destinos = sorted({(a.destino or "?") for a in filas})
        salida.append((r, len(filas), litros, importe, destinos))
    return salida


def main(aplicar: bool):
    db = SessionLocal()
    filas = candidatos(db)

    print("=" * 78)
    print("REMOLQUES MARCADOS 'NO USA COMBUSTIBLE' A LOS QUE SÍ SE LES COBRÓ DIÉSEL")
    print("-" * 78)
    if not filas:
        print("\n  Ninguno. La bandera y el libro mayor están de acuerdo.")
        db.close()
        return

    for r, n, litros, importe, destinos in filas:
        nombre = r.eco + (f" (hoy {r.eco_nuevo})" if r.eco_nuevo and r.eco_nuevo != r.eco else "")
        print(f"\n  {nombre}   id={r.id}   {r.marca or '-'} {r.anio or '-'}")
        print(f"     {n} asientos · {litros:,.1f} L · ${importe:,.2f} · destino: {'/'.join(destinos)}")
        print(f"     usa_combustible: False -> True")
        if not (r.serie_thermo or "").strip():
            print("     OJO: sigue sin serie del equipo termo. Eso NO lo arregla este script.")

    if not aplicar:
        print("\n" + "=" * 78)
        print("SIMULACIÓN: no se escribió nada. Para aplicar: --aplicar")
        db.close()
        return

    for r, *_ in filas:
        r.usa_combustible = True
    db.commit()

    print("\n" + "=" * 78)
    print(f"{len(filas)} remolque(s) corregido(s). Ningún otro campo se tocó.")
    print("\nRevertir:")
    print("  UPDATE remolques SET usa_combustible=false WHERE id IN ("
          + ", ".join(str(r.id) for r, *_ in filas) + ");")
    db.close()


if __name__ == "__main__":
    main("--aplicar" in sys.argv)
