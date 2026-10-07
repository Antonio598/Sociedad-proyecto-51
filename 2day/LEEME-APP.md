# Etapa 2 — la aplicación en el VPS

La etapa 1 ya demostró que el servidor, el certificado y el proxy funcionan. Ahora se pone la
app detrás. Ocho pasos.

---

## Paso 1 — Saca una copia de tu base y tus fotos

En **tu computadora**, con Docker corriendo:

```bash
docker exec evolution_postgres pg_dump -U evolution -d combustible -Fc > "C:/Users/janes/Desktop/base.dump"
```

> **No uses `>` en PowerShell para esto.** PowerShell le mete una marca al principio del
> archivo y el volcado sale corrupto. Si estás en PowerShell, usa esto en su lugar:
> ```bash
> docker exec evolution_postgres pg_dump -U evolution -d combustible -Fc -f /tmp/base.dump
> docker cp evolution_postgres:/tmp/base.dump "C:/Users/janes/Desktop/base.dump"
> ```

Y las fotos:

```bash
tar czf "C:/Users/janes/Desktop/fotos.tgz" -C "C:/Users/janes/OneDrive/Escritorio/Combustible App/data/media" .
```

Deberían salir unos 17 MB de base y 14 MB de fotos.

---

## Paso 2 — Sube todo al VPS

Desde **tu computadora**:

```bash
scp -r "C:/Users/janes/OneDrive/Escritorio/Combustible App/despliegue/." root@179.236.248.139:/opt/2day/
```

```bash
scp "C:/Users/janes/Desktop/base.dump" "C:/Users/janes/Desktop/fotos.tgz" root@179.236.248.139:/opt/
```

El `backend/` también hace falta, porque la imagen se construye con él:

```bash
scp -r "C:/Users/janes/OneDrive/Escritorio/Combustible App/backend" root@179.236.248.139:/opt/
```

> **No subas `backend/.venv`** si está dentro: son cientos de megas inútiles (la imagen
> instala sus propias dependencias). Si tarda demasiado, córtalo y súbelo sin esa carpeta.

---

## Paso 3 — El `.env`

En el **VPS**:

```bash
cd /opt/2day && cp env.ejemplo .env && openssl rand -base64 48
```

Copia lo que imprima: es tu `SESSION_SECRET`.

```bash
nano .env
```

Rellena todo lo que dice `PON-...` y las claves vacías. Guardar: `Ctrl+O`, `Enter`, `Ctrl+X`.

> La contraseña de Postgres tiene que ser **la misma** en `POSTGRES_PASSWORD` y dentro de
> `DATABASE_URL`. Es el error más común de este paso.

---

## Paso 4 — Cambia los archivos de configuración

```bash
cd /opt/2day && cp docker-compose.yml docker-compose.etapa1.yml && cp docker-compose.app.yml docker-compose.yml && cp Caddyfile.app Caddyfile
```

Si todavía **no** tienes el registro DNS, edita el `Caddyfile` y pon el nombre de ensayo en
lugar del dominio:

```bash
nano Caddyfile      # app.diesel2day.tech  ->  179.236.248.139.sslip.io
```

---

## Paso 5 — Levanta

```bash
cd /opt/2day && docker compose up -d --build
```

La primera vez tarda unos minutos: descarga Python y compila las dependencias.

```bash
docker compose ps
```

Los tres —`pg`, `app`, `caddy`— deben salir como `running`, y `pg` además `healthy`.

---

## Paso 6 — Mete tus datos

La app ya creó las tablas vacías al arrancar. Ahora se restaura encima:

```bash
docker cp /opt/base.dump pg:/tmp/base.dump
docker exec pg pg_restore -U evolution -d combustible --clean --if-exists /tmp/base.dump
```

> Va a imprimir algunos avisos sobre objetos que no existían. **Es normal** con `--clean`:
> intenta borrar lo que va a sustituir y algunas cosas aún no estaban.

Y las fotos:

```bash
docker run --rm -v 2day_media:/datos -v /opt:/entrada alpine tar xzf /entrada/fotos.tgz -C /datos
docker compose restart app
```

Comprueba que llegaron:

```bash
docker exec pg psql -U evolution -d combustible -tAc "select 'unidades',count(*) from unidades union all select 'remolques',count(*) from remolques union all select 'usuarios',count(*) from usuarios;"
```

Deben salir 55, 87 y 3.

---

## Paso 7 — Míralo

Abre tu dominio (o el de ensayo) en el navegador. Debe aparecer la pantalla de acceso de 2Day.

Entra con tu usuario y recorre: Resumen, Unidades, Remolques. Si los datos están, funcionó.

**Y pruébalo en un teléfono de verdad**, con datos móviles: entra como operador y abre la
cámara. Eso es lo único que no se puede comprobar desde la computadora, y es justo para lo
que hacía falta el certificado.

---

## Paso 8 — El respaldo

```bash
chmod +x /opt/2day/respaldo.sh && /opt/2day/respaldo.sh && ls -lh /opt/respaldos
```

Si salen los dos archivos con tamaño, prográmalo:

```bash
crontab -e
```

Añade al final:

```
15 3 * * * /opt/2day/respaldo.sh >> /var/log/respaldo-2day.log 2>&1
```

---

## Cuando llegue el registro DNS

```bash
cd /opt/2day && nano Caddyfile     # pon app.diesel2day.tech
docker compose restart caddy && docker compose logs -f caddy
```

Y **acuérdate de añadir el dominio nuevo en la consola de Google Maps**, o las rutas del mapa
dejarán de dibujarse: la clave de navegador está restringida por dominio.

---

## Si algo falla

```bash
docker compose logs app | tail -40
```

- **`app` reinicia sin parar** → casi siempre el `.env`: una variable obligatoria vacía
  (`DATABASE_URL`, `SESSION_SECRET`, `EVOLUTION_APIKEY`) o la contraseña de Postgres que no
  coincide entre las dos líneas.
- **502 desde Caddy** → la app aún no terminó de arrancar, o se cayó. Mira sus registros.
- **Entra pero sin datos** → el paso 6 no se completó.

---

## Lo que queda pendiente después

- Sacar los respaldos **fuera** del servidor (instantáneas de Hostinger o copia a otra
  máquina). Un respaldo en el mismo disco cubre «borré algo», no «se perdió el servidor».
- Las **114 lecturas de motor** sin importar y los Excel del proveedor de agosto en adelante.
- `COORDINADOR_WHATSAPP` sigue vacío: el botón de ayuda del operador no lleva a ningún sitio.
