"""Lectura de un CFDI (factura electrónica del SAT) para la conciliación de combustible.

Extrae lo mínimo para conciliar: folio fiscal (UUID), emisor, totales, y —lo importante—
los LITROS amparados (suma de los conceptos de combustible). Es tolerante a la versión del
CFDI (3.3 y 4.0) porque busca por el nombre local del nodo, ignorando el namespace, que es
lo único que cambia entre versiones. Usa la librería estándar (xml.etree): no agrega
dependencias ni compila nada nativo.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET


def _local(tag: str) -> str:
    """Nombre del nodo sin el namespace ('{http://...}Comprobante' -> 'Comprobante')."""
    return tag.rsplit("}", 1)[-1]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# Unidades que cuentan como litros.
_UNIDAD_LITRO = {"LTR", "LT", "L", "LITRO", "LITROS"}
# Conceptos medidos en litros que NO son combustible del tanque: no deben sumar litros.
_NO_COMBUSTIBLE = ("aceite", "lubricante", "urea", "adblue", "ad blue", "def ",
                   "refrigerante", "anticongelante", "aditivo", "grasa")


def _es_combustible(clave: str, unidad: str, desc: str) -> bool:
    clave = (clave or "").strip()
    unidad = (unidad or "").strip().upper()
    d = (desc or "").lower()
    # Excluir aditivos/lubricantes aunque vengan en litros: no van al tanque de diésel.
    if any(k in d for k in _NO_COMBUSTIBLE):
        return False
    if clave.startswith("1510"):   # familia SAT de combustibles
        return True
    if any(k in d for k in ("diesel", "diésel", "gasolina", "combustible", "magna", "premium")):
        return True
    # Último recurso: medido en litros y sin indicios de ser aditivo.
    return unidad in _UNIDAD_LITRO


class CFDIInvalido(ValueError):
    """El archivo no es un CFDI legible."""


# Un CFDI timbrado pesa unos KB; incluso con cientos de conceptos queda muy por debajo.
TAMANO_MAX = 2 * 1024 * 1024


def parse_cfdi(xml_bytes: bytes) -> dict:
    """Devuelve los datos clave del CFDI. Lanza CFDIInvalido si el XML no es un comprobante."""
    if len(xml_bytes) > TAMANO_MAX:
        raise CFDIInvalido("El archivo es demasiado grande para ser un CFDI (máximo 2 MB).")
    # Un CFDI nunca declara DTD ni entidades. Rechazarlos de entrada cierra la expansión de
    # entidades («billion laughs») sin depender de la versión de expat ni de otra librería.
    cabeza = xml_bytes[:4096].upper()
    if b"<!DOCTYPE" in cabeza or b"<!ENTITY" in xml_bytes.upper():
        raise CFDIInvalido("El XML trae declaraciones DOCTYPE/ENTITY, que un CFDI no lleva.")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise CFDIInvalido(f"El archivo no es un XML válido: {e}") from e
    if _local(root.tag) != "Comprobante":
        raise CFDIInvalido("El XML no es un CFDI (no se encontró el nodo Comprobante).")

    a = root.attrib
    # Sólo una factura de INGRESO ampara litros comprados. Una de Egreso (nota de crédito) o
    # de Pago sumaba sus conceptos como si fueran otra compra de diésel.
    tipo = (a.get("TipoDeComprobante") or "").strip().upper()
    if tipo and tipo != "I":
        nombres = {"E": "Egreso (nota de crédito)", "P": "Pago", "T": "Traslado", "N": "Nómina"}
        raise CFDIInvalido(f"El CFDI es de tipo {nombres.get(tipo, tipo)}; sólo se aceptan "
                           "facturas de Ingreso para conciliar litros.")
    data = {
        "uuid": None,
        "rfc_emisor": None,
        "nombre_emisor": None,
        "total": _num(a.get("Total")),
        "subtotal": _num(a.get("SubTotal")),
        "moneda": a.get("Moneda"),
        "fecha": a.get("Fecha"),
        "litros": 0.0,
        "conceptos": [],
    }

    for el in root.iter():
        ln = _local(el.tag)
        at = el.attrib
        if ln == "Emisor":
            data["rfc_emisor"] = at.get("Rfc") or at.get("rfc")
            data["nombre_emisor"] = at.get("Nombre")
        elif ln == "TimbreFiscalDigital":
            data["uuid"] = at.get("UUID") or at.get("uuid")
        elif ln == "Concepto":
            cant = _num(at.get("Cantidad"))
            clave = at.get("ClaveProdServ") or ""
            unidad = at.get("ClaveUnidad") or at.get("Unidad") or ""
            desc = at.get("Descripcion") or ""
            data["conceptos"].append({
                "cantidad": cant, "clave": clave, "unidad": unidad, "descripcion": desc,
                "importe": _num(at.get("Importe")),
            })
            if cant and _es_combustible(clave, unidad, desc):
                data["litros"] += cant

    data["litros"] = round(data["litros"], 3)
    return data
