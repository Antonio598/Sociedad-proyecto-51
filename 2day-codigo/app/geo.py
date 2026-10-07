"""Rutas y direcciones. Una sola puerta para todo lo geográfico del sistema.

POR QUÉ VIVE EN EL SERVIDOR Y NO EN EL NAVEGADOR
Dibujar el mapa es cosa del navegador, pero CALCULAR la ruta y resolver direcciones
son las llamadas que Google cobra. Si se hicieran desde el navegador, la clave que
las autoriza quedaría a la vista de cualquiera que abriera el inspector y podría
usarse desde fuera para gastar con la tarjeta de la flota. Haciéndolas aquí:
  · la clave cara nunca sale del servidor,
  · se puede TOPAR el consumo diario (google_maps_max_rutas_dia),
  · el día que cambie el proveedor se toca este archivo y nada más.

PROVEEDORES
Si hay clave de Google usa Google; si no, cae a OSRM/Nominatim (lo que ya se venía
usando). Así la app nunca se queda sin mapa por no tener clave todavía.

Google: se usa **Routes API** (no la Directions clásica). Directions quedó marcada
como heredada y los proyectos nuevos de Google Cloud ya no siempre pueden
habilitarla, así que para una cuenta recién creada Routes es lo que funciona.
"""

import logging

import httpx

from .config import fecha_flota, settings

log = logging.getLogger("combustible.geo")

_TIMEOUT = 12.0

# Clientes REUTILIZADOS. Antes cada llamada abría y cerraba su propio AsyncClient, así que
# pagaba un handshake TLS entero: medido, 1,3-2,0 s por ruta. Manteniendo la conexión viva
# ese costo se paga una vez. Son dos porque Nominatim exige User-Agent propio y Google no.
# Se guarda (event_loop, cliente): un AsyncClient queda ATADO al bucle donde nació, y
# reutilizarlo desde otro revienta con "Event loop is closed" en la SEGUNDA llamada — la
# primera parece funcionar, así que el fallo aparece tarde y disfrazado de caída de Google.
_CLI: dict = {}


def _cliente(con_ua: bool = False):
    import asyncio
    bucle = asyncio.get_running_loop()
    clave = "osm" if con_ua else "google"
    par = _CLI.get(clave)
    if par is not None:
        anterior, c = par
        if anterior is bucle and not c.is_closed:
            return c
        # El bucle cambió o murió. No se cierra el cliente viejo: cerrarlo exige su propio
        # bucle, que ya no existe, y lanzaría exactamente el error que se quiere evitar.
    c = httpx.AsyncClient(
        timeout=_TIMEOUT,
        headers=_UA if con_ua else None,
        limits=httpx.Limits(max_keepalive_connections=8, max_connections=16,
                            keepalive_expiry=120.0))
    _CLI[clave] = (bucle, c)
    return c


async def cerrar_clientes() -> None:
    """Cierra las conexiones al apagar la aplicación."""
    import asyncio
    try:
        actual = asyncio.get_running_loop()
    except RuntimeError:
        actual = None
    for bucle, c in list(_CLI.values()):
        if bucle is actual and not c.is_closed:
            try:
                await c.aclose()
            except Exception:
                pass
    _CLI.clear()
_UA = {"User-Agent": "2Day-Flota/1.0"}

# Contador del tope diario. Vive en memoria: se reinicia al reiniciar el backend,
# lo cual es aceptable para una red de seguridad (el tope duro de verdad se pone
# en las cuotas de Google Cloud).
_contador = {"dia": None, "n": 0}


def hay_google() -> bool:
    return bool(settings.google_maps_api_key or settings.google_maps_browser_key)


def _clave_servidor() -> str:
    """La del servidor; si solo se configuró una clave, se usa esa."""
    return settings.google_maps_api_key or settings.google_maps_browser_key


