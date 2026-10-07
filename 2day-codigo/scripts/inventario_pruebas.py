"""Inventario de DATOS DE PRUEBA vs DATOS REALES. Solo lectura: no borra nada.

Nace de la decision del cliente de "empezar limpio". La recomendacion es NO borrar la
historia de la flota, pero SI retirar lo que se creo durante el desarrollo. Este script
produce la lista concreta para que una persona la apruebe antes de tocar nada.

Uso:
    python -m scripts.inventario_pruebas
"""

import re

from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.models import (AsignacionViaje, EscaneoMotor, Operador, OrdenDespacho,
                        SolicitudRecarga, TransicionSolicitud, Unidad, Usuario, Viaje)

# Marcas que delatan un dato creado para probar, no para operar.
PATRON = re.compile(r"prueba|test|demo|xxx|aaa|zzz|tmp|temporal", re.I)


def _linea(t):
    print("\n" + "=" * 74)
    print(t)
    print("-" * 74)


def main() -> None:
    db = SessionLocal()
    total = lambda m: db.scalar(select(func.count()).select_from(m)) or 0

    _linea("PANORAMA")
    print(f"  viajes historicos ......... {total(Viaje):>6,}")
    print(f"  escaneos de motor ......... {total(EscaneoMotor):>6,}")
    print(f"  unidades .................. {total(Unidad):>6,}")
    print(f"  operadores ................ {total(Operador):>6,}")
    print(f"  solicitudes de recarga .... {total(SolicitudRecarga):>6,}")
    print(f"  asignaciones de viaje ..... {total(AsignacionViaje):>6,}")
    print(f"  cuentas de usuario ........ {total(Usuario):>6,}")

    # ── asignaciones de prueba ──────────────────────────────────────────────
    _linea("ASIGNACIONES DE VIAJE con marca de prueba  (candidatas a limpiar)")
    asigs = db.execute(select(AsignacionViaje).order_by(AsignacionViaje.id)).scalars().all()
    sospechosas = []
    for a in asigs:
        # Se guarda QUE campo delato la marca: quien aprueba la limpieza tiene que poder
        # juzgar por si mismo, no fiarse de que el script "dijo que era prueba".
        for campo, val in (("destino", a.destino), ("origen", a.origen), ("nota", a.nota)):
            m = PATRON.search(val or "")
            if m:
                sospechosas.append((a, campo, m.group(), val))
                break
    for a, campo, txt, val in sospechosas:
        op = db.get(Operador, a.operador_id)
        un = db.get(Unidad, a.unidad_id)
        alerta = "  << ACTIVA: un operador la ve ahora" if a.estado.value == "activa" else ""
        print(f"  id={a.id:<4} {a.estado.value:<10} {(un.clave if un else '?'):<7} "
              f"{(op.nombre[:20] if op else '?'):<22} {campo}='{(val or '')[:32]}'{alerta}")
    activas = [x for x in sospechosas if x[0].estado.value == "activa"]
    print(f"  -> {len(sospechosas)} de {len(asigs)} traen marca de prueba"
          + (f"; {len(activas)} de ellas ACTIVA(S): revisar una por una" if activas else ""))

    # ── solicitudes: reales vs de prueba ────────────────────────────────────
    _linea("SOLICITUDES DE RECARGA  (una por una: son solo 17)")
    sols = db.execute(select(SolicitudRecarga).order_by(SolicitudRecarga.id)).scalars().all()
    for s in sols:
        op = db.get(Operador, s.operador_id) if s.operador_id else None
        un = db.get(Unidad, s.unidad_id) if s.unidad_id else None
        orden = db.execute(select(OrdenDespacho).where(
            OrdenDespacho.solicitud_id == s.id)).scalar_one_or_none()
        ntr = db.scalar(select(func.count()).select_from(TransicionSolicitud).where(
            TransicionSolicitud.solicitud_id == s.id)) or 0
        marca = "PRUEBA?" if PATRON.search((s.motivo or "")) else ""
        print(f"  id={s.id:<4} {s.estado.value:<14} {s.tipo_recarga:<6} "
              f"{(un.clave if un else '—'):<7} {(op.nombre[:20] if op else '—'):<22} "
              f"lts={str(s.litros_solicitados or '—'):<7} orden={(orden.folio if orden else '—'):<12} "
              f"transiciones={ntr} {marca}")

    # ── ¿la operacion real ya usa la app? ───────────────────────────────────
    # Es una de las decisiones abiertas del cliente y los datos la contestan solos:
    # si TODA la captura viene de una persona sobre una o dos unidades, es desarrollo.
    _linea("¿LA OPERACION YA CAPTURA POR LA APP?  (lo dicen los datos)")
    ops = {}
    unis = {}
    for s in sols:
        ops[s.operador_id] = ops.get(s.operador_id, 0) + 1
        unis[s.unidad_id] = unis.get(s.unidad_id, 0) + 1
    print(f"  operadores distintos que han capturado: {len([k for k in ops if k])}")
    for oid, n in sorted(ops.items(), key=lambda x: -x[1]):
        o = db.get(Operador, oid) if oid else None
        print(f"     {n:>3} solicitudes  {(o.nombre if o else '(sin operador)')}")
    print(f"  unidades distintas involucradas: {len([k for k in unis if k])}")
    for uid, n in sorted(unis.items(), key=lambda x: -x[1])[:5]:
        u = db.get(Unidad, uid) if uid else None
        print(f"     {n:>3} solicitudes  {(u.clave if u else '(sin unidad)')}")
    if len([k for k in ops if k]) <= 2 and len([k for k in unis if k]) <= 3:
        print("\n  LECTURA: toda la captura viene de una o dos personas sobre unas pocas")
        print("  unidades. Eso es DESARROLLO, no operacion. La flota todavia NO captura")
        print("  por la app -> hay margen para hacer bien el rediseño antes de que entre")
        print("  volumen real.")

    # ── que se perderia si se borrara la historia ───────────────────────────
    _linea("LO QUE SE PERDERIA SI SE BORRARA LA HISTORIA (para dimensionar)")
    con_lts = db.scalar(select(func.count()).select_from(Viaje).where(
        Viaje.lts_real.isnot(None))) or 0
    con_rto = db.scalar(select(func.count()).select_from(Viaje).where(
        Viaje.rto_real.isnot(None))) or 0
    rango = db.execute(select(func.min(Viaje.fecha), func.max(Viaje.fecha))).first()
    print(f"  viajes con litros ......... {con_lts:,}")
    print(f"  viajes con rendimiento .... {con_rto:,}  <- la linea base de las anomalias")
    print(f"  rango de fechas ........... {rango[0]} .. {rango[1]}")

    # unidades que quedarian sin linea base (las anomalias exigen >=5)
    porund = dict(db.execute(
        select(Viaje.unidad_id, func.count()).where(Viaje.rto_real.isnot(None))
        .group_by(Viaje.unidad_id)).all())
    con_base = sum(1 for v in porund.values() if v >= 5)
    print(f"  unidades con >=5 registros . {con_base} de {total(Unidad)}")
    print("     (por debajo de 5, 'consumo atipico' y 'sifoneo' NO se pueden disparar)")

    _linea("RECOMENDACION")
    print("  CONSERVAR: los viajes historicos, los escaneos, las anomalias resueltas")
    print("             y las penalizaciones. Son la linea base de la vigilancia.")
    print("  LIMPIAR  : solo las asignaciones y solicitudes marcadas arriba, previa")
    print("             aprobacion, y dejando constancia de que se retiro.")
    print("\n  Este script NO borra nada. Es solo el inventario para decidir.")
    db.close()


if __name__ == "__main__":
    main()
