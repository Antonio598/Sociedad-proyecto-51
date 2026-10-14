"""Cargas de diésel FUERA de las plataformas (XIGA, Oxxo): transferencias a choferes.

El documento del dueño pide que la herramienta reciba también estas cargas, que hoy el
responsable de combustible lleva en un Excel. Entran de dos formas:
  · a mano, una por una (formulario);
  · importando la PLANTILLA de Excel que se descarga de aquí mismo.
Nada se borra: una carga equivocada se anula con motivo.

Las rutas viven aquí y no en main.py para no hacerlo crecer más; se registran al final de
main.py con `app.include_router(router)`.
"""

from __future__ import annotations

import io
import unicodedata
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalogo
from .models import CargaExterna, Operador

router = APIRouter()

COLUMNAS = ["Fecha", "Económico", "Uso (motor/termo)", "Operador", "Litros", "Importe",
            "Referencia", "Nota"]
_LIMITE_ARCHIVO = 10 * 1024 * 1024
_MAX_FILAS = 5000


def _deps():
    # Import tardío: este módulo se carga al FINAL de main.py, cuando ya existen.
    from .main import get_db, registrar_actividad, require_combustible
    return get_db, registrar_actividad, require_combustible


def _norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
    return " ".join(t.upper().split())


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _fecha(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _operador(db: Session, texto: str | None) -> int | None:
    """Por número de empleado o por nombre exacto (sin acentos ni mayúsculas). Si no hay una
    sola coincidencia, no se adivina: queda el texto para revisarlo."""
    t = (texto or "").strip()
    if not t:
        return None
    if t.isdigit():
        ids = db.execute(select(Operador.id).where(Operador.numero == t)).scalars().all()
        if len(ids) == 1:
            return ids[0]
    objetivo = _norm(t)
    ids = [o.id for o in db.execute(select(Operador.id, Operador.nombre)).all()
           if _norm(o.nombre) == objetivo]
    return ids[0] if len(ids) == 1 else None


def _validar(db: Session, fila: dict) -> tuple[dict | None, list[str]]:
    """Convierte una fila (del formulario o del Excel) en los campos de la carga."""
    errores = []
    f = _fecha(fila.get("fecha"))
    if f is None:
        errores.append("fecha inválida (usa AAAA-MM-DD o DD/MM/AAAA)")
    elif f > date.today():
        errores.append("la fecha está en el futuro")
    lts = _num(fila.get("litros"))
    if lts is None or lts <= 0:
        errores.append("litros inválidos")
    elif lts > 2000:
        errores.append("más de 2,000 L en una carga: revisa el dato")
    imp = _num(fila.get("importe"))
    if imp is not None and imp < 0:
        errores.append("importe negativo")
    uso = _norm(fila.get("uso") or "motor").lower()
    if uso not in ("motor", "termo"):
        errores.append("uso debe ser motor o termo")
    eco = (str(fila.get("eco") or "")).strip()
    if not eco:
        errores.append("falta el económico")
    r = catalogo.resolver_activo(db, eco=eco) if eco else None
    if eco and (r is None or not r.ok):
        errores.append(f"el económico {eco} no existe en el catálogo")
    if errores:
        return None, errores
    op_txt = (str(fila.get("operador") or "")).strip() or None
    return {
        "fecha": f, "litros": round(lts, 3), "importe": round(imp, 2) if imp is not None else None,
        "uso": uso, "eco_texto": eco[:40], "unidad_id": r.unidad_id, "remolque_id": r.remolque_id,
        "operador_id": _operador(db, op_txt), "operador_texto": (op_txt or "")[:120] or None,
        "referencia": (str(fila.get("referencia") or "").strip()[:80]) or None,
        "nota": (str(fila.get("nota") or "").strip()[:400]) or None,
    }, []


def carga_dict(c: CargaExterna) -> dict:
    return {
        "id": c.id, "fecha": c.fecha.isoformat(), "eco": (c.unidad.clave if c.unidad else
                                                          c.remolque.eco if c.remolque else c.eco_texto),
        "uso": c.uso, "operador_id": c.operador_id, "operador": c.operador_texto,
        "litros": c.litros, "importe": c.importe, "referencia": c.referencia, "nota": c.nota,
        "origen": c.origen, "creada_en": c.creada_en.isoformat() if c.creada_en else None,
        "anulada": c.anulada_en is not None, "motivo_anulacion": c.motivo_anulacion,
    }


def registrar(app) -> None:
    """Conecta las dependencias de main.py y registra las rutas."""
    global get_db, _registrar, require_combustible
    get_db, _registrar, require_combustible = _deps()

    @router.get("/api/cargas-externas")
    def listar(desde: str = Query(""), hasta: str = Query(""),
               incluir_anuladas: bool = Query(True),
               user: dict = Depends(require_combustible),
               db: Session = Depends(get_db)) -> dict:
        q = select(CargaExterna).order_by(CargaExterna.fecha.desc(), CargaExterna.id.desc())
        d, h = _fecha(desde), _fecha(hasta)
        if d:
            q = q.where(CargaExterna.fecha >= d)
        if h:
            q = q.where(CargaExterna.fecha <= h)
        if not incluir_anuladas:
            q = q.where(CargaExterna.anulada_en.is_(None))
        filas = db.execute(q.limit(2000)).scalars().all()
        vivas = [c for c in filas if c.anulada_en is None]
        return {"cargas": [carga_dict(c) for c in filas],
                "total_litros": round(sum(c.litros for c in vivas), 2),
                "total_importe": round(sum(c.importe or 0 for c in vivas), 2)}

    @router.post("/api/cargas-externas")
    def crear(fecha: str = Form(...), eco: str = Form(...), litros: str = Form(...),
              uso: str = Form("motor"), operador: str = Form(""), importe: str = Form(""),
              referencia: str = Form(""), nota: str = Form(""),
              user: dict = Depends(require_combustible),
              db: Session = Depends(get_db)) -> dict:
        datos, errores = _validar(db, {"fecha": fecha, "eco": eco, "litros": litros, "uso": uso,
                                        "operador": operador, "importe": importe,
                                        "referencia": referencia, "nota": nota})
        if errores:
            raise HTTPException(400, "; ".join(errores))
        c = CargaExterna(**datos, origen="formulario", capturada_por_id=user.get("id"))
        db.add(c)
        db.flush()
        _registrar(db, accion="carga_externa_capturada", usuario=user, entidad="carga_externa",
                   entidad_id=c.id, meta={"eco": datos["eco_texto"], "litros": datos["litros"]})
        db.commit()
        return {"ok": True, "carga": carga_dict(c)}

    @router.post("/api/cargas-externas/{carga_id}/anular")
    def anular(carga_id: int, motivo: str = Form(...),
               user: dict = Depends(require_combustible),
               db: Session = Depends(get_db)) -> dict:
        c = db.get(CargaExterna, carga_id)
        if c is None:
            raise HTTPException(404, "Carga no encontrada")
        if c.anulada_en is not None:
            raise HTTPException(409, "Esa carga ya está anulada")
        motivo = (motivo or "").strip()
        if len(motivo) < 5:
            raise HTTPException(400, "Escribe el motivo de la anulación")
        c.anulada_en = datetime.now(timezone.utc)
        c.anulada_por_id = user.get("id")
        c.motivo_anulacion = motivo[:400]
        _registrar(db, accion="carga_externa_anulada", usuario=user, entidad="carga_externa",
                   entidad_id=c.id, meta={"motivo": c.motivo_anulacion})
        db.commit()
        return {"ok": True, "carga": carga_dict(c)}

    @router.get("/api/cargas-externas/plantilla.xlsx")
    def plantilla(user: dict = Depends(require_combustible)):
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = "Cargas"
        ws.append(COLUMNAS)
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.append([date.today().strftime("%d/%m/%Y"), "T205", "motor", "Juan Pérez", 350, 9500,
                   "TRF-0001", "Ejemplo: borra esta fila"])
        for col, ancho in zip("ABCDEFGH", (12, 12, 18, 26, 10, 12, 16, 30)):
            ws.column_dimensions[col].width = ancho
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition":
                                 'attachment; filename="plantilla_cargas_externas.xlsx"'})

    @router.post("/api/cargas-externas/importar")
    def importar(file: UploadFile = File(...), dry: str = Form("true"),
                 user: dict = Depends(require_combustible),
                 db: Session = Depends(get_db)) -> dict:
        """Importa la plantilla. Con `dry` sólo enseña qué entraría y qué no, sin guardar.
        Se guarda TODO o NADA: si una fila tiene error, no entra ninguna, para que el Excel
        no quede medio cargado y sin saber cuál parte."""
        from openpyxl import load_workbook
        crudo = file.file.read(_LIMITE_ARCHIVO + 1)
        if len(crudo) > _LIMITE_ARCHIVO:
            raise HTTPException(400, "El archivo supera 10 MB")
        try:
            wb = load_workbook(io.BytesIO(crudo), read_only=True, data_only=True)
        except Exception:
            raise HTTPException(400, "No se pudo leer el archivo: usa la plantilla en .xlsx")
        ws = wb.worksheets[0]
        filas = ws.iter_rows(values_only=True)
        enc = [_norm(x) for x in (next(filas, None) or [])]
        claves = {"FECHA": "fecha", "ECONOMICO": "eco", "USO (MOTOR/TERMO)": "uso", "USO": "uso",
                  "OPERADOR": "operador", "LITROS": "litros", "IMPORTE": "importe",
                  "REFERENCIA": "referencia", "NOTA": "nota"}
        idx = {claves[h]: i for i, h in enumerate(enc) if h in claves}
        faltan = [k for k in ("fecha", "eco", "litros") if k not in idx]
        if faltan:
            raise HTTPException(400, "El archivo no tiene las columnas de la plantilla "
                                     f"(faltan: {', '.join(faltan)}). Descarga la plantilla.")
        buenas, malas = [], []
        for n, fila in enumerate(filas, start=2):
            if n - 1 > _MAX_FILAS:
                raise HTTPException(400, f"Más de {_MAX_FILAS} filas: divide el archivo")
            if not fila or all(v in (None, "") for v in fila):
                continue
            d = {k: (fila[i] if i < len(fila) else None) for k, i in idx.items()}
            if "borra esta fila" in str(d.get("nota") or "").lower():
                continue
            datos, errores = _validar(db, d)
            if errores:
                malas.append({"fila": n, "errores": errores})
            else:
                buenas.append({"fila": n, **datos})
        ensayo = str(dry).lower() in ("true", "1", "on")
        resumen = {"filas_validas": len(buenas), "filas_con_error": len(malas), "errores": malas[:200],
                   "litros": round(sum(b["litros"] for b in buenas), 2)}
        if ensayo or malas or not buenas:
            return {"ok": not malas, "guardado": False, **resumen}
        lote = uuid.uuid4().hex[:12]
        nombre = (file.filename or "archivo.xlsx")[:120]
        for b in buenas:
            b.pop("fila")
            db.add(CargaExterna(**b, origen=nombre, lote=lote, capturada_por_id=user.get("id")))
        _registrar(db, accion="cargas_externas_importadas", usuario=user, entidad="carga_externa",
                   meta={"archivo": nombre, "lote": lote, "filas": len(buenas),
                         "litros": resumen["litros"]})
        db.commit()
        return {"ok": True, "guardado": True, "lote": lote, **resumen}

    app.include_router(router)