def clave_navegador() -> str:
    """La que se le entrega al navegador para DIBUJAR el mapa.

    Si no hay clave de navegador se cae a la del servidor para que el mapa funcione,
    pero eso la EXPONE en el HTML: cualquiera puede leerla. Una clave de servidor no
    lleva restricción por dominio, así que quien la copie puede gastar con la tarjeta
    de la flota. Por eso `clave_compartida()` lo declara y se avisa al arrancar.
    """
    return settings.google_maps_browser_key or settings.google_maps_api_key


def clave_compartida() -> bool:
    """True cuando la clave que va al navegador es la MISMA del servidor.

    No es un detalle de configuración: es un riesgo de gasto. Se expone para que el
    panel lo muestre en vez de que viva en un comentario que nadie lee.
    """
    return bool(settings.google_maps_api_key
                and not settings.google_maps_browser_key)


def _cupo_disponible() -> bool:
    tope = settings.google_maps_max_rutas_dia or 0
    if tope <= 0:
        return True
    hoy = fecha_flota()
    if _contador["dia"] != hoy:
        _contador["dia"] = hoy
        _contador["n"] = 0
    return _contador["n"] < tope


def _suma_ruta() -> None:
    _contador["n"] = _contador.get("n", 0) + 1


def consumo_hoy() -> dict:
    return {"dia": _contador["dia"].isoformat() if _contador["dia"] else None,
            "rutas": _contador.get("n", 0),
            "tope": settings.google_maps_max_rutas_dia}


# ── Polilínea codificada de Google → lista de coordenadas ────────────────────
def _decodificar(poli: str) -> list:
    """Convierte la polilínea codificada de Google en [[lng,lat], ...].

    Se devuelve en orden lng,lat (no lat,lng) para que el formato coincida con el
    que ya entrega OSRM y el frontend no tenga que distinguir de dónde vino.
    """
    puntos = []
    i = lat = lng = 0
    while i < len(poli):
        for eje in ("lat", "lng"):
            resultado = desplazamiento = 0
            while i < len(poli):
                b = ord(poli[i]) - 63
                i += 1
                resultado |= (b & 0x1F) << desplazamiento
                desplazamiento += 5
                if b < 0x20:
                    break
            delta = ~(resultado >> 1) if (resultado & 1) else (resultado >> 1)
            if eje == "lat":
                lat += delta
            else:
                lng += delta
        puntos.append([lng / 1e5, lat / 1e5])
    return puntos


# ── RUTA ─────────────────────────────────────────────────────────────────────
async def ruta(o_lat: float, o_lng: float, d_lat: float, d_lng: float) -> dict:
    """Ruta por carretera entre dos puntos.

    Devuelve {km, puntos:[[lng,lat],...], fuente}. `fuente` dice de dónde salió,
    para que la interfaz pueda ser honesta ("línea recta" no es lo mismo que
    "ruta por carretera").
    """
    if hay_google():
        if not _cupo_disponible():
            log.warning("Tope diario de rutas alcanzado (%s); se cae a OSRM",
                        settings.google_maps_max_rutas_dia)
        else:
            try:
                r = await _ruta_google(o_lat, o_lng, d_lat, d_lng)
                _suma_ruta()
                return r
            except Exception:
                log.exception("Google Routes falló; se intenta con OSRM")
    try:
        return await _ruta_osrm(o_lat, o_lng, d_lat, d_lng)
    except Exception:
        log.exception("OSRM falló; se devuelve línea recta")
        return {"km": _haversine(o_lat, o_lng, d_lat, d_lng),
                "puntos": [[o_lng, o_lat], [d_lng, d_lat]],
                "fuente": "recta"}


