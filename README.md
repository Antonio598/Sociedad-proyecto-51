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
