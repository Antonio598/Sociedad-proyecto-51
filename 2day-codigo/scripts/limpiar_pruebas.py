"""Borra el lote de datos de PRUEBA del ciclo de solicitudes. Destructivo y deliberado.

POR QUÉ EXISTE. La flota todavía no captura por la aplicación: las 17 solicitudes son de UNA
sola persona sobre UNA sola unidad, con órdenes creadas con minutos de diferencia y una de
1,111 litros. Son de desarrollo, no de operación. Mientras estuvieran ahí, cualquier cifra que
la aplicación presente al cliente saldría mezclada con datos inventados.

LAS TRES FACTURAS VAN EN EL MISMO LOTE, y es lo que más importa: 11,600/400, 8,700/300 y
13,050/450 dan las tres EXACTAMENTE 29.00 $/L, con fechas a las 10:00, 11:00 y 12:00 del mismo
día. De ahí sale el `costo_litro` que la aplicación muestra hoy. Con las 639 cargas reales ya
importadas, el precio verdadero se puede calcular del proveedor y no hace falta inventarlo.

NO TOCA: unidades, remolques, operadores, viajes históricos, asignaciones, anomalías,
escaneos, etiquetas ni nada de la ingesta de proveedores.

Uso:
    python -m scripts.limpiar_pruebas --dry-run     (no borra NADA)
    python -m scripts.limpiar_pruebas --borrar      (borra, y hay que decirlo explícitamente)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (EvidenciaRecarga, Factura, OrdenDespacho, SolicitudRecarga,
                        TransicionSolicitud, Unidad)

# En orden de dependencia: lo que APUNTA se borra antes que lo apuntado.
ORDEN = [
    (EvidenciaRecarga, "evidencias de recarga"),
    (TransicionSolicitud, "transiciones de solicitud"),
    (OrdenDespacho, "órdenes de despacho"),
    (SolicitudRecarga, "solicitudes de recarga"),
    (Factura, "facturas"),
]


def main(borrar: bool):
    db = SessionLocal()
    print("=" * 78)
    print("LOTE DE PRUEBA DEL CICLO DE SOLICITUDES")
    print("-" * 78)

    for modelo, etiqueta in ORDEN:
        print(f"  {db.query(modelo).count():>4}  {etiqueta}")

    print("\n  Quién las creó:")
    filas = db.execute(
        select(func.count(SolicitudRecarga.id), Unidad.clave)
        .select_from(SolicitudRecarga)
        .join(Unidad, Unidad.id == SolicitudRecarga.unidad_id, isouter=True)
        .group_by(Unidad.clave).order_by(func.count(SolicitudRecarga.id).desc())).all()
    for n, clave in filas:
        print(f"     {n:>4} solicitudes sobre {clave or '(sin unidad)'}")

    print("\n  Las facturas, y por qué son de prueba:")
    for f in db.execute(select(Factura).order_by(Factura.id)).scalars():
        pl = (f.total / f.litros) if (f.total and f.litros) else None
        print(f"     id={f.id} {f.nombre_emisor or '-':<20} ${f.total or 0:>10,.2f} / "
              f"{f.litros or 0:>6,.0f} L = {pl:.2f} $/L" if pl else f"     id={f.id}")

    print("\n  NO se tocan: unidades, remolques, operadores, viajes, asignaciones,")
    print("  anomalías, escaneos, etiquetas ni la ingesta de proveedores.")

    if not borrar:
        print("\n" + "=" * 78)
        print("SIMULACIÓN: no se borró nada. Para borrar de verdad: --borrar")
        db.close()
        return

    # `factura_id` de la orden apunta a la factura: se suelta antes para que el borrado no
    # dependa del orden en que la base resuelva las claves.
    db.query(OrdenDespacho).update({OrdenDespacho.factura_id: None},
                                   synchronize_session=False)
    borradas = {}
    for modelo, etiqueta in ORDEN:
        borradas[etiqueta] = db.query(modelo).delete(synchronize_session=False)
    db.commit()

    print("\n" + "=" * 78)
    for etiqueta, n in borradas.items():
        print(f"  {n:>4}  {etiqueta} borradas")
    print("\n  Quedan en pie:")
    for modelo, etiqueta in ORDEN:
        print(f"     {db.query(modelo).count():>4}  {etiqueta}")
    print("\n  Revertir: restaurar el respaldo del lote que se hizo antes de correr esto,")
    print("  en la carpeta respaldos/ (antes_limpiar_pruebas_*.sql).")
    db.close()


if __name__ == "__main__":
    main("--borrar" in sys.argv)
