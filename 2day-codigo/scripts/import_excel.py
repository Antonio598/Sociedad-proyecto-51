"""Importa el historial del Excel del cliente a la base de datos.

Carga las 4 hojas -> operadores, unidades, viajes, auditoria_thermo,
seguimiento_descuentos. Limpia datos sucios (espacios, fechas inválidas,
valores no numéricos) y reporta métricas de calidad.

Uso (desde backend/, con el venv activado):
    python -m scripts.import_excel "C:\\ruta\\Bitacora 25-26 editado.xlsx"
"""

import sys
import warnings
from datetime import date, datetime

import openpyxl

from app.db import SessionLocal
from app.models import (
    AuditoriaThermo,
    Operador,
    SeguimientoDescuento,
    TipoConfig,
    TipoUnidad,
    Unidad,
    Viaje,
)

warnings.filterwarnings("ignore")


# ── Coerciones seguras ──────────────────────────────────────────────────────
def f(v):
    """A float o None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def i(v):
    x = f(v)
    return int(round(x)) if x is not None else None


def d(v):
    """A date o None (descarta #VALUE! y basura)."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def clave(v):
    """Normaliza la clave de unidad; None si es basura."""
    if v is None:
        return None
    s = str(v).strip().upper()
    return s if len(s) >= 2 else None


def tokens(nombre):
    if not nombre:
        return frozenset()
    return frozenset(t for t in str(nombre).strip().upper().split() if t)


def tipo_unidad_de_clave(cl):
    return TipoUnidad.TRACTO if cl.startswith("T") else TipoUnidad.CAMION


def tipo_config(v):
    if not v:
        return None
    s = str(v).strip().upper()
    return {"SENCILLO": TipoConfig.SENCILLO, "FULL": TipoConfig.FULL,
            "THORTON": TipoConfig.THORTON}.get(s)


def rows(ws, start=2):
    for r in ws.iter_rows(min_row=start, values_only=True):
        yield r


def cell(r, idx):
    """idx es 1-based (columna A=1)."""
    return r[idx - 1] if idx - 1 < len(r) else None


def main(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ses = SessionLocal()
    stats = {}

    # Limpiar tablas de dominio (import idempotente).
    #
    # El orden importa y la lista debe estar COMPLETA: se borran primero las tablas que
    # apuntan a otras. Faltaban Anomalia (-> viajes) y EscaneoMotor (-> unidades), que se
    # agregaron después: sin ellas, borrar Viaje/Unidad reventaba por clave foránea y el
    # import se caía a la mitad, dejando la base con la mitad vieja y la mitad nueva.
    from app.models import Anomalia, EscaneoMotor

    # ── SEGURO (2026-08) ────────────────────────────────────────────────────
    # Este borrado se escribió cuando la única fuente era el Excel. Después nació toda
    # la operación de la app (solicitudes de recarga, órdenes, asignaciones, cuentas de
    # operador, bitácora de actividad) y NINGUNA está contemplada arriba. Correr esto hoy
    # con datos vivos rompería la clave foránea o dejaría huérfano el trabajo del día:
    # `usuarios.operador_id` (los accesos de los choferes), `solicitudes_recarga`
    # (operador/unidad/viaje), `asignaciones_viaje`, `remolques.operador_asignado_id` y
    # `registro_actividad`. Antes fallaba a la mitad y dejaba la base partida; ahora se
    # niega a empezar y dice por qué.
    from sqlalchemy import func, select
    from app.models import AsignacionViaje, SolicitudRecarga, Usuario

    vivos = {
        "solicitudes de recarga": ses.scalar(select(func.count()).select_from(SolicitudRecarga)) or 0,
        "asignaciones de viaje": ses.scalar(select(func.count()).select_from(AsignacionViaje)) or 0,
        "cuentas ligadas a un operador": ses.scalar(
            select(func.count()).select_from(Usuario).where(Usuario.operador_id.isnot(None))) or 0,
    }
    if any(vivos.values()) and "--borrar-operacion" not in sys.argv:
        print("ABORTADO: la base ya tiene operación viva y este import BORRA viajes, "
              "unidades y operadores.")
        for k, v in vivos.items():
            if v:
                print(f"   · {v} {k}")
        print("\nEste script es para poblar una base VACÍA desde el Excel histórico.")
        print("Si de verdad quieres borrar la operación, repite con --borrar-operacion")
        print("(y haz un respaldo antes: no hay vuelta atrás).")
        ses.close()
        sys.exit(1)

    for model in (Anomalia, SeguimientoDescuento, AuditoriaThermo, EscaneoMotor,
                  Viaje, Unidad, Operador):
        ses.query(model).delete()
    ses.commit()

    # ── 1) Operadores (hoja 'op') ───────────────────────────────────────────
    op_by_tokens = {}   # frozenset(tokens) -> Operador
    for r in rows(wb["op"]):
        nombre = cell(r, 1)
        if not nombre or not str(nombre).strip():
            continue
        o = Operador(nombre=str(nombre).strip(), numero=i(cell(r, 2)))
        ses.add(o)
        op_by_tokens[tokens(nombre)] = o
    ses.flush()
    stats["operadores_catalogo"] = len(op_by_tokens)

    sueltos = {}  # tokens -> Operador creado desde nombres de viaje sin match

    def resolver_operador(nombre):
        if not nombre or not str(nombre).strip():
            return None
        tk = tokens(nombre)
        if tk in op_by_tokens:
            return op_by_tokens[tk]
        if tk in sueltos:
            return sueltos[tk]
        o = Operador(nombre=str(nombre).strip(), numero=None)
        ses.add(o)
        ses.flush()
        sueltos[tk] = o
        return o

    # ── 2) Unidades (de BITACORA + THORTON AUDITORIA) ───────────────────────
    uni_by_clave = {}

    def resolver_unidad(v):
        cl = clave(v)
        if not cl:
            return None
        if cl not in uni_by_clave:
            u = Unidad(clave=cl, tipo=tipo_unidad_de_clave(cl))
            ses.add(u)
            ses.flush()
            uni_by_clave[cl] = u
        return uni_by_clave[cl]

    # ── 3) Viajes (hoja BITACORA) ───────────────────────────────────────────
    n_via = n_skip_fecha = n_skip_uni = 0
    for r in rows(wb["BITACORA"]):
        fecha = d(cell(r, 1))
        uni = resolver_unidad(cell(r, 2))
        if fecha is None:
            n_skip_fecha += 1
            continue
        if uni is None:
            n_skip_uni += 1
            continue
        op = resolver_operador(cell(r, 4))
        analista = cell(r, 15)
        ses.add(Viaje(
            fecha=fecha, unidad_id=uni.id, tipo_config=tipo_config(cell(r, 3)),
            operador_id=(op.id if op else None),
            kilometros=f(cell(r, 5)), lts_scaner=f(cell(r, 6)), rto=f(cell(r, 7)),
            dif=f(cell(r, 8)), lts_real=f(cell(r, 9)), rto_real=f(cell(r, 10)),
            pct=f(cell(r, 11)),
            codigo_cv=(str(cell(r, 12)).strip()[:10] if cell(r, 12) else None),
            descuentos=f(cell(r, 13)), pct_cummins=f(cell(r, 14)),
            analista=(str(analista).strip()[:10] if analista else None),
            odometro=f(cell(r, 16)), vel_max=f(cell(r, 17)), rpm=f(cell(r, 18)),
            ralenti=f(cell(r, 19)), crucero=f(cell(r, 20)),
            paradas_panico=i(cell(r, 21)), num_frenadas=i(cell(r, 22)),
            neutralizacion=f(cell(r, 23)), top_gear=f(cell(r, 24)),
            km_top_gear=f(cell(r, 25)), pct_top_gear=f(cell(r, 26)),
            gear_down=f(cell(r, 27)), km_gear_down=f(cell(r, 28)),
            pct_gear_down=f(cell(r, 29)), cambios_descendentes=f(cell(r, 30)),
            c_manejo=f(cell(r, 31)),
        ))
        n_via += 1
    stats["viajes"] = n_via
    stats["viajes_saltados_sin_fecha"] = n_skip_fecha
    stats["viajes_saltados_sin_unidad"] = n_skip_uni

    # ── 4) Auditoría Thermo (hoja THORTON AUDITORIA) ────────────────────────
    n_th = 0
    for r in rows(wb["THORTON AUDITORIA"]):
        uni = resolver_unidad(cell(r, 1))
        if uni is None:
            continue
        op = resolver_operador(cell(r, 2))
        ses.add(AuditoriaThermo(
            unidad_id=uni.id, operador_id=(op.id if op else None),
            kilometros=f(cell(r, 3)), lts_scaner=f(cell(r, 4)), rto=f(cell(r, 5)),
            dif=f(cell(r, 6)), lts_real=f(cell(r, 7)), rto_real=f(cell(r, 8)),
            pct=f(cell(r, 9)),
            remolque=(str(cell(r, 10)).strip()[:20] if cell(r, 10) else None),
            hrs_inic=f(cell(r, 11)), hrs_fin=f(cell(r, 12)), horas_trab=f(cell(r, 13)),
            litros_thermo=f(cell(r, 14)), rto_thermo=f(cell(r, 15)),
            mes=(str(cell(r, 16)).strip()[:20] if cell(r, 16) else None),
        ))
        n_th += 1
    stats["auditoria_thermo"] = n_th

    # ── 5) Seguimiento Descuentos (bloque principal A–G) ────────────────────
    n_de = 0
    for r in rows(wb["Seguimiento Descuentos 2023-202"]):
        unidad_txt = cell(r, 2)
        operador_txt = cell(r, 4)
        if not unidad_txt and not operador_txt:
            continue
        contesta_raw = str(cell(r, 6)).strip().upper() if cell(r, 6) else None
        contesta = True if contesta_raw == "SI" else (False if contesta_raw == "NO" else None)
        ses.add(SeguimientoDescuento(
            fecha=d(cell(r, 1)),
            unidad_clave=(str(unidad_txt).strip()[:20] if unidad_txt else None),
            tipo=(str(cell(r, 3)).strip()[:20] if cell(r, 3) else None),
            operador_nombre=(str(operador_txt).strip()[:120] if operador_txt else None),
            lts=f(cell(r, 5)), contesta=contesta,
            area=(str(cell(r, 7)).strip()[:60] if cell(r, 7) else None),
        ))
        n_de += 1
    stats["seguimiento_descuentos"] = n_de

    ses.commit()

    stats["unidades"] = len(uni_by_clave)
    stats["operadores_creados_desde_viajes"] = len(sueltos)
    stats["operadores_total"] = len(op_by_tokens) + len(sueltos)

    print("=== IMPORTACIÓN COMPLETA ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    ses.close()


def _confirmar() -> None:
    """Este import REEMPLAZA el dominio entero. Si ya hay datos, avisa antes de borrarlos.

    Es una importación inicial, no incremental: sin este aviso, correrla sobre una base con
    meses de capturas del bot los borraba todos sin decir nada.
    """
    from sqlalchemy import func, select

    from app.models import (Anomalia, AsignacionViaje, EscaneoMotor, SolicitudRecarga,
                            Usuario)

    with SessionLocal() as s:
        conteos = [(m.__name__, s.scalar(select(func.count()).select_from(m)) or 0)
                   for m in (Viaje, Unidad, Operador, AuditoriaThermo, EscaneoMotor, Anomalia)]
        # Lo que este script NO borra pero SÍ rompe: la operación de la app apunta a
        # operadores, unidades y viajes. Antes no se mencionaba y el aviso parecía
        # hablar solo de historia vieja.
        rotos = [(m.__name__, s.scalar(select(func.count()).select_from(m)) or 0)
                 for m in (SolicitudRecarga, AsignacionViaje)]
        rotos.append(("Usuario (cuentas de operador)", s.scalar(
            select(func.count()).select_from(Usuario).where(Usuario.operador_id.isnot(None))) or 0))
    hay = [(n, c) for n, c in conteos if c]
    if not hay:
        return
    # Sin emoji a propósito: la consola de Windows usa cp1252 y un caracter fuera de esa
    # tabla revienta el aviso con UnicodeEncodeError ANTES de poder advertir nada.
    print("AVISO: esta importacion BORRA y reemplaza el dominio. Hoy hay:")
    for n, c in hay:
        print(f"     {c:>8,}  {n}")
    peligro = [(n, c) for n, c in rotos if c]
    if peligro:
        print("\n  Y esto QUEDARIA ROTO O HUERFANO (el script no lo contempla):")
        for n, c in peligro:
            print(f"     {c:>8,}  {n}")
    try:
        if input("\n  Escribe BORRAR para continuar: ").strip() != "BORRAR":
            sys.exit("Cancelado.")
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nCancelado.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Uso: python -m scripts.import_excel "ruta\\al\\archivo.xlsx" [--forzar]')
        sys.exit(1)
    if "--forzar" not in sys.argv:
        _confirmar()
    main(sys.argv[1])
