# Imagen de la aplicación para EasyPanel (se construye desde la raíz del repositorio).
#
# La base de datos es Supabase: aquí no hay Postgres ni proxy. EasyPanel pone el HTTPS
# delante (Traefik) y reenvía al puerto 8000.
FROM python:3.13-slim

# `config.py` calcula `media_dir` como `BASE_DIR/../data/media`, con BASE_DIR = la carpeta
# del código. Con el código en /app/backend las fotos quedan en /app/data/media, que es
# donde EasyPanel debe montar el VOLUMEN. Sin volumen, cada despliegue borra las fotos.
WORKDIR /app/backend

# `curl` para el healthcheck; `tzdata` para que las fechas salgan en la zona de la flota.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

# Las dependencias ANTES que el código: un cambio en el HTML no obliga a reinstalar todo.
COPY 2day-codigo/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Todo el código de una vez: app/, frontend/ y scripts/ cuando exista (lo necesita
# POST /api/proveedor/importar). Lo que no debe entrar lo filtra .dockerignore.
COPY 2day-codigo/ ./

# No corre como root.
RUN useradd --system --uid 10001 dosdias \
 && mkdir -p /app/data/media \
 && chown -R dosdias:dosdias /app
USER dosdias

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# `--proxy-headers`: sin esto la app ve la IP del proxy de EasyPanel y todos los usuarios
# comparten "origen" en el freno del login (ocho fallos de cualquiera bloquean a todos).
# `*` es seguro porque el puerto 8000 no se publica: sólo el proxy de EasyPanel llega a él.
#
# UN SOLO WORKER: el freno del login, el tope diario de Google Maps y el cerrojo de la
# importación viven en la memoria del proceso. Con dos procesos cada uno lleva su cuenta.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--workers", "1", \
     "--no-server-header"]
