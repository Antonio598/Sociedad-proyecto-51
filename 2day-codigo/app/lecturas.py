"""Lecturas iniciales de los equipos y horas de termo de la plataforma web.

Dos cosas que la app no hacía:

1. LECTURA INICIAL. El punto de partida de cada equipo —odómetro de un tracto o camión,
   horas del termo de un remolque o camión, litros en sus tanques— lo captura el
   administrador. Es la lectura «anterior» de la primera foto: sin ella el primer odómetro
   no tenía contra qué compararse ni el termo desde dónde medir sus horas.

2. HORAS DE TERMO. La sección «Horas de termo» del panel sólo la llenaba el bot de WhatsApp
   (retirado). Ahora, cuando combustible despacha una recarga de TERMO, se anota su renglón
   con las horas de la foto, las horas trabajadas desde la lectura anterior de ESE termo,
   los litros cargados y el consumo L/h.

   POR TERMO, NO POR TRACTO: el bot llevaba la cuenta por tracto y el equipo de frío va en el
   REMOLQUE —un tracto engancha remolques distintos—, lo que producía saltos de miles de
   horas. Aquí la lectura anterior es la del mismo remolque (o la del camión, cuyo termo va
   pegado a la unidad).

Las rutas se registran desde el final de main.py con `registrar(app)`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import fecha_flota
from .models import (AuditoriaThermo, LecturaInicial, Remolque, SolicitudRecarga, TipoUnidad,
                     Unidad)

log = logging.getLogger("combustible.lecturas")
router = APIRouter()

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


# ── consultas que usa el resto de la app ─────────────────────────────────────
def inicial_vigente(db: Session, unidad_id: int | None = None,
                    remolque_id: int | None = None) -> LecturaInicial | None:
    q = select(LecturaInicial).where(LecturaInicial.anulada_en.is_(None))
    q = (q.where(LecturaInicial.unidad_id == unidad_id) if unidad_id is not None
         else q.where(LecturaInicial.remolque_id == remolque_id))
    return db.execute(q.order_by(LecturaInicial.fecha.desc(), LecturaInicial.id.desc())
                      .limit(1)).scalar()


def odometro_inicial(db: Session, unidad_id: int, hasta_fecha: date):
    """(odómetro, fecha) de la lectura inicial vigente de la unidad, si es de esa fecha o antes."""
    li = inicial_vigente(db, unidad_id=unidad_id)
    if li is None or li.odometro is None or li.fecha > hasta_fecha:
        return None
    return (li.odometro, li.fecha)


def ultimas_horas_termo(db: Session, *, remolque: Remolque | None = None,
                        unidad: Unidad | None = None) -> float | None:
    """Las últimas horas conocidas de ESE termo: su último renglón de «Horas de termo» o su
    lectura inicial, la que sea mayor (un horómetro no retrocede)."""
    candidatos = []
    if remolque is not None:
        r = db.execute(select(AuditoriaThermo.hrs_fin).where(
            AuditoriaThermo.remolque == remolque.eco, AuditoriaThermo.hrs_fin.isnot(None))
            .order_by(AuditoriaThermo.id.desc()).limit(1)).scalar()
        li = inicial_vigente(db, remolque_id=remolque.id)
    else:
        r = db.execute(select(AuditoriaThermo.hrs_fin).where(
            AuditoriaThermo.unidad_id == unidad.id, AuditoriaThermo.remolque.is_(None),
            AuditoriaThermo.hrs_fin.isnot(None))
            .order_by(AuditoriaThermo.id.desc()).limit(1)).scalar()
        li = inicial_vigente(db, unidad_id=unidad.id)
    if r is not None:
        candidatos.append(float(r))
    if li is not None and li.horas_termo is not None:
        candidatos.append(float(li.horas_termo))
    return max(candidatos) if candidatos else None


def registrar_termo_de_despacho(db: Session, s: SolicitudRecarga, litros: float | None) -> None:
    """Anota en «Horas de termo» una recarga de termo recién despachada. No hace commit."""
    if (s.tipo_recarga or "") != "termo" or s.unidad_id is None:
        return
    vals = [(e.valor_final if e.valor_final is not None else e.valor_ia)
            for e in (s.evidencias or []) if e.tipo == "termo"]
    vals = [v for v in vals if v is not None]
    if not vals:
        return
    horas = float(vals[-1])
    uni = db.get(Unidad, s.unidad_id)
    rem = db.get(Remolque, s.remolque_id) if s.remolque_id else None
    if rem is None and uni is not None and uni.tipo == TipoUnidad.TRACTO:
        return   # un termo de tracto va en un remolque: sin remolque no se sabe de cuál es
    previas = ultimas_horas_termo(db, remolque=rem, unidad=uni)
    trab = (horas - previas) if (previas is not None and horas > previas) else None
    if previas is not None and horas < previas:
        log.warning("Horas de termo %s menores que las anteriores (%s) de %s: se anotan sin "
                    "horas trabajadas", horas, previas, rem.eco if rem else uni.clave)
    reg = AuditoriaThermo(
        unidad_id=s.unidad_id, operador_id=s.operador_id, remolque=rem.eco if rem else None,
        mes=_MESES[fecha_flota().month - 1], hrs_inic=previas, hrs_fin=horas, horas_trab=trab,
        litros_thermo=litros, rto_thermo=(round(litros / trab, 3) if litros and trab else None))
    db.add(reg)


# ── rutas ─────────────────────────────────────────────────────────────────────
def lectura_dict(li: LecturaInicial) -> dict:
    return {
        "id": li.id, "fecha": li.fecha.isoformat(),
        "tipo": "unidad" if li.unidad_id else "remolque",
        "activo_id": li.unidad_id or li.remolque_id,
        "eco": li.unidad.clave if li.unidad else (li.remolque.eco if li.remolque else None),
        "odometro": li.odometro, "horas_termo": li.horas_termo,
        "litros_tanque": li.litros_tanque, "litros_tanque_termo": li.litros_tanque_termo,
        "nota": li.nota, "creada_en": li.creada_en.isoformat() if li.creada_en else None,
        "anulada": li.anulada_en is not None, "motivo_anulacion": li.motivo_anulacion,
    }


def _num(v, que, minimo=0.0, maximo=None):
    v = (str(v) if v is not None else "").strip().replace(",", "")
    if not v:
        return None
    try:
        x = float(v)
    except ValueError:
        raise HTTPException(400, f"{que}: escribe sólo el número")
    if x < minimo or (maximo is not None and x > maximo):
        raise HTTPException(400, f"{que} fuera de rango")
    return x


def registrar(app) -> None:
    from .main import get_db, registrar_actividad, require_admin, require_rol
    puede_ver = require_rol("admin", "coordinador", "combustible", "gerente")

    @router.get("/api/lecturas-iniciales")
    def listar(incluir_anuladas: bool = Query(True), user: dict = Depends(puede_ver),
               db: Session = Depends(get_db)) -> list[dict]:
        q = select(LecturaInicial).order_by(LecturaInicial.fecha.desc(), LecturaInicial.id.desc())
        if not incluir_anuladas:
            q = q.where(LecturaInicial.anulada_en.is_(None))
        return [lectura_dict(x) for x in db.execute(q.limit(1000)).scalars()]

    @router.post("/api/lecturas-iniciales")
    def crear(tipo: str = Form(...), activo_id: int = Form(...), fecha: str = Form(""),
              odometro: str = Form(""), horas_termo: str = Form(""),
              litros_tanque: str = Form(""), litros_tanque_termo: str = Form(""),
              nota: str = Form(""), user: dict = Depends(require_admin),
              db: Session = Depends(get_db)) -> dict:
        tipo = (tipo or "").strip().lower()
        if tipo not in ("unidad", "remolque"):
            raise HTTPException(400, "Elige si es una unidad o un remolque")
        try:
            f = date.fromisoformat(fecha) if fecha else fecha_flota()
        except ValueError:
            raise HTTPException(400, "Fecha inválida")
        if f > fecha_flota():
            raise HTTPException(400, "La fecha no puede ser futura")
        odo = _num(odometro, "El odómetro", maximo=5_000_000)
        hrs = _num(horas_termo, "Las horas del termo", maximo=500_000)
        lts = _num(litros_tanque, "Los litros del tanque", maximo=3000)
        lts_t = _num(litros_tanque_termo, "Los litros del tanque del termo", maximo=1000)
        if tipo == "unidad":
            u = db.get(Unidad, activo_id)
            if u is None:
                raise HTTPException(404, "Unidad no encontrada")
            if hrs is not None and u.tipo == TipoUnidad.TRACTO:
                raise HTTPException(400, "Un tracto no lleva termo: las horas van en el remolque")
            if odo is None and hrs is None and lts is None and lts_t is None:
                raise HTTPException(400, "Captura al menos el odómetro o los litros")
            li = LecturaInicial(unidad_id=u.id, fecha=f, odometro=odo, horas_termo=hrs,
                                litros_tanque=lts, litros_tanque_termo=lts_t)
            ref = u.clave
        else:
            r = db.get(Remolque, activo_id)
            if r is None:
                raise HTTPException(404, "Remolque no encontrado")
            if odo is not None:
                raise HTTPException(400, "Un remolque no lleva odómetro: captura las horas del termo")
            if hrs is None and lts is None:
                raise HTTPException(400, "Captura las horas del termo o los litros de su tanque")
            li = LecturaInicial(remolque_id=r.id, fecha=f, horas_termo=hrs, litros_tanque=lts)
            ref = r.eco
        li.nota = (nota or "").strip()[:400] or None
        li.capturada_por_id = user.get("id")
        db.add(li)
        db.flush()
        registrar_actividad(db, accion="lectura_inicial", usuario=user, entidad="lectura_inicial",
                            entidad_id=li.id, meta={"eco": ref, "odometro": odo, "horas": hrs,
                                                    "litros": lts, "litros_termo": lts_t})
        db.commit()
        db.refresh(li)
        return {"ok": True, "lectura": lectura_dict(li)}

    @router.post("/api/lecturas-iniciales/{lid}/anular")
    def anular(lid: int, motivo: str = Form(...), user: dict = Depends(require_admin),
               db: Session = Depends(get_db)) -> dict:
        li = db.get(LecturaInicial, lid)
        if li is None:
            raise HTTPException(404, "Lectura no encontrada")
        if li.anulada_en is not None:
            raise HTTPException(409, "Esa lectura ya está anulada")
        if len((motivo or "").strip()) < 5:
            raise HTTPException(400, "Escribe el motivo de la anulación")
        li.anulada_en = datetime.now(timezone.utc)
        li.anulada_por_id = user.get("id")
        li.motivo_anulacion = motivo.strip()[:400]
        registrar_actividad(db, accion="lectura_inicial_anulada", usuario=user,
                            entidad="lectura_inicial", entidad_id=lid,
                            meta={"motivo": li.motivo_anulacion})
        db.commit()
        return {"ok": True, "lectura": lectura_dict(li)}

    app.include_router(router)
