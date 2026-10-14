#!/bin/sh
# Arranque del contenedor.
#
# POR QUÉ EXISTE. EasyPanel monta el volumen de /app/data como una carpeta del servidor que
# pertenece a root. La app corre como `dosdias` (sin privilegios), así que al crear
# /app/data/media recibía «Permission denied», se caía, EasyPanel la reiniciaba y el
# despliegue nunca terminaba. Aquí se arregla el dueño como root y DESPUÉS se sueltan los
# privilegios: la app sigue sin correr como root.
set -e

mkdir -p /app/data/media
# Sólo lo que no es de la app, para no recorrer miles de fotos en cada arranque.
find /app/data ! -user dosdias -exec chown dosdias:dosdias {} + 2>/dev/null \
  || echo "AVISO: no se pudo ajustar el dueño de /app/data" >&2

exec setpriv --reuid=dosdias --regid=dosdias --init-groups "$@"
