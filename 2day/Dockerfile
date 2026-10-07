# La aplicación, en su propia imagen.
#
# POR QUÉ 3.13 Y NO LO QUE TRAIGA EL SISTEMA. El entorno de desarrollo es Python 3.13.14 y
# Ubuntu 24.04 trae 3.12. No vi nada en el código que EXIJA 3.13, así que probablemente
# correría igual — pero «probablemente» no es como se despliega. Escribiéndolo aquí, la
# versión deja de depender de qué traiga el servidor.
FROM python:3.13-slim

# LA ESTRUCTURA DE CARPETAS IMPORTA. `config.py` calcula `media_dir` como
# `BASE_DIR/../data/media`, donde BASE_DIR es la carpeta `backend`. Si se aplana el árbol, las
# fotos acaban en otro sitio y el servidor las guarda donde nadie las busca. Se reproduce el
# mismo reparto que en desarrollo:
#     /app/backend/   el código
#     /app/data/      las fotos (volumen)
WORKDIR /app/backend

# `psycopg[binary]` trae su propia librería compilada, así que no hace falta build-essential ni
# libpq-dev: la imagen queda en la mitad de tamaño.
# `curl` es para el healthcheck; `tzdata` para que las fechas salgan en la zona de la flota.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

# Las dependencias ANTES que el código: así un cambio en el HTML no obliga a reinstalar todo.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/frontend ./frontend
# `POST /api/proveedor/importar` hace `from scripts.import_proveedor import …`: sin esta
# carpeta el endpoint revienta con ModuleNotFoundError. También trae las migraciones
# (migrate_e2/e3) que crean los índices únicos parciales que `create_all` no sabe crear.
COPY backend/scripts ./scripts

# No corre como root: si alguien encuentra un agujero en la aplicación, se encuentra con una
# cuenta sin permisos en vez de con el dueño del contenedor.
RUN useradd --system --uid 10001 dosdias \
 && mkdir -p /app/data/media \
 && chown -R dosdias:dosdias /app
USER dosdias

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# `--proxy-headers` NO ES OPCIONAL AQUÍ, y es la línea más importante de este fichero.
# `app/limites.py` sólo se fía de `X-Forwarded-For` cuando quien se conecta es loopback. Con
# Caddy en otro contenedor, la app vería la IP del puente de Docker: no es loopback, así que
# ignoraría la cabecera y TODOS los usuarios compartirían origen. El freno del login va por
# origen — ocho fallos de cualquiera y quedan bloqueados todos.
# Con `--proxy-headers`, uvicorn reescribe la IP del cliente antes de que el código la lea, y
# cada visitante vuelve a tener la suya.
# `--forwarded-allow-ips=*` es seguro porque este puerto NO está publicado: sólo Caddy, dentro
# de la red interna, puede hablar con él.
#
# UN SOLO WORKER, a propósito. Varias piezas guardan estado en la memoria del proceso: el
# freno del login y el aforo de hashing (limites.py), el tope diario de rutas de Google
# (geo.py) y el cerrojo de la importación de proveedor (main.py). Con 2 procesos cada uno
# lleva su propia cuenta —el atacante tiene el doble de intentos y el tope de Maps vale el
# doble— y el arranque (create_all + admin inicial) corre dos veces a la vez. Lo pesado ya
# va a hilos con run_in_threadpool, así que un proceso basta para esta flota.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--workers", "1", \
     "--no-server-header"]
