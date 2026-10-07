"""Lectura de los PDF de escaneo del motor (la computadora del propio camión).

La flota tiene DOS marcas de motor, pero TRES reportes distintos:
  - CUMMINS  -> PowerSpec "Engine Trip Detail Report (One Page)"  (inglés, 1 página)
  - CUMMINS  -> PowerSpec "Engine Trip Summary Report"            (inglés, 2 páginas)
  - DDEC     -> Detroit Diesel "Reportes de DDEC - Actividad del Viaje" (español, 2 páginas)

El «Summary» no trae los acumulados de por vida ni los litros: los litros se derivan de la
distancia entre el rendimiento (dos números impresos) y `odometro_total` queda en None. Eso
deja a esos archivos sin la segunda puerta de idempotencia; está contemplado aguas abajo.

Ambos traen capa de texto (no hace falta OCR) y describen un PERÍODO CERRADO de la unidad:
lo recorrido y consumido desde la extracción anterior. Verificado contra los acumulados:
  Δ(Total Engine Distance) == Distance del período   y   Δ(Total Fuel Used) == Fuel del período.

Este módulo los normaliza a una MISMA estructura para poder auditarlos igual, sin importar
la marca del motor.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("combustible.escaneo")

M = re.MULTILINE


def _num(texto: str, patron: str) -> float | None:
    m = re.search(patron, texto, M)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _txt(texto: str, patron: str) -> str | None:
    m = re.search(patron, texto, M)
    return m.group(1).strip() if m else None


def _fecha(cadena: str | None, formatos: tuple[str, ...]) -> date | None:
    if not cadena:
        return None
    for f in formatos:
        try:
            return datetime.strptime(cadena.strip(), f).date()
        except ValueError:
            continue
    return None


# ── Formato CUMMINS (PowerSpec, inglés) ──────────────────────────────────────
def _parsear_cummins(t: str) -> dict:
    def fila(etiqueta: str) -> dict:
        """Fila del detalle: %tiempo, tiempo, distancia, litros ('N/A' -> None)."""
        m = re.search(rf"^{etiqueta}\s+([\d.]+)\s+([\d:]+)\s+(N/A|[\d.]+)\s+(N/A|[\d.]+)", t, M)
        if not m:
            return {}
        conv = lambda s: None if s == "N/A" else float(s)
        return {"pct": float(m.group(1)), "tiempo": m.group(2),
                "km": conv(m.group(3)), "lts": conv(m.group(4))}

    ralenti = fila("Idle")
    crucero = fila("Cruise")
    top = fila("Top Gear")
    return {
        "formato": "CUMMINS",
        "unidad": _txt(t, r"Unit Number\s+(\S+)"),
        "motor": _txt(t, r"Engine Type\s+(.+?)\s{2,}"),
        "serie_motor": _txt(t, r"Engine Serial Number\s+(\S+)"),
        "periodo_fin": _fecha(_txt(t, r"Extraction Date\s+([\d\-]+)"), ("%m-%d-%Y",)),
        "periodo_inicio": None,          # Cummins no lo trae: se infiere del escaneo anterior
        # Acumulados de por vida del motor
        "odometro_total": _num(t, r"Total Engine Distance\s+([\d.]+)"),
        "lts_total": _num(t, r"Total Fuel Used\s+([\d.]+)"),
        "lts_ralenti_total": _num(t, r"Total Idle Fuel Used\s+([\d.]+)"),
        # Del PERÍODO
        "km": _num(t, r"^Distance\s+([\d.]+)"),
        "litros": _num(t, r"^Fuel\s+([\d.]+)\s*L"),
        "rendimiento": _num(t, r"^Fuel Economy\s+([\d.]+)"),
        "tiempo": _txt(t, r"^Time\s+([\d:]+)"),
        "lts_ralenti": ralenti.get("lts"),
        "pct_ralenti": ralenti.get("pct"),
        "tiempo_ralenti": ralenti.get("tiempo"),
        "vel_max": _num(t, r"Maximum Vehicle Speed\s+([\d.]+)"),
        "vel_prom": _num(t, r"Average Vehicle Speed\s+([\d.]+)"),
        "rpm_prom": _num(t, r"Average Engine Speed\s+([\d.]+)"),
        "rpm_max": _num(t, r"Maximum Engine Speed\s+([\d.]+)"),
        "carga_prom": _num(t, r"Average Engine Load\s+([\d.]+)"),
        "km_crucero": crucero.get("km"),
        "pct_crucero": crucero.get("pct"),
        "km_top_gear": top.get("km"),
        "pct_top_gear": top.get("pct"),
        "frenadas": _num(t, r"Service Brake Applications\s+([\d.]+)"),
        "paradas_panico": _num(t, r"Total Number of Sudden Decel\s+([\d.]+)"),
        "fuera_marcha": _num(t, r"Number of Coasts Out of Gear\s+([\d.]+)"),
    }



# ── Formato CUMMINS «Summary» (PowerSpec, 2 páginas) ─────────────────────────
def _parsear_cummins_resumen(t: str) -> dict:
    """El «Engine Trip Summary Report»: mismo motor, otro reporte.

    Trae el período y cómo se condujo, pero NO los acumulados de por vida ni los litros.
    Los litros salen de dividir la distancia entre el rendimiento —dos números impresos—,
    y `odometro_total` se queda en None, que es lo honesto: no está en el papel.
    """
    km = _num(t, r"Trip Distance\s+([\d.]+)")
    rend = _num(t, r"Overall Fuel Economy\s+([\d.]+)")
    # Los litros NO vienen impresos: son la distancia entre el rendimiento. Aritmética de
    # dos números del propio reporte, no una estimación —pero derivado, y por eso se dice.
    litros = (km / rend) if (km and rend) else None
    return {
        "formato": "CUMMINS",
        "unidad": _txt(t, r"Unit Number\s+(\S+)"),
        "motor": _txt(t, r"Engine Type\s+(.+?)\s{2,}"),
        "serie_motor": _txt(t, r"Engine Serial Number\s+(\S+)"),
        # Igual que en el Detail: el inicio no viene, lo infiere el importador como el fin
        # del escaneo anterior de esa misma unidad.
        "periodo_inicio": None,
        "periodo_fin": _fecha(_txt(t, r"Extraction Date\s+(\d{2}-\d{2}-\d{4})"),
                              ("%m-%d-%Y",)),
        # Sin acumulados de por vida: este reporte no los imprime.
        "odometro_total": None,
        "lts_total": None,
        "lts_ralenti_total": None,
        "km": km,
        "litros": litros,
        "rendimiento": rend,
        "tiempo": _txt(t, r"Trip Time\s+([\d:]+)"),
        # `lts_ralenti` se queda vacío a propósito (ver el encabezado del módulo): sólo se
        # llegaría a él encadenando tres derivaciones, y acabaría en la misma columna donde
        # los otros formatos guardan una lectura directa.
        "lts_ralenti": None,
        "pct_ralenti": _num(t, r"% Idle Time\s+([\d.]+)"),
        "vel_max": _num(t, r"Maximum Vehicle Speed\s+([\d.]+)"),
        "vel_prom": _num(t, r"Average Vehicle Speed\s+([\d.]+)"),
        "rpm_max": _num(t, r"Maximum Engine Speed\s+([\d.]+)"),
        "rpm_prom": _num(t, r"Average Engine Speed\s+([\d.]+)"),
        "carga_prom": _num(t, r"Average Engine Load\s+([\d.]+)"),
        "pct_top_gear": _num(t, r"% Top Gear Distance\s+([\d.]+)"),
        "pct_crucero": _num(t, r"% Cruise Control Distance\s+([\d.]+)"),
        # `frenadas` se queda vacío: este reporte da «Service Brake Actuations / 1Kkm», que
        # es una TASA por cada mil kilómetros, no una cuenta. Meterla en la columna de la
        # cuenta haría que 639 frenadas por 1,000 km pareciera 639 frenadas.
        "frenadas": None,
        "paradas_panico": _num(t, r"Sudden Deceleration Counts\s+([\d.]+)"),
        "fuera_marcha": _num(t, r"Number of Coasts Out of Gear\s+([\d.]+)"),
    }

# ── Formato DDEC (Detroit Diesel, español) ───────────────────────────────────
def _parsear_ddec(t: str) -> dict:
    # "Viaje: 05/28/26 18:04:27 Para 06/07/26 (EST)" -> inicio y fin del período
    ini = fin = None
    m = re.search(r"Viaje:\s*([\d/]+)[\d:\s]*Para\s+([\d/]+)", t)
    if m:
        ini = _fecha(m.group(1), ("%m/%d/%y", "%m/%d/%Y"))
        fin = _fecha(m.group(2), ("%m/%d/%y", "%m/%d/%Y"))

    def bloque(titulo: str, campo: str) -> float | None:
        """Un campo (Distancia, Porcentaje…) dentro de un bloque indentado del DDEC.

        El título NO está solo en su línea: comparte renglón con la columna derecha del
        reporte. Se localiza la línea que EMPIEZA exactamente con ese título (no una
        variante más larga como 'Travesía de Velocidad Superior') y se busca el campo en
        las líneas indentadas que le siguen.
        """
        lineas = t.splitlines()
        for i, linea in enumerate(lineas):
            izq = linea.rstrip()
            if not izq.startswith(titulo):
                continue
            resto = izq[len(titulo):]
            if resto and not resto.startswith("  "):
                continue          # es un título más largo (p.ej. 'Travesía de …')
            for sig in lineas[i + 1:i + 7]:
                if sig[:1] not in (" ", "\t"):
                    break         # se acabó el bloque indentado
                m = re.search(rf"{campo}\s+([\d.]+)", sig)
                if m:
                    return float(m.group(1))
        return None

    return {
        "formato": "DDEC",
        "unidad": _txt(t, r"Identificaci[óo]n Veh[íi]culo:\s*(\S+)"),
        "motor": "Detroit Diesel",
        "serie_motor": _txt(t, r"N/S del Motor:\s*(\S+)"),
        "periodo_inicio": ini,
        "periodo_fin": fin,
        "odometro_total": _num(t, r"Od[óo]metro:\s*([\d.]+)"),
        "lts_total": None,               # DDEC no reporta el acumulado de combustible
        "lts_ralenti_total": None,
        "km": _num(t, r"Distancia del Viaje\s*([\d.]+)"),
        "litros": _num(t, r"Combustible del Viaje\s*([\d.]+)"),
        "rendimiento": _num(t, r"Econom[íi]a de Combustible\s*([\d.]+)"),
        "tiempo": _txt(t, r"Tiempo del Viaje\s*([\d:]+)"),
        "lts_ralenti": _num(t, r"Combustible de Marcha Lenta\s*([\d.]+)"),
        "pct_ralenti": _num(t, r"Porcentaje de Marcha Lenta\s*([\d.]+)"),
        "tiempo_ralenti": _txt(t, r"Tiempo de Marcha Lenta\s*([\d:]+)"),
        "vel_max": _num(t, r"Velocidad M[áa]xima\s*([\d.]+)"),
        "vel_prom": _num(t, r"Velocidad del Veh[íi]culo Promedio\s*([\d.]+)"),
        "rpm_prom": None,                # DDEC no da RPM promedio
        "rpm_max": _num(t, r"RPM M[áa]xima\s*([\d.]+)"),
        "carga_prom": _num(t, r"Carga de Manejo Promedio\s*([\d.]+)"),
        "km_crucero": bloque("Travesía", "Distancia"),
        "pct_crucero": bloque("Travesía", "Porcentaje"),
        "km_top_gear": bloque("Velocidad Superior", "Distancia"),
        "pct_top_gear": bloque("Velocidad Superior", "Porcentaje"),
        "frenadas": _num(t, r"Cuenta de Freno\s+([\d.]+)"),
        "paradas_panico": _num(t, r"Cuenta de Freno Duro\s+([\d.]+)"),
        # DDEC no cuenta "coasts out of gear"; su equivalente es el % de marcha por inercia
        "fuera_marcha": _num(t, r"Porcentaje de Marchar por Inercia\s*([\d.]+)"),
    }


def leer_pdf(path: str | Path) -> dict:
    """Parsea un PDF de escaneo (cualquiera de los dos formatos) y lo normaliza.

    Devuelve un dict con las claves comunes + 'archivo'. Lanza ValueError si el formato
    no se reconoce o si faltan los datos mínimos (unidad, km, litros).
    """
    from pypdf import PdfReader

    path = Path(path)
    reader = PdfReader(str(path))
    # Modo layout: conserva las columnas, indispensable porque los números van pegados.
    texto = "\n".join((p.extract_text(extraction_mode="layout") or "") for p in reader.pages)

    if "Engine Trip Detail Report" in texto:
        d = _parsear_cummins(texto)
    elif "Engine Trip Summary Report" in texto:
        d = _parsear_cummins_resumen(texto)
    elif "DDEC" in texto or "Distancia del Viaje" in texto:
        d = _parsear_ddec(texto)
    else:
        raise ValueError(f"Formato de escaneo no reconocido: {path.name}")

    faltan = [k for k in ("unidad", "km", "litros", "periodo_fin") if not d.get(k)]
    if faltan:
        raise ValueError(f"{path.name}: no se pudieron leer {', '.join(faltan)}")

    d["unidad"] = "".join(str(d["unidad"]).upper().split())
    d["archivo"] = path.name
    return d


def llave_odometro(unidad_id: int, odometro_total, km) -> tuple[int, float] | None:
    """La identidad del PERÍODO: qué unidad y en qué kilómetro de por vida cierra.

    `odometro_total` es el contador de por vida del motor, estrictamente creciente dentro de
    una unidad: un escaneo con km > 0 no puede dejarlo donde estaba. Dos lecturas de la misma
    unidad que cierran en el mismo kilómetro son la misma lectura.

    Devuelve None cuando el escaneo NO puede identificarse así, y entonces no se compara con
    nadie —es preferible dejar entrar un repetido raro que rechazar una lectura buena—:
      · sin `odometro_total` no hay llave. Le pasa a TODO el formato «Summary», que no
        imprime los acumulados de por vida;
      · con km <= 0 la unidad no se movió, así que dos lecturas DISTINTAS sí pueden cerrar en
        el mismo kilómetro sin ser la misma. Es justo el caso que rompería la regla.

    El redondeo a 2 decimales es el mismo de `rendimiento._serie()`: dos lecturas del mismo
    período no pueden diferir en centímetros, y así las dos defensas entienden lo mismo por
    «igual».
    """
    if odometro_total is None or km is None or float(km) <= 0:
        return None
    return (unidad_id, round(float(odometro_total), 2))