async def _ruta_google(o_lat, o_lng, d_lat, d_lng) -> dict:
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    cabeceras = {
        "X-Goog-Api-Key": _clave_servidor(),
        # El FieldMask es obligatorio en Routes API y además abarata la llamada:
        # se piden SOLO la distancia y el trazo, nada de instrucciones giro a giro.
        "X-Goog-FieldMask": "routes.distanceMeters,routes.polyline.encodedPolyline",
        "Content-Type": "application/json",
    }
    cuerpo = {
        "origin": {"location": {"latLng": {"latitude": o_lat, "longitude": o_lng}}},
        "destination": {"location": {"latLng": {"latitude": d_lat, "longitude": d_lng}}},
        "travelMode": "DRIVE",
    }
    c = _cliente(False)
    resp = await c.post(url, json=cuerpo, headers=cabeceras)
    resp.raise_for_status()
    datos = resp.json()
    rutas = datos.get("routes") or []
    if not rutas:
        raise ValueError("Google no devolvio ninguna ruta")
    metros = rutas[0].get("distanceMeters") or 0
    poli = ((rutas[0].get("polyline") or {}).get("encodedPolyline")) or ""
    return {"km": round(metros / 1000.0, 1),
            "puntos": _decodificar(poli) if poli else [[o_lng, o_lat], [d_lng, d_lat]],
            "fuente": "google"}


async def _ruta_osrm(o_lat, o_lng, d_lat, d_lng) -> dict:
    url = ("https://router.project-osrm.org/route/v1/driving/"
           f"{o_lng},{o_lat};{d_lng},{d_lat}?overview=full&geometries=geojson")
    c = _cliente(True)
    resp = await c.get(url)
    resp.raise_for_status()
    datos = resp.json()
    rutas = datos.get("routes") or []
    if not rutas:
        raise ValueError("OSRM no devolvio ninguna ruta")
    return {"km": round(rutas[0]["distance"] / 1000.0, 1),
            "puntos": rutas[0]["geometry"]["coordinates"],
            "fuente": "osrm"}


