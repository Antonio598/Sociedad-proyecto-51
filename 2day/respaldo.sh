#!/bin/sh
# Respaldo de la base Y de las fotos. Las dos cosas, siempre.
#
# POR QUÉ LAS DOS. Las fotos de evidencia NO están en la base de datos: viven en un volumen.
# Un respaldo que sólo copie Postgres deja una base llena de registros que apuntan a
# evidencia que ya no existe — y eso no se nota hasta que alguien reclama una carga y la foto
# no aparece.
#
# Instalación (en el VPS):
#     chmod +x /opt/2day/respaldo.sh
#     crontab -e
#     # y añadir esta línea, que lo corre todos los días a las 3:15 de la madrugada:
#     15 3 * * * /opt/2day/respaldo.sh >> /var/log/respaldo-2day.log 2>&1
#
# Comprobar que funciona: córrelo a mano UNA vez y mira que aparezcan los dos ficheros.
#     /opt/2day/respaldo.sh && ls -lh /opt/respaldos

set -eu

DESTINO=/opt/respaldos
DIAS=14                      # cuántos días se conservan
FECHA=$(date +%Y%m%d-%H%M)

mkdir -p "$DESTINO"

# ── La base ───────────────────────────────────────────────────────────────────
# Formato `custom` (-Fc) y no SQL plano: se restaura con `pg_restore`, va comprimido y permite
# recuperar una sola tabla si hiciera falta.
docker exec pg pg_dump -U evolution -d combustible -Fc \
  > "$DESTINO/base-$FECHA.dump"

# ── Las fotos ─────────────────────────────────────────────────────────────────
# Se leen DESDE DENTRO del contenedor: el volumen es de Docker y su ruta real en el disco del
# anfitrión no es estable entre versiones.
docker run --rm -v 2day_media:/datos:ro -v "$DESTINO":/salida alpine \
  tar czf "/salida/fotos-$FECHA.tgz" -C /datos .

# ── Comprobar que no salió un fichero vacío ───────────────────────────────────
# Un respaldo de 0 bytes es peor que ninguno: da la falsa sensación de estar cubierto.
for f in "$DESTINO/base-$FECHA.dump" "$DESTINO/fotos-$FECHA.tgz"; do
  if [ ! -s "$f" ]; then
    echo "$(date '+%F %T')  ERROR: $f salió vacío" >&2
    exit 1
  fi
done

# ── Tirar los viejos ──────────────────────────────────────────────────────────
find "$DESTINO" -name 'base-*.dump' -mtime +$DIAS -delete
find "$DESTINO" -name 'fotos-*.tgz' -mtime +$DIAS -delete

echo "$(date '+%F %T')  OK  $(du -sh "$DESTINO" | cut -f1) en $DESTINO"

# ── LO QUE ESTO NO HACE ───────────────────────────────────────────────────────
# Guarda las copias EN EL MISMO SERVIDOR. Sirve para «borré algo sin querer», no para «se
# perdió el servidor». Para eso hay que sacarlas fuera: las instantáneas de Hostinger, o un
# `rsync` a otra máquina. Decídelo pronto — un respaldo que vive en el disco que se puede
# perder no es un respaldo.
