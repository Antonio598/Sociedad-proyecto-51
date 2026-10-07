# Tutorial: poner una página de prueba en tu dominio

Nueve pasos. Cada uno dice qué escribir y qué deberías ver.

Primero una página de prueba, no la aplicación. Si algo falla, falla con un HTML de tres
líneas y sabes exactamente dónde. La app va después, encima de esto mismo.

---

## Paso 1 — Apunta el dominio

En el panel de tu dominio, zona DNS, crea un registro:

| Tipo | Nombre | Valor | TTL |
|------|--------|-------|-----|
| A | `app` | la IP de tu VPS | 3600 |

Esto tarda de minutos a dos horas. **Empieza por aquí** y sigue con lo demás mientras tanto.

---

## Paso 2 — Comprueba que el dominio ya responde

En tu computadora:

```bash
nslookup app.tudominio.mx
```

**Debes ver** la IP de tu VPS. Si no, espera y vuelve a intentar. No sigas hasta que salga.

---

## Paso 3 — Entra al servidor

```bash
ssh root@LA-IP-DE-TU-VPS
```

**Debes ver** una línea que termina en `#`. Ya estás dentro.

---

## Paso 4 — Mira que no haya nada estorbando

```bash
docker ps -a
```

```bash
ss -tlnp | grep -E ':80|:443'
```

**Debes ver** las dos listas vacías. Si hay un contenedor de ejemplo, quítalo:

```bash
docker rm -f NOMBRE-DEL-CONTENEDOR
```

---

## Paso 5 — Actualiza y abre el cortafuegos

```bash
apt update && apt upgrade -y
```

```bash
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

> **Cópialo tal cual.** El 22 se abre antes de encender el cortafuegos. Si cambias el orden,
> pierdes el acceso al servidor y hay que entrar por la consola de rescate de Hostinger.

**Debes ver** `Firewall is active and enabled on system startup`.

---

## Paso 6 — Sube los archivos

Desde **tu computadora** (abre otra terminal, no la del servidor):

```bash
scp -r "C:/Users/janes/OneDrive/Escritorio/Combustible App/despliegue" root@LA-IP:/opt/2day
```

---

## Paso 7 — Escribe tu dominio

De vuelta en el servidor:

```bash
nano /opt/2day/Caddyfile
```

Cambia **solo** dos cosas:

- `CAMBIA_ESTO@ejemplo.com` → tu correo
- `app.CAMBIA-ESTO.mx` → tu dominio

> El dominio tiene que quedar **idéntico** al del paso 1. Si ahí pusiste `app.tudominio.mx`,
> aquí también. Es el error más común: con `tudominio.mx` a secas, no funciona.

Guardar en nano: `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## Paso 8 — Enciende

```bash
cd /opt/2day && docker compose up -d
```

```bash
docker compose logs -f caddy
```

**Debes ver** una línea con `certificate obtained successfully`. Sal con `Ctrl+C` (eso no
apaga nada).

Si ves errores, casi siempre es una de tres: el DNS aún no propagó, el dominio del paso 7 no
coincide con el del paso 1, o algo ocupa el puerto 80.

---

## Paso 9 — Míralo

Abre `https://app.tudominio.mx` en el navegador.

**Debes ver** la página con **HTTPS** y **certificado válido** en verde.

Pruébalo también en el teléfono **con datos móviles, no con wifi**: así confirmas que de
verdad sale a internet.

---

## Listo

Ya funciona la cadena completa: dominio, certificado y servidor. La aplicación se monta
encima de esto, sin rehacer nada.

---

## Dos cosas que no hay que hacer

**No borres el volumen `caddy_data`.** Ahí viven los certificados. Let's Encrypt solo permite
cinco por dominio y semana; si lo borras varias veces te quedas sin certificado unos días.

**No abras el puerto 5432.** La base de datos no sale a internet. Cuando la montemos, vivirá
en la red interna de Docker y solo la verá la aplicación.

---

## Qué sigue

Cuando el paso 9 salga en verde, la etapa 2 añade: la base de datos, la aplicación en un
contenedor, el volumen de las fotos de evidencia (que **no** están en la base de datos y hay
que respaldar aparte) y el respaldo programado.