def _haversine(a_lat, a_lng, b_lat, b_lng) -> float:
    from math import asin, cos, radians, sin, sqrt
    r = 6371.0
    dlat, dlng = radians(b_lat - a_lat), radians(b_lng - a_lng)
    h = sin(dlat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(dlng / 2) ** 2
    return round(2 * r * asin(sqrt(h)), 1)


# ── DIRECCIONES ──────────────────────────────────────────────────────────────
async def direccion(lat: float, lng: float) -> dict:
    """Direccion legible de un punto (geocodificacion inversa)."""
    if hay_google():
        try:
            return await _dir_google(lat, lng)
        except Exception:
            log.exception("Google Geocoding (inverso) fallo; se intenta Nominatim")
    try:
        return await _dir_nominatim(lat, lng)
    except Exception:
        log.exception("Nominatim (inverso) fallo")
        return {"texto": f"{lat:.5f}, {lng:.5f}", "fuente": "coords"}


async def _dir_google(lat, lng) -> dict:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    c = _cliente(False)
    resp = await c.get(url, params={"latlng": f"{lat},{lng}",
                                    "key": _clave_servidor(), "language": "es"})
    resp.raise_for_status()
    d = resp.json()
    res = d.get("results") or []
    if not res:
        raise ValueError(f"Geocoding sin resultados ({d.get('status')})")
    return {"texto": res[0].get("formatted_address") or "", "fuente": "google"}


async def _dir_nominatim(lat, lng) -> dict:
    url = "https://nominatim.openstreetmap.org/reverse"
    c = _cliente(True)
    resp = await c.get(url, params={"format": "jsonv2", "lat": lat, "lon": lng})
    resp.raise_for_status()
    return {"texto": (resp.json() or {}).get("display_name") or "", "fuente": "osm"}


async def buscar(texto: str) -> dict:
    """Busca una direccion escrita y devuelve su punto."""
    texto = (texto or "").strip()
    if not texto:
        return {}
    if hay_google():
        try:
            return await _buscar_google(texto)
        except Exception:
            log.exception("Google Geocoding fallo; se intenta Nominatim")
    try:
        return await _buscar_nominatim(texto)
    except Exception:
        log.exception("Nominatim fallo")
        return {}


async def _buscar_google(texto) -> dict:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    c = _cliente(False)
    resp = await c.get(url, params={"address": texto, "key": _clave_servidor(),
                                    "language": "es", "region": "mx"})
    resp.raise_for_status()
    d = resp.json()
    res = d.get("results") or []
    if not res:
        return {}
    loc = res[0]["geometry"]["location"]
    return {"lat": loc["lat"], "lng": loc["lng"],
            "texto": res[0].get("formatted_address") or texto, "fuente": "google"}


async def sugerencias(texto: str, limite: int = 6) -> list:
    """Lugares que coinciden con lo tecleado, para el desplegable del buscador.

    Devuelve [{texto, principal, lat, lng, fuente}] — SIEMPRE con coordenadas, así que
    elegir una sugerencia no cuesta otra llamada.

    Se exige un mínimo de 3 caracteres porque cada pulsación es una llamada facturable y con
    una o dos letras las sugerencias no valen nada; el que llama debe además esperar a que
    la persona deje de teclear.
    """
    texto = (texto or "").strip()
    if len(texto) < 3:
        return []
    if hay_google():
        try:
            return await _sug_google(texto, limite)
        except Exception:
            log.exception("Google Autocomplete falló; se intenta Nominatim")
    try:
        return await _sug_nominatim(texto, limite)
    except Exception:
        log.exception("Nominatim (sugerencias) falló")
        return []


async def _sug_google(texto, limite) -> list:
    """Varios resultados del Geocoding, cada uno con su punto.

    Es el mismo servicio que ya usa `buscar()`, pidiendo la lista entera en vez de quedarse
    con el primero. `components=country:MX` acota a México: sin eso "Tenabo" compite con
    lugares de medio mundo y la primera sugerencia puede caer en otro continente.
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    c = _cliente(False)
    resp = await c.get(url, params={"address": texto, "key": _clave_servidor(),
                                    "language": "es", "region": "mx",
                                    "components": "country:MX"})
    resp.raise_for_status()
    d = resp.json()
    if d.get("status") not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(f"Geocoding devolvió {d.get('status')}: {d.get('error_message')}")
    salida = []
    for r in (d.get("results") or [])[:limite]:
        loc = (r.get("geometry") or {}).get("location") or {}
        if "lat" not in loc:
            continue
        # `components=country:MX` hace que un texto sin sentido caiga al PAÍS entero:
        # escribir "xyzzyqq" sugería "México" en el centro del mapa, que como destino de un
        # viaje no significa nada. Un resultado que es un país o un estado no es un lugar
        # al que se maneja.
        tipos = set(r.get("types") or [])
        if tipos & {"country", "administrative_area_level_1"}:
            continue
        txt = r.get("formatted_address") or texto
        salida.append({"texto": txt,
                       # La primera parte es lo que distingue una sugerencia de otra de un
                       # vistazo; el resto (estado, país) se repite en todas.
                       "principal": txt.split(",")[0],
                       "lat": loc["lat"], "lng": loc["lng"], "fuente": "google"})
    return salida


async def _sug_nominatim(texto, limite) -> list:
    url = "https://nominatim.openstreetmap.org/search"
    c = _cliente(True)
    resp = await c.get(url, params={"format": "jsonv2", "limit": limite,
                                    "q": texto, "countrycodes": "mx"})
    resp.raise_for_status()
    salida = []
    for a in (resp.json() or [])[:limite]:
        salida.append({"texto": a.get("display_name") or "",
                       "principal": (a.get("name") or a.get("display_name") or "").split(",")[0],
                       # Nominatim ya trae el punto: no hace falta una segunda llamada.
                       "lat": float(a["lat"]), "lng": float(a["lon"]),
                       "fuente": "osm"})
    return salida


async def _buscar_nominatim(texto) -> dict:
    url = "https://nominatim.openstreetmap.org/search"
    c = _cliente(True)
    resp = await c.get(url, params={"format": "jsonv2", "limit": 1, "q": texto})
    resp.raise_for_status()
    a = resp.json() or []
    if not a:
        return {}
    return {"lat": float(a[0]["lat"]), "lng": float(a[0]["lon"]),
            "texto": a[0].get("display_name") or texto, "fuente": "osm"}
