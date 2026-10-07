# La aplicación detrás del proxy. Sustituye al `Caddyfile` cuando la etapa 2 esté lista.
#
#     cd /opt/2day && cp Caddyfile.app Caddyfile && docker compose restart caddy
#
# CAMBIA EL DOMINIO si no usas `app.diesel2day.tech`. Tiene que ser EXACTAMENTE el del registro A.
# Mientras no exista ese registro, puedes dejar el de ensayo (179.236.248.139.sslip.io), que
# ya funciona: la app se sirve igual, sólo cambia el nombre por el que se llega.

app.diesel2day.tech {
	encode gzip

	# Las fotos de evidencia rondan los 90 KB, pero el servidor acepta hasta 12 MB (una foto
	# sin comprimir de un teléfono moderno). Se deja holgado: si el proxy corta antes que la
	# app, el operador ve un error raro en vez del mensaje que la app sabe dar.
	request_body {
		# 32 MB y no 15: el Excel del proveedor (POST /api/proveedor/importar) admite hasta 30 MB,
		# y entre 15 y 30 el usuario veía un 413 de Caddy sin explicación.
		max_size 32MB
	}

	# `app` es el nombre del servicio en el docker-compose, y 8000 el puerto INTERNO. Ese
	# puerto no está publicado: sólo se llega por aquí.
	reverse_proxy app:8000

	header {
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
		# OJO: `X-Frame-Options DENY` estaba en la página de prueba y aquí NO se pone. La app
		# del operador abre la cámara y el lector de QR, y algunos navegadores en móvil tratan
		# esas superficies de forma especial. Si más adelante quieres blindarlo, mejor con
		# `Content-Security-Policy: frame-ancestors 'none'`, que es el sustituto moderno — pero
		# pruébalo en un teléfono de verdad antes de dejarlo puesto.
		# Strict-Transport-Security "max-age=31536000"
	}
}
