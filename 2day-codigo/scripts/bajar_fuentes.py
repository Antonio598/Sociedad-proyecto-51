# -*- coding: utf-8 -*-
"""Trae IBM Plex al proyecto para que la app deje de depender de Google.

La app se usa en carretera. Cada arranque en frio pedia la hoja a fonts.googleapis.com y los
archivos a fonts.gstatic.com: dos dominios ajenos, dos resoluciones de DNS y dos conexiones
antes de poder leer nada con la tipografia de la casa. Y no hay respaldo local ni service
worker -se desinstala a proposito-, asi que no hay cache que valga.

IBM Plex es SIL OFL 1.1, o sea que se puede redistribuir con el proyecto. Se piden los woff2
haciendose pasar por un Chrome moderno (con otro agente Google devuelve formatos viejos y
mucho mas pesados), y se guardan solo los subconjuntos que esta app necesita: latin y
latin-ext, que es donde viven los acentos y la enye.
"""
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DESTINO = Path(r"C:\Users\janes\OneDrive\Escritorio\Combustible App\backend\frontend\static\fuentes")
DESTINO.mkdir(parents=True, exist_ok=True)

CSS = ("https://fonts.googleapis.com/css2"
       "?family=IBM+Plex+Sans:wght@400;500;600"
       "&family=IBM+Plex+Mono:wght@400;500;600&display=swap")
# Sin este agente, Google sirve ttf/woff en vez de woff2: el triple de peso.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
QUIERO = ("latin", "latin-ext")


def traer(url, binario=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = r.read()
    return d if binario else d.decode("utf-8")


print("  pidiendo la hoja a Google…")
hoja = traer(CSS)

bloques = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", hoja, re.S)
print(f"  {len(bloques)} declaraciones, de {len({b[0] for b in bloques})} subconjuntos")

reglas, total = [], 0
for subconj, cuerpo in bloques:
    if subconj not in QUIERO:
        continue
    fam = re.search(r"font-family:\s*'([^']+)'", cuerpo).group(1)
    peso = re.search(r"font-weight:\s*(\d+)", cuerpo).group(1)
    estilo = re.search(r"font-style:\s*(\w+)", cuerpo).group(1)
    rango = re.search(r"unicode-range:\s*([^;]+)", cuerpo).group(1).strip()
    url = re.search(r"url\((https://[^)]+\.woff2)\)", cuerpo).group(1)

    nombre = f"{fam.replace(' ', '')}-{peso}-{subconj}.woff2"
    datos = traer(url, binario=True)
    (DESTINO / nombre).write_bytes(datos)
    total += len(datos)
    print(f"      {nombre:34} {len(datos)//1024:>3} KB")

    reglas.append(
        "@font-face{\n"
        f"  font-family:'{fam}';\n"
        f"  font-style:{estilo};\n"
        f"  font-weight:{peso};\n"
        "  font-display:swap;\n"
        f"  src:url('/static/fuentes/{nombre}') format('woff2');\n"
        f"  unicode-range:{rango};\n"
        "}"
    )

cabecera = (
    "/* IBM Plex, servido por esta misma app.\n"
    " *\n"
    " * Antes venia de fonts.googleapis.com (la hoja) y fonts.gstatic.com (los archivos): dos\n"
    " * dominios ajenos que resolver y conectar antes de leer nada con la tipografia de la casa,\n"
    " * en una app que se usa en carretera y sin service worker que cachee. Ahora viaja con el\n"
    " * proyecto: sin peticiones a terceros y sin nada que se caiga cuando la antena flojea.\n"
    " *\n"
    " * IBM Plex es SIL OFL 1.1, que permite redistribuirla. Solo los subconjuntos latin y\n"
    " * latin-ext, que es donde estan los acentos y la enye.\n"
    " *\n"
    " * Generado por scripts/bajar_fuentes.py. Para reponerlo, volver a correrlo. */\n\n"
)
hoja_local = DESTINO.parent / "fuentes.css"
hoja_local.write_bytes((cabecera + "\n".join(reglas) + "\n").encode("utf-8"))

print(f"\n  {len(reglas)} declaraciones, {total//1024} KB en total")
print(f"  hoja: {hoja_local}")
