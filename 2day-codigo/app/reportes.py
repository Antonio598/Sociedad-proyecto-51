"""Reportes por periodo con comparativo, según el documento del dueño.

  «Siempre con base a cortes de tiempo (mes, bimestre, trimestre, semestre, año…) y con
   comparativos contra la información histórica (enero 2026 contra enero 2025, 2026 contra
   2025).»

Lo que arma, para un periodo [desde, hasta] y su periodo de comparación:
  · CONSUMO general en litros y en costo: cargas de XIGA y Oxxo (importe real facturado) más
    las cargas fuera de plataforma no anuladas. Por plataforma, motor/termo y por mes.
  · RANKING de tractocamiones y de camiones por rendimiento (km/l), con el dato duro de las
    lecturas de motor (PDF) cuyo periodo termina dentro del rango.
  · RANKING de termos de remolque y de termos de camión por consumo (L/h): litros cargados
    al termo entre la primera y la última lectura de horas, sobre las horas trabajadas.
  · ALERTAS de exceso: órdenes autorizadas o despachadas por encima del tope calculado.
  · HISTÓRICO mensual de los últimos 24 meses.

Las rutas se registran desde el final de main.py con `registrar(app)`.
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import fecha_flota, precio_del_litro
from .models import (CargaExterna, CargaProveedor, EscaneoMotor, EstadoSolicitud,
                     EvidenciaRecarga, OrdenDespacho, Proveedor, Remolque, SolicitudRecarga,
                     TipoUnidad, Unidad)

router = APIRouter()

# Debajo de esto una lectura de motor no es una medición de carretera (arranque en frío,
# maniobras): ver `piso_km_escaneo` en config.py. Se marca, no se descarta.
_PISO_KM = 500.0
_DESPACHADAS = (EstadoSolicitud.DESPACHADA, EstadoSolicitud.FACTURADA,
                EstadoSolicitud.CONCILIADA, EstadoSolicitud.EN_DISCREPANCIA)


# ── periodos ──────────────────────────────────────────────────────────────────
def _menos_un_anio(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:            # 29 de febrero
        return d.replace(year=d.year - 1, day=28)


def periodo_comparacion(desde: date, hasta: date, modo: str) -> tuple[date, date] | None:
    if modo == "anio_anterior":
        return _menos_un_anio(desde), _menos_un_anio(hasta)
    if modo == "anterior":
        dias = (hasta - desde).days + 1
        return desde - timedelta(days=dias), desde - timedelta(days=1)
    return None


# ── consumo ───────────────────────────────────────────────────────────────────
def _cond_proveedor(d: date, h: date):
    return (CargaProveedor.vigente.is_(True), CargaProveedor.fecha_operacion >= d,
            CargaProveedor.fecha_operacion <= h, CargaProveedor.fuera_de_flota.is_(False),
            func.coalesce(CargaProveedor.combustible, "diesel") == "diesel")


def _importe_externa(c: CargaExterna) -> tuple[float, bool]:
    """(importe, estimado). Sin importe capturado se valúa al precio del año (config)."""
    if c.importe is not None:
        return float(c.importe), False
    return float(c.litros) * precio_del_litro(c.fecha.year), True


def consumo(db: Session, d: date, h: date) -> dict:
    plataformas = []
    for clave, nombre, lts, imp in db.execute(
            select(Proveedor.clave, Proveedor.nombre, func.sum(CargaProveedor.litros),
                   func.sum(CargaProveedor.importe))
            .join(Proveedor, Proveedor.id == CargaProveedor.proveedor_id)
            .where(*_cond_proveedor(d, h)).group_by(Proveedor.clave, Proveedor.nombre)):
        plataformas.append({"clave": clave, "nombre": nombre,
                            "litros": round(float(lts or 0), 2), "importe": round(float(imp or 0), 2)})
    por_destino = defaultdict(float)
    for dest, lts in db.execute(select(CargaProveedor.destino, func.sum(CargaProveedor.litros))
                                .where(*_cond_proveedor(d, h)).group_by(CargaProveedor.destino)):
        por_destino[dest if dest in ("motor", "termo") else "sin_clasificar"] += float(lts or 0)

    externas = db.execute(select(CargaExterna).where(
        CargaExterna.anulada_en.is_(None), CargaExterna.fecha >= d,
        CargaExterna.fecha <= h)).scalars().all()
    ext_lts = sum(c.litros for c in externas)
    ext_imp = 0.0
    ext_estimado = False
    for c in externas:
        imp, est = _importe_externa(c)
        ext_imp += imp
        ext_estimado = ext_estimado or est
        por_destino[c.uso if c.uso in ("motor", "termo") else "sin_clasificar"] += c.litros
    plataformas.append({"clave": "EXTERNA", "nombre": "Fuera de plataforma",
                        "litros": round(ext_lts, 2), "importe": round(ext_imp, 2),
                        "importe_estimado": ext_estimado})

    total_l = sum(p["litros"] for p in plataformas)
    total_i = sum(p["importe"] for p in plataformas)
    return {
        "litros": round(total_l, 2), "importe": round(total_i, 2),
        "precio_promedio": round(total_i / total_l, 4) if total_l else None,
        "por_plataforma": plataformas,
        "por_destino": {k: round(v, 2) for k, v in por_destino.items()},
        "mensual": mensual(db, d, h),
    }


def mensual(db: Session, d: date, h: date) -> list[dict]:
    meses: dict[str, dict] = {}
    mes = func.to_char(CargaProveedor.fecha_operacion, "YYYY-MM")
    for m, lts, imp in db.execute(select(mes, func.sum(CargaProveedor.litros),
                                         func.sum(CargaProveedor.importe))
                                  .where(*_cond_proveedor(d, h)).group_by(mes)):
        meses[m] = {"mes": m, "litros": float(lts or 0), "importe": float(imp or 0)}
    for c in db.execute(select(CargaExterna).where(
            CargaExterna.anulada_en.is_(None), CargaExterna.fecha >= d,
            CargaExterna.fecha <= h)).scalars():
        m = c.fecha.strftime("%Y-%m")
        x = meses.setdefault(m, {"mes": m, "litros": 0.0, "importe": 0.0})
        x["litros"] += c.litros
        x["importe"] += _importe_externa(c)[0]
    return [{"mes": k, "litros": round(v["litros"], 2), "importe": round(v["importe"], 2)}
            for k, v in sorted(meses.items())]


# ── rendimiento de motor (km/l) ───────────────────────────────────────────────
def ranking_motor(db: Session, d: date, h: date) -> dict:
    acum: dict[int, dict] = {}
    for e, clave, tipo in db.execute(
            select(EscaneoMotor, Unidad.clave, Unidad.tipo)
            .join(Unidad, Unidad.id == EscaneoMotor.unidad_id)
            .where(EscaneoMotor.periodo_fin >= d, EscaneoMotor.periodo_fin <= h)):
        if not e.km or not e.litros or e.km <= 0 or e.litros <= 0:
            continue
        x = acum.setdefault(e.unidad_id, {"unidad_id": e.unidad_id, "eco": clave,
                                           "tipo": tipo.value if tipo else None,
                                           "km": 0.0, "litros": 0.0, "lecturas": 0})
        x["km"] += e.km
        x["litros"] += e.litros
        x["lecturas"] += 1
    filas = []
    for x in acum.values():
        x["kml"] = round(x["km"] / x["litros"], 3)
        x["km"] = round(x["km"], 1)
        x["litros"] = round(x["litros"], 1)
        x["poca_muestra"] = x["km"] < _PISO_KM
        filas.append(x)
    filas.sort(key=lambda r: r["kml"], reverse=True)   # mejor primero; la UI invierte
    return {"tractos": [f for f in filas if f["tipo"] == TipoUnidad.TRACTO.value],
            "camiones": [f for f in filas if f["tipo"] == TipoUnidad.CAMION.value]}


# ── consumo de termos (L/h) ───────────────────────────────────────────────────
def _horas(evs: list[EvidenciaRecarga]) -> float | None:
    vals = [(e.valor_final if e.valor_final is not None else e.valor_ia)
            for e in evs if e.tipo == "termo"]
    vals = [v for v in vals if v is not None]
    return float(vals[-1]) if vals else None


def ranking_termos(db: Session, d: date, h: date) -> dict:
    """Por cada termo: lecturas de horas en orden; L/h = litros cargados DESPUÉS de la
    primera lectura ÷ (horas de la última − horas de la primera). Con menos de dos lecturas
    no hay cuenta posible y no se inventa."""
    filas = db.execute(
        select(SolicitudRecarga, OrdenDespacho)
        .join(OrdenDespacho, OrdenDespacho.solicitud_id == SolicitudRecarga.id)
        .where(SolicitudRecarga.tipo_recarga == "termo",
               SolicitudRecarga.estado.in_(_DESPACHADAS))).all()
    grupos: dict[tuple, list] = defaultdict(list)
    for s, o in filas:
        cuando = o.despachada_en or o.autorizada_en
        if cuando is None:
            continue
        dia = fecha_flota(cuando)
        if not (d <= dia <= h):
            continue
        horas = _horas(s.evidencias or [])
        litros = o.litros_reales if o.litros_reales is not None else o.litros_autorizados
        if horas is None or not litros:
            continue
        clave = ("R", s.remolque_id) if s.remolque_id else ("U", s.unidad_id)
        if clave[1] is None:
            continue
        grupos[clave].append((cuando, horas, float(litros)))
    remolques, camiones = [], []
    for (tipo, aid), lecturas in grupos.items():
        lecturas.sort()
        if len(lecturas) < 2:
            continue
        dh = lecturas[-1][1] - lecturas[0][1]
        lts = sum(x[2] for x in lecturas[1:])
        if dh <= 0:
            continue
        eco = (db.get(Remolque, aid).eco if tipo == "R" else db.get(Unidad, aid).clave)
        fila = {"id": aid, "eco": eco, "horas": round(dh, 1), "litros": round(lts, 1),
                "lh": round(lts / dh, 3), "lecturas": len(lecturas)}
        (remolques if tipo == "R" else camiones).append(fila)
    for lista in (remolques, camiones):
        lista.sort(key=lambda r: r["lh"])     # menor consumo por hora = mejor, primero
    return {"remolques": remolques, "camiones": camiones}


# ── alertas de exceso ─────────────────────────────────────────────────────────
def alertas_exceso(db: Session, d: date, h: date) -> list[dict]:
    """Litros autorizados o cargados por ENCIMA de lo que el viaje debía gastar (tope). El
    documento: «si sólo se consumieron 300 L y le están asignando más, que arroje alertas»."""
    out = []
    for o, s, clave in db.execute(
            select(OrdenDespacho, SolicitudRecarga, Unidad.clave)
            .join(SolicitudRecarga, SolicitudRecarga.id == OrdenDespacho.solicitud_id)
            .outerjoin(Unidad, Unidad.id == SolicitudRecarga.unidad_id)
            .where(OrdenDespacho.tope_litros.is_not(None))):
        dia = fecha_flota(o.autorizada_en) if o.autorizada_en else None
        if dia is None or not (d <= dia <= h):
            continue
        cargado = o.litros_reales if o.litros_reales is not None else o.litros_autorizados
        if cargado is None or cargado <= o.tope_litros:
            continue
        out.append({"folio": o.folio, "fecha": dia.isoformat(), "eco": clave,
                    "tope": o.tope_litros, "estimado": o.tope_estimado,
                    "autorizados": o.litros_autorizados, "reales": o.litros_reales,
                    "exceso": round(cargado - o.tope_litros, 1),
                    "exceso_pct": round((cargado / o.tope_litros - 1) * 100, 1)})
    out.sort(key=lambda r: r["exceso"], reverse=True)
    return out


# ── el reporte completo ──────────────────────────────────────────────────────
def _var(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 1)


def generar(db: Session, d: date, h: date, comparar: str = "anio_anterior") -> dict:
    actual = {"desde": d.isoformat(), "hasta": h.isoformat(), "consumo": consumo(db, d, h),
              "motor": ranking_motor(db, d, h), "termos": ranking_termos(db, d, h),
              "alertas_exceso": alertas_exceso(db, d, h)}
    salida = {"actual": actual, "comparacion": None, "variacion": None, "modo": comparar}
    p2 = periodo_comparacion(d, h, comparar)
    if p2:
        c = {"desde": p2[0].isoformat(), "hasta": p2[1].isoformat(),
             "consumo": consumo(db, *p2), "motor": ranking_motor(db, *p2)}
        salida["comparacion"] = c
        flota_kml = {}
        for k, per in (("actual", actual), ("comparacion", c)):
            tot_km = sum(r["km"] for t in per["motor"].values() for r in t)
            tot_l = sum(r["litros"] for t in per["motor"].values() for r in t)
            flota_kml[k] = round(tot_km / tot_l, 3) if tot_l else None
        salida["variacion"] = {
            "litros_pct": _var(actual["consumo"]["litros"], c["consumo"]["litros"]),
            "importe_pct": _var(actual["consumo"]["importe"], c["consumo"]["importe"]),
            "kml_flota": flota_kml,
            "kml_flota_pct": _var(flota_kml["actual"], flota_kml["comparacion"]),
        }
    hoy = fecha_flota()
    inicio_hist = date(hoy.year - 2, hoy.month, 1)
    salida["historico"] = mensual(db, inicio_hist, hoy)
    return salida


def _rango(desde: str, hasta: str) -> tuple[date, date]:
    hoy = fecha_flota()
    try:
        d = date.fromisoformat(desde) if desde else hoy.replace(day=1)
        h = date.fromisoformat(hasta) if hasta else hoy
    except ValueError:
        raise HTTPException(400, "Fechas inválidas: usa AAAA-MM-DD")
    if d > h:
        raise HTTPException(400, "La fecha inicial es posterior a la final")
    if (h - d).days > 3 * 366:
        raise HTTPException(400, "El rango máximo es de 3 años")
    return d, h


def registrar(app) -> None:
    from .main import get_db, require_rol
    puede_ver = require_rol("coordinador", "combustible", "admin", "gerente")

    @router.get("/api/reportes")
    def reporte(desde: str = Query(""), hasta: str = Query(""),
                comparar: str = Query("anio_anterior", pattern="^(anterior|anio_anterior|no)$"),
                user: dict = Depends(puede_ver), db: Session = Depends(get_db)) -> dict:
        d, h = _rango(desde, hasta)
        return generar(db, d, h, comparar)

    @router.get("/api/reportes.xlsx")
    def reporte_xlsx(desde: str = Query(""), hasta: str = Query(""),
                     comparar: str = Query("anio_anterior", pattern="^(anterior|anio_anterior|no)$"),
                     user: dict = Depends(puede_ver), db: Session = Depends(get_db)):
        from openpyxl import Workbook
        from openpyxl.styles import Font
        d, h = _rango(desde, hasta)
        r = generar(db, d, h, comparar)
        wb = Workbook()

        def hoja(titulo, enc, filas):
            ws = wb.create_sheet(titulo[:31])
            ws.append(enc)
            for c in ws[1]:
                c.font = Font(bold=True)
            for f in filas:
                ws.append(f)
            for i, _ in enumerate(enc):
                ws.column_dimensions[chr(65 + i)].width = 16

        wb.remove(wb.active)
        a, c = r["actual"], r["comparacion"]
        filas = [["Periodo", a["desde"] + " a " + a["hasta"], a["consumo"]["litros"], a["consumo"]["importe"]]]
        if c:
            filas.append(["Comparación", c["desde"] + " a " + c["hasta"], c["consumo"]["litros"],
                          c["consumo"]["importe"]])
            v = r["variacion"]
            filas.append(["Variación %", "", v["litros_pct"], v["importe_pct"]])
        hoja("Consumo", ["", "Rango", "Litros", "Costo"], filas)
        hoja("Por plataforma", ["Plataforma", "Litros", "Costo"],
             [[p["nombre"], p["litros"], p["importe"]] for p in a["consumo"]["por_plataforma"]])
        hoja("Mensual", ["Mes", "Litros", "Costo"],
             [[m["mes"], m["litros"], m["importe"]] for m in a["consumo"]["mensual"]])
        for k, t in (("tractos", "Tractocamiones km-l"), ("camiones", "Camiones km-l")):
            hoja(t, ["Económico", "Km", "Litros", "Km/l", "Lecturas"],
                 [[x["eco"], x["km"], x["litros"], x["kml"], x["lecturas"]] for x in a["motor"][k]])
        for k, t in (("remolques", "Termos remolque L-h"), ("camiones", "Termos camión L-h")):
            hoja(t, ["Económico", "Horas", "Litros", "L/h", "Lecturas"],
                 [[x["eco"], x["horas"], x["litros"], x["lh"], x["lecturas"]] for x in a["termos"][k]])
        hoja("Alertas de exceso", ["Folio", "Fecha", "Económico", "Tope", "Autorizados", "Reales",
                                   "Exceso L", "Exceso %"],
             [[x["folio"], x["fecha"], x["eco"], x["tope"], x["autorizados"], x["reales"],
               x["exceso"], x["exceso_pct"]] for x in a["alertas_exceso"]])
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition":
                                 f'attachment; filename="reporte_{d.isoformat()}_{h.isoformat()}.xlsx"'})

    app.include_router(router)
