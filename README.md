# 2Day — Control de combustible

Aplicación web (FastAPI + HTML) para solicitudes de recarga, despacho, conciliación con
proveedores y análisis de rendimiento de la flota.

```
2day-codigo/   código de la aplicación (app/, frontend/, requirements.txt)
2day/          despliegue anterior en VPS con Docker Compose + Caddy (referencia)
Dockerfile     imagen para EasyPanel
.env.example   lista de variables de entorno
```

## Despliegue en EasyPanel + Supabase

1. **Supabase**: crea el proyecto en la región más cercana al servidor de EasyPanel. Mientras
   copias los datos, apaga la Data API (*Settings → API*). La app activa RLS y quita los
   permisos de `anon`/`authenticated` en cada arranque.
2. **EasyPanel → nuevo servicio *App***:
   - *Source*: GitHub, este repositorio, rama `main`. *Build*: Dockerfile (`Dockerfile`).
   - *Environment*: las variables de `.env.example`, con los valores reales.
   - *Mounts*: volumen montado en **`/app/data`** (fotos, licencias y PDF del motor).
   - *Domains*: tu dominio, puerto **8000**, HTTPS activado.
3. Despliega y comprueba `https://TU_DOMINIO/health` → `{"status":"ok"}`.

## Copiar los datos de la base anterior a Supabase

```sh
pg_dump -Fc --no-owner --no-privileges -d "URL_DE_LA_BASE_ANTERIOR" > base.dump
pg_restore --no-owner --no-privileges -d "URL_DE_SUPABASE_SESSION_POOLER" base.dump
```

Las fotos no están en la base: copia también la carpeta `data/media` del servidor anterior
al volumen de EasyPanel.

## Fuentes de cargas de combustible

| Fuente | Cómo entra hoy |
|---|---|
| XIGA | Excel del proveedor → *Combustible → Cargas del proveedor* (`ingesta.CONTRATOS["XYGA"]`) |
| Oxxo (EOX) | Excel del proveedor → mismo lugar (`ingesta.CONTRATOS["OXXO"]`) |
| Fuera de plataforma (transferencias) | *Combustible → Cargas fuera de plataforma*: formulario o plantilla de Excel |

**Pendiente: API de XIGA.** Cuando se tenga la documentación y las credenciales, la conexión
entra como un lector más en `ingesta.LECTORES`, que entregue las mismas filas que hoy salen del
Excel. Así la API y el Excel comparten el mismo camino de importación, validación y conciliación.

## Recuperar el acceso del administrador

Si nadie recuerda la contraseña de `ADMIN_USER`, pon `ADMIN_PASSWORD_RESET=<contraseña nueva>`
en las variables de la app y despliega. Al arrancar se le pone esa contraseña y se cierran sus
sesiones. Entra y **borra la variable**.
