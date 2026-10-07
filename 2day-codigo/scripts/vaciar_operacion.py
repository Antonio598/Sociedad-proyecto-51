"""Vacía la OPERACIÓN y conserva los ACTIVOS, los escaneos y la ingesta de proveedores.

Decisión del usuario (21-sep-2026): «todo, que quede limpia de operadores, viajes y todo,
menos de los pdf y los de oxxo y xyga».

QUÉ SE VA — la operación:
    viajes, operadores, usuarios, solicitudes y sus transiciones, órdenes de despacho,
    evidencias, asignaciones, anomalías, descuentos, registro de actividad, eventos de
    WhatsApp, uso de IA, auditoría de termo y propuestas de catálogo.

QUÉ SE QUEDA — y por qué, porque tres de estas NO se pidieron explícitamente:
    · escaneos_motor y sus PDF        → se pidió («los pdf»)
    · toda la ingesta de proveedores  → se pidió («los de oxxo y xyga»)
    · unidades                        → OBLIGADO: `escaneos_motor.unidad_id` no acepta nulo,
                                        así que conservar los escaneos exige conservarlas
    · remolques                       → si se fueran, 124 cargas del proveedor y sus 124
                                        asientos perderían el remolque: sería degradar
                                        justamente lo que se pidió conservar
    · alias_eco, etiquetas_activo     → son lo que RESUELVE los textos del proveedor a cada
                                        activo; sin ellos la próxima importación de OXXO o
                                        XYGA resolvería mucho peor

EL ACCESO. Al vaciar `usuarios` no queda ninguna cuenta. Es recuperable:
`auth.crear_admin_si_falta()` corre al arrancar y crea el admin cuando la tabla está vacía.
Sin eso, este script dejaría el sistema sin puerta de entrada.

Uso:
    python -m scripts.vaciar_operacion --dry-run     (no borra NADA, enseña el plan)
    python -m scripts.vaciar_operacion --borrar      (borra, y hay que decirlo)
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app.db import engine

# Se conserva. Todo lo demás se vacía.
CONSERVAR = {
    "escaneos_motor",                                    # «los pdf»
    "cargas_proveedor", "importaciones_proveedor",       # «los de oxxo y xyga»
    "proveedores", "estaciones_proveedor",
    "tarjetas_combustible", "empleados_proveedor",
    "asientos_consumo",
    "unidades", "remolques", "alias_eco",                # los activos y su resolución
    "etiquetas_activo", "importaciones_etiquetas", "importaciones_placas",
}

borrar = "--borrar" in sys.argv
if not borrar and "--dry-run" not in sys.argv:
    print(__doc__)
    raise SystemExit(2)

insp = inspect(engine)
tablas = sorted(insp.get_table_names())
desconocidas = CONSERVAR - set(tablas)
if desconocidas:
    print(f"AVISO: estas tablas de la lista no existen: {sorted(desconocidas)}")
vaciar = [t for t in tablas if t not in CONSERVAR]

with engine.connect() as c:
    n = {t: c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tablas}

# Columnas de lo que SE CONSERVA que apuntan a lo que SE VA: hay que ponerlas en nulo antes,
# o la clave foránea impide el borrado. Se calcula del esquema, no a mano.
a_nulo = []
for t in sorted(CONSERVAR & set(tablas)):
    cols = {col["name"]: col for col in insp.get_columns(t)}
    for fk in insp.get_foreign_keys(t):
        if not fk["constrained_columns"]:
            continue
        col = fk["constrained_columns"][0]
        if fk["referred_table"] in vaciar:
            if not cols[col]["nullable"]:
                raise SystemExit(
                    f"IMPOSIBLE: {t}.{col} apunta a {fk['referred_table']} y no acepta nulo. "
                    f"No se puede conservar {t} y vaciar {fk['referred_table']}.")
            with engine.connect() as c:
                usados = c.execute(text(
                    f'SELECT count(*) FROM "{t}" WHERE "{col}" IS NOT NULL')).scalar_one()
            a_nulo.append((t, col, fk["referred_table"], usados))

print("── SE CONSERVA " + "─" * 54)
for t in sorted(CONSERVAR & set(tablas), key=lambda x: -n[x]):
    print(f"  {n[t]:>7,}  {t}")
print("── SE VACÍA " + "─" * 57)
for t in sorted(vaciar, key=lambda x: -n[x]):
    if n[t]:
        print(f"  {n[t]:>7,}  {t}")
print(f"\n  {sum(n[t] for t in vaciar):,} filas se borran · "
      f"{sum(n[t] for t in CONSERVAR & set(tablas)):,} se conservan")

if a_nulo:
    print("\n── VÍNCULOS QUE SE SUELTAN (se ponen en nulo antes de borrar) " + "─" * 6)
    for t, col, destino, usados in a_nulo:
        print(f"  {t}.{col} -> {destino}: {usados} fila(s)")

if not borrar:
    print("\nENSAYO: no se tocó nada. Para hacerlo de verdad:  --borrar")
    raise SystemExit

# DELETE y no TRUNCATE. TRUNCATE rechaza por la EXISTENCIA de una clave foránea que apunte
# a la tabla, aunque no quede ni una fila apuntando: «cannot truncate a table referenced in a
# foreign key constraint». Y aquí hay tablas que se conservan —unidades, cargas_proveedor—
# apuntando a otras que se vacían. DELETE sólo mira las filas de verdad, que es lo que hace
# falta una vez soltados los vínculos.
#
# Hijas antes que madres: se ordenan topológicamente por las claves foráneas DENTRO del grupo
# que se vacía. Sin ese orden, borrar `usuarios` antes que `registro_actividad` falla.
def madres(t: str) -> set:
    """A qué tablas apunta `t` con una clave foránea."""
    return {fk["referred_table"] for fk in insp.get_foreign_keys(t)} - {t}


orden, pendientes = [], list(vaciar)
while pendientes:
    # Una tabla se puede vaciar cuando ninguna de sus madres sigue pendiente.
    libres = [t for t in pendientes if not (madres(t) & set(pendientes))]
    if not libres:            # ciclo entre tablas: se vacían juntas al final
        orden.extend(pendientes)
        break
    orden.extend(libres)
    pendientes = [t for t in pendientes if t not in libres]
orden.reverse()               # las hijas primero: las madres se borran al final

# Todo en UNA transacción: o se hace entero o no se hace nada.
with engine.begin() as c:
    for t, col, _destino, usados in a_nulo:
        if usados:
            c.execute(text(f'UPDATE "{t}" SET "{col}" = NULL WHERE "{col}" IS NOT NULL'))
            print(f"  soltado  {t}.{col} ({usados} filas)")
    for t in orden:
        r = c.execute(text(f'DELETE FROM "{t}"'))
        if r.rowcount:
            print(f"  vaciada  {t} ({r.rowcount:,} filas)")
    # Los contadores vuelven a 1: se pidió que quedara limpia.
    for t in vaciar:
        seq = c.execute(text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": t}).scalar()
        if seq:
            c.execute(text(f"ALTER SEQUENCE {seq} RESTART WITH 1"))
    print(f"  vaciadas {len(vaciar)} tablas, contadores a 1")

with engine.connect() as c:
    quedan = {t: c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tablas}
mal = [t for t in vaciar if quedan[t]]
print(f"\n  vaciadas OK: {not mal}" + (f"  ({mal} siguen con filas)" if mal else ""))
print(f"  conservadas: {sum(quedan[t] for t in CONSERVAR & set(tablas)):,} filas")
print("\nReinicia el backend: `crear_admin_si_falta()` recrea el admin al arrancar.")
