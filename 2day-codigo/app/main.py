import base64
import binascii
import logging
import mimetypes
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date, datetime, time, timedelta, timezone

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Numeric, and_, case, delete, false, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import ai, catalogo, geo, limites, models, rendimiento  # noqa: F401
from .auth import (autenticar, crear_admin_si_falta, generar_password_fuerte, hash_password,
                   validar_password_fuerte, verify_password)
from .vault import cifrar, descifrar, vault_debil
from .config import BASE_DIR, _tz, fecha_flota, precio_del_litro, ruta_media, settings
from .db import Base, SessionLocal, engine
from .cfdi import TAMANO_MAX as CFDI_TAMANO_MAX, CFDIInvalido, parse_cfdi
from .models import (
    AnalisisReporte, Anomalia, AsientoConsumo, AsignacionViaje, AuditoriaThermo,
    CargaProveedor, EmpleadoProveedor, EstacionProveedor, ImportacionProveedor,
    Proveedor, TarjetaCombustible, EscaneoMotor,
    EtiquetaActivo, ImportacionEtiquetas, ImportacionPlacas, PropuestaCatalogo,
    EstadoAnomalia, EstadoAsignacion, EstadoSolicitud, EventoWhatsapp, EvidenciaRecarga,
    Factura, Operador, OrdenDespacho, Procedencia, RegistroActividad, Remolque, SeguimientoDescuento,
    SolicitudRecarga, TipoConfig,
    TipoUnidad, TransicionSolicitud, Unidad, Usuario, Viaje,
)
from .validacion import CATEGORIA_ANOMALIA
from .webhook import procesar_messages_upsert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("combustible")

# Un viaje RETRACTADO (su reporte se borró del grupo) se conserva, pero deja de contar para
# indicadores y estadísticas. Se aplica este criterio en vez de repetir la condición suelta,
# para que sea fácil auditar dónde está puesto y dónde falta.
VIGENTE = Viaje.retractado_en.is_(None)

FRONT = BASE_DIR / "frontend"


def _ajustes_de_esquema() -> None:
    """Cambios de esquema que `create_all` no hace sobre tablas que ya existen.

    `create_all` crea tablas que faltan, pero no añade columnas ni índices parciales a una
    tabla vieja. Todo aquí es idempotente (IF NOT EXISTS) y se puede correr en cada arranque.
    """
    from sqlalchemy import text
    with engine.begin() as cx:
        # Ver Usuario.sesion_version: cerrar sesiones al restablecer la contraseña.
        cx.execute(text("ALTER TABLE usuarios "
                        "ADD COLUMN IF NOT EXISTS sesion_version integer NOT NULL DEFAULT 0"))
    try:
        with engine.begin() as cx:
            # Una penalización por viaje, garantizado por la base y no sólo por el `if` del
            # endpoint: dos clics seguidos pasaban los dos la comprobación y creaban dos.
            cx.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_seguimiento_descuentos_viaje "
                            "ON seguimiento_descuentos (viaje_id) WHERE viaje_id IS NOT NULL"))
    except Exception:
        # Si ya hay duplicados de antes, el índice no se puede crear: se avisa y se sigue.
        log.exception("No se pudo crear ux_seguimiento_descuentos_viaje: revisa si hay "
                      "viajes con más de una penalización en seguimiento_descuentos")
    _cerrar_api_supabase()


def _cerrar_api_supabase() -> None:
    """Impide que la API REST de Supabase llegue a las tablas de esta app.

    Supabase expone por REST las tablas a los roles `anon`, `authenticated` y `service_role`.
    Sin esto, `usuarios` —hashes y la bóveda de claves de operadores— se podría leer desde
    fuera sin pasar por la app. Se activa RLS sin políticas (niega todo a esos roles) y se les
    quitan los permisos, también para lo que `create_all` añada más adelante. La app no lo
    nota: se conecta como dueña de las tablas, y al dueño no le aplica RLS.

    SÓLO toca el esquema de la app (`current_schema()`) y sólo las tablas de las que es dueña.
    La base puede ser compartida con otra aplicación —en este despliegue, un CRM vive en
    `public`—, y activar RLS o quitar permisos en sus tablas la rompería.

    Sólo corre si existe el rol `anon`, o sea, en Supabase. En un Postgres normal no hace nada.
    """
    from sqlalchemy import text
    try:
        with engine.begin() as cx:
            if not cx.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'anon'")).first():
                return
            esquema = cx.execute(text("SELECT current_schema()")).scalar_one()
            tablas = cx.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() "
                "AND tableowner = current_user AND NOT rowsecurity")).scalars().all()
            for t in tablas:
                cx.execute(text(f'ALTER TABLE "{esquema}"."{t}" ENABLE ROW LEVEL SECURITY'))
            if esquema != "public":
                # Un esquema propio: basta con cerrarle la puerta entera a los roles de la API.
                cx.execute(text(f'REVOKE ALL ON SCHEMA "{esquema}" '
                                "FROM anon, authenticated, service_role"))
            else:
                # En `public` (Supabase dedicado a esta app) sólo lo que es de la app.
                for t in cx.execute(text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                        "AND tableowner = current_user")).scalars().all():
                    cx.execute(text(f'REVOKE ALL ON public."{t}" '
                                    "FROM anon, authenticated, service_role"))
        if tablas:
            log.info("Supabase: RLS activado en %d tabla(s) de %s", len(tablas), esquema)
    except Exception:
        log.exception("SEGURIDAD: no se pudo cerrar la API REST de Supabase. Apaga la "
                      "Data API en el panel de Supabase (Settings → API) mientras tanto.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)   # crea tablas faltantes (usuarios) sin borrar datos
    _ajustes_de_esquema()
    crear_admin_si_falta()
    if geo.clave_compartida():
        # No es un aviso de estilo: la clave del servidor no lleva restricción por dominio,
        # así que expuesta en el HTML cualquiera puede gastar con la tarjeta de la flota.
        log.warning(
            'MAPAS: se está entregando al navegador la MISMA clave del servidor. Queda '
            'visible en el HTML y no tiene restricción por dominio. Crea una segunda clave '
            'en Google Cloud limitada a "Maps JavaScript API" y restringida por referrer, '
            'y ponla en GOOGLE_MAPS_BROWSER_KEY.')
    if settings.bot_whatsapp_activo:
        # Rehidrata la cola: reprocesa lo que quedó pendiente de un reinicio previo.
        try:
            from .captura import rehidratar_cola
            rehidratar_cola()
        except Exception:
            log.exception("Falló la rehidratación de la cola al arrancar")
    else:
        log.info("Asistente de WhatsApp RETIRADO (Fase A cerrada): la captura pasa por la "
                 "aplicación web. Para reactivarlo, BOT_WHATSAPP_ACTIVO=1 en .env.")
    # Un barrido al arrancar: así el atraso se limpia aunque nadie abra la pantalla de
    # viajes en todo el día.
    try:
        from .db import SessionLocal
        with SessionLocal() as _s:
            _n = cerrar_viajes_inactivos(_s)
        if _n:
            log.info("Al arrancar se cerraron %d viaje(s) abandonado(s)", len(_n))
    except Exception:
        log.exception("Falló el barrido de viajes abandonados al arrancar")
    yield
    # Las conexiones a Google se mantienen abiertas entre peticiones para no repetir el
    # saludo TLS (2 s la primera vez, ~150 ms las siguientes). Al apagar hay que cerrarlas.
    await geo.cerrar_clientes()


app = FastAPI(
    title="Control de Combustible", lifespan=lifespan,
    # Sin esto, /docs, /redoc y /openapi.json contestan 200 SIN sesión y entregan la lista
    # completa de endpoints, sus parámetros y sus esquemas. No filtra datos, pero le ahorra
    # a quien busque un hueco todo el trabajo de encontrarlo.
    docs_url="/docs" if settings.docs_abiertas else None,
    redoc_url="/redoc" if settings.docs_abiertas else None,
    openapi_url="/openapi.json" if settings.docs_abiertas else None,
)
app.add_middleware(
    SessionMiddleware, secret_key=settings.session_secret, max_age=60 * 60 * 8,
    # `Secure`: la cookie deja de viajar por HTTP. Antes salía sin la marca, así que una
    # sola petición en claro la entregaba entera.
    https_only=settings.cookie_segura,
    # `Lax` y no `Strict` a propósito: con Strict, llegar al panel desde un enlace externo
    # —un correo, el mensaje de WhatsApp del coordinador— no mandaría la cookie y la persona
    # vería la pantalla de acceso teniendo la sesión abierta.
    same_site="lax",
)
# Windows no trae los tipos de las tipografías en su registro, así que `mimetypes`
# devuelve None y StaticFiles acaba sirviendo los .woff2 como `application/octet-stream`.
# El navegador los acepta igual —el `format('woff2')` del CSS se lo dice—, pero un proxy
# estricto puede negarse, y el tipo correcto cuesta dos líneas.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
app.mount("/static", StaticFiles(directory=FRONT / "static"), name="static")


@app.middleware("http")
async def _cookie_de_sesion_rota(request: Request, call_next):
    """Una cookie de sesión ilegible se descarta en vez de reventar la petición.

    `SessionMiddleware` hace `b64decode` del contenido ANTES de que corra nada nuestro, y si
    el valor está mutilado —un truncado del navegador, una copia mal pegada, un proxy que
    recorta— lanza `binascii.Error` y la respuesta sale 500. Lo grave no es el 500 suelto: es
    que ocurre en TODAS las rutas, incluida `/login`, así que la víctima no tiene ninguna
    pantalla desde la que arreglarlo. Queda atrapada hasta que sepa borrar cookies a mano.

    Aquí se atrapa, se contesta como si no hubiera sesión y se BORRA la cookie mala, que es
    lo que devuelve al usuario a un estado del que puede salir solo.

    Lo único que este middleware necesita del orden es quedar POR FUERA de
    `SessionMiddleware`, y eso se cumple: los declarados con `@app.middleware` envuelven a los
    montados con `add_middleware`. Por eso puede atrapar lo que revienta dentro del
    decodificador de la cookie.
    """
    try:
        return await call_next(request)
    except (binascii.Error, ValueError) as e:
        if "session" not in request.cookies:
            raise
        log.warning("Cookie de sesión ilegible desde %s (%s): se descarta",
                    request.client.host if request.client else "?", type(e).__name__)
        destino = "/login" if not request.url.path.startswith("/api/") else None
        resp = (RedirectResponse(destino, status_code=303) if destino
                else JSONResponse({"detail": "Sesión inválida; vuelve a iniciar sesión."},
                                  status_code=401))
        resp.delete_cookie("session", path="/")
        return resp


@app.middleware("http")
async def _sin_cache_en_api(request: Request, call_next):
    """Los datos del panel NUNCA se cachean en el navegador.

    Sin esta cabecera el navegador reusaba libremente las respuestas de /api/ (no traen
    Last-Modified ni ETag, así que aplica su heurística) y el panel mostraba datos viejos:
    los viajes nuevos no aparecían en el Historial hasta recargar a la fuerza o limpiar
    caché. Se respeta la cabecera propia de un endpoint si ya la trae (p.ej. la foto del
    operador usa 'no-cache' para revalidar en vez de re-descargar siempre).
    """
    response = await call_next(request)

    # Cabeceras de seguridad, en todas las respuestas. No hay Content-Security-Policy: los
    # seis paneles llevan su JavaScript EN LÍNEA, así que la única política que no los
    # rompería tendría que permitir 'unsafe-inline' — y eso es justo lo que una CSP sirve
    # para prohibir. Ponerla así sería decoración; hacerla de verdad exige sacar el JS a
    # ficheros aparte, que es un trabajo aparte.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # La URL del túnel es lo único que separa la aplicación de internet mientras dura una
    # demo, y sin esto se la regalábamos a fonts.googleapis.com en cada carga.
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Permissions-Policy",
                                "geolocation=(self), camera=(self), microphone=()")

    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    # Lo MISMO para /static/: StaticFiles manda ETag y Last-Modified pero NINGÚN
    # Cache-Control, así que el navegador aplica su heurística y reutiliza el CSS/JS
    # viejo SIN preguntar. Efecto práctico: se cambiaba el diseño y en el navegador
    # "se veía igual" hasta forzar Ctrl+F5. 'no-cache' NO desactiva la caché: obliga a
    # revalidar, y como sí hay ETag la respuesta habitual es un 304 vacío (barato).
    elif request.url.path.startswith("/static/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_user(request: Request, db: Session = Depends(get_db)) -> dict:
    """Exige sesión iniciada Y comprueba contra la BASE que la cuenta sigue siendo lo que dice.

    POR QUÉ SE VUELVE A LEER. El rol se escribe DENTRO de la cookie al entrar, y antes las
    tres dependencias decidían con ese valor sin tocar la base nunca más. Con `max_age` de 8
    horas eso significaba que dar de baja a alguien —o bajarle el rol— no surtía efecto en su
    pestaña abierta: seguía entrando con los permisos congelados de cuando inició sesión.
    Despedir a un coordinador y que conserve el panel durante el resto de la jornada no es un
    matiz teórico. Ahora la cookie dice QUIÉN es; la base dice QUÉ puede.

    El coste es una búsqueda por clave primaria por petición, sobre la misma sesión de base
    que el endpoint ya usa (FastAPI resuelve `get_db` una vez por petición), así que no abre
    conexión aparte.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    uid = user.get("id")
    if uid is None:
        # Cookie anterior a que existiera el id: falla CERRADO, no se le supone nada.
        request.session.clear()
        raise HTTPException(status_code=401, detail="Vuelve a iniciar sesión")
    fila = db.get(Usuario, uid)
    if fila is None or not fila.activo:
        request.session.clear()
        raise HTTPException(status_code=401,
                            detail="Tu cuenta ya no está activa. Habla con el administrador.")
    # La contraseña se restableció después de abrir esta sesión: deja de valer. Una cookie
    # sin `sv` (anterior a este cambio) cuenta como versión 0, así no se expulsa a todos.
    if user.get("sv", 0) != (fila.sesion_version or 0):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Tu contraseña cambió; vuelve a iniciar sesión")
    # Mismas llaves que antes —nadie aguas abajo ve un diccionario distinto—, con los valores
    # de la fila viva.
    return {**user, "rol": fila.rol or "admin",
            "username": fila.username, "nombre": fila.nombre}


def require_rol(*roles: str):
    """Fábrica de dependencias: exige que el usuario tenga UNO de los roles dados.

    El permiso se valida en el SERVIDOR, no escondiendo botones. Falla CERRADO: una sesión
    sin rol (cookie vieja) no pasa ningún filtro. Uso:  Depends(require_rol("coordinador", "admin"))
    """
    permitidos = set(roles)

    def _dep(request: Request, db: Session = Depends(get_db)) -> dict:
        user = require_user(request, db)
        if user.get("rol") not in permitidos:
            raise HTTPException(
                status_code=403,
                detail=("No tienes permiso para esta acción. Si acabas de actualizar el "
                        "sistema, vuelve a iniciar sesión."))
        return user

    return _dep


# Atajos legibles por perfil. 'admin' entra en todos porque puede todo.
require_coordinador = require_rol("coordinador", "admin")
require_combustible = require_rol("combustible", "admin")
require_operador = require_rol("operador", "coordinador", "admin")
# GESTIÓN: coordinador + admin + gerente. OJO — el gerente NO es solo-lectura: es un rol de
# OPERACIONES que, desde su panel (gerente.html), ASIGNA viajes (POST /api/asignaciones) y
# RESUELVE anomalías (confirmar/rechazar) — decisión de producto (2026-08). require_gestion
# cubre esas acciones operativas del gerente MÁS las lecturas que audita (padrón, anomalías,
# export). Lo que el gerente NO hace —CRUD de catálogos, despacho, cuentas, IA facturable— se
# queda en require_coordinador / require_combustible / require_admin, que NO lo incluyen. No
# "endurecer" asignaciones/anomalías a require_coordinador: rompería el panel del gerente.
require_gestion = require_rol("coordinador", "admin", "gerente")
# Lectura de la analítica de combustible: la ve combustible (dueño), admin y gerente (audita).
require_ver_combustible = require_rol("combustible", "admin", "gerente")


def require_admin(request: Request, db: Session = Depends(get_db)) -> dict:
    """Solo administradores.

    Protege dos clases de acción: las que GASTAN crédito de IA (cada llamada se factura)
    y las que cambian catálogos, usuarios o configuración. El coordinador ve todo y
    resuelve la operación diaria; lo que no puede es alterar la base ni quemar crédito.
    La verificación es del lado del SERVIDOR: ocultar un botón no es un permiso.
    """
    user = require_user(request, db)
    # FALLA CERRADO: si la sesión no trae rol (cookie emitida antes de que existiera el
    # campo), NO se asume admin. Antes `(user.get("rol") or "admin")` convertía la ausencia
    # de rol en permiso total — justo al revés de lo que debe hacer un control de acceso.
    if user.get("rol") != "admin":
        raise HTTPException(status_code=403,
                            detail="Requiere permisos de administrador. Si acabas de "
                                   "actualizar el sistema, vuelve a iniciar sesión.")
    return user


def registrar_actividad(db: Session, *, accion: str, usuario: dict | int | None = None,
                        operador_id: int | None = None, rol: str | None = None,
                        entidad: str | None = None, entidad_id: int | None = None,
                        meta: dict | None = None, latencia_seg: int | None = None,
                        commit: bool = False) -> None:
    """Anota una acción en la bitácora general (base del panorama de Desempeño). Es SECUNDARIA
    al negocio: si falla, se traga el error para no romper el flujo principal. `usuario` acepta el
    dict de sesión (de donde toma id y rol) o un id suelto. Por defecto NO hace commit: se apoya en
    el commit del endpoint que la llama; usa commit=True si el endpoint ya cerró su transacción."""
    try:
        uid = None
        if isinstance(usuario, dict):
            uid = usuario.get("id")
            rol = rol or usuario.get("rol")
        elif isinstance(usuario, int):
            uid = usuario
        db.add(RegistroActividad(
            usuario_id=uid, operador_id=operador_id, rol=rol, accion=accion,
            entidad=entidad, entidad_id=entidad_id, meta=meta, latencia_seg=latencia_seg))
        if commit:
            db.commit()
    except Exception:
        log.exception("No se pudo registrar actividad %s", accion)
        try:
            db.rollback()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Páginas (login / dashboard)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# El modo sin conexión se retiró el 3-sep-2026 ("es una app que si o si requiere internet").
# Este service worker sólo existe para DESINSTALAR al anterior, y hay que dejarlo hasta que
# todos los teléfonos hayan pasado por aquí al menos una vez.
#
# Por qué no basta con borrar el archivo: un service worker instalado no muere porque el
# código deje de registrarlo. Sigue interceptando y sirviendo la versión cacheada, así que el
# operador se quedaría con la app vieja para siempre. Y devolver un 500 —que es lo que haría
# FileResponse sobre un archivo que ya no existe— tampoco lo retira: sólo un 404 o un SW
# nuevo lo consiguen, y el 404 depende de que el navegador decida buscar actualizaciones.
# Servir un SW válido que se retira a sí mismo cubre además los teléfonos que abren la app
# desde la caché y nunca llegarían a ejecutar el `unregister()` de la página.
_SW_ADIOS = """// 2Day — service worker de retirada. Su único trabajo es desinstalarse.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const c of await caches.keys()) await caches.delete(c);
    await self.registration.unregister();
    for (const cli of await self.clients.matchAll({type: 'window'})) cli.navigate(cli.url);
  })());
});
"""


@app.get("/sw.js")
def service_worker():
    """Devuelve el service worker de retirada. Se sirve desde la RAÍZ porque el que hay que
    reemplazar controlaba '/', y un SW sólo puede sustituir a otro del mismo alcance."""
    return Response(
        _SW_ADIOS,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


_NO_CACHE = {  # en desarrollo el HTML no se cachea: el celular siempre trae la versión más reciente
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


@app.get("/login")
def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/", status_code=303)
    return FileResponse(FRONT / "login.html", headers=_NO_CACHE)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    origen = limites.ip_cliente(request)
    espera = limites.espera_login(origen)
    if espera:
        # No se hashea nada: ese es el punto. El castigo cuesta cero CPU.
        log.warning("LOGIN bloqueado para %s (faltan %s s)", origen, espera)
        return RedirectResponse(f"/login?error=2&espera={espera}", status_code=303)

    # El byte nulo no es una credencial: es lo único que el driver de Postgres rechaza al
    # enlazar el parámetro, y sin esta guarda cualquiera provocaba un 500 a voluntad desde un
    # endpoint público. Se trata como lo que es, un usuario que no existe. (Que el NUL llegue
    # hasta el driver en vez de romper la consulta es, de paso, otra prueba de que el valor
    # viaja parametrizado y no concatenado.)
    with limites.aforo_login() as hay_sitio:
        if not hay_sitio:
            # Ya hay demasiadas verificaciones en curso. Se rechaza SIN hashear, que es lo
            # que impide que un flujo de intentos se lleve la CPU del panel entero.
            log.warning("LOGIN rechazado por aforo (origen %s)", origen)
            return RedirectResponse("/login?error=3", status_code=303)
        u = None if "\x00" in username else autenticar(db, username.strip(), password)

    if not u:
        # Los fallos SÍ se registran: antes sólo se anotaba el acierto, así que un ataque de
        # fuerza bruta no dejaba ni una línea en la bitácora.
        castigo = limites.anotar_fallo(origen)
        log.warning("LOGIN fallido · origen=%s usuario=%r%s",
                    origen, username[:40], f" · castigo {castigo}s" if castigo else "")
        return RedirectResponse("/login?error=1", status_code=303)

    limites.limpiar_origen(origen)
    u.ultimo_acceso = datetime.now(timezone.utc)   # bitácora de acceso ("quién ve y cuándo")
    registrar_actividad(db, accion="login", usuario=u.id, rol=u.rol or "admin",
                        operador_id=u.operador_id, entidad="usuario", entidad_id=u.id)
    db.commit()
    # Se descarta cualquier sesión anterior ANTES de escribir la nueva. Sin esto, lo que
    # hubiera en la sesión previa sobrevive al cambio de usuario: quien consiga sembrar una
    # sesión en el navegador de otro conserva sus valores cuando esa persona entra de verdad.
    request.session.clear()
    request.session["user"] = {"id": u.id, "username": u.username, "nombre": u.nombre,
                               "rol": u.rol or "admin", "sv": u.sesion_version or 0}
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/")
def index(request: Request):
    """Cada rol entra a SU pantalla, no todos al panel de admin.

    El operador y combustible tienen apps propias y acotadas; el coordinador y el admin
    comparten el panel operativo/analítico (con las secciones que a cada uno le tocan).
    """
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    rol = user.get("rol")
    if rol == "operador":
        return FileResponse(FRONT / "operador.html", headers=_NO_CACHE)
    if rol == "combustible":
        return FileResponse(FRONT / "combustible.html", headers=_NO_CACHE)
    if rol == "coordinador":
        return FileResponse(FRONT / "coordinador.html", headers=_NO_CACHE)
    if rol == "gerente":
        return FileResponse(FRONT / "gerente.html", headers=_NO_CACHE)
    return FileResponse(FRONT / "dashboard.html", headers=_NO_CACHE)


# ─────────────────────────────────────────────────────────────────────────────
# API del dashboard (protegida por sesión)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/me")
def me(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    # Si es operador, se resuelve su ficha del padrón para que su app sepa quién es y qué
    # unidad trae, sin que él lo escriba (base de la Fase B).
    salida = dict(user)
    if user.get("rol") == "operador":
        op = db.execute(
            select(Operador.id, Operador.nombre, Operador.numero)
            .join(Usuario, Usuario.operador_id == Operador.id)
            .where(Usuario.id == user.get("id"))
        ).first()
        if op:
            salida["operador_id"] = op[0]
            salida["operador_nombre"] = op[1]
            salida["operador_numero"] = op[2]
            # Contacto del coordinador para el botón "Contactar" de su app (WhatsApp).
            salida["coordinador_nombre"] = settings.coordinador_nombre
            salida["coordinador_whatsapp"] = settings.coordinador_whatsapp
            # Unidad SUGERIDA: primero el VIAJE ACTIVO que le asignó el coordinador (la
            # fuente real, y con destino); si no tiene, la última que manejó, o la de la que
            # es titular. Así su app la trae pre-elegida y no la busca entre 62.
            uid, uclave = _unidad_actual_operador(op[0], db)
            if uid:
                salida["unidad_sugerida_id"] = uid
                salida["unidad_sugerida_clave"] = uclave
    # Personalización del propio usuario (#7): teléfono, preferencias y si tiene foto.
    u = db.get(Usuario, user.get("id"))
    if u is not None:
        salida["telefono"] = u.telefono
        salida["prefs"] = u.prefs or {}
        salida["tiene_foto"] = bool(u.foto)
    return salida


# ─────────────────────────────────────────────────────────────────────────────
# Asignación de viajes (Fase B): el coordinador asigna operador + unidad + destino; el
# operador ve en su app su unidad y destino ACTUALES y su historial de viajes.
# ─────────────────────────────────────────────────────────────────────────────
def _mi_operador_id(user: dict, db: Session) -> int | None:
    """El operador_id ligado a la cuenta actual (None si la cuenta no es de un operador)."""
    return db.execute(
        select(Usuario.operador_id).where(Usuario.id == user.get("id"))).scalar()


def _unidad_actual_operador(op_id: int, db: Session):
    """La unidad ACTUAL del operador (id, clave). La establece el coordinador: es la del viaje
    ACTIVO asignado; si no hay, la del último viaje; si no, la de la que es titular. El
    operador NO la elige ni la cambia — es la única fuente de la unidad de sus solicitudes."""
    asig = db.execute(
        select(AsignacionViaje).where(
            AsignacionViaje.operador_id == op_id,
            AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
        .order_by(AsignacionViaje.creada_en.desc()).limit(1)).scalar_one_or_none()
    if asig is not None:
        return asig.unidad_id, asig.unidad.clave
    u = db.execute(
        select(Unidad.id, Unidad.clave).join(Viaje, Viaje.unidad_id == Unidad.id)
        .where(Viaje.operador_id == op_id)
        .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(1)).first()
    if u is None:
        u = db.execute(select(Unidad.id, Unidad.clave)
                       .where(Unidad.operador_asignado_id == op_id).limit(1)).first()
    return (u[0], u[1]) if u else (None, None)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distancia en línea recta (km) entre dos puntos, para estimar los km del destino a
    partir de las coordenadas elegidas en el mapa."""
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(min(1.0, math.sqrt(h))), 1)


def _asignacion_dict(a: AsignacionViaje, db: Session) -> dict:
    remolques = []
    if a.remolque_ids:
        rows = db.execute(
            select(Remolque.id, Remolque.eco, Remolque.usa_combustible, Remolque.operador_asignado_id)
            .where(Remolque.id.in_(a.remolque_ids))).all()
        info = {rid: (e, uc, tit) for rid, e, uc, tit in rows}
        ovr = a.remolque_operadores or {}
        op_ids = {(ovr.get(str(rid)) or info[rid][2]) for rid in a.remolque_ids
                  if rid in info and (ovr.get(str(rid)) or info[rid][2])}
        nombres = dict(db.execute(
            select(Operador.id, Operador.nombre).where(Operador.id.in_(op_ids))).all()) if op_ids else {}
        remolques = []
        for rid in a.remolque_ids:
            if rid in info:
                e, uc, tit = info[rid]
                oid = ovr.get(str(rid)) or tit
                remolques.append({"id": rid, "eco": e, "termo": bool(uc),
                                  "operador_id": oid, "operador": nombres.get(oid)})
    # Quién lo asignó: `creada_por_id` estaba en la tabla y no salía nunca. Es de las
    # primeras cosas que se preguntan al abrir el detalle de un viaje.
    quien = db.get(Usuario, a.creada_por_id) if a.creada_por_id else None
    return {
        "id": a.id,
        "creada_por": (quien.nombre or quien.username) if quien else None,
        # Si el operador ya la vio o no es lo que decide si hay que llamarle por teléfono.
        # Sin este campo el detalle afirmaba "todavía no" en todos los casos, que es peor
        # que no decir nada: es una afirmación falsa.
        "visto_en": a.visto_en.isoformat(sep=" ", timespec="minutes") if a.visto_en else None,
        "operador_id": a.operador_id,
        "operador_nombre": a.operador.nombre if a.operador else None,
        "operador_numero": a.operador.numero if a.operador else None,
        "unidad_id": a.unidad_id,
        "unidad_clave": a.unidad.clave if a.unidad else None,
        "unidad_tipo": a.unidad.tipo.value if a.unidad and a.unidad.tipo else None,
        "destino": a.destino,
        "origen": a.origen,
        "origen_lat": a.origen_lat, "origen_lng": a.origen_lng,
        "destino_lat": a.destino_lat, "destino_lng": a.destino_lng,
        "km_destino": a.km_destino,
        "km_estimado": a.km_estimado,
        "km_modificado": a.km_modificado,
        "km_modificado_en": a.km_modificado_en.isoformat() if a.km_modificado_en else None,
        # Días sin actividad. Va aquí para que la pantalla pueda avisar ANTES de que el
        # barrido cierre el viaje: un cierre que nadie vio venir se lee como un fallo.
        "dias_inactivo": _dias_inactivo(a, db),
        # Los días que le quedan antes de que el barrido lo cierre. Se calcula aquí y no
        # en la pantalla porque el plazo es ajustable: un «14» escrito en el HTML dejaría
        # de ser verdad en cuanto alguien lo suba, que es lo que pasó con el «+2%».
        "dias_para_cerrarse": _dias_para_cerrarse(a, db),
        "km_modificado_por": (db.get(Usuario, a.km_modificado_por_id).nombre
                              if a.km_modificado_por_id else None),
        "es_retorno": a.es_retorno,
        "nota": a.nota,
        "remolques": remolques,
        "estado": a.estado.value,
        "creada_en": a.creada_en.isoformat() if a.creada_en else None,
        "finalizada_en": a.finalizada_en.isoformat() if a.finalizada_en else None,
    }


@app.post("/api/asignaciones")
async def asignacion_crear(request: Request, user: dict = Depends(require_gestion),
                           db: Session = Depends(get_db)) -> dict:
    """El coordinador asigna a un operador una unidad y un destino (su viaje en curso).
    Asignar una nueva FINALIZA la asignación activa anterior de ese operador: una a la vez."""
    f = await request.form()

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    operador_id = _int(f.get("operador_id"))
    unidad_id = _int(f.get("unidad_id"))
    destino = (f.get("destino") or "").strip()
    origen = (f.get("origen") or "").strip() or None
    nota = (f.get("nota") or "").strip() or None

    def _flt(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    origen_lat, origen_lng = _flt(f.get("origen_lat")), _flt(f.get("origen_lng"))
    destino_lat, destino_lng = _flt(f.get("destino_lat")), _flt(f.get("destino_lng"))
    es_retorno = str(f.get("es_retorno") or "").strip().lower() in ("1", "true", "si", "sí", "on")
    km_estimado = _flt(f.get("km_estimado"))   # km de la ruta (OSRM) que manda el frontend
    km_destino = _flt(f.get("km_destino"))
    # Fallback: si no llega el estimado de ruta pero hay coords, línea recta (haversine).
    if km_estimado is None and None not in (origen_lat, origen_lng, destino_lat, destino_lng):
        km_estimado = _haversine_km(origen_lat, origen_lng, destino_lat, destino_lng)
    if km_destino is None:
        km_destino = km_estimado
    km_modificado = (km_destino is not None and km_estimado is not None
                     and abs(km_destino - km_estimado) > 0.5)
    # Remolques enganchados al viaje (ids separados por coma; opcional).
    raw_rem = (f.get("remolque_ids") or "").replace(" ", "")
    remolque_ids = list(dict.fromkeys(int(x) for x in raw_rem.split(",") if x.isdigit()))
    # Override de operador por remolque (opcional): {"<remolque_id>": operador_id}.
    import json
    remolque_operadores = None
    raw_ro = (f.get("remolque_operadores") or "").strip()
    if raw_ro:
        try:
            ro = {}
            for k, v in (json.loads(raw_ro) or {}).items():
                rid_, oid_ = _pi(str(k)), _pi(str(v))
                if rid_ and oid_ and rid_ in remolque_ids:
                    ro[str(rid_)] = oid_
            if ro:   # descarta operadores inexistentes (evita ids basura)
                validos = set(db.execute(
                    select(Operador.id).where(Operador.id.in_(set(ro.values())))).scalars().all())
                ro = {k: v for k, v in ro.items() if v in validos}
            remolque_operadores = ro or None
        except (ValueError, TypeError, AttributeError):
            remolque_operadores = None
    if not operador_id or db.get(Operador, operador_id) is None:
        raise HTTPException(400, "Operador no válido")
    unidad = db.get(Unidad, unidad_id) if unidad_id else None
    if unidad is None:
        raise HTTPException(400, "Unidad no válida")
    # Y que siga en la flota. Se comprobaba que EXISTIERA, no que estuviera activa, y hay
    # 12 unidades dadas de baja en la base: asignar una manda al chofer a un camión que ya
    # no existe para el resto del sistema.
    if not unidad.activo:
        raise HTTPException(400, f"La unidad {unidad.clave} está dada de baja")
    if not destino:
        raise HTTPException(400, "Falta el destino")
    # REGLA: un tracto lleva de 0 a 2 remolques; un camión no lleva remolques.
    if len(remolque_ids) > 2:
        raise HTTPException(400, "Un tracto lleva máximo 2 remolques")
    if remolque_ids and unidad.tipo != TipoUnidad.TRACTO:
        raise HTTPException(400, "Solo un tracto puede llevar remolques")
    if remolque_ids:
        rems = db.execute(select(Remolque)
                          .where(Remolque.id.in_(remolque_ids))).scalars().all()
        if len(rems) != len(remolque_ids):
            raise HTTPException(400, "Algún remolque no existe")
        # Lo mismo para los remolques: hay 8 de baja.
        bajas = [r.eco for r in rems if not r.activo]
        if bajas:
            raise HTTPException(
                400, f"Dado de baja: {', '.join(bajas)}")
        # REGLA DE ENGANCHE (declarada por el dueño el 1-sep-2026): un tracto lleva UN
        # remolque de la medida que sea, o DOS sólo si los dos son de 40 pies. No se
        # enganchan dos de 53, ni uno de 40 con uno de 53. El remolque sin medida conocida
        # no puede ir en pareja: no se puede afirmar que sea de 40, y suponerlo aquí es
        # exactamente el tipo de suposición que después arma un tren que no existe.
        if len(rems) > 1:
            malos = [r for r in rems if r.medida_pies != 40]
            if malos:
                detalle = ", ".join(
                    f"{r.eco} ({str(r.medida_pies) + ' pies' if r.medida_pies else 'medida desconocida'})"
                    for r in malos)
                raise HTTPException(
                    400, "Sólo se pueden enganchar dos remolques si ambos son de 40 pies. "
                         f"No cumplen: {detalle}")
    # ── UN ACTIVO, UN VIAJE ──────────────────────────────────────────────────────────
    #
    # La regla de abajo cierra el viaje anterior DEL OPERADOR, y sólo eso. Por eso un mismo
    # camión podía quedar en dos viajes a la vez —basta con dárselo a otro chofer— y un
    # remolque en cuatro: el remolque ni se miraba. En la base había 1 unidad y 3 remolques
    # duplicados el 21-sep-2026.
    #
    # No se cierra el viaje ajeno en silencio: ese chofer se quedaría sin viaje asignado y su
    # app le diría «Aún no tienes un viaje asignado» delante de la bomba. Se dice CON QUIÉN
    # choca y se deja confirmar, igual que la segunda carga del día y que el tope.
    confirmar = str(f.get("confirmar") or "").strip() in ("1", "true", "si", "sí", "on")
    activas = db.execute(
        select(AsignacionViaje)
        .where(AsignacionViaje.estado == EstadoAsignacion.ACTIVA,
               AsignacionViaje.operador_id != operador_id)).scalars().all()
    pedidos = set(remolque_ids or [])
    choques = []
    for otra in activas:
        motivos = []
        if otra.unidad_id == unidad_id:
            u = db.get(Unidad, unidad_id)
            motivos.append(f"la unidad {u.clave if u else unidad_id}")
        for rid in sorted(pedidos & set(otra.remolque_ids or [])):
            r = db.get(Remolque, rid)
            motivos.append(f"el remolque {r.eco if r else rid}")
        if not motivos:
            continue
        quien = db.get(Operador, otra.operador_id) if otra.operador_id else None
        choques.append({
            "asignacion_id": otra.id,
            "que": motivos,
            "operador": (quien.nombre if quien else None) or "otro operador",
            "destino": otra.destino,
            "desde": otra.creada_en.isoformat(sep=" ", timespec="minutes") if otra.creada_en else None,
        })

    if choques and not confirmar:
        # El cuerpo va estructurado para que el panel pueda preguntar con nombres y no con
        # un texto pegado: es el mismo contrato que `doble_carga_hoy`.
        detalle = "; ".join(
            f"{' y '.join(c['que'])} va en el viaje de {c['operador']} a {c['destino']}"
            + (f" (desde el {c['desde'][:10]})" if c.get("desde") else "")
            for c in choques)
        raise HTTPException(409, detail={
            "codigo": "activo_en_otro_viaje",
            "mensaje": f"Ya hay un viaje abierto con esto: {detalle}. "
                       "Si el viaje anterior ya terminó, confírmalo y se cerrará.",
            "choques": choques})

    # Cierra la asignación activa anterior del operador (regla: una activa por operador).
    db.execute(
        update(AsignacionViaje)
        .where(AsignacionViaje.operador_id == operador_id,
               AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
        .values(estado=EstadoAsignacion.FINALIZADA, finalizada_en=func.now()))
    # Y los viajes ajenos que el coordinador acaba de dar por terminados. Se cierran de
    # VERDAD —con su `finalizada_en`— en vez de dejarlos abiertos contradiciendo al nuevo.
    if choques:
        db.execute(
            update(AsignacionViaje)
            .where(AsignacionViaje.id.in_([c["asignacion_id"] for c in choques]))
            .values(estado=EstadoAsignacion.FINALIZADA, finalizada_en=func.now()))
        log.info("Asignación nueva cierra %d viaje(s) ajeno(s): %s por confirmación de %s",
                 len(choques), [c["asignacion_id"] for c in choques], user.get("id"))
    a = AsignacionViaje(operador_id=operador_id, unidad_id=unidad_id, destino=destino,
                        origen=origen, nota=nota, remolque_ids=remolque_ids or None,
                        remolque_operadores=remolque_operadores,
                        origen_lat=origen_lat, origen_lng=origen_lng,
                        destino_lat=destino_lat, destino_lng=destino_lng,
                        km_destino=km_destino, km_estimado=km_estimado,
                        km_modificado=km_modificado,
                        km_modificado_por_id=(user.get("id") if km_modificado else None),
                        km_modificado_en=(datetime.now(timezone.utc) if km_modificado else None),
                        es_retorno=es_retorno,
                        estado=EstadoAsignacion.ACTIVA, creada_por_id=user.get("id"))
    db.add(a)
    db.commit()
    db.refresh(a)
    registrar_actividad(db, accion="asignacion_creada", usuario=user, entidad="asignacion",
                        entidad_id=a.id, meta={"operador_id": operador_id, "unidad_id": unidad_id,
                                               "destino": destino}, commit=True)
    log.info("Asignación de viaje %s: operador %s -> unidad %s (%s remolques), destino %r",
             a.id, operador_id, unidad_id, len(remolque_ids), destino)
    return _asignacion_dict(a, db)


@app.post("/api/asignaciones/{asig_id}/km")
async def asignacion_km(asig_id: int, request: Request, user: dict = Depends(require_gestion),
                        db: Session = Depends(get_db)) -> dict:
    """El coordinador VERIFICA y MODIFICA los km al destino del viaje (auto-estimados desde el
    mapa, aquí ajustables). km_destino vacío = quitar."""
    a = db.get(AsignacionViaje, asig_id)
    if a is None:
        raise HTTPException(404, "Asignación no encontrada")
    # Los km sostienen la estimación de litros del viaje. Cambiarlos con la asignación ya
    # cerrada mueve la base de decisiones que ya se tomaron contra ellos.
    if a.estado is not None and a.estado.value != "activa":
        raise HTTPException(409, f"Esta asignación está {a.estado.value}: sus kilómetros ya "
                                 f"no se pueden ajustar.")
    f = await request.form()
    raw = (f.get("km_destino") or "").strip()
    if raw == "":
        a.km_destino = None
    else:
        try:
            a.km_destino = max(0.0, float(raw))
        except (ValueError, TypeError):
            raise HTTPException(400, "Km no válido")
    # `km_modificado` dice si el número de AHORA se aparta del estimado de la ruta, y eso sí
    # puede volver a ser falso. Quién lo tocó y cuándo NO: es un hecho pasado. La rama `else`
    # los ponía en None, así que devolver los km a su estimado borraba el rastro de quien los
    # había movido — el endpoint destruía su propia auditoría.
    a.km_modificado = bool(a.km_destino is not None and a.km_estimado is not None
                           and abs(a.km_destino - a.km_estimado) > 0.5)
    a.km_modificado_por_id = user.get("id")
    a.km_modificado_en = datetime.now(timezone.utc)
    db.commit()
    log.info("Km de la asignación %s -> %s (modificado=%s)", a.id, a.km_destino, a.km_modificado)
    return {"ok": True, "id": a.id, "km_destino": a.km_destino}


# ─────────────────────────────────────────────────────────────────────────────
# Geografía (mapas). El navegador DIBUJA el mapa; el cálculo de rutas y las
# direcciones pasan por aquí para que la clave que cobra Google nunca salga del
# servidor y para poder topar el consumo. Ver app/geo.py.
# Van con require_gestion (coordinador/admin/gerente) a propósito: son los únicos
# que usan mapas, y así una sesión cualquiera no puede quemar cuota de Google.
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/geo/config")
def geo_config(user: dict = Depends(require_user)) -> dict:
    """Qué proveedor de mapas usar y, si es Google, la clave del NAVEGADOR.

    Lo normal es entregar solo la clave de navegador, restringida por dominio en Google
    Cloud. Pero si no se configuró una aparte, se cae a la del servidor para que el mapa
    funcione — y entonces SÍ queda expuesta. `clave_compartida` lo dice para que el panel
    pueda avisar en vez de que el riesgo pase inadvertido.
    """
    return {"proveedor": "google" if geo.hay_google() else "osm",
            "clave": geo.clave_navegador() if geo.hay_google() else "",
            "clave_compartida": geo.clave_compartida()}


@app.post("/api/geo/ruta")
async def geo_ruta(request: Request, user: dict = Depends(require_gestion)) -> dict:
    """Ruta por carretera entre dos puntos: km + trazo para dibujar."""
    f = await request.form()

    def _f(k):
        try:
            return float(f.get(k))
        except (TypeError, ValueError):
            return None
    o_lat, o_lng, d_lat, d_lng = _f("o_lat"), _f("o_lng"), _f("d_lat"), _f("d_lng")
    if None in (o_lat, o_lng, d_lat, d_lng):
        raise HTTPException(400, "Faltan coordenadas de origen o destino")
    return await geo.ruta(o_lat, o_lng, d_lat, d_lng)


@app.get("/api/geo/direccion")
async def geo_direccion(lat: float, lng: float,
                        user: dict = Depends(require_gestion)) -> dict:
    """Dirección legible de un punto del mapa."""
    return await geo.direccion(lat, lng)


@app.get("/api/geo/buscar")
async def geo_buscar(q: str = "", user: dict = Depends(require_gestion)) -> dict:
    """Busca una dirección escrita y devuelve su punto."""
    return await geo.buscar(q)


@app.get("/api/geo/sugerencias")
async def geo_sugerencias(q: str = "", user: dict = Depends(require_gestion)) -> dict:
    """Lugares que empiezan por lo tecleado, para el desplegable del buscador.

    Va por el servidor, como el resto de geo, para que la clave no salga al navegador. Cada
    sugerencia trae ya su punto, así que elegir una no cuesta otra llamada.
    """
    return {"sugerencias": await geo.sugerencias(q)}


@app.get("/api/geo/consumo")
def geo_consumo(user: dict = Depends(require_admin)) -> dict:
    """Cuántas rutas se han pedido hoy contra el tope (control de gasto)."""
    return geo.consumo_hoy()


def _dias_inactivo(a: AsignacionViaje, db: Session) -> float | None:
    """Días desde lo último que pasó en este viaje; desde que se creó, si no pasó nada."""
    if a.estado != EstadoAsignacion.ACTIVA:
        return None
    fechas = [f for (f,) in db.execute(
        select(SolicitudRecarga.creada_en)
        .where(SolicitudRecarga.asignacion_id == a.id)).all() if f]
    ultima = max(fechas) if fechas else a.creada_en
    if ultima is None:
        return None
    return round((datetime.now(timezone.utc) - ultima).total_seconds() / 86400, 1)


def _dias_para_cerrarse(a: AsignacionViaje, db: Session) -> float | None:
    """Lo que le queda a este viaje antes de cerrarse solo. None si no aplica."""
    if a.estado != EstadoAsignacion.ACTIVA or settings.viaje_inactivo_dias <= 0:
        return None
    # Un viaje con una solicitud viva no se cierra nunca, así que tampoco tiene cuenta
    # atrás: decir que le quedan tres días sería prometer algo que no va a pasar.
    VIVAS = (EstadoSolicitud.ENVIADA, EstadoSolicitud.EN_VALIDACION,
             EstadoSolicitud.DEVUELTA, EstadoSolicitud.AUTORIZADA)
    if db.scalar(select(func.count(SolicitudRecarga.id))
                 .where(SolicitudRecarga.asignacion_id == a.id,
                        SolicitudRecarga.estado.in_(VIVAS))):
        return None
    quieto = _dias_inactivo(a, db)
    if quieto is None:
        return None
    return round(max(0.0, settings.viaje_inactivo_dias - quieto), 1)


def cerrar_viajes_inactivos(db: Session) -> list[int]:
    """Cierra los viajes abandonados. Devuelve los que cerró.

    Un viaje no termina por sí solo: sólo se cierra si alguien pulsa el botón o si al
    chofer se le asigna otro. Cuando no pasa ninguna de las dos, el viaje se queda abierto
    para siempre —la asignación 27 llevaba 18 días— y con él la unidad y los remolques
    ocupados, que es de donde salían las colisiones.

    LA GUARDA QUE MANDA: nunca se cierra un viaje con una solicitud VIVA. Cerrarlo dejaría
    al coordinador con una decisión pendiente sobre un viaje que la app da por terminado, y
    al chofer con una recarga en el aire. Hoy eso protege a dos de los siete abiertos.

    Se mide desde la ÚLTIMA actividad, no desde que se creó: un viaje largo con recargas
    cada pocos días está vivo, aunque lleve semanas abierto.
    """
    dias = settings.viaje_inactivo_dias
    if dias <= 0:
        return []
    corte = datetime.now(timezone.utc) - timedelta(days=dias)
    VIVAS = (EstadoSolicitud.ENVIADA, EstadoSolicitud.EN_VALIDACION,
             EstadoSolicitud.DEVUELTA, EstadoSolicitud.AUTORIZADA)
    cerrados: list[int] = []
    for a in db.execute(select(AsignacionViaje)
                        .where(AsignacionViaje.estado == EstadoAsignacion.ACTIVA)).scalars().all():
        sols = db.execute(select(SolicitudRecarga)
                          .where(SolicitudRecarga.asignacion_id == a.id)).scalars().all()
        if any(s.estado in VIVAS for s in sols):
            continue   # alguien todavía tiene que decidir sobre este viaje
        fechas = [s.creada_en for s in sols if s.creada_en]
        ultima = max(fechas) if fechas else a.creada_en
        if ultima is None or ultima > corte:
            continue
        a.estado = EstadoAsignacion.FINALIZADA
        a.finalizada_en = datetime.now(timezone.utc)
        cerrados.append(a.id)
    if cerrados:
        db.commit()
        log.info("Viajes cerrados por %d días sin actividad: %s", dias, cerrados)
    return cerrados

@app.get("/api/asignaciones")
def asignaciones(estado: str | None = None, limit: int = Query(100, ge=1, le=500),
                 user: dict = Depends(require_gestion),
                 db: Session = Depends(get_db)) -> list[dict]:
    # Se barre aquí porque este proyecto no tiene planificador y ésta es la pantalla
    # donde el coordinador mira los viajes: se limpia solo cuando alguien se asoma. Son
    # siete filas activas, no es trabajo.
    cerrar_viajes_inactivos(db)
    q = select(AsignacionViaje).order_by(AsignacionViaje.creada_en.desc()).limit(limit)
    if estado:
        try:
            q = q.where(AsignacionViaje.estado == EstadoAsignacion(estado))
        except ValueError:
            raise HTTPException(400, "Estado no válido")
    return [_asignacion_dict(a, db) for a in db.execute(q).scalars().all()]


@app.post("/api/asignaciones/{asig_id}/finalizar")
async def asignacion_finalizar(asig_id: int, request: Request,
                               user: dict = Depends(require_coordinador),
                               db: Session = Depends(get_db)) -> dict:
    a = db.get(AsignacionViaje, asig_id)
    if a is None:
        raise HTTPException(404, "Asignación no encontrada")
    f = await request.form()
    cancelar = str(f.get("cancelar") or "").lower() in ("1", "true", "si", "sí", "on")
    if a.estado == EstadoAsignacion.ACTIVA:
        a.estado = EstadoAsignacion.CANCELADA if cancelar else EstadoAsignacion.FINALIZADA
        a.finalizada_en = datetime.now(timezone.utc)
        a.finalizada_por_id = user.get("id")
        registrar_actividad(
            db, accion=("asignacion_cancelada" if cancelar else "asignacion_finalizada"),
            usuario=user, entidad="asignacion", entidad_id=a.id,
            meta={"operador_id": a.operador_id})
        db.commit()
        db.refresh(a)
    return _asignacion_dict(a, db)


@app.get("/api/mi-viaje")
def mi_viaje(user: dict = Depends(require_operador), db: Session = Depends(get_db)) -> dict:
    """El viaje ACTIVO del operador: la unidad y el destino que le asignó el coordinador."""
    op_id = _mi_operador_id(user, db)
    if op_id is None:
        return {"activo": None}
    a = db.execute(
        select(AsignacionViaje)
        .where(AsignacionViaje.operador_id == op_id,
               AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
        .order_by(AsignacionViaje.creada_en.desc()).limit(1)
    ).scalar_one_or_none()
    # Primera vez que el operador ABRE su asignación: marca "visto" y registra el tiempo desde
    # que el coordinador la creó (la "notificación"). Solo se estampa una vez (idempotente).
    if a is not None and a.visto_en is None:
        ahora = datetime.now(timezone.utc)
        # Estampa "visto" de forma ATÓMICA: si dos peticiones concurrentes entran a la vez (doble
        # montaje de la PWA, reintento, otra pestaña), solo una gana el UPDATE (WHERE visto_en IS
        # NULL) y registra el evento UNA vez; la otra no duplica la vista ni la latencia.
        gano = db.execute(
            update(AsignacionViaje)
            .where(AsignacionViaje.id == a.id, AsignacionViaje.visto_en.is_(None))
            .values(visto_en=ahora)).rowcount
        if gano:
            lat = None
            if a.creada_en:
                try:
                    lat = max(0, int((ahora - a.creada_en).total_seconds()))
                except (TypeError, ValueError):
                    lat = None
            registrar_actividad(db, accion="asignacion_vista", usuario=user, rol="operador",
                                operador_id=op_id, entidad="asignacion", entidad_id=a.id,
                                latencia_seg=lat)
        db.commit()
        a.visto_en = ahora   # refleja el cambio en el objeto que se devuelve
    return {"activo": _asignacion_dict(a, db) if a else None}


# ══════════════════════════════════════════════════════════════════════════════
# COMBUSTIBLE DEL PROVEEDOR (E2) Y LIBRO MAYOR (E3)
#
# Hasta hoy estas dos etapas SÓLO existían por línea de comandos: 639 cargas y 639 asientos
# que cuadran al centavo y que nadie del equipo podía ver. Estos tres endpoints son su
# primera ventana.
#
# Son de LECTURA. Importar, proyectar y revertir siguen siendo scripts a propósito: son
# operaciones que reescriben meses enteros y deben dejar rastro en una consola, no
# dispararse desde un clic.
# ══════════════════════════════════════════════════════════════════════════════

def _carga_fila(c, prov, est, tar, uni, rem, asi) -> dict:
    """Una carga tal como se pinta en la tabla. NO incluye `fila_cruda` a propósito.

    `fila_cruda` es el jsonb con las 25 celdas verbatim del proveedor: unos 372 bytes por
    fila, o sea 238 kB si viajaran las 639, y encima su significado depende del
    `encabezado_leido` de SU importación, que es otra tabla. Va sólo en el detalle.
    """
    activo = (uni.clave if uni else ((rem.eco_nuevo or rem.eco) if rem else None))
    return {
        "id": c.id,
        "fecha": c.fecha_operacion.isoformat() if c.fecha_operacion else None,
        "momento": c.momento_local.isoformat(sep=" ", timespec="minutes") if c.momento_local else None,
        "proveedor": prov.clave if prov else None,
        "estacion": (est.nombre if est and est.nombre else None) or c.estacion_nombre_txt or c.estacion_txt,
        "estacion_codigo": est.codigo if est else c.estacion_txt,
        "folio": c.folio_txt,
        "tarjeta": tar.numero_txt if tar else c.tarjeta_txt,
        "eco_txt": c.eco_txt,
        "placa_txt": c.placa_txt,
        "activo": activo,
        "activo_tipo": ("unidad" if uni else ("remolque" if rem else None)),
        "litros": c.litros,
        "importe": c.importe,
        # El precio del proveedor sólo viene poblado en Xyga (313 de 639), así que cuando
        # falta se deriva. Se marca cuál es cuál para no presentar un cálculo como un dato.
        "precio": c.precio if c.precio is not None else (
            round(c.importe / c.litros, 4) if (c.litros and c.importe) else None),
        "precio_derivado": c.precio is None,
        "combustible": c.combustible or c.producto_norm,
        "destino": c.destino,
        "estado": c.estado_resolucion,
        "via": c.resuelto_via,
        "fuera_de_flota": bool(c.fuera_de_flota),
        "motivo": c.motivo_estado,
        "asiento_id": asi.id if asi else None,
    }


@app.get("/api/proveedor/cargas")
def proveedor_cargas(
    proveedor: str = Query(""),
    estado: str = Query(""),
    q: str = Query(""),
    desde: str = Query(""),
    hasta: str = Query(""),
    limite: int = Query(150, ge=1, le=1000),
    user: dict = Depends(require_gestion),
    db: Session = Depends(get_db),
) -> dict:
    """Las cargas que los proveedores facturaron, con su atribución a un activo.

    Sólo las VIGENTES: cuando el proveedor corrige una fila, la vieja deja de serlo y su
    sustituta ocupa su lugar. Mostrar las dos sumaría el mismo diésel dos veces.
    """
    # Las condiciones se arman UNA vez y se aplican a dos consultas distintas. Antes los
    # totales salían de `select_from(base.subquery())`, y eso obliga a Postgres a
    # materializar una tabla derivada con las 7 entidades ORM enteras —66 columnas sólo de
    # cargas_proveedor— para después contar sus filas: la petición no terminaba.
    cond = [CargaProveedor.vigente.is_(True)]
    if estado:
        # 'fuera_flota' no es un estado_resolucion: es una BANDERA aparte que hoy convive con
        # estado_resolucion='cuarentena'. Si no se separasen, esta pantalla diría 43 cargas en
        # cuarentena mientras el libro mayor dice 39, y las dos tendrían razón. Se separan aquí
        # para que las dos pantallas cuenten lo mismo.
        if estado == "fuera_flota":
            cond.append(CargaProveedor.fuera_de_flota.is_(True))
        elif estado == "cuarentena":
            cond.append(CargaProveedor.estado_resolucion == "cuarentena")
            cond.append(CargaProveedor.fuera_de_flota.is_not(True))
        else:
            cond.append(CargaProveedor.estado_resolucion == estado)
    # `fecha_operacion` es DATE: pasarle el texto del querystring hace que Postgres rechace la
    # comparación entera ('operator does not exist: date >= character varying'). Una fecha mal
    # escrita se ignora en vez de tirar la petición: el filtro es una ayuda, no un contrato.
    for txt, comparar in ((desde, "ge"), (hasta, "le")):
        if not txt:
            continue
        try:
            d = datetime.strptime(txt.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        cond.append(CargaProveedor.fecha_operacion >= d if comparar == "ge"
                    else CargaProveedor.fecha_operacion <= d)
    if proveedor:
        pid = db.scalar(select(Proveedor.id).where(Proveedor.clave == proveedor))
        # Un proveedor que no existe no puede casar con nada; -1 lo deja vacío sin
        # necesidad de sumar el join a la consulta de totales.
        cond.append(CargaProveedor.proveedor_id == (pid if pid is not None else -1))
    if q:
        t = "%" + q.strip().lower() + "%"
        cond.append(or_(
            func.lower(func.coalesce(CargaProveedor.eco_txt, "")).like(t),
            func.lower(func.coalesce(CargaProveedor.placa_txt, "")).like(t),
            func.lower(func.coalesce(CargaProveedor.folio_txt, "")).like(t),
            func.lower(func.coalesce(CargaProveedor.estacion_nombre_txt, "")).like(t),
            func.lower(func.coalesce(CargaProveedor.tarjeta_txt, "")).like(t),
        ))

    # Los totales se calculan sobre TODO lo filtrado, no sobre la página que se pinta: si
    # sumaran sólo las 150 visibles, el encabezado mentiría en cuanto el filtro trajera más.
    tot = db.execute(
        select(func.count(CargaProveedor.id),
               func.coalesce(func.sum(CargaProveedor.litros), 0.0),
               func.coalesce(func.sum(CargaProveedor.importe), 0.0))
        .where(*cond)).one()

    filas = db.execute(
        select(CargaProveedor, Proveedor, EstacionProveedor, TarjetaCombustible,
               Unidad, Remolque, AsientoConsumo)
        .outerjoin(Proveedor, Proveedor.id == CargaProveedor.proveedor_id)
        .outerjoin(EstacionProveedor, EstacionProveedor.id == CargaProveedor.estacion_id)
        .outerjoin(TarjetaCombustible, TarjetaCombustible.id == CargaProveedor.tarjeta_id)
        .outerjoin(Unidad, Unidad.id == CargaProveedor.unidad_id)
        .outerjoin(Remolque, Remolque.id == CargaProveedor.remolque_id)
        .outerjoin(AsientoConsumo, and_(AsientoConsumo.carga_id == CargaProveedor.id,
                                        AsientoConsumo.vigente.is_(True)))
        .where(*cond)
        .order_by(CargaProveedor.momento_local.desc().nullslast(),
                  CargaProveedor.id.desc())
        .limit(limite)).all()
    return {
        "cargas": [_carga_fila(*r) for r in filas],
        "total": tot[0],
        "litros": round(float(tot[1] or 0), 3),
        "importe": round(float(tot[2] or 0), 2),
        "precio": (round(float(tot[2]) / float(tot[1]), 4) if tot[1] else None),
        "mostradas": len(filas),
        "limite": limite,
        "proveedores": [p.clave for p in db.execute(
            select(Proveedor).order_by(Proveedor.clave)).scalars()],
        # 'fuera_flota' se ofrece como un estado más aunque en la tabla sea una bandera:
        # para quien mira la pantalla son cargas que no son de la flota, y esconderlas dentro
        # de cuarentena es justo lo que hacía que este panel y el libro no cuadraran.
        "estados": ([e for (e,) in db.execute(
            select(CargaProveedor.estado_resolucion)
            .where(CargaProveedor.vigente.is_(True))
            .distinct().order_by(CargaProveedor.estado_resolucion)).all() if e]
            + (["fuera_flota"] if db.scalar(
                select(func.count()).select_from(CargaProveedor)
                .where(CargaProveedor.vigente.is_(True),
                       CargaProveedor.fuera_de_flota.is_(True))) else [])),
    }


@app.get("/api/proveedor/cargas/{carga_id}")
def proveedor_carga_detalle(carga_id: int, user: dict = Depends(require_gestion),
                            db: Session = Depends(get_db)) -> dict:
    """El expediente de una carga: la fila verbatim, su origen y su asiento contable.

    Aquí SÍ viaja `fila_cruda`, emparejada con el encabezado que se leyó en SU importación:
    sin ese encabezado la lista de celdas no significa nada, porque las columnas no son las
    mismas entre los dos proveedores.
    """
    c = db.get(CargaProveedor, carga_id)
    if c is None:
        raise HTTPException(404, "Esa carga no existe")
    prov = db.get(Proveedor, c.proveedor_id) if c.proveedor_id else None
    est = db.get(EstacionProveedor, c.estacion_id) if c.estacion_id else None
    tar = db.get(TarjetaCombustible, c.tarjeta_id) if c.tarjeta_id else None
    uni = db.get(Unidad, c.unidad_id) if c.unidad_id else None
    rem = db.get(Remolque, c.remolque_id) if c.remolque_id else None
    emp = db.get(EmpleadoProveedor, c.empleado_id) if c.empleado_id else None
    imp = db.get(ImportacionProveedor, c.importacion_id) if c.importacion_id else None
    asi = db.scalar(select(AsientoConsumo).where(AsientoConsumo.carga_id == c.id,
                                                 AsientoConsumo.vigente.is_(True)))
    cabeceras = list(getattr(imp, "encabezado_leido", None) or [])
    cruda = list(c.fila_cruda or []) if isinstance(c.fila_cruda, list) else []
    verbatim = [{"col": (str(cabeceras[i]) if i < len(cabeceras) and cabeceras[i] else "col " + str(i + 1)),
                 "val": ("" if v is None else str(v))}
                for i, v in enumerate(cruda)]
    d = _carga_fila(c, prov, est, tar, uni, rem, asi)
    d.update({
        "verbatim": verbatim,
        "fila_num": c.fila_num,
        "sha256": c.sha256_fila,
        "revision": c.revision,
        "estado_revision": c.estado_revision,
        "conductor": c.conductor_txt or (emp.nombre_proveedor if emp else None),
        "bomba": c.bomba_txt,
        "producto": c.producto_txt,
        "subtotal": c.subtotal, "iva": c.iva, "ieps": c.ieps,
        "discrepancias": c.discrepancias or None,
        "importacion": ({"id": imp.id, "archivo": imp.archivo,
                         "cuando": (imp.importado_en.isoformat(sep=" ", timespec="minutes")
                                    if imp.importado_en else None),
                         "estado": imp.estado} if imp else None),
        "asiento": ({"id": asi.id, "litros": float(asi.litros or 0),
                     "importe": float(asi.importe or 0), "atribucion": asi.atribucion,
                     "destino": asi.destino, "contable": bool(asi.contable),
                     "motivo": asi.motivo} if asi else None),
    })
    return d


def _palabras_nombre(s: str | None) -> set:
    """Las palabras de un nombre, sin acentos y en mayúsculas, para poder compararlos."""
    import unicodedata
    t = unicodedata.normalize("NFD", (s or "").upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return {p for p in re.sub(r"[^A-Z ]", " ", t).split() if len(p) > 1}


@app.get("/api/proveedor/empleados")
def proveedor_empleados(user: dict = Depends(require_admin),
                        db: Session = Depends(get_db)) -> dict:
    """Los empleados que nombra el proveedor, con un CANDIDATO PROPUESTO y su fuerza.

    Propone, no enlaza. 12 de los 34 nombres casan con sólo dos palabras y ahí «MIGUEL VITAL
    ALCANTARA» casa con «LUIS VITAL ALCANTARA», que es otra persona: enlazarlo solo le
    colgaría el diésel de uno al otro. La fuerza va al cliente para que se vea de un golpe
    cuáles se pueden aceptar sin pensar y cuáles hay que mirar.

    Ordenados por LITROS: las 34 decisiones no valen lo mismo.
    """
    ops = [(o.id, o.nombre, _palabras_nombre(o.nombre))
           for o in db.execute(select(Operador).where(Operador.activo.is_(True))).scalars()]
    filas = []
    for e in db.execute(select(EmpleadoProveedor)
                        .order_by(EmpleadoProveedor.litros.desc())).scalars():
        palabras = _palabras_nombre(e.nombre_proveedor)
        cand = sorted(((len(palabras & p), oid, nom) for oid, nom, p in ops if palabras & p),
                      reverse=True)[:4]
        prov = db.get(Proveedor, e.proveedor_id)
        ya = db.get(Operador, e.operador_id) if e.operador_id else None
        filas.append({
            "id": e.id, "numero": e.numero, "nombre_proveedor": e.nombre_proveedor,
            "proveedor": prov.clave if prov else None,
            "estado": e.vinculo_estado, "n_cargas": e.n_cargas, "litros": e.litros,
            "operador_id": e.operador_id,
            "operador_nombre": ya.nombre if ya else None,
            "operador_numero": ya.numero if ya else None,
            # `fuerza` son las palabras en común. 3 o más es seguro; 2 es justo donde
            # aparecen los homónimos y donde hace falta que alguien mire.
            "candidatos": [{"operador_id": oid, "nombre": nom, "fuerza": n}
                           for n, oid, nom in cand],
        })
    return {"empleados": filas,
            "pendientes": sum(1 for f in filas if f["estado"] == "pendiente"),
            "total": len(filas)}


@app.post("/api/proveedor/empleados/{emp_id}/vincular")
async def proveedor_empleado_vincular(emp_id: int, request: Request,
                                      user: dict = Depends(require_admin),
                                      db: Session = Depends(get_db)) -> dict:
    """Enlaza a una persona del proveedor con su ficha, o anota que no está en el padrón.

    «No está» es una respuesta de pleno derecho: se guarda como `sin_equivalente` para no
    volver a preguntarla el mes que viene. Y se puede deshacer volviendo a «pendiente»,
    porque una decisión humana equivocada también es humana.
    """
    e = db.get(EmpleadoProveedor, emp_id)
    if e is None:
        raise HTTPException(404, "Ese empleado del proveedor no existe")
    f = await request.form()
    estado = (f.get("estado") or "").strip().lower()
    if estado not in ("vinculado", "sin_equivalente", "pendiente"):
        raise HTTPException(400, "Estado no válido")

    if estado == "vinculado":
        oid = _pi(f.get("operador_id"))
        o = db.get(Operador, oid) if oid else None
        if o is None:
            raise HTTPException(400, "Elige el operador con el que se enlaza")
        # Un mismo operador no puede ser dos empleados DEL MISMO proveedor: sería la misma
        # persona cobrando dos veces, y el libro de consumo la contaría dos veces.
        otro = db.scalar(select(EmpleadoProveedor).where(
            EmpleadoProveedor.operador_id == o.id,
            EmpleadoProveedor.proveedor_id == e.proveedor_id,
            EmpleadoProveedor.id != e.id))
        if otro is not None:
            raise HTTPException(400, (f"{o.nombre} ya está enlazado con {otro.numero} "
                                      f"en este proveedor"))
        e.operador_id = o.id
    else:
        e.operador_id = None

    e.vinculo_estado = estado
    e.vinculado_por_id = user.get("id")
    e.vinculado_en = datetime.now(timezone.utc)
    registrar_actividad(db, accion="empleado_proveedor_vinculado", usuario=user,
                        entidad="empleado_proveedor", entidad_id=emp_id,
                        meta={"numero": e.numero, "estado": estado,
                              "operador_id": e.operador_id})
    db.commit()
    return {"ok": True, "id": e.id, "estado": e.vinculo_estado,
            "operador_id": e.operador_id}


# ── El Excel del proveedor entra por aquí ────────────────────────────────────
# `redirect_stdout` es global al proceso y dos corridas sobre `cargas_proveedor` a la vez
# no son buena idea de todas formas: se serializan.
_CERROJO_IMPORTACION = threading.Lock()
_PROV_LIMITE = 30 * 1024 * 1024


@app.post("/api/proveedor/importar")
def proveedor_importar(file: UploadFile = File(...),
                       proveedor: str = Form(...),
                       dry: str = Form("false"),
                       forzar: str = Form("false"),
                       user: dict = Depends(require_combustible),
                       db: Session = Depends(get_db)) -> dict:
    """Guarda el Excel de un proveedor. Idempotente: reimportar el mismo mes no duplica nada.

    NO reescribe el importador: llama al de `scripts/import_proveedor.py`, que ya recibe la
    sesión desde fuera. Y devuelve SU informe tal cual —el que explica fila por fila qué
    entró, qué se corrigió y qué se fue a cuarentena—, porque escribir aquí otro resumen
    sería duplicarlo para que los dos se separen con el tiempo.

    `dry` enseña el plan completo sin escribir ni una carga. Es lo que conviene hacer la
    primera vez con el archivo de un mes nuevo.

    La función es SÍNCRONA a propósito: leer 326 filas tarda segundos y FastAPI corre las
    síncronas en un hilo aparte. En una `async def` eso detendría el servidor entero.
    """
    import io
    import tempfile
    from contextlib import redirect_stdout

    from scripts.import_proveedor import Aborta, importar
    from .ingesta import CONTRATOS, LayoutInesperado

    clave = (proveedor or "").strip().upper()
    if clave not in CONTRATOS:
        raise HTTPException(400, f"No hay contrato de lectura para «{proveedor}». "
                                 f"Los que hay son {', '.join(sorted(CONTRATOS))}.")
    crudo = file.file.read(_PROV_LIMITE + 1)
    if len(crudo) > _PROV_LIMITE:
        raise HTTPException(400, "El archivo supera el límite de 30 MB")
    if not crudo:
        raise HTTPException(400, "El archivo llegó vacío")

    # El nombre original se conserva: el importador lo guarda en `importaciones_proveedor`
    # y es lo que permite decir «este mes entró con este archivo». Va dentro de una carpeta
    # temporal propia, así que el nombre no puede chocar ni escaparse a ningún sitio.
    nombre = Path((file.filename or "archivo.xlsx").replace("\\", "/")).name[:300]
    ensayo = str(dry).lower() in ("true", "1", "on")
    forzado = str(forzar).lower() in ("true", "1", "on")

    with tempfile.TemporaryDirectory(prefix="prov_") as carpeta:
        ruta = Path(carpeta) / nombre
        ruta.write_bytes(crudo)
        salida = io.StringIO()
        with _CERROJO_IMPORTACION:
            try:
                with redirect_stdout(salida):
                    imp = importar(db, clave, str(ruta), dry=ensayo, forzar=forzado,
                                   usuario_id=user.get("id"))
            except (Aborta, LayoutInesperado) as e:
                # El importador se detiene SIN escribir; el rollback deshace lo que hubiera
                # quedado a medias. El mensaje de `Aborta` explica qué hacer, no qué falló.
                db.rollback()
                return {"ok": False, "detenido": True, "motivo": str(e),
                        "informe": salida.getvalue()}

    # `importar` devuelve None cuando el sha256 del archivo YA está en la base: ni falla
    # ni importa, simplemente no hay nada que hacer. Tratarlo como éxito haría que la
    # pantalla dijera «listo» y quien subió el mes se quedara creyendo que entró.
    if imp is None:
        ya = db.scalar(select(ImportacionProveedor)
                       .where(ImportacionProveedor.sha256 == _sha_archivo(crudo),
                              ImportacionProveedor.vigente.is_(True)))
        return {"ok": True, "ya_estaba": True, "informe": salida.getvalue(),
                # El informe del script dice «--forzar» y «python -m ...», que es cierto en
                # una terminal e inútil en un navegador. Esta frase habla de la pantalla.
                "motivo": ("Este archivo ya está cargado, así que no se volvió a leer. "
                           "Si de verdad quieres releerlo, marca «Volver a leerlo»."),
                "importacion": _importacion_dict(ya) if ya is not None else None}

    registrar_actividad(db, accion="proveedor_importado", usuario=user,
                        entidad="importacion_proveedor",
                        entidad_id=getattr(imp, "id", None),
                        meta={"proveedor": clave, "archivo": nombre, "ensayo": ensayo})
    db.commit()
    return {"ok": True, "ensayo": ensayo, "informe": salida.getvalue(),
            "importacion": _importacion_dict(imp)}


def _sha_archivo(datos: bytes) -> str:
    """El mismo sha256 con el que el importador identifica un archivo."""
    import hashlib
    return hashlib.sha256(datos).hexdigest()


def _importacion_dict(i) -> dict:
    """Lo que hizo una corrida del importador, en cifras."""
    return {
        "id": i.id, "archivo": i.archivo, "estado": i.estado, "vigente": i.vigente,
        "sha256": (i.sha256 or "")[:16],
        "periodo_desde": i.periodo_desde.isoformat() if i.periodo_desde else None,
        "periodo_hasta": i.periodo_hasta.isoformat() if i.periodo_hasta else None,
        "n_filas_leidas": i.n_filas_leidas, "n_nuevas": i.n_nuevas,
        "n_repetidas": i.n_repetidas, "n_corregidas": i.n_corregidas,
        "n_omitidas": i.n_omitidas, "n_cuarentena": i.n_cuarentena,
        "n_fuera_flota": i.n_fuera_flota, "n_resueltas": i.n_resueltas,
        "litros_total": float(i.litros_total or 0), "importe_total": float(i.importe_total or 0),
        "importado_en": i.importado_en.isoformat() if i.importado_en else None,
    }


# ── Cerrar el ciclo contra el renglón del proveedor ──────────────────────────
# Tolerancias del emparejamiento. Anchas a propósito: esto PROPONE, no decide, y una
# propuesta de más se descarta de un vistazo mientras que una de menos no aparece nunca.
_CONC_DIAS = 2          # el despacho y el cobro pueden caer en días distintos
_CONC_PCT = 0.10        # 10% de diferencia en litros


@app.get("/api/despacho/pendientes-proveedor")
def despacho_pendientes_proveedor(user: dict = Depends(require_ver_combustible),
                                  db: Session = Depends(get_db)) -> dict:
    """Órdenes despachadas sin cerrar, con los renglones del proveedor que podrían serlo.

    Propone y no cierra: la fuerza de cada candidato va al cliente para que se vea de un
    golpe cuál se puede aceptar sin pensar y cuál hay que mirar.
    """
    abiertas = db.execute(
        select(OrdenDespacho).join(SolicitudRecarga,
                                   SolicitudRecarga.id == OrdenDespacho.solicitud_id)
        .where(OrdenDespacho.litros_reales.is_not(None),
               OrdenDespacho.carga_proveedor_id.is_(None),
               SolicitudRecarga.estado.in_([EstadoSolicitud.DESPACHADA,
                                            EstadoSolicitud.EN_DISCREPANCIA]))
        .order_by(OrdenDespacho.despachada_en.desc())).scalars().all()

    # Los renglones ya usados no se vuelven a ofrecer: un renglón, una orden.
    usados = {c for (c,) in db.execute(select(OrdenDespacho.carga_proveedor_id)
                                       .where(OrdenDespacho.carga_proveedor_id.is_not(None)))}
    filas = []
    for o in abiertas:
        s = db.get(SolicitudRecarga, o.solicitud_id)
        uni = db.get(Unidad, s.unidad_id) if s is not None and s.unidad_id else None
        cuando = (o.despachada_en or o.autorizada_en)
        dia = fecha_flota(cuando) if cuando else None
        cands = []
        if uni is not None and dia is not None:
            q = select(CargaProveedor).where(
                CargaProveedor.vigente.is_(True),
                CargaProveedor.unidad_id == uni.id,
                CargaProveedor.fecha_operacion.between(
                    dia - timedelta(days=_CONC_DIAS), dia + timedelta(days=_CONC_DIAS)))
            for c in db.execute(q).scalars():
                if c.id in usados:
                    continue
                dl = (abs((c.litros or 0) - (o.litros_reales or 0))
                      / (o.litros_reales or 1))
                prov = db.get(Proveedor, c.proveedor_id)
                cands.append({
                    "carga_id": c.id, "proveedor": prov.clave if prov else None,
                    "folio": c.folio_txt, "estacion": c.estacion_txt,
                    "fecha": c.fecha_operacion.isoformat() if c.fecha_operacion else None,
                    "litros": c.litros, "importe": c.importe,
                    "dif_litros": round((c.litros or 0) - (o.litros_reales or 0), 2),
                    # `ajustado` = los litros cuadran dentro del 10%. Es lo que distingue
                    # «acéptalo» de «míralo»: la fecha sola empareja cualquier cosa.
                    "ajustado": dl <= _CONC_PCT,
                })
        cands.sort(key=lambda x: (not x["ajustado"], abs(x["dif_litros"])))
        filas.append({
            "orden_id": o.id, "folio": o.folio,
            "solicitud_id": o.solicitud_id,
            "estado": s.estado.value if s is not None else None,
            "unidad": uni.clave if uni is not None else None,
            "litros_reales": o.litros_reales,
            "despachada_en": cuando.isoformat() if cuando else None,
            "candidatos": cands[:5],
        })
    return {
        "ordenes": filas,
        "n": len(filas),
        "con_candidato": sum(1 for f in filas if f["candidatos"]),
        # El rango que cubre el archivo del proveedor. Sin esto, «0 candidatos» parece un
        # fallo del emparejador cuando lo que pasa es que el mes todavía no ha llegado.
        "proveedor_desde": (lambda v: v.isoformat() if v else None)(
            db.scalar(select(func.min(CargaProveedor.fecha_operacion))
                      .where(CargaProveedor.vigente.is_(True)))),
        "proveedor_hasta": (lambda v: v.isoformat() if v else None)(
            db.scalar(select(func.max(CargaProveedor.fecha_operacion))
                      .where(CargaProveedor.vigente.is_(True)))),
    }


@app.post("/api/despacho/{orden_id}/conciliar")
async def despacho_conciliar(orden_id: int, request: Request,
                             user: dict = Depends(require_combustible),
                             db: Session = Depends(get_db)) -> dict:
    """Cierra una orden despachada contra el renglón del proveedor que la cobró.

    EXIGE el renglón. Retiradas las facturas, ésa es la prueba de que el diésel se cobró, y
    sin prueba no se cierra un ciclo: cerrarlo «porque sí» es exactamente el agujero que la
    guarda de coherencia física vino a tapar.
    """
    # Dentro de la función, como el resto de main.py: `solicitudes` se importa donde se usa.
    from .solicitudes import TransicionInvalida, transicionar

    o = db.get(OrdenDespacho, orden_id)
    if o is None:
        raise HTTPException(404, "Esa orden de despacho no existe")
    f = await request.form()
    carga_id = _pi(f.get("carga_id"))
    c = db.get(CargaProveedor, carga_id) if carga_id else None
    if c is None or not c.vigente:
        raise HTTPException(400, "Elige el renglón del proveedor que cobró esta recarga")
    otra = db.scalar(select(OrdenDespacho).where(
        OrdenDespacho.carga_proveedor_id == c.id, OrdenDespacho.id != o.id))
    if otra is not None:
        # Se dice CUÁL se lo llevó, en vez de devolver un choque de clave: la respuesta
        # útil es «ya lo usó la orden tal», no «conflicto».
        raise HTTPException(400, f"Ese renglón ya cerró la orden {otra.folio}")
    s = db.get(SolicitudRecarga, o.solicitud_id)
    if s is None:
        raise HTTPException(404, "La solicitud de esa orden no existe")

    o.carga_proveedor_id = c.id
    o.conciliada_en = datetime.now(timezone.utc)
    try:
        transicionar(db, s, EstadoSolicitud.CONCILIADA, user,
                     nota=(f"Conciliada contra {c.folio_txt or c.id} "
                           f"({c.litros:g} L, ${c.importe:,.2f})" if c.litros and c.importe
                           else f"Conciliada contra el renglón {c.id} del proveedor"),
                     cambios={"carga_proveedor_id": c.id})
    except TransicionInvalida as e:
        raise HTTPException(409, str(e))
    registrar_actividad(db, accion="despacho_conciliado", usuario=user,
                        entidad="orden_despacho", entidad_id=o.id,
                        meta={"carga_id": c.id, "folio": o.folio})
    db.commit()
    return {"ok": True, "orden_id": o.id, "carga_id": c.id,
            "estado": s.estado.value,
            "dif_litros": round((c.litros or 0) - (o.litros_reales or 0), 2)}


@app.get("/api/proveedor/libro")
def proveedor_libro(user: dict = Depends(require_gestion),
                    db: Session = Depends(get_db)) -> dict:
    """El cuadre entre lo que cobró el proveedor y lo que registró el libro mayor.

    La cifra que importa es la DIFERENCIA. Si no es cero, alguien cobró litros que el libro
    no reconoce —o al revés— y todo lo que se calcule encima estará mal.
    """
    cg = db.execute(select(func.count(), func.coalesce(func.sum(CargaProveedor.litros), 0.0),
                           func.coalesce(func.sum(CargaProveedor.importe), 0.0))
                    .where(CargaProveedor.vigente.is_(True))).one()
    lb = db.execute(select(func.count(), func.coalesce(func.sum(AsientoConsumo.litros), 0),
                           func.coalesce(func.sum(AsientoConsumo.importe), 0))
                    .where(AsientoConsumo.vigente.is_(True),
                           AsientoConsumo.contable.is_(True))).one()
    por_atrib = [{"atribucion": a, "n": n, "litros": round(float(l or 0), 3),
                  "importe": round(float(i or 0), 2)}
                 for a, n, l, i in db.execute(
                     select(AsientoConsumo.atribucion, func.count(),
                            func.sum(AsientoConsumo.litros), func.sum(AsientoConsumo.importe))
                     .where(AsientoConsumo.vigente.is_(True), AsientoConsumo.contable.is_(True))
                     .group_by(AsientoConsumo.atribucion)
                     .order_by(func.sum(AsientoConsumo.litros).desc())).all()]
    # Sólo las importaciones VIGENTES: la tabla guarda también las simuladas (--dry-run), que
    # no escribieron ni una carga. Listarlas todas haría creer que el mes se cargó diez veces.
    imps = []
    for i in db.execute(select(ImportacionProveedor)
                        .where(ImportacionProveedor.vigente.is_(True))
                        .order_by(ImportacionProveedor.importado_en.desc())).scalars():
        pv = db.get(Proveedor, i.proveedor_id) if i.proveedor_id else None
        imps.append({"id": i.id, "archivo": i.archivo,
                     "proveedor": pv.clave if pv else None,
                     "cuando": (i.importado_en.isoformat(sep=" ", timespec="minutes")
                                if i.importado_en else None),
                     "estado": i.estado, "filas": i.n_filas_leidas, "nuevas": i.n_nuevas,
                     "repetidas": i.n_repetidas, "resueltas": i.n_resueltas,
                     "cuarentena": i.n_cuarentena, "fuera_flota": i.n_fuera_flota,
                     "litros": i.litros_total, "importe": i.importe_total})
    d_lit = round(float(cg[1] or 0) - float(lb[1] or 0), 3)
    d_imp = round(float(cg[2] or 0) - float(lb[2] or 0), 2)
    return {
        "cargas": {"n": cg[0], "litros": round(float(cg[1] or 0), 3),
                   "importe": round(float(cg[2] or 0), 2)},
        "libro": {"n": lb[0], "litros": round(float(lb[1] or 0), 3),
                  "importe": round(float(lb[2] or 0), 2)},
        "diferencia": {"litros": d_lit, "importe": d_imp,
                       "cuadra": d_lit == 0 and d_imp == 0},
        "por_atribucion": por_atrib,
        "importaciones": imps,
        "precio_real": (round(float(cg[2]) / float(cg[1]), 4) if cg[1] else None),
        # Import local, como en bitacora.py y reporte.py: el precio se lee en el momento de
        # servir, no al arrancar, para que cambiarlo no exija reiniciar el proceso entero.
        "precio_config": precio_del_litro(datetime.now(timezone.utc).year),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXTO Y GASTO PARA EL ROL COMBUSTIBLE
#
# Quien despacha veía folio, unidad, operador y litros: firmaba sin saber por qué se piden
# esos litros. Esto le da el viaje que los justifica y el historial contra el que compararlos.
#
# OJO CON LA FUENTE: aquí los litros salen de `viajes` (el historial importado, 2,436 viajes
# con litros, de ene-2025 a jul-2026), NO de `cargas_proveedor` (las 639 facturas de julio).
# Son universos distintos y sumarlos sería inventar. Cada respuesta lo declara en `fuente`.
# ══════════════════════════════════════════════════════════════════════════════

def _dispersion_rto(db: Session, cond) -> dict:
    """Percentiles del rendimiento real (km/L) de los viajes que cumplen `cond`.

    Se toman de los viajes individuales, no del total: el total da EL rendimiento, y lo que
    hace falta aquí es cuánto VARÍA de un viaje a otro. Devuelve vacío si no hay suficientes
    viajes con las dos cifras — con cuatro observaciones un percentil no dice nada.
    """
    r = db.execute(select(
        func.count(),
        func.percentile_cont(0.25).within_group(Viaje.kilometros / Viaje.lts_real),
        func.percentile_cont(0.75).within_group(Viaje.kilometros / Viaje.lts_real),
    ).where(*cond, Viaje.kilometros.is_not(None), Viaje.lts_real > 0)).one()
    n, p25, p75 = r
    if not n or n < 5 or p25 is None or p75 is None:
        return {"rto_p25": None, "rto_p75": None, "rto_viajes": int(n or 0)}
    return {"rto_p25": round(float(p25), 3), "rto_p75": round(float(p75), 3),
            "rto_viajes": int(n)}


def _stats_viajes(db: Session, *, unidad_id=None, operador_id=None, limite=6) -> dict:
    """Cuánto gasta históricamente una unidad o un operador, para tener con qué comparar.

    `dif` es litros cargados menos litros que el motor dice haber quemado: es la cifra por la
    que este rol existe, así que se devuelve siempre.
    """
    cond = [Viaje.lts_real.is_not(None)]
    if unidad_id:
        cond.append(Viaje.unidad_id == unidad_id)
    if operador_id:
        cond.append(Viaje.operador_id == operador_id)
    r = db.execute(select(
        func.count(), func.sum(Viaje.lts_real), func.sum(Viaje.kilometros),
        func.sum(Viaje.lts_scaner), func.sum(Viaje.dif),
        func.avg(Viaje.lts_real), func.max(Viaje.fecha),
    ).where(*cond)).one()
    n, lts, km, scan, dif, prom, ult = r
    return {
        "viajes": n or 0,
        "litros": round(float(lts or 0), 1),
        "km": round(float(km or 0), 1),
        "litros_motor": round(float(scan or 0), 1),
        "diferencia": round(float(dif or 0), 1),
        "litros_por_viaje": round(float(prom or 0), 1),
        # El rendimiento se calcula del TOTAL, no promediando los rendimientos de cada viaje:
        # un viaje corto pesaría igual que uno de 3,000 km y el número saldría falso.
        "km_por_litro": (round(float(km) / float(lts), 3) if lts and km else None),
        # La DISPERSIÓN de ese rendimiento, que es lo que separa «cabe en lo normal» de «hay
        # que preguntar». Sin ella la banda del viaje era un ±25% inventado a mano bajo un
        # comentario que juraba lo contrario.
        **_dispersion_rto(db, cond),
        "ultimo_viaje": ult.isoformat() if ult else None,
        "recientes": [
            {"id": v.id, "fecha": v.fecha.isoformat() if v.fecha else None,
             "km": v.kilometros, "litros": v.lts_real, "litros_motor": v.lts_scaner,
             "diferencia": v.dif, "km_por_litro": v.rto_real}
            for v in db.execute(select(Viaje).where(*cond)
                                .order_by(Viaje.fecha.desc(), Viaje.id.desc())
                                .limit(limite)).scalars()
        ],
    }


@app.get("/api/solicitudes/{sol_id}/contexto")
def solicitud_contexto(sol_id: int, user: dict = Depends(require_user),
                       db: Session = Depends(get_db)) -> dict:
    """El viaje que justifica los litros de una solicitud, y con qué compararlos.

    Existe porque el rol `combustible` NO pasa por `require_gestion` y no puede consultar
    asignaciones ni viajes por su cuenta —y no debe: no le toca navegar la operación—. Aquí
    recibe sólo lo de LA solicitud que tiene delante.
    """
    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Esa solicitud no existe")
    if user.get("rol") == "operador":
        mio = db.scalar(select(Usuario.operador_id).where(Usuario.id == user.get("id")))
        if s.operador_id != mio:
            raise HTTPException(403, "No es tu solicitud")

    uni = db.get(Unidad, s.unidad_id) if s.unidad_id else None
    op = db.get(Operador, s.operador_id) if s.operador_id else None

    # EL VIAJE DE ESTA SOLICITUD, no el que esté activo ahora. De ahí salen el origen, el
    # destino y los kilómetros que justifican los litros, así que si el operador ya va en
    # otro viaje, medir contra ese otro sería juzgar la solicitud por un camino que no hizo.
    #
    # Se cae a la activa sólo cuando la solicitud no lo tiene guardado: las anteriores a la
    # migración que no encajaron en ninguna ventana.
    asig = None
    if s.operador_id:
        a = db.get(AsignacionViaje, s.asignacion_id) if s.asignacion_id else None
        if a is None:
            a = db.scalar(select(AsignacionViaje)
                          .where(AsignacionViaje.operador_id == s.operador_id,
                                 AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
                          .order_by(AsignacionViaje.creada_en.desc()).limit(1))
        if a is not None:
            quien = db.get(Usuario, a.creada_por_id) if a.creada_por_id else None
            asig = {"id": a.id, "origen": a.origen, "destino": a.destino,
                    # Los DOS números: el del trazo automático y el que dejó el
                    # coordinador. `km_modificado` dice si se apartan, pero sin el
                    # segundo la pantalla no puede decir cuál sostiene el cálculo.
                    "km_estimado": a.km_estimado, "km_destino": a.km_destino,
                    "km_modificado": a.km_modificado,
                    "es_retorno": bool(a.es_retorno), "nota": a.nota,
                    "creada_en": a.creada_en.isoformat(sep=" ", timespec="minutes")
                    if a.creada_en else None,
                    "creada_por": (quien.nombre or quien.username) if quien else None,
                    "vista_por_operador": bool(a.visto_en)}

    orden = db.scalar(select(OrdenDespacho).where(OrdenDespacho.solicitud_id == s.id))
    autorizo = db.get(Usuario, orden.autorizada_por_id) if orden and orden.autorizada_por_id else None

    # Los litros que DEBERÍAN salir: los kilómetros del viaje entre el rendimiento histórico
    # de esta unidad. Los dos números ya estaban aquí y nadie los cruzaba.
    #
    # NO ES UNA PREDICCIÓN y el cliente lo dice con esas palabras: una recarga no tiene por
    # qué cubrir el viaje entero, así que esto sirve para cantar un 900 contra un 576, no
    # para discutir 560 contra 576. Una cifra que aparenta precisión que no tiene haría más
    # daño que no darla.
    hu = _stats_viajes(db, unidad_id=s.unidad_id) if s.unidad_id else None
    esperado = None
    # El corregido manda sobre el automático: mismo orden que `rendimiento.km_efectivo`
    # y que el que ya pintaban las pantallas de Viajes.
    km = None
    if asig:
        km = asig.get("km_destino")
        if km is None:
            km = asig.get("km_estimado")
    rend = (hu or {}).get("km_por_litro")
    # El TERMO no recorre los kilómetros del tracto: estimarlo con ellos daba 575.9 L donde
    # los remolques cargan ~130 de mediana. Para el termo no hay estimación de viaje, y
    # decirlo es más honesto que dar la del motor.
    if getattr(s, "tipo_recarga", "motor") == "termo":
        km = None
    if km and rend:
        litros = km / rend
        esperado = {
            "litros": round(litros, 1),
            "km": km,
            "km_por_litro": rend,
            "viajes_base": (hu or {}).get("viajes"),
            # El margen sale de la dispersión REAL de esta unidad —los percentiles de su
            # rendimiento por viaje—, que es lo que este comentario prometía y el código no
            # cumplía: era un ±25% escrito a mano. Más rendimiento gasta MENOS litros, así que
            # p75 del rendimiento da el mínimo de litros y p25 el máximo. Si no hay bastantes
            # viajes para un percentil honesto, se cae al ±25% y se dice en `margen`.
            # El centro sale del rendimiento PONDERADO y los extremos de los percentiles POR
            # VIAJE: dos estadísticos distintos, así que el ponderado puede caer fuera del
            # rango intercuartil y dejar a la banda sin contener su propio número. Se ensancha
            # lo justo para que eso no pase: una banda que excluye su centro no se puede leer.
            "min": min(round(litros, 1),
                       round(km / (hu or {}).get("rto_p75"), 1)
                       if (hu or {}).get("rto_p75") else round(litros * 0.75, 1)),
            "max": max(round(litros, 1),
                       round(km / (hu or {}).get("rto_p25"), 1)
                       if (hu or {}).get("rto_p25") else round(litros * 1.25, 1)),
            "margen": ("dispersión real de esta unidad sobre "
                       f"{(hu or {}).get('rto_viajes')} viajes"
                       if (hu or {}).get("rto_p25") else "±25% (pocos viajes para medirla)"),
            "nota": ("Estimado del viaje completo a partir del rendimiento histórico de la "
                     "unidad. Una recarga puede cubrir sólo parte del viaje: sirve para "
                     "detectar un número muy fuera de lugar, no para ajustar litros."),
        }

    return {
        "solicitud": {
            "id": s.id, "estado": s.estado.value if s.estado else None,
            "tipo_recarga": getattr(s, "tipo_recarga", "motor"),
            "litros_solicitados": s.litros_solicitados,
            "odometro": s.odometro, "nivel_tanque": s.nivel_tanque,
            "motivo": s.motivo,
            "creada_en": s.creada_en.isoformat(sep=" ", timespec="minutes") if s.creada_en else None,
        },
        "unidad": ({"id": uni.id, "clave": uni.clave,
                    "tipo": uni.tipo.value if uni.tipo else None,
                    "marca": uni.marca, "anio": uni.anio} if uni else None),
        "operador": ({"id": op.id, "nombre": op.nombre, "numero": op.numero} if op else None),
        "asignacion": asig,
        "orden": ({"folio": orden.folio, "litros_autorizados": orden.litros_autorizados,
                   "litros_reales": orden.litros_reales,
                   "autorizada_por": (autorizo.nombre or autorizo.username) if autorizo else None,
                   "autorizada_en": orden.autorizada_en.isoformat(sep=" ", timespec="minutes")
                   if orden.autorizada_en else None} if orden else None),
        "historial_unidad": hu,
        # Lo que de verdad cabe en una carga de ESTE activo. Va aparte del historial de
        # viajes a propósito: son dos escalas distintas y confundirlas es justo el error que
        # ponía 876.8 L delante de quien iba a teclear 280.
        "cargas_unidad": _stats_cargas(db, unidad_id=s.unidad_id),
        "cargas_remolque": (_stats_cargas(db, remolque_id=s.remolque_id)
                            if s.remolque_id else None),
        "historial_operador": _stats_viajes(db, operador_id=s.operador_id) if s.operador_id else None,
        "esperado": esperado,
        "fuente": "historial de viajes (viajes.lts_real), ene-2025 a jul-2026",
    }


@app.get("/api/combustible/gasto")
def combustible_gasto(desde: str = Query(""), hasta: str = Query(""),
                      orden: str = Query("litros"),
                      user: dict = Depends(require_ver_combustible),
                      db: Session = Depends(get_db)) -> dict:
    """El gasto de combustible por CONDUCTOR, con lo que el motor dice frente a lo cargado."""
    cond = [Viaje.lts_real.is_not(None), Viaje.operador_id.is_not(None)]
    for txt, cmp_ in ((desde, "ge"), (hasta, "le")):
        if not txt:
            continue
        try:
            d = datetime.strptime(txt.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        cond.append(Viaje.fecha >= d if cmp_ == "ge" else Viaje.fecha <= d)

    filas = db.execute(
        select(Operador.id, Operador.nombre, Operador.numero,
               func.count(Viaje.id), func.sum(Viaje.lts_real), func.sum(Viaje.kilometros),
               func.sum(Viaje.lts_scaner), func.sum(Viaje.dif),
               func.min(Viaje.fecha), func.max(Viaje.fecha))
        .select_from(Viaje).join(Operador, Operador.id == Viaje.operador_id)
        .where(*cond).group_by(Operador.id, Operador.nombre, Operador.numero)).all()

    out = []
    for f in filas:
        oid, nom, num, n, lts, km, scan, dif, f0, f1 = f
        lts = float(lts or 0); km = float(km or 0); scan = float(scan or 0); dif = float(dif or 0)
        out.append({
            "operador_id": oid, "nombre": nom, "numero": num,
            "viajes": n, "litros": round(lts, 1), "km": round(km, 1),
            "litros_motor": round(scan, 1), "diferencia": round(dif, 1),
            # Qué proporción de lo que el motor quemó se cargó de más. Es la señal, no el
            # total: un conductor que hace más viajes gasta más y eso no dice nada.
            "desvio_pct": (round(100.0 * dif / scan, 2) if scan else None),
            "km_por_litro": (round(km / lts, 3) if lts and km else None),
            "litros_por_viaje": (round(lts / n, 1) if n else None),
            "desde": f0.isoformat() if f0 else None,
            "hasta": f1.isoformat() if f1 else None,
        })
    claves = {"litros": lambda x: -(x["litros"] or 0),
              "diferencia": lambda x: -(x["diferencia"] or 0),
              "desvio": lambda x: -(x["desvio_pct"] if x["desvio_pct"] is not None else -1e9),
              "rendimiento": lambda x: (x["km_por_litro"] if x["km_por_litro"] is not None else 1e9),
              "viajes": lambda x: -(x["viajes"] or 0),
              "nombre": lambda x: (x["nombre"] or "").upper()}
    out.sort(key=claves.get(orden, claves["litros"]))

    tot_l = sum(x["litros"] for x in out)
    tot_k = sum(x["km"] for x in out)
    tot_s = sum(x["litros_motor"] for x in out)
    return {
        "conductores": out,
        "totales": {"conductores": len(out),
                    "viajes": sum(x["viajes"] for x in out),
                    "litros": round(tot_l, 1), "km": round(tot_k, 1),
                    "litros_motor": round(tot_s, 1),
                    "diferencia": round(tot_l - tot_s, 1),
                    "km_por_litro": (round(tot_k / tot_l, 3) if tot_l else None)},
        "fuente": "historial de viajes (viajes.lts_real); NO son las facturas de proveedor",
    }


@app.get("/api/combustible/gasto/viajes")
def combustible_gasto_viajes(operador_id: int = Query(...), limite: int = Query(200, ge=1, le=1000),
                             user: dict = Depends(require_ver_combustible),
                             db: Session = Depends(get_db)) -> dict:
    """Los viajes de un conductor, uno por uno, con su gasto."""
    op = db.get(Operador, operador_id)
    if op is None:
        raise HTTPException(404, "Ese operador no existe")
    filas = db.execute(
        select(Viaje, Unidad.clave)
        .outerjoin(Unidad, Unidad.id == Viaje.unidad_id)
        .where(Viaje.operador_id == operador_id, Viaje.lts_real.is_not(None))
        .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(limite)).all()
    return {
        "operador": {"id": op.id, "nombre": op.nombre, "numero": op.numero},
        "viajes": [{
            "id": v.id, "fecha": v.fecha.isoformat() if v.fecha else None,
            "unidad": clave, "km": v.kilometros, "litros": v.lts_real,
            "litros_motor": v.lts_scaner, "diferencia": v.dif,
            "km_por_litro": v.rto_real, "km_por_litro_motor": v.rto,
            "desvio": v.pct, "odometro": v.odometro, "remolque": v.remolque_thermo,
            "horas_termo": v.horas_termo,
        } for v, clave in filas],
        "stats": _stats_viajes(db, operador_id=operador_id, limite=0),
        "fuente": "historial de viajes (viajes.lts_real)",
    }


@app.get("/api/salud")
def salud() -> dict:
    """Responde que el servidor está vivo. Sin sesión y sin tocar la base.

    Existe para que la app del operador pueda AVERIGUAR si hay conexión en vez de creerle a
    `navigator.onLine`, que sólo sabe si el teléfono tiene una interfaz de red levantada y
    dice que sí en un wifi sin salida o con los datos agotados.

    Va sin autenticación a propósito: si exigiera sesión, una sesión caducada se leería como
    "sin internet". Y no consulta la base porque la pregunta es "¿te llego?", no "¿estás
    sano?": mezclarlas haría que una base caída mandara al operador a modo sin conexión y le
    encolara todo, cuando lo correcto es que vea el error de verdad.
    """
    return {"ok": True}


@app.get("/api/mi-viaje/ruta")
async def mi_viaje_ruta(user: dict = Depends(require_operador),
                        db: Session = Depends(get_db)) -> dict:
    """La ruta por carretera del viaje asignado al operador, para dibujarla en su mapa.

    Existe aparte de `/api/geo/ruta` (que es de gestión) porque el operador no elige las
    coordenadas: salen de SU asignación activa. Así puede ver por dónde va sin que la
    consulta libre de rutas quede abierta a cualquier sesión.
    """
    op_id = _mi_operador_id(user, db)
    if op_id is None:
        raise HTTPException(400, "Tu cuenta no está ligada a un operador del padrón")
    a = db.scalar(
        select(AsignacionViaje)
        .where(AsignacionViaje.operador_id == op_id,
               AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
        .order_by(AsignacionViaje.creada_en.desc()).limit(1))
    if a is None:
        raise HTTPException(404, "No tienes un viaje asignado")
    if a.origen_lat is None or a.destino_lat is None:
        # Sin los dos extremos no hay ruta que trazar, y decirlo es mejor que devolver una
        # recta que el operador tomaría por el camino.
        # El km efectivo, no el del trazo: si el coordinador lo corrigió, el chofer tiene
        # que ver el corregido. Y aquí es además donde más falta hace: sin coordenadas no
        # hubo trazo, así que `km_estimado` suele venir en nulo.
        return {"km": rendimiento.km_efectivo(a), "fuente": None, "puntos": [],
                "origen": a.origen, "destino": a.destino,
                "origen_lat": a.origen_lat, "origen_lng": a.origen_lng,
                "destino_lat": a.destino_lat, "destino_lng": a.destino_lng}
    r = await geo.ruta(a.origen_lat, a.origen_lng, a.destino_lat, a.destino_lng)
    return {**r, "origen": a.origen, "destino": a.destino,
            "origen_lat": a.origen_lat, "origen_lng": a.origen_lng,
            "destino_lat": a.destino_lat, "destino_lng": a.destino_lng}


@app.get("/api/mis-viajes")
def mis_viajes(limit: int = Query(40, ge=1, le=200),
               user: dict = Depends(require_operador), db: Session = Depends(get_db)) -> dict:
    """Historial de viajes del operador: los renglones de la BITÁCORA a su nombre.
    Incluye rendimiento real (km/l), meta y desviación POR VIAJE (vista 'por viaje completo')."""
    op_id = _mi_operador_id(user, db)
    if op_id is None:
        return {"viajes": []}
    q = (select(Viaje).where(Viaje.operador_id == op_id, VIGENTE)
         .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(limit))
    out = []
    _ref_cache: dict[tuple, tuple[float | None, str | None]] = {}

    def _referencia(unidad, config):
        """El rendimiento medido de esa unidad EN ESA CONFIGURACIÓN, o nada.

        NO se cae a la constante. Se memoriza por (unidad, configuración): un operador con
        cien viajes sobre el mismo camión haría cien consultas idénticas de escáneres, pero
        memorizar sólo por unidad le devolvería a un viaje FULL la referencia del SENCILLO.
        """
        if unidad is None:
            return None, None
        clave = (unidad.id, config)
        if clave not in _ref_cache:
            r = rendimiento.vigente(db, unidad, config=config)
            _ref_cache[clave] = ((r.valor, r.motivo) if r.hay else (None, r.motivo))
        return _ref_cache[clave]

    for v in db.execute(q).scalars().all():
        ideal, ref_motivo = _referencia(
            v.unidad, v.tipo_config.value if v.tipo_config else None)
        out.append({
            "id": v.id,
            "fecha": v.fecha.isoformat() if v.fecha else None,
            "unidad": v.unidad.clave if v.unidad else None,
            "kilometros": v.kilometros,
            "lts_real": v.lts_real,
            "rto_real": v.rto_real,
            "rto_ideal": round(ideal, 2) if ideal else None,
            "ref_motivo": ref_motivo,
            "pct": round(v.rto_real / ideal, 3) if (v.rto_real and ideal) else None,
            "tipo": v.tipo_config.value if v.tipo_config else None,
        })
    return {"viajes": out}


@app.get("/api/mis-avisos")
def mis_avisos(user: dict = Depends(require_operador),
               db: Session = Depends(get_db)) -> dict:
    """Lo que el sistema detectó en los viajes de ESTE operador, contado para él.

    No es una lista de penalizaciones: es lo que puede hacer distinto mañana. Por eso van
    sólo las anomalías CONFIRMADAS por una persona (una sin revisar es una sospecha del
    sistema: de las 45 que hay en la base, 40 acabaron rechazadas) y sólo las categorías que
    describen su trabajo. Las de fraude no salen por aquí a propósito — eso lo habla alguien
    con él, no una notificación. La política entera vive en app/avisos.py.
    """
    from . import avisos as _av
    from .models import Anomalia

    op_id = _mi_operador_id(user, db)
    if op_id is None:
        return {"avisos": [], "total": 0}

    filas = db.execute(
        select(Anomalia, Viaje.fecha, Viaje.kilometros, Unidad.clave)
        .join(Viaje, Viaje.id == Anomalia.viaje_id)
        .join(Unidad, Unidad.id == Viaje.unidad_id, isouter=True)
        .where(Viaje.operador_id == op_id,
               Anomalia.estado == "CONFIRMADA",
               VIGENTE)
        .order_by(Viaje.fecha.desc(), Anomalia.id.desc())
        .limit(40)
    ).all()

    salida = []
    for a, fecha, km, clave in filas:
        if not _av.visible_para_operador(a.tipo):
            continue
        d = _av.para_operador(a.tipo)
        d.update({"id": a.id, "fecha": fecha.isoformat() if fecha else None,
                  "unidad": clave, "km": round(km) if km else None})
        salida.append(d)
    return {"avisos": salida, "total": len(salida)}


@app.get("/api/mi-rendimiento")
def mi_rendimiento(user: dict = Depends(require_operador), db: Session = Depends(get_db)) -> dict:
    """Rendimiento del operador POR MES (real vs esperado) + sus recargas del mes. Es la base
    de su vista de desempeño y del mapa de calor."""
    op_id = _mi_operador_id(user, db)
    if op_id is None:
        return {"meses": []}
    vs = db.execute(
        select(Viaje.fecha, Viaje.kilometros, Viaje.rto_real, Viaje.unidad_id,
               Viaje.tipo_config)
        .where(Viaje.operador_id == op_id, VIGENTE, Viaje.fecha.isnot(None))
    ).all()
    # La referencia por (unidad, configuración), una sola vez cada combinación: el mismo
    # camión no rinde igual jalando uno que dos remolques.
    _ref: dict[tuple, float | None] = {}
    for _u, _c in {(u, c.value if c else None) for *_, u, c in vs if u is not None}:
        r = rendimiento.vigente(db, _u, config=_c)
        _ref[(_u, _c)] = r.valor if r.hay else None
    recs = db.execute(
        select(SolicitudRecarga.creada_en, OrdenDespacho.litros_reales, OrdenDespacho.litros_autorizados)
        .join(OrdenDespacho, OrdenDespacho.solicitud_id == SolicitudRecarga.id, isouter=True)
        # Mismo criterio que el historial: no cuenta como recarga del mes lo que no llegó a
        # serlo. El BORRADOR es una captura a medias que el chofer nunca envió —sin folio y
        # sin litros—, y la ANULADA se canceló sin surtir nada: al operador 551 le salía
        # «1 recarga, 0 L» en septiembre, y esa recarga era justo la anulada.
        # Es un número suyo, del que se le acaba preguntando.
        .where(SolicitudRecarga.operador_id == op_id,
               SolicitudRecarga.estado.not_in([EstadoSolicitud.BORRADOR,
                                               EstadoSolicitud.ANULADA]))
    ).all()

    meses: dict[str, dict] = {}

    def M(k):
        return meses.setdefault(k, {"km": 0.0, "viajes": 0, "rto_sum": 0.0, "rto_n": 0,
                                    "ideal_sum": 0.0, "recargas": 0, "litros": 0.0})

    for fecha, km, rto, uid in vs:
        m = M(fecha.strftime("%Y-%m"))
        m["viajes"] += 1
        m["km"] += km or 0
        if rto is not None:
            m["rto_sum"] += rto
            m["rto_n"] += 1
            # Sólo promedian los viajes de unidades CON escáner: mezclar los que no tienen
            # referencia con los que sí daría un promedio que no significa nada.
            ref = _ref.get(uid)
            if ref:
                m["ideal_sum"] += ref
                m["ideal_n"] = m.get("ideal_n", 0) + 1
    for creada, lr, la in recs:
        if creada is None:
            continue
        m = M(creada.strftime("%Y-%m"))
        m["recargas"] += 1
        m["litros"] += (lr if lr is not None else (la or 0)) or 0

    salida = []
    for k in sorted(meses):
        m = meses[k]
        rto_real = round(m["rto_sum"] / m["rto_n"], 3) if m["rto_n"] else None
        n_ref = m.get("ideal_n", 0)
        ideal = round(m["ideal_sum"] / n_ref, 3) if n_ref else None
        salida.append({
            "mes": k, "viajes": m["viajes"], "km": round(m["km"]),
            "rto_real": rto_real, "rto_ideal": ideal,
            "pct": round(rto_real / ideal, 3) if (rto_real and ideal) else None,
            "recargas": m["recargas"], "litros": round(m["litros"]),
        })
    return {"meses": salida[-12:]}   # últimos 12 meses


@app.get("/api/mis-recargas")
def mis_recargas(limit: int = Query(60, ge=1, le=200),
                 user: dict = Depends(require_operador), db: Session = Depends(get_db)) -> dict:
    """Historial de recargas del operador (por viaje): folio, litros, estado y fecha."""
    op_id = _mi_operador_id(user, db)
    if op_id is None:
        return {"recargas": []}
    q = (select(SolicitudRecarga, OrdenDespacho)
         .join(OrdenDespacho, OrdenDespacho.solicitud_id == SolicitudRecarga.id, isouter=True)
         # Este panel es el historial de recargas: lo que SÍ se cargó. Quedan fuera dos
         # estados en los que no salió diésel y que sólo ensuciaban la lista:
         #
         #   BORRADOR  capturas a medias que el chofer nunca envió: sin folio y sin litros.
         #             Son 11 de las 17 solicitudes de la flota —el 64%—, y al operador 551
         #             le dejaban 5 filas vacías contra 1 evento real. Es el rastro del F5.
         #   ANULADA   la solicitud se canceló y no se surtió nada. No desaparece de su
         #             vista: sigue en «Mis solicitudes» con el motivo, y la campana se lo
         #             avisa en el momento. Lo que no es, es una recarga.
         #
         # Las DEVUELTAS y RECHAZADAS sí se quedan, con su estado escrito al lado: a la
         # devuelta todavía le toca hacer algo, y hoy no hay ninguna rechazada en la base.
         .where(SolicitudRecarga.operador_id == op_id,
                SolicitudRecarga.estado.not_in([EstadoSolicitud.BORRADOR,
                                                EstadoSolicitud.ANULADA]))
         .order_by(SolicitudRecarga.creada_en.desc()).limit(limit))
    out = []
    for s, o in db.execute(q).all():
        litros = None
        if o is not None:
            litros = o.litros_reales if o.litros_reales is not None else o.litros_autorizados
        if litros is None:
            litros = s.litros_solicitados
        out.append({
            "id": s.id,
            "folio": o.folio if o is not None else None,
            "estado": s.estado.value,
            "fecha": s.creada_en.isoformat() if s.creada_en else None,
            "litros": litros,
            "unidad": s.unidad.clave if s.unidad else None,
            "viaje_id": s.viaje_id,
        })
    return {"recargas": out}


# ─────────────────────────────────────────────────────────────────────────────
# Facturación y conciliación por folio (Fase 3): el módulo de combustible sube el CFDI del
# proveedor y el sistema lo concilia contra las órdenes despachadas del día.
# ─────────────────────────────────────────────────────────────────────────────
def _factura_dict(f: Factura, db: Session) -> dict:
    ordenes = db.execute(
        select(OrdenDespacho).where(OrdenDespacho.factura_id == f.id)).scalars().all()
    return {
        "id": f.id, "uuid_cfdi": f.uuid_cfdi, "rfc_emisor": f.rfc_emisor,
        "nombre_emisor": f.nombre_emisor, "total": f.total, "subtotal": f.subtotal,
        "moneda": f.moneda, "litros": f.litros, "fecha": f.fecha, "conciliada": f.conciliada,
        "subida_en": f.subida_en.isoformat() if f.subida_en else None,
        "n_ordenes": len(ordenes),
        "folios": [o.folio for o in ordenes],
        "litros_ordenes": round(sum(o.litros_reales or 0 for o in ordenes), 3),
    }


def _candidatas_factura(db: Session) -> list[dict]:
    """Órdenes que un CFDI puede amparar: despachadas y aún sin factura.

    También las que están EN_DISCREPANCIA sin factura. Sin ellas, marcar una discrepancia
    sobre una orden despachada la sacaba de esta lista para siempre: `factura_conciliar` sí
    las acepta, pero ninguna pantalla se las ofrecía a quien concilia, así que la única
    salida con botón la cerraba como conciliada SIN CFDI —y conciliada es final—. La puerta
    existía y no tenía picaporte.
    """
    q = (select(SolicitudRecarga, OrdenDespacho)
         .join(OrdenDespacho, OrdenDespacho.solicitud_id == SolicitudRecarga.id)
         .where(SolicitudRecarga.estado.in_([EstadoSolicitud.DESPACHADA,
                                             EstadoSolicitud.EN_DISCREPANCIA]),
                OrdenDespacho.factura_id.is_(None)))
    return [{
        "solicitud_id": s.id, "folio": o.folio, "litros_reales": o.litros_reales,
        "estado": s.estado.value,
        "unidad": s.unidad.clave if s.unidad else None,
        "operador": s.operador.nombre if s.operador else None,
    } for s, o in db.execute(q).all()]


@app.get("/api/facturas")
def facturas(limit: int = Query(50, ge=1, le=200),
             user: dict = Depends(require_combustible), db: Session = Depends(get_db)) -> list[dict]:
    q = select(Factura).order_by(Factura.subida_en.desc()).limit(limit)
    return [_factura_dict(f, db) for f in db.execute(q).scalars().all()]


@app.post("/api/facturas")
async def factura_subir(file: UploadFile = File(...),
                        user: dict = Depends(require_combustible),
                        db: Session = Depends(get_db)) -> dict:
    """Sube un CFDI (XML), lo lee y lo guarda. Aún NO concilia: devuelve los datos leídos y
    las órdenes despachadas candidatas para que el módulo de combustible elija cuáles ampara."""
    # Se lee un byte más del tope para saber si lo pasa sin cargar un archivo enorme entero.
    raw = await file.read(CFDI_TAMANO_MAX + 1)
    try:
        datos = parse_cfdi(raw)
    except CFDIInvalido as e:
        raise HTTPException(400, str(e))
    # Idempotencia: por el folio fiscal (UUID) y, si el CFDI no viene timbrado, por el hash
    # del propio XML, para que re-subir el mismo archivo no cree una factura duplicada.
    import hashlib
    dedup = datos["uuid"] or ("sin-uuid:" + hashlib.sha256(raw).hexdigest()[:34])
    prev = db.scalar(select(Factura).where(Factura.uuid_cfdi == dedup))
    if prev is not None:
        return {"ok": True, "factura": _factura_dict(prev, db), "duplicada": True,
                "candidatas": _candidatas_factura(db)}
    # Se truncan a lo que aguanta la columna: un CFDI válido puede traer nombre hasta 254.
    fac = Factura(
        uuid_cfdi=dedup, rfc_emisor=(datos["rfc_emisor"] or "")[:20] or None,
        nombre_emisor=(datos["nombre_emisor"] or "")[:200] or None,
        total=datos["total"], subtotal=datos["subtotal"],
        moneda=(datos["moneda"] or "")[:10] or None, litros=datos["litros"],
        fecha=(datos["fecha"] or "")[:40] or None, subida_por_id=user.get("id"))
    db.add(fac)
    db.flush()
    try:
        fdir = settings.media_dir / "facturas"
        fdir.mkdir(parents=True, exist_ok=True)
        p = fdir / f"{fac.id}.xml"
        p.write_bytes(raw)
        fac.xml_path = str(p)
    except Exception:
        log.exception("No se pudo guardar el XML de la factura %s", fac.id)
    db.commit()
    log.info("Factura %s subida (CFDI %s, %s L)", fac.id, fac.uuid_cfdi, fac.litros)
    return {"ok": True, "factura": _factura_dict(fac, db), "conceptos": datos["conceptos"],
            "candidatas": _candidatas_factura(db)}


@app.post("/api/facturas/{fid}/conciliar")
async def factura_conciliar(fid: int, request: Request,
                            user: dict = Depends(require_combustible),
                            db: Session = Depends(get_db)) -> dict:
    """Concilia una factura contra las órdenes despachadas que ampara: si los litros del CFDI
    cuadran con la suma de lo despachado (dentro de tolerancia), pasan a FACTURADA→CONCILIADA;
    si no, quedan EN_DISCREPANCIA para revisión."""
    from .solicitudes import transicionar
    fac = db.get(Factura, fid)
    if fac is None:
        raise HTTPException(404, "Factura no encontrada")
    # Una factura CONCILIADA no se vuelve a conciliar: atribuiría los mismos litros dos veces.
    if fac.conciliada:
        raise HTTPException(409, "Esta factura ya fue conciliada")
    # Pero un intento FALLIDO sí se reintenta. Antes no: el intento dejaba las órdenes ligadas
    # y este mismo guard las tomaba por conciliadas, así que una factura que no cuadró se
    # quedaba «pendiente» de por vida y una discrepancia resuelta a mano no tenía vuelta.
    # Se sueltan las ligaduras del intento anterior — y SÓLO las del lote que se reintenta.
    # Soltarlas todas dejaba huérfana cualquier orden que no viniera en esta petición: sin
    # factura, sin estar en la lista de candidatas y sin ninguna constancia de que lo estuvo.
    f0 = await request.form()
    lote = {int(x) for x in (f0.get("solicitud_ids") or "").replace(" ", "").split(",")
            if x.isdigit()}
    previas = db.execute(select(OrdenDespacho)
                         .where(OrdenDespacho.factura_id == fac.id)).scalars().all()
    fuera = [o for o in previas if o.solicitud_id not in lote]
    if fuera:
        raise HTTPException(409, "Esta factura ya ampara órdenes que no vienen en el "
                                 "reintento (" + ", ".join(o.folio for o in fuera) + "). "
                                 "Inclúyelas o revísalas antes de reintentar.")
    for o in previas:
        s0 = db.get(SolicitudRecarga, o.solicitud_id)
        if s0 is not None and s0.estado not in (EstadoSolicitud.DESPACHADA,
                                                EstadoSolicitud.EN_DISCREPANCIA):
            raise HTTPException(409, f"La orden {o.folio} ya avanzó a "
                                     f"'{s0.estado.value}': esta factura no se puede reintentar.")
        o.factura_id = None
    f = f0
    raw_ids = (f.get("solicitud_ids") or "").replace(" ", "")
    ids = list(dict.fromkeys(int(x) for x in raw_ids.split(",") if x.isdigit()))  # únicos, en orden
    if not ids:
        raise HTTPException(400, "Selecciona al menos una orden despachada")
    sols = []
    for sid in ids:
        s = db.get(SolicitudRecarga, sid)
        # También se acepta una que ya está EN_DISCREPANCIA: es justo el caso del reintento.
        if s is None or s.orden is None or s.estado not in (EstadoSolicitud.DESPACHADA,
                                                            EstadoSolicitud.EN_DISCREPANCIA):
            raise HTTPException(400, f"La solicitud {sid} no está lista para facturar")
        sols.append(s)
    sum_litros = round(sum(s.orden.litros_reales or 0 for s in sols), 3)
    fac_litros = fac.litros or 0.0
    diff = round(fac_litros - sum_litros, 3)
    tol = max(2.0, 0.01 * sum_litros)   # 1% del despacho, con piso de 2 litros
    cuadra = abs(diff) <= tol
    for s in sols:
        s.orden.factura_id = fac.id
        en_disc = s.estado == EstadoSolicitud.EN_DISCREPANCIA
        if cuadra:
            # Desde DESPACHADA se pasa por FACTURADA; desde EN_DISCREPANCIA se va derecho a
            # CONCILIADA, que es la única salida que el grafo deja (no hay discrepancia →
            # facturada). Es el camino del reintento que cuadra a la segunda.
            if not en_disc:
                transicionar(db, s, EstadoSolicitud.FACTURADA, user,
                             nota=f"Facturada · CFDI {fac.uuid_cfdi or fac.id}")
            transicionar(db, s, EstadoSolicitud.CONCILIADA, user,
                         nota=("Conciliada al reintentar: los litros del CFDI ya cuadran"
                               if en_disc else
                               "Conciliada: los litros del CFDI cuadran con lo despachado"))
        elif not en_disc:
            transicionar(db, s, EstadoSolicitud.EN_DISCREPANCIA, user,
                         nota=(f"Discrepancia: CFDI {fac_litros:g} L vs despachado "
                               f"{sum_litros:g} L (dif {diff:+.1f} L)"))
    fac.conciliada = cuadra
    db.commit()
    log.info("Factura %s conciliada=%s (CFDI %s L vs %s L, dif %s)",
             fac.id, cuadra, fac_litros, sum_litros, diff)
    return {"ok": True, "cuadra": cuadra, "litros_factura": fac_litros,
            "litros_ordenes": sum_litros, "diferencia": diff, "tolerancia": round(tol, 2),
            "n": len(sols), "estado": "conciliada" if cuadra else "en_discrepancia"}


@app.get("/api/combustible/rendimiento")
def combustible_rendimiento(user: dict = Depends(require_ver_combustible),
                            db: Session = Depends(get_db)) -> dict:
    """Rendimiento REAL por operador (km/l) contra su OBJETIVO, con alerta cuando cae bajo
    la tolerancia. Agrega los viajes VIGENTES con rto_real capturado. El objetivo de cada
    viaje es el de la unidad si está capturado; si no, el ideal por configuración. El umbral
    se deriva de la tolerancia de la unidad (con signo) o 0.90 por defecto. Ordena por pct
    ascendente (los que peor rinden, primero).

    LA REFERENCIA ES EL ESCÁNER DE LA UNIDAD, no una meta de catálogo. Hasta hoy se caía a
    `RENDIMIENTO_IDEAL` —FULL 1.9, SENCILLO 2.8, THORTON 4.0, igual para las 53 unidades y
    que nadie midió—, y con ese número se le ponía a una PERSONA la etiqueta «bajo
    objetivo» que también ve el gerente. Eran 1,785 de los 2,409 viajes: el 74%.

    Sin escáner NO hay comparación y se dice cuántos quedaron fuera. Un operador al que no
    se puede medir no es un operador que rinda mal."""

    filas = db.execute(
        select(Operador.id, Operador.nombre, Viaje.rto_real, Viaje.unidad_id,
               Unidad.pct_tolerancia, Viaje.tipo_config)
        .join(Viaje, Viaje.operador_id == Operador.id)
        .join(Unidad, Viaje.unidad_id == Unidad.id)
        .where(Viaje.rto_real.isnot(None), VIGENTE)
    ).all()

    # La referencia de cada (unidad, configuración), una sola vez: un operador con cien
    # viajes sobre el mismo camión haría cien consultas idénticas de escáneres, pero la
    # referencia no es la misma jalando uno que dos remolques.
    _ref: dict[tuple, float | None] = {}

    def _referencia(unidad_id: int | None, config: str | None) -> float | None:
        if unidad_id is None:
            return None
        if (unidad_id, config) not in _ref:
            r = rendimiento.vigente(db, unidad_id, config=config)
            _ref[(unidad_id, config)] = r.valor if r.hay else None
        return _ref[(unidad_id, config)]

    ops: dict[int, dict] = {}
    sin_referencia = 0
    for op_id, nombre, rto, unidad_id, tol, _tc in filas:
        objetivo = _referencia(unidad_id, _tc.value if _tc else None)
        if not objetivo:
            # Sin lectura de escáner no hay con qué comparar. Se CUENTA en vez de
            # descartarse en silencio: que falten 1,785 viajes de 2,409 es el dato.
            sin_referencia += 1
            m = ops.setdefault(op_id, {"nombre": nombre, "n": 0, "rto_sum": 0.0,
                                       "obj_sum": 0.0, "umbral_sum": 0.0, "sin_ref": 0})
            m["sin_ref"] += 1
            continue
        # Umbral del viaje: derivado de la tolerancia de la unidad (con signo), o 0.90.
        umbral = (1 - abs(tol)) if tol is not None else 0.90
        m = ops.setdefault(op_id, {"nombre": nombre, "n": 0, "rto_sum": 0.0,
                                   "obj_sum": 0.0, "umbral_sum": 0.0, "sin_ref": 0})
        m["n"] += 1
        m["rto_sum"] += rto
        m["obj_sum"] += objetivo
        m["umbral_sum"] += umbral

    operadores = []
    n_alertas = 0
    for m in ops.values():
        n = m["n"]
        # Un operador puede tener viajes con y sin referencia. Los que no la tienen no
        # entran en el promedio —mezclarlos daría una media que no significa nada— pero
        # sí se dicen, para que su ficha no aparente medir más de lo que mide.
        rto_real = m["rto_sum"] / n if n else None
        objetivo = m["obj_sum"] / n if n else None
        umbral = m["umbral_sum"] / n if n else None
        pct = rto_real / objetivo if (rto_real and objetivo) else None
        alerta = pct is not None and umbral is not None and pct < umbral
        if alerta:
            n_alertas += 1
        operadores.append({
            "operador": m["nombre"],
            "n_viajes": n,
            "n_sin_referencia": m["sin_ref"],
            "rto_real": round(rto_real, 2) if rto_real is not None else None,
            "objetivo": round(objetivo, 2) if objetivo is not None else None,
            "pct": round(pct, 3) if pct is not None else None,
            "umbral": round(umbral, 3) if umbral is not None else None,
            "alerta": alerta,
        })

    operadores.sort(key=lambda o: (o["pct"] is None, o["pct"] if o["pct"] is not None else 0))
    return {"operadores": operadores, "n_alertas": n_alertas,
            "n_operadores": len(operadores),
            # Cuántos viajes se quedaron sin con qué compararse. La pantalla lo dice: un
            # hueco sin explicación se lee como un cero.
            "n_sin_referencia": sin_referencia}


# ─────────────────────────────────────────────────────────────────────────────
# Penalizaciones: descuentos por litros fuera del objetivo. AUTO-SUGERIDOS desde la
# desviación de cada viaje, EDITABLES antes de aplicar, con rastro de quién y cuándo.
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/combustible/penalizaciones")
def penalizaciones(user: dict = Depends(require_combustible),
                   db: Session = Depends(get_db)) -> dict:
    """Sugeridas (viajes VIGENTES con descuentos por litros > 0) y aplicadas (historial de
    SeguimientoDescuento). El descuento se auto-sugiere desde la desviación de cada viaje
    pero es EDITABLE antes de aplicar; cada aplicación deja rastro (ver el POST).

    LOS LITROS DE LA PENALIZACIÓN NO SE TOCAN: salen de `viaje.dif` —lo cargado menos lo
    que el escáner dice que se quemó—, con la tolerancia de cada unidad. Es una cantidad
    medida. Lo que sí se corrige es el «objetivo» que se pintaba AL LADO, que salía del
    catálogo retirado y engaña a quien decide si aplicarla."""
    # Viajes YA penalizados desde el panel (llevan viaje_id): no se vuelven a sugerir. Las
    # filas del Excel histórico tienen viaje_id NULL y no participan en la deduplicación.
    penalizados = {vid for (vid,) in db.execute(
        select(SeguimientoDescuento.viaje_id).where(SeguimientoDescuento.viaje_id.isnot(None))
    ).all()}
    q = (select(Viaje).where(VIGENTE, Viaje.descuentos.isnot(None), Viaje.descuentos > 0)
         .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(300))
    sugeridas = []
    for v in db.execute(q).scalars().all():
        if v.id in penalizados:
            continue   # ese viaje ya se penalizó
        # La referencia del ESCÁNER de esa unidad. MISMA regla que /api/mis-viajes y que
        # /api/combustible/rendimiento: una sola vara. Donde no hay escáner no se inventa
        # ninguna, y la fila sale sin comparación.
        _r = (rendimiento.vigente(
            db, v.unidad_id, config=v.tipo_config.value if v.tipo_config else None)
              if v.unidad_id else None)
        objetivo = _r.valor if (_r is not None and _r.hay) else None
        sugeridas.append({
            "viaje_id": v.id,
            "fecha": v.fecha.isoformat() if v.fecha else None,
            "operador": v.operador.nombre if v.operador else None,
            "unidad": v.unidad.clave if v.unidad else None,
            "litros_sugeridos": round(v.descuentos, 2) if v.descuentos is not None else None,
            "rto_real": v.rto_real,
            "objetivo": round(objetivo, 2) if objetivo else None,
            "pct": round(v.rto_real / objetivo, 3) if (v.rto_real and objetivo) else v.pct,
        })
        if len(sugeridas) >= 200:
            break
    # Aplicadas: el historial de descuentos, con QUIÉN y CUÁNDO cuando vino del panel.
    ap = db.execute(
        select(SeguimientoDescuento).order_by(SeguimientoDescuento.id.desc()).limit(200)
    ).scalars().all()
    ids = [s.aplicada_por_id for s in ap if s.aplicada_por_id]
    quien = {u.id: (u.nombre or u.username) for u in db.execute(
        select(Usuario).where(Usuario.id.in_(ids))).scalars().all()} if ids else {}
    aplicadas = [{
        "id": s.id,
        "fecha": s.fecha.isoformat() if s.fecha else None,
        "unidad": s.unidad_clave,
        "operador": s.operador_nombre,
        "lts": s.lts,
        "tipo": s.tipo,
        "area": s.area,
        "aplicada_por": quien.get(s.aplicada_por_id) if s.aplicada_por_id else None,
        "aplicada_en": s.aplicada_en.isoformat() if s.aplicada_en else None,
    } for s in ap]
    return {"sugeridas": sugeridas, "aplicadas": aplicadas,
            "n_sugeridas": len(sugeridas), "n_aplicadas": len(aplicadas)}


@app.post("/api/combustible/penalizaciones")
async def penalizacion_aplicar(request: Request,
                               user: dict = Depends(require_combustible),
                               db: Session = Depends(get_db)) -> dict:
    """Aplica una penalización: crea un SeguimientoDescuento con los litros EDITADOS por el
    usuario (llegan del body). Unidad y operador se derivan del viaje —no de lo que mande el
    cliente— para que el rastro apunte a datos reales.

    RASTRO: se registra quién aplicó (aplicada_por_id), cuándo (aplicada_en) y el viaje que
    la originó (viaje_id). El viaje_id además impide penalizar dos veces el mismo viaje."""
    f = await request.form()
    raw_vid = f.get("viaje_id")
    viaje_id = int(raw_vid) if raw_vid and str(raw_vid).isdigit() else None
    lts = _pf(f.get("lts"))
    if lts is None or lts <= 0:
        raise HTTPException(400, "Indica los litros a descontar")
    v = db.get(Viaje, viaje_id) if viaje_id else None
    if v is None:
        raise HTTPException(404, "Viaje no encontrado")
    if db.scalar(select(SeguimientoDescuento.id)
                 .where(SeguimientoDescuento.viaje_id == v.id).limit(1)):
        raise HTTPException(409, "Ese viaje ya tiene una penalización aplicada")
    tipo = ((f.get("tipo") or "").strip() or "TRACTOR")[:20]   # descuento de diésel del motor
    aplicador = user.get("nombre") or user.get("username") or f"usuario {user.get('id')}"
    seg = SeguimientoDescuento(
        fecha=fecha_flota(),
        unidad_clave=(v.unidad.clave if v.unidad else None),
        tipo=tipo,
        operador_nombre=(v.operador.nombre if v.operador else None),
        lts=lts,
        contesta=None,
        area=f"Combustible · {aplicador}"[:60],
        aplicada_por_id=user.get("id"),
        aplicada_en=datetime.now(timezone.utc),
        viaje_id=v.id,
    )
    db.add(seg)
    try:
        db.commit()
    except IntegrityError:
        # Dos clics a la vez: los dos pasaron el `if` de arriba y el índice único
        # ux_seguimiento_descuentos_viaje detuvo al segundo.
        db.rollback()
        raise HTTPException(409, "Ese viaje ya tiene una penalización aplicada")
    log.info("Penalización aplicada: viaje %s, %s L, unidad %s, por %s",
             viaje_id, lts, seg.unidad_clave, aplicador)
    return {"ok": True, "id": seg.id, "lts": lts, "unidad": seg.unidad_clave,
            "operador": seg.operador_nombre, "tipo": seg.tipo,
            "fecha": seg.fecha.isoformat() if seg.fecha else None, "area": seg.area}


@app.get("/api/combustible/motor")
def combustible_motor(
    user: dict = Depends(require_ver_combustible), db: Session = Depends(get_db),
) -> dict:
    """Lecturas recientes de la computadora del motor (Cummins/Detroit).

    Cada EscaneoMotor cubre un PERÍODO cerrado de la unidad (no un viaje): es la verdad
    "del carro". Se marca alerta cuando el período rinde bastante menos de lo que esa
    misma unidad venía haciendo, o cuando quemó parado una parte grande de su diésel.
    Devuelve los más recientes primero, limitado a ~50.
    """
    # Se cargan TODAS y se agrupa por unidad, aunque sólo se devuelvan 50: la alerta juzga
    # cada lectura contra la serie de su unidad, y con 50 filas sueltas no hay serie.
    todos = db.execute(select(EscaneoMotor)).scalars().all()
    por_unidad: dict[int, list] = {}
    for e in todos:
        por_unidad.setdefault(e.unidad_id, []).append(e)
    alertas: dict[int, dict] = {}
    for uid, serie in por_unidad.items():
        alertas.update(alertas_de_serie(serie, serie[0].unidad if serie else None))

    escaneos = sorted((e for e in todos if e.periodo_fin),
                      key=lambda e: (e.periodo_fin, e.id), reverse=True)[:50]
    filas, n_alertas = [], 0
    for e in escaneos:
        objetivo = e.unidad.rendimiento_objetivo if e.unidad else None
        a = alertas.get(e.id, {})
        rend_bajo, ralenti_alto = a.get("rend_bajo", False), a.get("ralenti_alto", False)
        alerta = bool(a.get("alerta"))
        if alerta:
            n_alertas += 1
        filas.append({
            "id": e.id,
            "unidad": e.unidad.clave if e.unidad else None,
            "formato": e.formato,
            "periodo_fin": e.periodo_fin.isoformat() if e.periodo_fin else None,
            "km": e.km,
            "litros": e.litros,
            "rendimiento": e.rendimiento,
            "objetivo": objetivo,
            "pct_ralenti": e.pct_ralenti,
            "lts_ralenti": e.lts_ralenti,
            "analisis": e.analisis,
            "alerta": alerta,
            "rend_bajo": rend_bajo,
            "ralenti_alto": ralenti_alto,
            # Los motivos EN PALABRAS. «Alerta» a secas obliga a ir a buscar por qué.
            "motivos": a.get("motivos", []),
            "referencia": a.get("referencia"),
            "medible": a.get("medible", False),
        })
    return {"escaneos": filas, "n_alertas": n_alertas}


# ── Comparativa mensual: recargas (despachos) vs facturas del proveedor ──────────
# El módulo de combustible SOLO consulta la comparativa y SOLICITA el análisis; la IA lo
# genera y aparece en los paneles de Admin y Gerencia (no aquí).
@app.get("/api/combustible/facturas-mensual")
def combustible_facturas_mensual(user: dict = Depends(require_combustible),
                                 db: Session = Depends(get_db)) -> dict:
    """Agrega POR MES los litros despachados (recargas) contra los amparados por CFDI.

    Devuelve los últimos ~12 meses en orden cronológico, cada uno con litros_despachados,
    litros_proveedor, dif (despachado − cobrado), n_ordenes, n_cargas y el precio real."""
    # Despachos por mes: por la fecha de despacho; si aún no se despacha, la de autorización.
    mes_od = func.to_char(
        func.coalesce(OrdenDespacho.despachada_en, OrdenDespacho.autorizada_en), "YYYY-MM")
    despachos = db.execute(
        select(mes_od,
               func.round(func.sum(OrdenDespacho.litros_reales).cast(Numeric), 3),
               func.count(OrdenDespacho.id))
        .group_by(mes_od)
    ).all()
    # Lo que el proveedor COBRÓ ese mes, de su propio archivo. Antes esto eran los litros
    # amparados por CFDI: con cero facturas en la base la columna iba siempre en 0 y la
    # diferencia era todo lo despachado. Un control que siempre acusa no controla.
    mes_pv = func.to_char(CargaProveedor.fecha_operacion, "YYYY-MM")
    proveedor_mes = db.execute(
        select(mes_pv,
               func.round(func.sum(CargaProveedor.litros).cast(Numeric), 3),
               func.count(CargaProveedor.id),
               func.round(func.sum(CargaProveedor.importe).cast(Numeric), 2))
        .where(CargaProveedor.vigente.is_(True))
        .group_by(mes_pv)
    ).all()

    acc: dict[str, dict] = {}

    def _mes(m):
        return acc.setdefault(m, {"mes": m, "litros_despachados": 0.0,
                                  "litros_proveedor": 0.0, "importe_proveedor": 0.0,
                                  "n_ordenes": 0, "n_cargas": 0})

    for m, litros, n in despachos:
        if not m:
            continue
        d = _mes(m)
        d["litros_despachados"] = float(litros or 0)
        d["n_ordenes"] = int(n or 0)
    for m, litros, n, importe in proveedor_mes:
        if not m:
            continue
        d = _mes(m)
        d["litros_proveedor"] = float(litros or 0)
        d["importe_proveedor"] = float(importe or 0)
        d["n_cargas"] = int(n or 0)

    meses = sorted(acc.values(), key=lambda x: x["mes"])[-12:]
    for d in meses:
        d["dif"] = round(d["litros_despachados"] - d["litros_proveedor"], 3)
        # El precio REAL del litro ese mes, del propio archivo del proveedor. Es la única
        # cifra de precio que no es una suposición: `settings.precio_litro` es un número
        # de configuración, esto es lo que se pagó.
        d["precio_real"] = (round(d["importe_proveedor"] / d["litros_proveedor"], 4)
                            if d["litros_proveedor"] else None)
    return {"meses": meses, "precio_config": precio_del_litro()}


@app.post("/api/combustible/solicitar-analisis-facturas")
def combustible_solicitar_analisis_facturas(user: dict = Depends(require_ver_combustible),
                                            db: Session = Depends(get_db)) -> dict:
    """Genera el análisis IA de la comparativa mensual recargas-vs-proveedor y lo guarda.
    Combustible lo solicita; el RESULTADO se muestra en los paneles de Admin y Gerencia."""
    mes_od = func.to_char(
        func.coalesce(OrdenDespacho.despachada_en, OrdenDespacho.autorizada_en), "YYYY-MM")
    desp = db.execute(select(mes_od, func.sum(OrdenDespacho.litros_reales)).group_by(mes_od)).all()
    mes_pv = func.to_char(CargaProveedor.fecha_operacion, "YYYY-MM")
    pv = db.execute(select(mes_pv, func.sum(CargaProveedor.litros))
                    .where(CargaProveedor.vigente.is_(True)).group_by(mes_pv)).all()
    acc: dict[str, dict] = {}
    for m, l in desp:
        if m:
            acc.setdefault(m, {"mes": m, "recargas_l": 0.0, "proveedor_l": 0.0})["recargas_l"] = float(l or 0)
    for m, l in pv:
        if m:
            acc.setdefault(m, {"mes": m, "recargas_l": 0.0, "proveedor_l": 0.0})["proveedor_l"] = float(l or 0)
    meses = sorted(acc.values(), key=lambda x: x["mes"])[-12:]
    for d in meses:
        d["dif"] = round(d["recargas_l"] - d["proveedor_l"], 2)
    if not meses:
        raise HTTPException(400, "Aún no hay recargas ni cargas del proveedor para analizar.")
    resumen = {"tipo": "consumo", "meses": meses}
    texto = ai.analizar_facturas(resumen)
    rep = AnalisisReporte(texto=texto, resumen=resumen)
    db.add(rep)
    db.commit()
    log.info("Análisis IA de facturas generado (rep %s) por %s", rep.id, user.get("username"))
    return {"ok": True, "id": rep.id}


@app.get("/api/analisis-facturas")
def analisis_facturas_ultimo(user: dict = Depends(require_ver_combustible),
                             db: Session = Depends(get_db)) -> dict:
    """El último análisis IA de recargas-vs-facturas (para mostrar en Admin y Gerencia)."""
    r = db.execute(
        # `consumo`, no `facturas`: la comparativa cambió de fuente. Había 0 análisis
        # guardados, así que no hay nada que migrar.
        select(AnalisisReporte).where(AnalisisReporte.resumen.op("->>")("tipo") == "consumo")
        .order_by(AnalisisReporte.id.desc()).limit(1)
    ).scalar_one_or_none()
    if r is None:
        return {"analisis": None}
    return {"id": r.id, "analisis": r.texto,
            "creado_en": r.creado_en.isoformat() if r.creado_en else None,
            "meses": (r.resumen or {}).get("meses", [])}


def _export_fecha(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _export_bloques(db, sel, d0, d1):
    """Arma los bloques de reporte seleccionados (compartido por Excel y la vista imprimible)."""
    vf = [VIGENTE]
    if d0:
        vf.append(Viaje.fecha >= d0)
    if d1:
        vf.append(Viaje.fecha <= d1)
    bloques = []
    if "general" in sel:
        nv = db.scalar(select(func.count()).select_from(Viaje).where(*vf)) or 0
        km = float(db.scalar(select(func.coalesce(func.sum(Viaje.kilometros), 0.0)).where(*vf)) or 0)
        lts = float(db.scalar(select(func.coalesce(func.sum(Viaje.lts_real), 0.0)).where(*vf)) or 0)
        anoms = db.scalar(select(func.count()).select_from(Anomalia)
                          .where(Anomalia.estado == EstadoAnomalia.PENDIENTE)) or 0
        bloques.append({"tipo": "general", "titulo": "Resumen general",
                        "columnas": ["Indicador", "Valor"],
                        "filas": [["Viajes", nv], ["Kilómetros", round(km)], ["Litros", round(lts)],
                                  ["Rendimiento global km/l", round(km / lts, 2) if lts else 0],
                                  ["Anomalías pendientes", anoms]]})
    if "rendimiento" in sel:
        rows = []
        for nombre, nvj, rto in db.execute(
            select(Operador.nombre, func.count(Viaje.id), func.avg(Viaje.rto_real))
            .join(Viaje, Viaje.operador_id == Operador.id)
            .where(*vf, Viaje.rto_real.isnot(None)).group_by(Operador.id, Operador.nombre)
        ).all():
            rows.append([nombre, nvj, round(float(rto), 2) if rto is not None else None])
        rows.sort(key=lambda r: (r[2] is None, r[2] or 0))
        bloques.append({"tipo": "rendimiento", "titulo": "Rendimiento por operador",
                        "columnas": ["Operador", "Viajes", "km/l real"], "filas": rows})
    if "anomalias" in sel:
        af = []
        if d0:
            af.append(Anomalia.creado_en >= d0)
        if d1:
            af.append(Anomalia.creado_en <= d1)
        rows = []
        for a in db.execute(select(Anomalia).where(*af)
                            .order_by(Anomalia.creado_en.desc()).limit(2000)).scalars().all():
            uni = a.viaje.unidad.clave if (a.viaje and a.viaje.unidad) else "—"
            rows.append([a.creado_en.strftime("%Y-%m-%d") if a.creado_en else "", uni,
                         a.tipo, a.descripcion, a.estado.value])
        bloques.append({"tipo": "anomalias", "titulo": "Anomalías",
                        "columnas": ["Fecha", "Unidad", "Tipo", "Descripción", "Estado"], "filas": rows})
    if "operadores" in sel:
        rows = []
        for nombre, numero, nvj, km, lts, rto in db.execute(
            select(Operador.nombre, Operador.numero, func.count(Viaje.id),
                   func.coalesce(func.sum(Viaje.kilometros), 0.0),
                   func.coalesce(func.sum(Viaje.lts_real), 0.0), func.avg(Viaje.rto_real))
            .join(Viaje, Viaje.operador_id == Operador.id)
            .where(*vf).group_by(Operador.id, Operador.nombre, Operador.numero)
        ).all():
            rows.append([nombre, numero, nvj, round(float(km or 0)), round(float(lts or 0)),
                         round(float(rto), 2) if rto is not None else None])
        rows.sort(key=lambda r: r[2], reverse=True)
        bloques.append({"tipo": "operadores", "titulo": "Operadores",
                        "columnas": ["Operador", "N°", "Viajes", "Km", "Litros", "km/l"], "filas": rows})
    return bloques


@app.get("/api/export.json")
def export_json(tipos: str = "general", desde: str | None = None, hasta: str | None = None,
                user: dict = Depends(require_gestion), db: Session = Depends(get_db)) -> dict:
    """Datos de los reportes seleccionados (para la vista imprimible / PDF del navegador)."""
    sel = [t.strip() for t in tipos.split(",") if t.strip()]
    return {"bloques": _export_bloques(db, sel, _export_fecha(desde or ""), _export_fecha(hasta or "")),
            "desde": desde, "hasta": hasta}


@app.get("/api/export.xlsx")
def export_xlsx(tipos: str = "general", desde: str | None = None, hasta: str | None = None,
                user: dict = Depends(require_gestion), db: Session = Depends(get_db)):
    """Descarga los reportes seleccionados como un Excel con una hoja por reporte."""
    import io
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Font
    from fastapi.responses import StreamingResponse
    sel = [t.strip() for t in tipos.split(",") if t.strip()]
    bloques = _export_bloques(db, sel, _export_fecha(desde or ""), _export_fecha(hasta or ""))
    # Reportes con una columna numérica clara: (columna a graficar, título del gráfico). Se
    # dibuja una gráfica de barras NATIVA de Excel para que el archivo llegue "con gráficos".
    GRAFICABLES = {"rendimiento": (3, "km/l real por operador"),
                   "operadores": (3, "Viajes por operador")}
    wb = Workbook()
    wb.remove(wb.active)
    if not bloques:
        wb.create_sheet("Vacío")
    for b in bloques:
        ws = wb.create_sheet(b["titulo"][:31])
        ws.append(b["columnas"])
        for c in ws[1]:
            c.font = Font(bold=True)
        for fila in b["filas"]:
            ws.append(["" if v is None else v for v in fila])
        for col in ws.columns:
            w = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(48, max(10, w + 2))
        # Gráfico nativo (barras) sobre las primeras 25 filas del reporte, si aplica.
        graf = GRAFICABLES.get(b["tipo"])
        if graf and b["filas"]:
            val_col, titulo = graf
            maxr = min(len(b["filas"]) + 1, 26)   # +1 por el encabezado; tope 25 filas
            ch = BarChart()
            ch.type = "bar"
            ch.title = titulo
            ch.legend = None
            ch.height = max(7, min(20, maxr * 0.5))
            ch.width = 16
            ch.add_data(Reference(ws, min_col=val_col, min_row=1, max_row=maxr),
                        titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=maxr))
            ancla = chr(ord("A") + len(b["columnas"]) + 1)   # una columna a la derecha de la tabla
            ws.add_chart(ch, f"{ancla}2")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="reporte_2day.xlsx"'})


@app.get("/api/costos")
def costos(user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Rendimiento de COSTOS: cruza el dinero (facturas del proveedor) con el consumo (litros
    y km de la bitácora). El costo por litro es el PROMEDIO de las facturas y se aplica al
    consumo para estimar el costo por flota y por operador."""
    n_fac = db.scalar(select(func.count()).select_from(Factura)) or 0
    total_mxn = float(db.scalar(select(func.coalesce(func.sum(Factura.total), 0.0))) or 0)
    litros_fac = float(db.scalar(select(func.coalesce(func.sum(Factura.litros), 0.0))) or 0)
    conciliadas = db.scalar(select(func.count()).select_from(Factura)
                            .where(Factura.conciliada.is_(True))) or 0
    costo_litro = round(total_mxn / litros_fac, 3) if litros_fac else 0.0
    n_ord = db.scalar(select(func.count()).select_from(OrdenDespacho)) or 0
    litros_desp = float(db.scalar(
        select(func.coalesce(func.sum(OrdenDespacho.litros_reales), 0.0))) or 0)

    # Por flota (tipo de unidad): consumo, rendimiento y costo estimado.
    flota = []
    for tipo, nv, km, lts, rto in db.execute(
        select(Unidad.tipo, func.count(Viaje.id),
               func.coalesce(func.sum(Viaje.kilometros), 0.0),
               func.coalesce(func.sum(Viaje.lts_real), 0.0), func.avg(Viaje.rto_real))
        .join(Viaje, Viaje.unidad_id == Unidad.id)
        .where(VIGENTE, Viaje.lts_real > 0).group_by(Unidad.tipo)
    ).all():
        km = float(km or 0); lts = float(lts or 0); costo = round(lts * costo_litro, 2)
        flota.append({"flota": tipo.value if tipo else "—", "viajes": nv,
                      "km": round(km), "litros": round(lts),
                      "rto_real": round(float(rto), 2) if rto is not None else None,
                      "costo": costo, "costo_km": round(costo / km, 2) if km else None})

    # Por operador (mayor costo estimado primero).
    operadores = []
    for nombre, nv, km, lts, rto in db.execute(
        select(Operador.nombre, func.count(Viaje.id),
               func.coalesce(func.sum(Viaje.kilometros), 0.0),
               func.coalesce(func.sum(Viaje.lts_real), 0.0), func.avg(Viaje.rto_real))
        .join(Viaje, Viaje.operador_id == Operador.id)
        .where(VIGENTE, Viaje.lts_real > 0).group_by(Operador.id, Operador.nombre)
    ).all():
        lts = float(lts or 0)
        operadores.append({"operador": nombre, "viajes": nv, "km": round(float(km or 0)),
                           "litros": round(lts), "costo": round(lts * costo_litro, 2),
                           "rto_real": round(float(rto), 2) if rto is not None else None})
    operadores.sort(key=lambda o: o["costo"], reverse=True)

    # Costos por mes (facturas del proveedor).
    mes_fa = func.to_char(Factura.subida_en, "YYYY-MM")
    meses = []
    for m, t, l in db.execute(
        select(mes_fa, func.sum(Factura.total), func.sum(Factura.litros)).group_by(mes_fa)
    ).all():
        if not m:
            continue
        t = float(t or 0); l = float(l or 0)
        meses.append({"mes": m, "total": round(t, 2), "litros": round(l),
                      "costo_litro": round(t / l, 3) if l else None})
    meses.sort(key=lambda x: x["mes"])

    return {
        "costo_litro": costo_litro,
        "facturas": {"n": n_fac, "total_mxn": round(total_mxn, 2), "litros": round(litros_fac),
                     "conciliadas": conciliadas, "pendientes": n_fac - conciliadas},
        "recargas": {"n_ordenes": n_ord, "litros_desp": round(litros_desp)},
        "flota": flota, "operadores": operadores[:20], "meses": meses[-12:],
    }


@app.get("/api/kpis")
def kpis(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    total_viajes = db.scalar(select(func.count()).select_from(Viaje).where(VIGENTE))
    unidades = db.scalar(select(func.count()).select_from(Unidad).where(Unidad.activo.is_(True)))
    operadores = db.scalar(select(func.count()).select_from(Operador))
    pendientes = db.scalar(
        select(func.count()).select_from(Anomalia).where(Anomalia.estado == EstadoAnomalia.PENDIENTE)
    )
    # Rendimiento de la flota: kilómetros totales entre litros totales, la MISMA definición
    # del reporte ejecutivo. Antes era `avg(rto_real)` —el promedio de los rendimientos de
    # cada viaje, sin ponderar por tamaño— y daba 3.03 km/L mientras el reporte daba 2.24
    # con los mismos datos: una pantalla decía que la flota superaba el ideal de 2.8 y la
    # de al lado le facturaba millones por ir por debajo. Un viaje de 50 km pesaba igual
    # que uno de 5,000.
    from .reporte import _plausible

    rto_prom = db.scalar(
        select(func.round((func.sum(Viaje.kilometros) / func.nullif(func.sum(Viaje.lts_real), 0))
                          .cast(Numeric), 3))
        .where(VIGENTE, Viaje.kilometros > 0, Viaje.lts_real > 0, _plausible()))
    lts_total = db.scalar(
        select(func.round(func.sum(Viaje.lts_real).cast(Numeric), 0)).where(VIGENTE))
    retractados = db.scalar(
        select(func.count()).select_from(Viaje).where(Viaje.retractado_en.isnot(None)))
    por_tipo = {
        t.value: db.scalar(select(func.count()).select_from(Unidad).where(Unidad.tipo == t))
        for t in TipoUnidad
    }
    return {
        "total_viajes": total_viajes, "unidades": unidades, "operadores": operadores,
        "anomalias_pendientes": pendientes, "rendimiento_promedio": float(rto_prom or 0),
        "litros_totales": float(lts_total or 0), "unidades_por_tipo": por_tipo,
        # Se informa aparte en vez de esconderlo: si hay reportes retirados, hay que verlo.
        "viajes_retractados": int(retractados or 0),
    }


@app.get("/api/resumen-admin")
def resumen_admin(periodo: str = Query("mes"),
                  user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Cockpit ejecutivo del Admin: un panorama de TODO el sistema en una sola llamada.

    `periodo` (dia|semana|mes|anio, default mes) define la ventana de calendario EN CURSO para
    las métricas de ACTIVIDAD (consumo, dinero, penalizaciones, IA, viajes asignados/reportados).
    Las métricas de ESTADO ACTUAL (bandeja pendiente, viajes en curso, flota, anomalías/sugeridas
    pendientes) y el bloque `global` (histórico) NO se filtran: son "ahora", no del periodo.
    Solo lectura; admin-only porque expone dinero facturado (Factura.total) y gasto de IA. Cada
    número reusa la MISMA definición de su endpoint fuente (ver comentarios)."""
    from .reporte import _plausible
    from .models import UsoIA

    hoy = fecha_flota()   # fecha EN HORA DE LA FLOTA (el VPS corre en UTC; Viaje.fecha es local)
    if periodo == "dia":
        ini = hoy
    elif periodo == "semana":
        ini = hoy - timedelta(days=hoy.weekday())      # lunes de la semana en curso
    elif periodo == "anio":
        ini = hoy.replace(month=1, day=1)
    else:
        periodo, ini = "mes", hoy.replace(day=1)
    ini_dt = datetime(ini.year, ini.month, ini.day, tzinfo=_tz())  # medianoche LOCAL como instante

    # ── A · Operación (en curso = snapshot; asignados/finalizados = del periodo) ─────────────
    en_curso = db.scalar(select(func.count()).select_from(AsignacionViaje)
                         .where(AsignacionViaje.estado == EstadoAsignacion.ACTIVA)) or 0
    en_ruta = db.scalar(select(func.count(func.distinct(AsignacionViaje.operador_id)))
                        .where(AsignacionViaje.estado == EstadoAsignacion.ACTIVA)) or 0
    asignados = db.scalar(select(func.count()).select_from(AsignacionViaje)
                          .where(AsignacionViaje.creada_en >= ini_dt)) or 0
    finalizados = db.scalar(select(func.count()).select_from(AsignacionViaje)
                            .where(AsignacionViaje.estado == EstadoAsignacion.FINALIZADA,
                                   AsignacionViaje.finalizada_en >= ini_dt)) or 0

    # ── B · Bandeja de trabajo (máquina de estados; SNAPSHOT: pendiente AHORA, no del periodo) ─
    # group_by solo trae estados con filas; sembramos los 11 del enum para que nunca falten.
    _crudos = dict(db.execute(select(SolicitudRecarga.estado, func.count(SolicitudRecarga.id))
                              .group_by(SolicitudRecarga.estado)).all())
    por_estado = {e.value: int(_crudos.get(e, 0)) for e in EstadoSolicitud}
    # "Quién tiene la pelota" — derivado del flujo (interpretación, no una columna en BD).
    por_rol = {
        "operador": por_estado["devuelta"],
        "coordinador": por_estado["enviada"] + por_estado["en_validacion"],
        "combustible": por_estado["autorizada"] + por_estado["despachada"],
        "revision": por_estado["en_discrepancia"] + por_estado["facturada"],
    }
    esperan_total = sum(por_rol.values())
    # Órdenes despachadas aún sin CFDI ligado (backlog de facturación).
    por_facturar = db.scalar(
        select(func.count(OrdenDespacho.id))
        .join(SolicitudRecarga, OrdenDespacho.solicitud_id == SolicitudRecarga.id)
        .where(SolicitudRecarga.estado == EstadoSolicitud.DESPACHADA,
               OrdenDespacho.factura_id.is_(None))) or 0

    # ── C · Combustible & dinero (del PERIODO) ──────────────────────────────────────────────
    litros = float(db.scalar(select(func.coalesce(func.sum(Viaje.lts_real), 0.0))
                             .where(VIGENTE, Viaje.fecha >= ini)) or 0)
    km = float(db.scalar(select(func.coalesce(func.sum(Viaje.kilometros), 0.0))
                         .where(VIGENTE, Viaje.fecha >= ini)) or 0)
    # Rendimiento PONDERADO sum(km)/sum(lts) — la definición oficial de /api/kpis (no avg simple).
    rto_p = db.scalar(select(func.round(
        (func.sum(Viaje.kilometros) / func.nullif(func.sum(Viaje.lts_real), 0)).cast(Numeric), 3))
        .where(VIGENTE, Viaje.fecha >= ini, Viaje.kilometros > 0, Viaje.lts_real > 0, _plausible()))
    rendimiento = float(rto_p or 0)
    # Dinero: el ÚNICO que existe vive en Factura.total (CFDI del proveedor, MXN). El periodo se
    # filtra por subida_en (Factura.fecha es texto libre del CFDI y no siempre parsea).
    gasto_facturado = float(db.scalar(
        select(func.coalesce(func.sum(Factura.total), 0.0))
        .where(Factura.subida_en >= ini_dt)) or 0)
    _total_fac = float(db.scalar(select(func.coalesce(func.sum(Factura.total), 0.0))) or 0)
    _litros_fac = float(db.scalar(select(func.coalesce(func.sum(Factura.litros), 0.0))) or 0)
    costo_litro = round(_total_fac / _litros_fac, 3) if _litros_fac else 0.0
    gasto_estimado = round(litros * costo_litro, 2)   # estimación: litros del periodo × $/L global
    # Contexto histórico (siempre poblado; el periodo en curso puede venir en 0 si aún no hay
    # viajes). Misma definición que /api/kpis para que cuadre con esa pantalla.
    total_viajes = db.scalar(select(func.count()).select_from(Viaje).where(VIGENTE)) or 0
    litros_hist = float(db.scalar(select(func.coalesce(func.sum(Viaje.lts_real), 0.0)).where(VIGENTE)) or 0)
    rto_global = db.scalar(select(func.round(
        (func.sum(Viaje.kilometros) / func.nullif(func.sum(Viaje.lts_real), 0)).cast(Numeric), 3))
        .where(VIGENTE, Viaje.kilometros > 0, Viaje.lts_real > 0, _plausible()))

    # ── D · Facturación & conciliación (SNAPSHOT del estado de conciliación) ─────────────────
    fac_conc = db.scalar(select(func.count(Factura.id)).where(Factura.conciliada.is_(True))) or 0
    fac_pend = db.scalar(select(func.count(Factura.id)).where(Factura.conciliada.is_(False))) or 0

    # ── E · Cumplimiento (del periodo) & penalizaciones (aplicadas del periodo; sugeridas = snapshot) ─
    # "Completo" = trae odómetro Y nivel de tanque. Es COMPLETITUD, no "a tiempo" (ese dato no existe).
    _completo = case((and_(Viaje.odometro.isnot(None), Viaje.nivel_tanque.isnot(None)), 1), else_=0)
    _rc = db.execute(select(func.count(Viaje.id), func.coalesce(func.sum(_completo), 0))
                     .where(Viaje.reportado_por.isnot(None), VIGENTE, Viaje.fecha >= ini)).one()
    reportados, cumplen = int(_rc[0] or 0), int(_rc[1] or 0)
    pct_cumplimiento = round(cumplen / reportados, 3) if reportados else None
    _pm = db.execute(select(func.count(SeguimientoDescuento.id),
                            func.coalesce(func.sum(SeguimientoDescuento.lts), 0.0))
                     .where(SeguimientoDescuento.fecha >= ini)).one()
    pen_n, pen_lts = int(_pm[0] or 0), float(_pm[1] or 0)
    # Descuentos SUGERIDOS por viajes con descuentos>0 que aún no se penalizan (dedup en Python
    # contra los viaje_id ya aplicados, igual que /api/combustible/penalizaciones). SNAPSHOT.
    _penalizados = {vid for (vid,) in db.execute(
        select(SeguimientoDescuento.viaje_id).where(SeguimientoDescuento.viaje_id.isnot(None))).all()}
    _sq = select(func.count(Viaje.id)).where(VIGENTE, Viaje.descuentos > 0)
    _sl = select(func.coalesce(func.sum(Viaje.descuentos), 0.0)).where(VIGENTE, Viaje.descuentos > 0)
    if _penalizados:
        _sq = _sq.where(Viaje.id.notin_(_penalizados))
        _sl = _sl.where(Viaje.id.notin_(_penalizados))
    sugeridas_n = int(db.scalar(_sq) or 0)
    sugeridas_lts = float(db.scalar(_sl) or 0)
    anomalias_pend = db.scalar(select(func.count()).select_from(Anomalia)
                               .where(Anomalia.estado == EstadoAnomalia.PENDIENTE)) or 0

    # ── F · Flota & personal (SNAPSHOT del catálogo) ────────────────────────────────────────
    por_tipo = {t.value: (db.scalar(select(func.count()).select_from(Unidad)
                                    .where(Unidad.tipo == t, Unidad.activo.is_(True))) or 0)
                for t in TipoUnidad}
    _cmp = db.execute(select(
        func.count(Remolque.id),
        func.coalesce(func.sum(case((Remolque.es_dolly.is_(True), 1), else_=0)), 0),
        func.coalesce(func.sum(case((Remolque.usa_combustible.is_(True), 1), else_=0)), 0),
        func.coalesce(func.sum(case((and_(Remolque.es_dolly.is_(False),
                                          Remolque.usa_combustible.is_(False)), 1), else_=0)), 0),
    ).where(Remolque.activo.is_(True))).one()
    remolques = {"total": int(_cmp[0] or 0), "dollys": int(_cmp[1] or 0),
                 "refrigerados": int(_cmp[2] or 0), "secos": int(_cmp[3] or 0)}
    operadores_total = db.scalar(select(func.count()).select_from(Operador)) or 0
    _con_cuenta = select(Usuario.operador_id).where(Usuario.operador_id.isnot(None))
    operadores_sin_acceso = db.scalar(
        select(func.count(Operador.id)).where(Operador.id.notin_(_con_cuenta))) or 0

    # ── G · IA & sistema (del periodo; eventos con problema = snapshot pendiente) ────────────
    _ia = db.execute(select(
        func.count(UsoIA.id), func.coalesce(func.sum(UsoIA.costo_usd), 0.0),
        func.coalesce(func.sum(UsoIA.tokens_entrada), 0),
        func.coalesce(func.sum(UsoIA.tokens_salida), 0),
    ).where(UsoIA.momento >= ini_dt)).one()
    ia_fallidas = db.scalar(select(func.count(UsoIA.id))
                            .where(UsoIA.momento >= ini_dt, UsoIA.ok.is_(False))) or 0
    # Eventos de WhatsApp con problema SIN resolver (fallido = agotó reintentos; pendiente = atascado).
    eventos_problema = db.scalar(select(func.count(EventoWhatsapp.id))
                                 .where(EventoWhatsapp.estado_proceso.in_(("fallido", "pendiente")))) or 0

    return {
        "periodo": periodo,
        "periodo_ini": ini.isoformat(),
        "operacion": {"en_curso": int(en_curso), "en_ruta": int(en_ruta),
                      "asignados": int(asignados), "finalizados": int(finalizados)},
        "bandeja": {"por_estado": por_estado, "por_rol": por_rol, "esperan_total": int(esperan_total),
                    "por_despachar": por_estado["autorizada"], "por_facturar": int(por_facturar),
                    "discrepancias": por_estado["en_discrepancia"]},
        "combustible": {"litros": round(litros, 0), "km": round(km, 0),
                        "rendimiento": rendimiento,
                        "gasto_facturado": round(gasto_facturado, 2),
                        "gasto_estimado": gasto_estimado, "costo_litro": costo_litro},
        "global": {"total_viajes": int(total_viajes), "litros_historicos": round(litros_hist, 0),
                   "rendimiento_global": float(rto_global or 0)},
        "facturacion": {"conciliadas": int(fac_conc), "pendientes": int(fac_pend),
                        "total": int(fac_conc + fac_pend), "monto_facturado": round(_total_fac, 2),
                        "litros_facturados": round(_litros_fac, 0),
                        "discrepancias": por_estado["en_discrepancia"]},
        "cumplimiento": {"pct": pct_cumplimiento, "reportados": reportados, "cumplen": cumplen,
                         "pen_n": pen_n, "pen_lts": round(pen_lts, 1),
                         "sugeridas_n": sugeridas_n, "sugeridas_lts": round(sugeridas_lts, 1),
                         "anomalias_pendientes": int(anomalias_pend)},
        "flota": {"por_tipo": por_tipo, "remolques": remolques,
                  "operadores_total": int(operadores_total),
                  "operadores_sin_acceso": int(operadores_sin_acceso)},
        "ia": {"llamadas": int(_ia[0] or 0), "costo_usd": round(float(_ia[1] or 0), 2),
               "tokens_entrada": int(_ia[2] or 0), "tokens_salida": int(_ia[3] or 0),
               "fallidas": int(ia_fallidas), "eventos_problema": int(eventos_problema)},
    }


def _viaje_dict(v: Viaje, detalle: bool = False) -> dict:
    d = {
        "id": v.id, "fecha": v.fecha.isoformat() if v.fecha else None,
        "unidad": v.unidad.clave if v.unidad else None,
        "tipo_unidad": v.unidad.tipo.value if v.unidad else None,
        "operador": v.operador.nombre if v.operador else None,
        "tipo": v.tipo_config.value if v.tipo_config else None,
        # El formulario del panel lee la clave `tipo_config`. Sin este alias el <select>
        # se pintaba vacío y al guardar mandaba tipo_config='', que el endpoint interpreta
        # como "quitarle el tipo": editar cualquier campo del viaje le borraba su
        # configuración, y un viaje SIN TIPO sale del reporte con ideal 0 -> sobrecosto
        # cero y brecha en verde. Ya hay 26 viajes así.
        "tipo_config": v.tipo_config.value if v.tipo_config else None,
        "kilometros": v.kilometros, "lts_scaner": v.lts_scaner, "lts_real": v.lts_real,
        "rto": v.rto, "rto_real": v.rto_real, "dif": v.dif, "pct": v.pct,
        "descuentos": v.descuentos, "odometro": v.odometro,
        "nivel_tanque": v.nivel_tanque,   # fracción 0-1 de la aguja (lo que hoy captura el bot)
        "creado_en": v.creado_en.isoformat() if v.creado_en else None,   # fecha Y HORA del registro
        "reportado_por": v.reportado_por,
        "codigo_cv": v.codigo_cv, "analista": v.analista,
        "anomalias": len(v.anomalias),
        # Un viaje retractado o corregido se sigue mostrando, marcado: ocultarlo sería
        # esconder justo lo que hay que revisar.
        "retractado": v.retractado_en.isoformat() if v.retractado_en else None,
        "retractado_motivo": v.retractado_motivo,
        "correcciones": v.correcciones,
    }
    if detalle:
        d["telemetria"] = {
            "vel_max": v.vel_max, "rpm": v.rpm, "ralenti": v.ralenti, "crucero": v.crucero,
            "paradas_panico": v.paradas_panico, "num_frenadas": v.num_frenadas,
            "neutralizacion": v.neutralizacion, "top_gear": v.top_gear,
            "km_top_gear": v.km_top_gear, "pct_top_gear": v.pct_top_gear,
            "gear_down": v.gear_down, "km_gear_down": v.km_gear_down,
            "pct_gear_down": v.pct_gear_down, "cambios_descendentes": v.cambios_descendentes,
            "c_manejo": v.c_manejo,
        }
    return d


@app.get("/api/viajes")
def viajes(limit: int = Query(50, ge=1, le=500), unidad: str | None = None,
           con_anomalias: bool = False, incompletos: bool = False,
           user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    # limit lo valida FastAPI (1..500): antes un negativo llegaba a Postgres y daba 500.
    q = select(Viaje).order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(limit)
    # Un operador solo puede ver SUS viajes; los demás roles ven toda la flota.
    if user.get("rol") == "operador":
        op_id = _mi_operador_id(user, db)
        q = q.where(Viaje.operador_id == op_id) if op_id else q.where(false())
    if unidad:
        q = q.join(Unidad).where(Unidad.clave == unidad.strip().upper())
    if con_anomalias:
        q = q.join(Anomalia).distinct()
    if incompletos:   # faltan datos clave del reporte del bot (km del tablero o nivel de aguja)
        q = q.where(or_(Viaje.odometro.is_(None), Viaje.nivel_tanque.is_(None)))
    rows = db.execute(q).scalars().all()
    # "En curso": el par (operador, unidad) tiene una asignación ACTIVA ahora mismo. Como NO hay
    # FK viaje↔asignación, se marca SOLO el viaje MÁS RECIENTE de cada par activo (la lista viene
    # ordenada por fecha desc), para no pintar todo el historial de ese par como activo.
    activos = {(o, u) for o, u in db.execute(
        select(AsignacionViaje.operador_id, AsignacionViaje.unidad_id)
        .where(AsignacionViaje.estado == EstadoAsignacion.ACTIVA)).all()}
    marcados: set = set()
    out = []
    for v in rows:
        d = _viaje_dict(v)
        par = (v.operador_id, v.unidad_id)
        if par in activos and par not in marcados:
            d["activo"] = True
            marcados.add(par)
        out.append(d)
    return out


@app.get("/api/viajes/{viaje_id}")
def viaje_detalle(viaje_id: int, user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    v = db.get(Viaje, viaje_id)
    if v is None:
        raise HTTPException(404, "Viaje no encontrado")
    # Un operador solo ve el detalle de SUS viajes (el listado ya filtra; aquí faltaba).
    if user.get("rol") == "operador" and v.operador_id != _mi_operador_id(user, db):
        raise HTTPException(403, "No es tu viaje")
    d = _viaje_dict(v, detalle=True)
    # "En curso" solo si este viaje es el MÁS RECIENTE del par (operador, unidad) y ese par
    # tiene una asignación ACTIVA — consistente con el marcado de la lista.
    d["activo"] = False
    if v.operador_id and v.unidad_id:
        hay_activa = db.scalar(select(AsignacionViaje.id).where(
            AsignacionViaje.estado == EstadoAsignacion.ACTIVA,
            AsignacionViaje.operador_id == v.operador_id,
            AsignacionViaje.unidad_id == v.unidad_id).limit(1))
        if hay_activa:
            ultimo = db.scalar(select(Viaje.id)
                               .where(Viaje.operador_id == v.operador_id,
                                      Viaje.unidad_id == v.unidad_id, VIGENTE)
                               .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(1))
            d["activo"] = (ultimo == v.id)
    d["anomalias_detalle"] = [
        {"id": a.id, "tipo": a.tipo, "descripcion": a.descripcion, "estado": a.estado.value}
        for a in v.anomalias
    ]
    return d


@app.post("/api/viajes/{viaje_id}")
async def viaje_editar(viaje_id: int, request: Request, user: dict = Depends(require_admin),
                       db: Session = Depends(get_db)) -> dict:
    """Completa/corrige un viaje con información parcial y RE-VALIDA (recalcula
    rendimiento y re-evalúa anomalías)."""
    from .validacion import validar
    v = db.get(Viaje, viaje_id)
    if v is None:
        raise HTTPException(404, "Viaje no encontrado")
    # Un viaje RETRACTADO se dio por no ocurrido. Reescribirlo aquí no sólo cambia cifras: la
    # `validar()` de más abajo le levantaría anomalías NUEVAS a un viaje que operativamente
    # ya no existe, y esas anomalías entran en la cola de alguien.
    if getattr(v, "retractado_en", None) is not None:
        raise HTTPException(409, "Este viaje está retractado: se dio por no ocurrido y no se "
                                 "puede volver a editar ni revalidar.")
    f = await request.form()
    for k in ("kilometros", "lts_scaner", "lts_real", "odometro", "descuentos"):
        if k in f:
            setattr(v, k, _pf(f.get(k)))
    for k in ("codigo_cv", "analista"):
        if k in f:
            setattr(v, k, _pstr(f, k))
    if "tipo_config" in f:
        tc = (f.get("tipo_config") or "").strip().upper()
        if tc:
            try:
                v.tipo_config = TipoConfig(tc)
            except ValueError:
                raise HTTPException(400, "Tipo de viaje inválido")
        else:
            v.tipo_config = None
    if "operador_numero" in f:   # reasignar operador por su número (opcional)
        num = _num_empleado(f.get("operador_numero"))
        if num is not None:
            op = db.execute(select(Operador).where(Operador.numero == num)).scalars().first()
            if op is None:
                raise HTTPException(400, f"No existe un operador con número de empleado {num}")
            v.operador_id = op.id
    validar(db, v)   # recalcula rto/dif/pct y re-evalúa las anomalías del viaje
    db.commit()
    d = _viaje_dict(v, detalle=True)
    d["anomalias_detalle"] = [
        {"id": a.id, "tipo": a.tipo, "descripcion": a.descripcion, "estado": a.estado.value}
        for a in v.anomalias
    ]
    return d


@app.get("/api/anomalias")
def anomalias(estado: EstadoAnomalia | None = EstadoAnomalia.PENDIENTE,
              user: dict = Depends(require_gestion),
              db: Session = Depends(get_db)) -> list[dict]:
    # El estado lo valida FastAPI contra el enum (422 si es inválido): antes un valor
    # cualquiera reventaba en EstadoAnomalia(estado) con ValueError -> 500.
    q = select(Anomalia).order_by(Anomalia.creado_en.desc()).limit(500)
    if estado is not None:
        q = q.where(Anomalia.estado == estado)
    return [
        {
            "id": a.id, "viaje_id": a.viaje_id, "tipo": a.tipo,
            "descripcion": a.descripcion, "estado": a.estado.value,
            "unidad": a.viaje.unidad.clave if a.viaje and a.viaje.unidad else None,
            "fecha": a.viaje.fecha.isoformat() if a.viaje and a.viaje.fecha else None,
            # Fecha/hora en que las reglas DETECTARON la anomalía (distinta de la fecha del viaje).
            "creado_en": a.creado_en.isoformat() if a.creado_en else None,
        }
        for a in db.execute(q).scalars().all()
    ]


def _anomalia_detalle(db: Session, a: Anomalia) -> dict:
    """Detalle COMPARTIDO de una anomalía para el modal (idéntico en Admin/Coordinador/Gerente):
    por qué se notificó, cuándo se detectó, el viaje que la disparó (RECIENTE) y su comparación
    contra el historial de la unidad (PREVIOS), reusando los helpers de validacion.py."""
    from statistics import mean, pstdev

    from .validacion import _historial_rto_real, _media_rto_codigo, _ultimo_odometro_con_fecha

    v = a.viaje
    d = {
        "id": a.id, "viaje_id": a.viaje_id, "tipo": a.tipo,
        "categoria": CATEGORIA_ANOMALIA.get(a.tipo, "base"),
        "descripcion": a.descripcion, "estado": a.estado.value,
        "creado_en": a.creado_en.isoformat() if a.creado_en else None,
        "resuelto_en": a.resuelto_en.isoformat() if a.resuelto_en else None,
        "unidad": v.unidad.clave if (v and v.unidad) else None,
        "tipo_unidad": v.unidad.tipo.value if (v and v.unidad) else None,
        "operador": v.operador.nombre if (v and v.operador) else None,
        "fecha_viaje": v.fecha.isoformat() if (v and v.fecha) else None,
    }
    if v is None:
        return d
    # RECIENTE: los valores capturados en el viaje que disparó la anomalía.
    d["reciente"] = {
        "kilometros": v.kilometros, "lts_scaner": v.lts_scaner, "lts_real": v.lts_real,
        "rto": v.rto, "rto_real": v.rto_real, "dif": v.dif, "pct": v.pct,
        "odometro": v.odometro, "nivel_tanque": v.nivel_tanque, "codigo_cv": v.codigo_cv,
        "vel_max": v.vel_max, "ralenti": v.ralenti, "paradas_panico": v.paradas_panico,
    }
    # PREVIOS: comparación contra el historial de la MISMA unidad (excluye el viaje actual).
    comp = {}
    hist = _historial_rto_real(db, v.unidad_id, v.id)
    if hist:
        m = mean(hist)
        s = pstdev(hist) if len(hist) > 1 else 0.0
        comp["rendimiento"] = {
            "actual": v.rto_real, "media_hist": round(m, 3), "sigma": round(s, 3), "n": len(hist),
            "desv_sigmas": round((v.rto_real - m) / s, 2) if (s and v.rto_real is not None) else None,
        }
    odo_prev, fecha_prev = _ultimo_odometro_con_fecha(db, v.unidad_id, v.fecha, v.id)
    if odo_prev is not None:
        comp["odometro"] = {
            "actual": v.odometro, "ultimo_previo": odo_prev,
            "fecha_previo": fecha_prev.isoformat() if fecha_prev else None,
            "avance": round(v.odometro - odo_prev, 1) if v.odometro is not None else None,
        }
    if v.codigo_cv:
        mc = _media_rto_codigo(db, v.unidad_id, v.codigo_cv, v.id)
        if mc is not None:
            comp["por_codigo"] = {"codigo": v.codigo_cv, "actual": v.rto_real,
                                  "media_codigo": round(float(mc), 3)}
    d["comparativa"] = comp
    # Contexto: los últimos viajes vigentes de la unidad (para ver la tendencia).
    prev = db.execute(
        select(Viaje).where(Viaje.unidad_id == v.unidad_id, Viaje.id != v.id, VIGENTE)
        .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(5)
    ).scalars().all()
    d["ultimos_viajes"] = [
        {"fecha": p.fecha.isoformat() if p.fecha else None, "km": p.kilometros,
         "lts_real": p.lts_real, "rto_real": p.rto_real, "pct": p.pct} for p in prev]
    return d


@app.get("/api/anomalias/{anom_id}")
def anomalia_detalle(anom_id: int, user: dict = Depends(require_gestion),
                     db: Session = Depends(get_db)) -> dict:
    """Detalle de UNA anomalía (lectura compartida admin/coordinador/gerente)."""
    a = db.get(Anomalia, anom_id)
    if a is None:
        raise HTTPException(404, "Anomalía no encontrada")
    return _anomalia_detalle(db, a)


@app.post("/api/anomalias/{anom_id}/analizar")
def anomalia_analizar(anom_id: int, user: dict = Depends(require_admin),
                      db: Session = Depends(get_db)) -> dict:
    """Diagnóstico de UNA anomalía con IA. Llamada FACTURABLE → admin-only (como todo lo que
    quema crédito). El coordinador/gerente ven el detalle, pero no disparan la IA."""
    a = db.get(Anomalia, anom_id)
    if a is None:
        raise HTTPException(404, "Anomalía no encontrada")
    texto = ai.diagnosticar_anomalia(_anomalia_detalle(db, a))
    return {"analisis": texto}


def _resolver_anomalia(anom_id: int, estado: EstadoAnomalia, db: Session, user: dict) -> dict:
    a = db.get(Anomalia, anom_id)
    if a is None:
        raise HTTPException(404, "Anomalía no encontrada")
    a.estado = estado
    a.resuelto_en = datetime.now(timezone.utc)
    a.resuelto_por_id = user.get("id")   # QUIÉN resolvió (base de la atribución de desempeño)
    lat = None
    if a.creado_en:
        try:
            lat = max(0, int((a.resuelto_en - a.creado_en).total_seconds()))
        except (TypeError, ValueError):
            lat = None
    registrar_actividad(
        db, accion=("anomalia_confirmada" if estado == EstadoAnomalia.CONFIRMADA else "anomalia_rechazada"),
        usuario=user, entidad="anomalia", entidad_id=a.id, latencia_seg=lat,
        meta={"tipo": a.tipo, "viaje_id": a.viaje_id})
    db.commit()
    return {"id": a.id, "estado": a.estado.value}


@app.post("/api/anomalias/{anom_id}/confirmar")
def confirmar_anomalia(anom_id: int, user: dict = Depends(require_gestion), db: Session = Depends(get_db)) -> dict:
    return _resolver_anomalia(anom_id, EstadoAnomalia.CONFIRMADA, db, user)


@app.post("/api/anomalias/{anom_id}/rechazar")
def rechazar_anomalia(anom_id: int, user: dict = Depends(require_gestion), db: Session = Depends(get_db)) -> dict:
    return _resolver_anomalia(anom_id, EstadoAnomalia.RECHAZADA, db, user)


def _mediana(nums):
    xs = sorted(x for x in nums if x is not None)
    n = len(xs)
    if not n:
        return None
    m = n // 2
    return xs[m] if n % 2 else (xs[m - 1] + xs[m]) / 2


@app.get("/api/desempeno")
def desempeno(periodo: str = Query("mes"),
              user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Panorama de DESEMPEÑO por persona y rol: quién trabaja y quién no, con las métricas propias
    de cada rol, los TIEMPOS DE RESPUESTA (desde que el trabajo cae en su bandeja hasta que actúa)
    y las anomalías/problemas atribuibles a cada quien. `periodo` (dia|semana|mes|anio, default mes).
    Solo lectura, admin-only (expone la actividad de todo el personal)."""
    # Fecha EN HORA DE LA FLOTA (America/Mexico_City), no del reloj del proceso: Viaje.fecha se
    # guarda con fecha_flota() y en producción el VPS corre en UTC. Si usáramos date.today()/UTC,
    # el borde del día quedaría corrido 6 h y por la tarde/noche local se perdería la actividad.
    hoy = fecha_flota()
    if periodo == "dia":
        ini = hoy
    elif periodo == "semana":
        ini = hoy - timedelta(days=hoy.weekday())
    elif periodo == "anio":
        ini = hoy.replace(month=1, day=1)
    else:
        periodo, ini = "mes", hoy.replace(day=1)
    # Medianoche LOCAL de la flota como instante absoluto: las columnas datetime son tz-aware,
    # así que la comparación es correcta; y Viaje.fecha (fecha local) se compara contra `ini`.
    ini_dt = datetime(ini.year, ini.month, ini.day, tzinfo=_tz())

    # ── Universo de personas: todas las cuentas + operadores activos sin cuenta ──
    usuarios = db.execute(select(Usuario)).scalars().all()
    op_con_cuenta = {u.operador_id for u in usuarios if u.operador_id}
    operadores = db.execute(select(Operador).where(Operador.activo.is_(True))).scalars().all()

    personas: dict[str, dict] = {}

    def _persona(pid, **kw):
        p = personas.setdefault(pid, {"pid": pid, "metricas": {}, "problemas": []})
        p.update(kw)
        return p

    for u in usuarios:
        prefs = u.prefs or {}
        _persona(f"u{u.id}", tipo="usuario", usuario_id=u.id, operador_id=u.operador_id,
                 nombre=u.nombre or u.username, username=u.username, rol=u.rol, activo=u.activo,
                 correo=prefs.get("correo"), telefono=u.telefono, puesto=prefs.get("puesto"),
                 tiene_foto=bool(u.foto),
                 ultimo_acceso=u.ultimo_acceso.isoformat() if u.ultimo_acceso else None)
    for o in operadores:
        if o.id in op_con_cuenta:
            continue
        _persona(f"o{o.id}", tipo="operador", usuario_id=None, operador_id=o.id,
                 nombre=o.nombre, username=None, rol="operador", activo=o.activo,
                 correo=None, telefono=o.telefono, puesto=None, tiene_foto=bool(o.foto),
                 ultimo_acceso=None, sin_cuenta=True)

    por_operador = {p["operador_id"]: p for p in personas.values() if p.get("operador_id")}
    por_usuario = {p["usuario_id"]: p for p in personas.values() if p.get("usuario_id")}

    # ── Métricas de OPERADOR (clave: operador_id) ────────────────────────────────
    for oid, nv, lts, km in db.execute(
            select(Viaje.operador_id, func.count(Viaje.id),
                   func.coalesce(func.sum(Viaje.lts_real), 0.0),
                   func.coalesce(func.sum(Viaje.kilometros), 0.0))
            .where(VIGENTE, Viaje.fecha >= ini, Viaje.operador_id.isnot(None))
            .group_by(Viaje.operador_id)).all():
        p = por_operador.get(oid)
        if p:
            p["metricas"].update(viajes=int(nv), litros=round(lts or 0, 1), km=round(km or 0, 1))

    for oid, nr in db.execute(
            select(SolicitudRecarga.operador_id, func.count(SolicitudRecarga.id))
            .where(SolicitudRecarga.creada_en >= ini_dt, SolicitudRecarga.operador_id.isnot(None))
            .group_by(SolicitudRecarga.operador_id)).all():
        p = por_operador.get(oid)
        if p:
            p["metricas"]["recargas"] = int(nr)

    # Reacción a la asignación: visto_en − creada_en (mediana) + % de asignaciones vistas.
    reac: dict[int, dict] = {}
    for oid, creada, visto in db.execute(
            select(AsignacionViaje.operador_id, AsignacionViaje.creada_en, AsignacionViaje.visto_en)
            .where(AsignacionViaje.creada_en >= ini_dt)).all():
        d = reac.setdefault(oid, {"total": 0, "lat": []})
        d["total"] += 1
        if visto and creada:
            d["lat"].append((visto - creada).total_seconds())
    for oid, d in reac.items():
        p = por_operador.get(oid)
        if p:
            med = _mediana(d["lat"])
            p["metricas"]["reaccion"] = {
                "asignaciones": d["total"], "vistas": len(d["lat"]),
                "mediana_min": round(med / 60, 1) if med is not None else None,
                "pct_vistas": round(100 * len(d["lat"]) / d["total"]) if d["total"] else None}

    # Anomalías atribuidas al OPERADOR del viaje, por categoría, + lista de las recientes.
    for oid, tipo, n in db.execute(
            select(Viaje.operador_id, Anomalia.tipo, func.count(Anomalia.id))
            .join(Viaje, Anomalia.viaje_id == Viaje.id)
            .where(Anomalia.creado_en >= ini_dt, Viaje.operador_id.isnot(None), VIGENTE)
            .group_by(Viaje.operador_id, Anomalia.tipo)).all():
        p = por_operador.get(oid)
        if p:
            a = p["metricas"].setdefault("anomalias", {"total": 0, "por_categoria": {}})
            cat = CATEGORIA_ANOMALIA.get(tipo, "base")
            a["total"] += int(n)
            a["por_categoria"][cat] = a["por_categoria"].get(cat, 0) + int(n)
    for aid, oid, tipo, est, cre, vf in db.execute(
            select(Anomalia.id, Viaje.operador_id, Anomalia.tipo, Anomalia.estado,
                   Anomalia.creado_en, Viaje.fecha)
            .join(Viaje, Anomalia.viaje_id == Viaje.id)
            .where(Anomalia.creado_en >= ini_dt, Viaje.operador_id.isnot(None), VIGENTE)
            .order_by(Anomalia.creado_en.desc())).all():
        p = por_operador.get(oid)
        if p and len(p["problemas"]) < 8:
            f = vf or (cre.date() if cre else None)
            p["problemas"].append({"id": aid, "tipo": tipo,
                                   "categoria": CATEGORIA_ANOMALIA.get(tipo, "base"),
                                   "estado": est.value if est else None,
                                   "fecha": f.isoformat() if f else None})

    # Penalizaciones por litros (vía viaje_id → operador).
    for oid, n, lts in db.execute(
            select(Viaje.operador_id, func.count(SeguimientoDescuento.id),
                   func.coalesce(func.sum(SeguimientoDescuento.lts), 0.0))
            .join(Viaje, SeguimientoDescuento.viaje_id == Viaje.id)
            .where(SeguimientoDescuento.fecha >= ini, Viaje.operador_id.isnot(None), VIGENTE)
            .group_by(Viaje.operador_id)).all():
        p = por_operador.get(oid)
        if p:
            p["metricas"]["penalizaciones"] = {"n": int(n), "litros": round(lts or 0, 1)}

    # ── Métricas por USUARIO (coordinador/combustible/gerente/admin) ─────────────
    def _cuenta(col, filtro):
        return {uid: int(n) for uid, n in db.execute(
            select(col, func.count()).where(filtro, col.isnot(None)).group_by(col)).all()}

    trans_n = _cuenta(TransicionSolicitud.por_usuario_id, TransicionSolicitud.momento >= ini_dt)
    asig_n = _cuenta(AsignacionViaje.creada_por_id, AsignacionViaje.creada_en >= ini_dt)
    desp_n = _cuenta(OrdenDespacho.despachada_por_id, OrdenDespacho.despachada_en >= ini_dt)
    auto_n = _cuenta(OrdenDespacho.autorizada_por_id, OrdenDespacho.autorizada_en >= ini_dt)
    fact_n = _cuenta(Factura.subida_por_id, Factura.subida_en >= ini_dt)
    anomres_n = _cuenta(Anomalia.resuelto_por_id, Anomalia.resuelto_en >= ini_dt)

    act: dict[int, dict] = {}
    for uid, n, ult in db.execute(
            select(RegistroActividad.usuario_id, func.count(), func.max(RegistroActividad.momento))
            .where(RegistroActividad.momento >= ini_dt, RegistroActividad.usuario_id.isnot(None))
            .group_by(RegistroActividad.usuario_id)).all():
        act[uid] = {"eventos": int(n), "ultima": ult}
    logins = _cuenta(RegistroActividad.usuario_id,
                     (RegistroActividad.momento >= ini_dt) & (RegistroActividad.accion == "login"))

    # Tiempo de respuesta por actor: reconstruye la secuencia de transiciones por solicitud y
    # atribuye a quien SACA el ítem de un estado el tiempo que estuvo esperando en él.
    sol_activas = [s for (s,) in db.execute(
        select(TransicionSolicitud.solicitud_id.distinct())
        .where(TransicionSolicitud.momento >= ini_dt)).all()]
    resp: dict[int, list] = {}
    if sol_activas:
        prev = None
        for sid, est, uid, mom in db.execute(
                select(TransicionSolicitud.solicitud_id, TransicionSolicitud.estado_nuevo,
                       TransicionSolicitud.por_usuario_id, TransicionSolicitud.momento)
                .where(TransicionSolicitud.solicitud_id.in_(sol_activas))
                .order_by(TransicionSolicitud.solicitud_id, TransicionSolicitud.momento)).all():
            if (prev is not None and prev[0] == sid and uid is not None
                    and mom is not None and prev[3] is not None):
                delta = (mom - prev[3]).total_seconds()
                # Solo esperas que EMPIEZAN y TERMINAN dentro del período (no arrastrar semanas de
                # cola previas al período). Descarta el borrador→envío del propio operador (no es
                # respuesta a un traspaso) y las cadenas de un mismo paso (mismo actor, casi-cero).
                if (prev[3] >= ini_dt and mom >= ini_dt and delta >= 0
                        and prev[1] != EstadoSolicitud.BORRADOR
                        and not (uid == prev[2] and delta < 5)):
                    resp.setdefault(uid, []).append(delta)
            prev = (sid, est, uid, mom)

    desp_lat: dict[int, list] = {}
    for uid, aut, desp in db.execute(
            select(OrdenDespacho.despachada_por_id, OrdenDespacho.autorizada_en,
                   OrdenDespacho.despachada_en)
            .where(OrdenDespacho.despachada_en >= ini_dt,
                   OrdenDespacho.despachada_por_id.isnot(None))).all():
        if aut and desp:
            desp_lat.setdefault(uid, []).append((desp - aut).total_seconds())

    for uid, p in por_usuario.items():
        m = p["metricas"]
        ev = act.get(uid, {})
        m["actividad"] = ev.get("eventos", 0)
        p["ultima_actividad"] = ev["ultima"].isoformat() if ev.get("ultima") else None
        m["logins"] = logins.get(uid, 0)
        for k, src in (("transiciones", trans_n), ("asignaciones_creadas", asig_n),
                       ("despachos", desp_n), ("autorizaciones", auto_n), ("facturas", fact_n),
                       ("anomalias_resueltas", anomres_n)):
            if src.get(uid):
                m[k] = src[uid]
        med_r = _mediana(resp.get(uid, []))
        if med_r is not None:
            m["respuesta"] = {"n": len(resp[uid]), "mediana_min": round(med_r / 60, 1)}
        med_d = _mediana(desp_lat.get(uid, []))
        if med_d is not None:
            m["despacho"] = {"n": len(desp_lat[uid]), "mediana_min": round(med_d / 60, 1)}

    orden_rol = {"admin": 0, "gerente": 1, "coordinador": 2, "combustible": 3, "operador": 4}
    lista = sorted(personas.values(),
                   key=lambda p: (orden_rol.get(p.get("rol"), 9), (p.get("nombre") or "").lower()))
    return {
        "periodo": periodo, "desde": ini.isoformat(),
        "generado_en": datetime.now(timezone.utc).isoformat(),
        "personas": lista,
        "caveats": [
            "La 'notificación' no es un aviso push: es el instante en que el trabajo cae en la bandeja del rol (transición de estado o asignación creada).",
            "El tiempo de respuesta de solicitudes se mide entre transiciones; las cadenas hechas en un mismo paso (≈0 s) se descartan.",
            "Las anomalías se atribuyen al OPERADOR del viaje; una anomalía por culpa de otro rol sigue colgando de ese operador (imputación aproximada).",
            "Los operadores sin cuenta aparecen por su actividad (viajes/recargas); no generan eventos de sesión.",
        ],
    }


def _rango_periodo(periodo: str):
    """(periodo, ini, ini_dt) en HORA DE LA FLOTA. Misma convención que /api/desempeno."""
    hoy = fecha_flota()
    if periodo == "dia":
        ini = hoy
    elif periodo == "semana":
        ini = hoy - timedelta(days=hoy.weekday())
    elif periodo == "anio":
        ini = hoy.replace(month=1, day=1)
    else:
        periodo, ini = "mes", hoy.replace(day=1)
    return periodo, ini, datetime(ini.year, ini.month, ini.day, tzinfo=_tz())


# Verbo legible por estado al que ENTRA una solicitud (para "qué hizo" en la bitácora de la persona).
VERBO_ESTADO = {
    "borrador": "Creó borrador", "enviada": "Envió", "en_validacion": "Tomó para validar",
    "autorizada": "Autorizó", "devuelta": "Devolvió", "rechazada": "Rechazó",
    "despachada": "Despachó", "facturada": "Facturó", "conciliada": "Concilió",
    "en_discrepancia": "Marcó discrepancia", "anulada": "Anuló",
}
# Color por familia de acción (para el donut de desglose).
_TIPO_COLOR = {
    "solicitud": "var(--blue)", "asignacion": "var(--accent)", "anomalia": "var(--red)",
    "login": "var(--muted)", "viaje": "var(--green)", "recarga": "var(--amber)",
}


def _dossier_persona(db: Session, pid: str, periodo: str) -> dict:
    """Expediente detallado de UNA persona: qué hizo (a quién y por qué), su línea de tiempo,
    actividad por día, desglose por tipo, tiempos de respuesta, comparación vs su rol e insights."""
    periodo, ini, ini_dt = _rango_periodo(periodo)
    if pid and pid.startswith("u"):
        u = db.get(Usuario, int(pid[1:] or 0))
        if u is None:
            raise HTTPException(404, "Persona no encontrada")
        rol, uid, op_id, prefs = u.rol, u.id, u.operador_id, (u.prefs or {})
        persona = {"pid": pid, "tipo": "usuario", "usuario_id": u.id, "operador_id": u.operador_id,
                   "nombre": u.nombre or u.username, "rol": u.rol, "correo": prefs.get("correo"),
                   "telefono": u.telefono, "puesto": prefs.get("puesto"), "tiene_foto": bool(u.foto),
                   "ultimo_acceso": u.ultimo_acceso.isoformat() if u.ultimo_acceso else None,
                   "activo": u.activo}
    elif pid and pid.startswith("o"):
        o = db.get(Operador, int(pid[1:] or 0))
        if o is None:
            raise HTTPException(404, "Persona no encontrada")
        rol, uid, op_id = "operador", None, o.id
        persona = {"pid": pid, "tipo": "operador", "usuario_id": None, "operador_id": o.id,
                   "nombre": o.nombre, "rol": "operador", "correo": None, "telefono": o.telefono,
                   "puesto": None, "tiene_foto": bool(o.foto), "ultimo_acceso": None,
                   "activo": o.activo, "sin_cuenta": True}
    else:
        raise HTTPException(400, "Identificador de persona inválido")

    tl: list[dict] = []

    def add(mom, tipo, verbo, a_quien=None, detalle=None, motivo=None, latencia=None, ref=None):
        if mom is not None:
            tl.append({"momento": mom, "tipo": tipo, "verbo": verbo, "a_quien": a_quien,
                       "detalle": detalle, "motivo": motivo, "latencia_seg": latencia, "ref": ref})

    # ── Acciones de USUARIO (personal) ───────────────────────────────────────
    if uid is not None:
        for mom, est, nota, folio, opnom, lts in db.execute(
                select(TransicionSolicitud.momento, TransicionSolicitud.estado_nuevo,
                       TransicionSolicitud.nota, OrdenDespacho.folio, Operador.nombre,
                       SolicitudRecarga.litros_solicitados)
                .join(SolicitudRecarga, TransicionSolicitud.solicitud_id == SolicitudRecarga.id)
                .join(Operador, SolicitudRecarga.operador_id == Operador.id, isouter=True)
                .join(OrdenDespacho, OrdenDespacho.solicitud_id == SolicitudRecarga.id, isouter=True)
                .where(TransicionSolicitud.por_usuario_id == uid,
                       TransicionSolicitud.momento >= ini_dt)
                .order_by(TransicionSolicitud.momento.desc()).limit(150)).all():
            det = []
            if folio:
                det.append("folio " + folio)
            if lts:
                det.append(f"{lts:g} L")
            add(mom, "solicitud", VERBO_ESTADO.get(est or "", "Cambió estado"),
                a_quien=opnom, detalle=" · ".join(det) or None, motivo=nota)
        for mom, opnom, destino in db.execute(
                select(AsignacionViaje.creada_en, Operador.nombre, AsignacionViaje.destino)
                .join(Operador, AsignacionViaje.operador_id == Operador.id, isouter=True)
                .where(AsignacionViaje.creada_por_id == uid, AsignacionViaje.creada_en >= ini_dt)
                .order_by(AsignacionViaje.creada_en.desc()).limit(80)).all():
            add(mom, "asignacion", "Asignó viaje", a_quien=opnom,
                detalle=("→ " + destino) if destino else None)
        for aid, mom, est, tipo, vid in db.execute(
                select(Anomalia.id, Anomalia.resuelto_en, Anomalia.estado, Anomalia.tipo,
                       Anomalia.viaje_id)
                .where(Anomalia.resuelto_por_id == uid, Anomalia.resuelto_en >= ini_dt)
                .order_by(Anomalia.resuelto_en.desc()).limit(80)).all():
            add(mom, "anomalia",
                "Confirmó anomalía" if est == EstadoAnomalia.CONFIRMADA else "Descartó anomalía",
                detalle=f"{tipo} · viaje {vid}", ref={"k": "anomalia", "id": aid})
        for (mom,) in db.execute(
                select(RegistroActividad.momento)
                .where(RegistroActividad.usuario_id == uid, RegistroActividad.accion == "login",
                       RegistroActividad.momento >= ini_dt)
                .order_by(RegistroActividad.momento.desc()).limit(50)).all():
            add(mom, "login", "Ingresó al sistema")

    # ── Acciones de OPERADOR ─────────────────────────────────────────────────
    if op_id is not None:
        for cre, fecha, clave, km, lts, rto in db.execute(
                select(Viaje.creado_en, Viaje.fecha, Unidad.clave, Viaje.kilometros,
                       Viaje.lts_real, Viaje.rto_real)
                .join(Unidad, Viaje.unidad_id == Unidad.id, isouter=True)
                .where(Viaje.operador_id == op_id, VIGENTE, Viaje.fecha >= ini)
                .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(100)).all():
            det = [x for x in (clave, f"{km:g} km" if km else None,
                               f"{lts:g} L" if lts else None,
                               f"{rto:g} km/l" if rto else None) if x]
            mom = cre or (datetime(fecha.year, fecha.month, fecha.day, tzinfo=_tz()) if fecha else None)
            add(mom, "viaje", "Reportó viaje", detalle=" · ".join(det) or None)
        for cre, est, lts in db.execute(
                select(SolicitudRecarga.creada_en, SolicitudRecarga.estado,
                       SolicitudRecarga.litros_solicitados)
                .where(SolicitudRecarga.operador_id == op_id, SolicitudRecarga.creada_en >= ini_dt)
                .order_by(SolicitudRecarga.creada_en.desc()).limit(80)).all():
            add(cre, "recarga", "Pidió recarga",
                detalle=(f"{lts:g} L · " if lts else "") + (est.value if est else ""))
        for creada, visto, destino in db.execute(
                select(AsignacionViaje.creada_en, AsignacionViaje.visto_en, AsignacionViaje.destino)
                .where(AsignacionViaje.operador_id == op_id, AsignacionViaje.visto_en >= ini_dt)
                .order_by(AsignacionViaje.visto_en.desc()).limit(50)).all():
            lat = int((visto - creada).total_seconds()) if (visto and creada) else None
            add(visto, "asignacion", "Vio su asignación",
                detalle=("→ " + destino) if destino else None, latencia=lat)
        for aid, cre, tipo, est in db.execute(
                select(Anomalia.id, Anomalia.creado_en, Anomalia.tipo, Anomalia.estado)
                .join(Viaje, Anomalia.viaje_id == Viaje.id)
                .where(Viaje.operador_id == op_id, VIGENTE, Anomalia.creado_en >= ini_dt)
                .order_by(Anomalia.creado_en.desc()).limit(50)).all():
            add(cre, "anomalia", "Anomalía detectada",
                detalle=f"{tipo} ({est.value if est else ''})", ref={"k": "anomalia", "id": aid})

    tl.sort(key=lambda x: x["momento"], reverse=True)

    # Actividad por día (LOCAL) y desglose por verbo — sobre TODAS las acciones, no solo las 60.
    por_dia: dict[str, int] = {}
    por_verbo: dict[str, int] = {}
    for it in tl:
        d = it["momento"].astimezone(_tz()).date().isoformat()
        por_dia[d] = por_dia.get(d, 0) + 1
        por_verbo[it["verbo"]] = por_verbo.get(it["verbo"], 0) + 1
    por_dia_list = [{"dia": k, "n": v} for k, v in sorted(por_dia.items())]
    por_tipo_list = [{"tipo": k, "n": v, "color": _TIPO_COLOR.get(_verbo_familia(k), "var(--muted)")}
                     for k, v in sorted(por_verbo.items(), key=lambda kv: -kv[1])]
    lats = [it["latencia_seg"] / 60 for it in tl if it.get("latencia_seg") is not None]

    # Comparación vs la mediana de su rol (proxy: viajes para operador; transiciones para personal).
    if rol == "operador":
        counts = dict(db.execute(
            select(Viaje.operador_id, func.count()).where(
                VIGENTE, Viaje.fecha >= ini, Viaje.operador_id.isnot(None))
            .group_by(Viaje.operador_id)).all())
        mio = counts.get(op_id, 0)
        etiqueta = "viajes"
    else:
        rol_uids = [x for (x,) in db.execute(select(Usuario.id).where(Usuario.rol == rol)).all()]
        counts = dict(db.execute(
            select(TransicionSolicitud.por_usuario_id, func.count()).where(
                TransicionSolicitud.momento >= ini_dt,
                TransicionSolicitud.por_usuario_id.in_(rol_uids or [-1]))
            .group_by(TransicionSolicitud.por_usuario_id)).all()) if rol_uids else {}
        mio = counts.get(uid, 0)
        etiqueta = "acciones en solicitudes"
    vals = sorted(int(v) for v in counts.values())
    med = _mediana(vals) or 0
    comparativa = {"metric": etiqueta, "mio": int(mio), "mediana_rol": round(med, 1),
                   "mejor": int(vals[-1]) if vals else 0, "n_rol": len(vals)}

    # Insights computados (análisis sin IA).
    insights: list[str] = []
    tot = len(tl)
    if tot == 0:
        insights.append("Sin actividad registrada en el período.")
    else:
        insights.append(f"{tot} acciones registradas en el período.")
        if por_tipo_list:
            insights.append(f"Acción más frecuente: {por_tipo_list[0]['tipo']} ({por_tipo_list[0]['n']}).")
        if por_dia_list:
            pk = max(por_dia_list, key=lambda d: d["n"])
            insights.append(f"Día más activo: {pk['dia']} con {pk['n']} acciones.")
        if lats:
            insights.append(f"Tiempo de respuesta mediano: {round(_mediana(lats))} min "
                            f"({len(lats)} mediciones).")
        if comparativa["mediana_rol"]:
            rel = ("por encima de" if comparativa["mio"] > comparativa["mediana_rol"]
                   else "por debajo de" if comparativa["mio"] < comparativa["mediana_rol"]
                   else "en línea con")
            insights.append(f"Está {rel} la mediana de su rol "
                            f"({comparativa['mio']} vs {comparativa['mediana_rol']} {comparativa['metric']}).")

    for it in tl:
        it["momento"] = it["momento"].isoformat()

    return {"persona": persona, "periodo": periodo, "desde": ini.isoformat(),
            "generado_en": datetime.now(timezone.utc).isoformat(),
            "total_acciones": tot, "timeline": tl[:60], "por_dia": por_dia_list,
            "por_tipo": por_tipo_list, "insights": insights, "comparativa": comparativa}


def _verbo_familia(verbo: str) -> str:
    """Mapea un verbo legible a su familia de color para el donut."""
    v = verbo.lower()
    if "anomal" in v:
        return "anomalia"
    if "asign" in v:
        return "asignacion"
    if "viaje" in v:
        return "viaje"
    if "recarga" in v:
        return "recarga"
    if "ingres" in v:
        return "login"
    return "solicitud"


@app.get("/api/desempeno/persona/{pid}")
def desempeno_persona(pid: str, periodo: str = Query("mes"),
                      user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Expediente detallado de una persona (timeline + gráficos + insights + comparativa). Se
    consulta al abrir su modal y en cada refresco 'en vivo'. Solo lectura, admin-only."""
    return _dossier_persona(db, pid, periodo)


@app.post("/api/desempeno/persona/{pid}/analizar")
def desempeno_persona_analizar(pid: str, periodo: str = Query("mes"),
                               user: dict = Depends(require_admin),
                               db: Session = Depends(get_db)) -> dict:
    """Análisis con IA del desempeño de la persona (FACTURABLE → admin-only, como todo lo que
    quema crédito). Reusa el expediente ya calculado, recortado para el prompt."""
    d = _dossier_persona(db, pid, periodo)
    payload = {
        "persona": {k: d["persona"].get(k) for k in ("nombre", "rol", "puesto", "activo")},
        "periodo": d["periodo"], "desde": d["desde"], "total_acciones": d["total_acciones"],
        "por_tipo": d["por_tipo"], "por_dia": d["por_dia"], "comparativa": d["comparativa"],
        "insights": d["insights"],
        "muestra_acciones": [{k: it.get(k) for k in ("verbo", "a_quien", "detalle", "motivo",
                              "latencia_seg", "momento")} for it in d["timeline"][:25]],
    }
    return {"analisis": ai.analizar_desempeno_persona(payload)}


# ── Catálogo: diferencias contra el maestro de placas ────────────────────────
# El importador NO escribe en el catálogo: deja propuestas. Aquí se aprueban una por
# una o en lote, siempre por una persona y siempre dejando constancia de quién fue.

@app.get("/api/catalogo/propuestas")
def catalogo_propuestas(estado: str = Query("pendiente"),
                        user: dict = Depends(require_admin),
                        db: Session = Depends(get_db)) -> dict:
    """Diferencias entre el catálogo y el maestro de placas del cliente."""
    q = select(PropuestaCatalogo).order_by(PropuestaCatalogo.id)
    if estado != "todas":
        q = q.where(PropuestaCatalogo.estado == estado)
    props = list(db.execute(q).scalars())

    imp = db.execute(select(ImportacionPlacas).order_by(
        ImportacionPlacas.id.desc()).limit(1)).scalar_one_or_none()

    def fila(p):
        # "YA OPERA" lo escribe el importador cruzando contra las cargas del proveedor:
        # es la diferencia entre un alta urgente y una que puede esperar.
        return {
            "id": p.id, "accion": p.accion, "entidad": p.entidad, "entidad_id": p.entidad_id,
            "eco": p.eco_texto, "placa": p.placa_texto, "estado": p.estado,
            "actual": p.valor_actual, "propuesto": p.valor_propuesto, "motivo": p.motivo,
            "urgente": "YA OPERA" in (p.motivo or ""),
            "aplicada_en": p.aplicada_en.isoformat() if p.aplicada_en else None,
        }

    filas = [fila(p) for p in props]
    resumen = {}
    for f in filas:
        resumen[f["accion"]] = resumen.get(f["accion"], 0) + 1
    return {
        "propuestas": filas, "resumen": resumen,
        "urgentes": sum(1 for f in filas if f["urgente"]),
        "importacion": None if imp is None else {
            "archivo": imp.archivo, "importado_en": imp.importado_en.isoformat(),
            "n_unidades": imp.n_unidades, "n_remolques": imp.n_remolques},
    }


@app.post("/api/catalogo/propuestas/aplicar")
async def catalogo_aplicar(request: Request, user: dict = Depends(require_admin),
                           db: Session = Depends(get_db)) -> dict:
    """Aplica las propuestas indicadas. Cada una responde por separado: que una falle
    no cancela las demás, y el detalle dice exactamente qué pasó con cada activo."""
    body = await request.json()
    ids = [int(i) for i in (body.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "no se indicó ninguna propuesta")

    hechos, fallos = [], []
    for pid in ids:
        p = db.get(PropuestaCatalogo, pid)
        if p is None:
            fallos.append({"id": pid, "motivo": "no existe"})
            continue
        ok, msg = catalogo.aplicar_propuesta(db, p, usuario_id=user.get("id"))
        (hechos if ok else fallos).append({"id": pid, "eco": p.eco_texto, "motivo": msg})

    registrar_actividad(db, accion="catalogo_aplicar", usuario=user, entidad="catalogo",
                        meta={"aplicadas": len(hechos), "fallidas": len(fallos),
                              "ids": ids[:50]})
    db.commit()
    return {"aplicadas": len(hechos), "detalle": hechos, "fallos": fallos}


@app.post("/api/catalogo/propuestas/descartar")
async def catalogo_descartar(request: Request, user: dict = Depends(require_admin),
                             db: Session = Depends(get_db)) -> dict:
    """Marca propuestas como descartadas. No toca el catálogo; solo deja constancia de
    que se revisaron y se decidió que no proceden."""
    body = await request.json()
    ids = [int(i) for i in (body.get("ids") or [])]
    motivo = (body.get("motivo") or "").strip()[:200]
    n = 0
    for pid in ids:
        p = db.get(PropuestaCatalogo, pid)
        if p is not None and p.estado == "pendiente":
            p.estado = "descartada"
            p.aplicada_por_id = user.get("id")
            p.aplicada_en = datetime.now(timezone.utc)
            if motivo:
                p.motivo = f"{p.motivo} | descartada: {motivo}"
            n += 1
    registrar_actividad(db, accion="catalogo_descartar", usuario=user, entidad="catalogo",
                        meta={"descartadas": n, "motivo": motivo})
    db.commit()
    return {"descartadas": n}


# ── Etiquetas físicas (hologramas) ──────────────────────────────────────────
# El operador escanea el holograma en vez de teclear el económico. Una etiqueta mal
# vinculada no se nota al capturar: se nota meses después, con los litros en la unidad
# equivocada. Por eso las ambiguas NO se vinculan solas y aparecen aquí para decidirse.

def _etq_activo(db, e):
    """Nombre legible del activo al que apunta una etiqueta."""
    o = (db.get(Unidad, e.unidad_id) if e.unidad_id
         else db.get(Remolque, e.remolque_id) if e.remolque_id else None)
    if o is None:
        return None
    return (f"Unidad {o.clave}" if e.unidad_id else f"Remolque {o.eco}")


def _etq_de_baja(db, e):
    """Si el activo al que apunta ya no está vigente. La etiqueta sigue resolviendo —lo que
    ya se registró necesita a qué apuntar— pero que un activo retirado vuelva a cargar diésel
    hay que verlo, no descubrirlo tres meses después."""
    o = (db.get(Unidad, e.unidad_id) if e.unidad_id
         else db.get(Remolque, e.remolque_id) if e.remolque_id else None)
    return bool(o is not None and o.activo is False)


@app.get("/api/catalogo/etiquetas")
def catalogo_etiquetas(user: dict = Depends(require_admin),
                       db: Session = Depends(get_db)) -> dict:
    """Estado de los hologramas: lo vinculado, lo ambiguo y lo que espera un alta."""
    etqs = list(db.execute(select(EtiquetaActivo).order_by(EtiquetaActivo.id)).scalars())

    # Los económicos que ya tienen un alta propuesta: sin esto, "se vinculará en cuanto se
    # dé de alta" es una promesa que no se puede cumplir para un económico que no existe en
    # ninguna lista, y nadie se entera de que esa etiqueta quedará muerta para siempre.
    propuestos = {catalogo.norm_eco(x) for x in db.execute(
        select(PropuestaCatalogo.eco_texto).where(
            PropuestaCatalogo.estado == "pendiente")).scalars() if x}

    # Los códigos hermanos de una duplicada: son las otras etiquetas del mismo económico.
    hermanos = {}
    for e in etqs:
        if e.estado == "duplicada":
            hermanos.setdefault(catalogo.norm_eco(e.eco_texto), []).append(e.codigo)

    def fila(e):
        # En un conflicto, `eco_texto` guarda TODOS los económicos candidatos separados por
        # coma: son justamente las opciones entre las que hay que elegir.
        cands = []
        if e.estado == "conflicto":
            for t in (e.eco_texto or "").split(","):
                t = t.strip()
                if not t:
                    continue
                r = catalogo.resolver_activo(db, eco=t)
                o = (db.get(Unidad, r.unidad_id) if r.unidad_id
                     else db.get(Remolque, r.remolque_id) if r.remolque_id else None)
                cands.append({"eco": t, "unidad_id": r.unidad_id,
                              "remolque_id": r.remolque_id, "existe": r.ok,
                              "vigente": bool(o is not None and o.activo)})
        return {"id": e.id, "codigo": e.codigo, "estado": e.estado, "uso": e.uso,
                "eco": e.eco_texto, "activo": _etq_activo(db, e), "nota": e.nota,
                "de_baja": _etq_de_baja(db, e),
                "candidatos": cands,
                # un huérfano sin alta propuesta no se vinculará nunca solo: hay que decidirlo
                "tiene_alta": (catalogo.norm_eco(e.eco_texto) in propuestos
                               if e.estado == "sin_activo" else None),
                "hermanos": [c for c in hermanos.get(catalogo.norm_eco(e.eco_texto), [])
                             if c != e.codigo] if e.estado == "duplicada" else []}

    filas = [fila(e) for e in etqs]
    resumen = {}
    for f in filas:
        resumen[f["estado"]] = resumen.get(f["estado"], 0) + 1

    # Cuánta flota sigue SIN poder escanear: es la pregunta que decide si el despliegue
    # está listo o no.
    con_etq_u = {e.unidad_id for e in etqs if e.unidad_id}
    con_etq_r = {e.remolque_id for e in etqs if e.remolque_id}
    # Un activo que aparece en un conflicto SÍ tiene etiqueta pegada: lo que no se sabe es
    # cuál. Mezclarlo con los que no tienen ninguna haría creer que faltan más stickers de
    # los que faltan, y esa es una decisión de compra.
    en_conflicto = set()
    for e in etqs:
        if e.estado == "conflicto":
            for t in (e.eco_texto or "").split(","):
                if t.strip():
                    en_conflicto.add(catalogo.norm_eco(t))

    sin = []
    for u in db.execute(select(Unidad).where(Unidad.activo.is_(True))).scalars():
        if u.id in con_etq_u:
            continue
        sin.append({"tipo": "unidad", "eco": u.clave, "consume": True,
                    "en_conflicto": catalogo.norm_eco(u.clave) in en_conflicto})
    for r in db.execute(select(Remolque).where(Remolque.activo.is_(True))).scalars():
        if r.id in con_etq_r:
            continue
        alias = {catalogo.norm_eco(r.eco), catalogo.norm_eco(r.eco_nuevo)} - {""}
        sin.append({"tipo": "remolque", "eco": r.eco,
                    # un dolly no quema diésel: no poder escanearlo no detiene una recarga
                    "consume": not r.es_dolly,
                    "en_conflicto": bool(alias & en_conflicto)})

    imp = db.execute(select(ImportacionEtiquetas).order_by(
        ImportacionEtiquetas.id.desc()).limit(1)).scalar_one_or_none()
    return {
        "etiquetas": filas, "resumen": resumen, "sin_etiqueta": sin,
        "faltan_stickers": sum(1 for x in sin if x["consume"] and not x["en_conflicto"]),
        "importacion": None if imp is None else {
            "archivo": imp.archivo, "importado_en": imp.importado_en.isoformat(),
            "n_filas": imp.n_filas},
    }


@app.post("/api/catalogo/etiquetas/{etq_id}/vincular")
async def catalogo_etiqueta_vincular(etq_id: int, request: Request,
                                     user: dict = Depends(require_admin),
                                     db: Session = Depends(get_db)) -> dict:
    """Resuelve un conflicto: una persona declara qué activo lleva pegado el holograma.

    Es la única forma de que una etiqueta ambigua pase a resolver, y queda registrada en
    la bitácora con quién lo decidió.
    """
    body = await request.json()
    eco = (body.get("eco") or "").strip()
    uso = (body.get("uso") or "").strip().lower() or None
    e = db.get(EtiquetaActivo, etq_id)
    if e is None:
        raise HTTPException(404, "esa etiqueta no existe")
    if not eco:
        raise HTTPException(400, "hay que indicar a qué activo pertenece")
    if uso and uso not in ("motor", "termo"):
        raise HTTPException(400, "el uso sólo puede ser 'motor' o 'termo'")

    r = catalogo.resolver_activo(db, eco=eco)
    if not r.ok:
        raise HTTPException(400, f"el económico {eco} no existe en el catálogo")

    # Una duplicada (varios hologramas en el mismo activo) sólo deja de ser ambigua cuando
    # se declara qué hace cada uno. Sin eso seguiría sin saberse qué sticker está pegado dónde.
    if e.estado == "duplicada" and not uso:
        raise HTTPException(
            400, "este activo tiene varias etiquetas: hay que declarar si es la del motor "
                 "o la del termo")

    antes = e.estado
    e.unidad_id, e.remolque_id = r.unidad_id, r.remolque_id
    e.estado = "vinculada"
    e.eco_texto = eco
    e.uso = uso
    e.nota = (f"declarado a mano: la lleva {eco}" + (f", uso {uso}" if uso else ""))
    e.actualizada_en = datetime.now(timezone.utc)
    registrar_actividad(db, accion="etiqueta_vincular", usuario=user,
                        entidad="etiqueta", entidad_id=e.id,
                        meta={"codigo": e.codigo, "eco": eco, "uso": uso,
                              "estado_previo": antes})
    db.commit()
    return {"ok": True, "codigo": e.codigo, "activo": _etq_activo(db, e), "uso": uso}


@app.post("/api/catalogo/etiquetas/{etq_id}/retirar")
async def catalogo_etiqueta_retirar(etq_id: int, request: Request,
                                    user: dict = Depends(require_admin),
                                    db: Session = Depends(get_db)) -> dict:
    """Anula una etiqueta: se despegó, se dañó o se reemplazó.

    NO se borra. Se conserva para poder leer el pasado: las cargas que se registraron
    mientras estuvo vigente siguen teniendo a qué apuntar. Lo que deja de hacer es
    resolver, así que escanearla ya no atribuye litros a nadie.
    """
    body = await request.json()
    motivo = (body.get("motivo") or "").strip()[:200]
    e = db.get(EtiquetaActivo, etq_id)
    if e is None:
        raise HTTPException(404, "esa etiqueta no existe")
    antes = e.estado
    e.estado = "retirada"
    e.nota = f"retirada: {motivo}" if motivo else "retirada"
    e.actualizada_en = datetime.now(timezone.utc)
    registrar_actividad(db, accion="etiqueta_retirar", usuario=user,
                        entidad="etiqueta", entidad_id=e.id,
                        meta={"codigo": e.codigo, "motivo": motivo, "estado_previo": antes})
    db.commit()
    return {"ok": True, "codigo": e.codigo}


# ── Helpers de formulario para el CRUD de flota ──────────────────────────────
def _pi(v) -> int | None:
    v = (str(v) if v is not None else "").strip().replace(",", "")
    try:
        return int(float(v)) if v else None
    except ValueError:
        return None


# Lo que un número de empleado NO puede ser, porque va a ser un nombre de usuario.
# Hoy no chocan por casualidad, no por diseño: nada impedía capturar «admin».
_NUM_RESERVADOS = {"admin", "gerente", "combustible", "coordinador", "operador",
                   "root", "sistema", "soporte", "test"}
_NUM_FORMA = re.compile(r"[A-Z0-9][A-Z0-9._-]*$")


def _num_empleado(v) -> str | None:
    """El número de empleado, normalizado. Vacío devuelve None; inválido lanza 400.

    NO usa `_pi`: `_pi` hace int(float(v)) y devuelve None cuando falla, así que con la
    columna en texto habría guardado NULL al teclear CFRUIT056 —sin un solo error— y el
    operador se quedaba sin número sin que nadie se enterara.

    Mayúsculas y sin espacios porque el número de la gasolinera viene así y porque en
    texto «cfruit056» y «CFRUIT056» serían dos filas para el UNIQUE y una sola persona
    para quien captura.
    """
    s = re.sub(r"\s+", "", (str(v) if v is not None else "")).upper()
    if not s:
        return None
    if len(s) > 30:
        raise HTTPException(400, "El número de empleado no puede pasar de 30 caracteres")
    if not _NUM_FORMA.match(s):
        raise HTTPException(400, (f"«{s}» no sirve como número de empleado: sólo letras, "
                                  "dígitos, punto, guion y guion bajo, empezando por letra "
                                  "o dígito"))
    if s.lower() in _NUM_RESERVADOS:
        raise HTTPException(400, f"«{s}» está reservado para las cuentas del sistema")
    return s


def _pf_litros(v, que: str) -> float:
    """Litros tecleados por una persona: con cifras, mayores que cero, y sin trucos.

    `_pf` acaba en `float()`, que acepta '1e5', 'inf' y 'nan'. Y el campo del panel es un
    <input type=number>, para el que '1e5' es un número perfectamente válido: `.value` lo
    devuelve tal cual. Eran 100,000 L en tres pulsaciones, y Postgres los guarda en un
    DOUBLE PRECISION sin protestar.

    No se toca `_pf`: lo usan decenas de sitios donde la notación científica no llega
    nunca. Aquí se filtra antes de convertir, quitando separadores y exigiendo dígitos, lo
    que conserva los decimales que `_pf` ya entendía (350.5, 350,5 y 1,234.5).
    """
    crudo = (str(v) if v is not None else "").strip()
    if crudo and not crudo.replace(",", "").replace(".", "").isdigit():
        raise HTTPException(400, f"Escribe {que} con cifras (350 o 350.5)")
    n = _pf(crudo)
    if n is None or n <= 0:
        raise HTTPException(400, f"Indica {que}")
    return n


def _pf(v) -> float | None:
    v = (str(v) if v is not None else "").strip()
    if not v:
        return None
    if "," in v and "." in v:
        v = v.replace(",", "")       # coma = separador de miles (1,234.5)
    elif "," in v:
        v = v.replace(",", ".")      # coma = separador decimal (0,05 -> 0.05)
    try:
        return float(v)
    except ValueError:
        return None


def _pdate(v):
    v = (str(v) if v is not None else "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"Fecha inválida: {v} (usa AAAA-MM-DD)")


def _pstr(f, k):
    return (f.get(k) or "").strip() or None


def _pbool(f, k):
    return str(f.get(k)).lower() in ("true", "on", "1")


def _tipo_unidad(valor) -> TipoUnidad:
    try:
        return TipoUnidad(valor)
    except ValueError:
        raise HTTPException(400, "Tipo de unidad inválido (usa TRACTO o CAMION)")


def _commit(db: Session, msg: str = "Registro duplicado") -> None:
    """Commit que degrada un choque de unicidad (carrera o doble-submit) a un 400 legible."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, msg)


def _es_imagen(data: bytes, ext: str) -> bool:
    """Valida la firma (magic bytes) del archivo, no solo el content-type declarado."""
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if ext == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    return data[:3] == b"\xff\xd8\xff"   # jpg/jpeg


def _apply_unidad(u: Unidad, f) -> None:
    if f.get("tipo"):
        u.tipo = _tipo_unidad(f.get("tipo"))
    for k in ("marca", "placa", "placas_nuevas", "serie", "motor", "motor_cc",
              "operador_asignado", "descripcion"):
        if k in f:
            setattr(u, k, _pstr(f, k))
    if "anio" in f:
        u.anio = _pi(f.get("anio"))
    if "rendimiento_objetivo" in f:
        u.rendimiento_objetivo = _pf(f.get("rendimiento_objetivo"))
    if "pct_tolerancia" in f:
        u.pct_tolerancia = _pf(f.get("pct_tolerancia"))
    if "usa_remolque" in f:
        u.usa_remolque = _pbool(f, "usa_remolque")
    if "activo" in f:
        u.activo = _pbool(f, "activo")
    if "operador_asignado_id" in f:   # titular real (relación al catálogo); "" o 0 = ninguno
        u.operador_asignado_id = _pi(f.get("operador_asignado_id")) or None


def _unidad_dict(u: Unidad, viajes: int | None = None, rto=None) -> dict:
    return {
        "id": u.id, "clave": u.clave, "tipo": u.tipo.value, "activo": u.activo,
        "viajes": viajes, "rto_real_prom": float(rto) if rto is not None else None,
        "marca": u.marca, "anio": u.anio, "placa": u.placa, "placas_nuevas": u.placas_nuevas,
        "serie": u.serie, "motor": u.motor, "motor_cc": u.motor_cc,
        "operador_asignado": u.operador_asignado, "descripcion": u.descripcion,
        "usa_remolque": u.usa_remolque, "rendimiento_objetivo": u.rendimiento_objetivo,
        "pct_tolerancia": u.pct_tolerancia,
        "operador_asignado_id": u.operador_asignado_id,
        "operador_titular": u.operador_titular.nombre if u.operador_titular else None,
    }


@app.get("/api/unidades")
def unidades(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(
        select(Unidad, func.count(Viaje.id), func.round(func.avg(Viaje.rto_real).cast(Numeric), 3))
        .outerjoin(Viaje, Viaje.unidad_id == Unidad.id)
        .group_by(Unidad.id).order_by(Unidad.clave)
    ).all()
    return [_unidad_dict(u, n, rto) for (u, n, rto) in filas]


@app.post("/api/unidades")
async def unidad_crear(request: Request, user: dict = Depends(require_admin),
                       db: Session = Depends(get_db)) -> dict:
    f = await request.form()
    clave = "".join((f.get("clave") or "").upper().split())
    if len(clave) < 2:
        raise HTTPException(400, "La clave es obligatoria (ej. T205, C012)")
    if db.execute(select(Unidad).where(Unidad.clave == clave)).scalar_one_or_none():
        raise HTTPException(400, f"La unidad {clave} ya existe")
    tipo = f.get("tipo") or ("TRACTO" if clave.startswith("T") else "CAMION")
    u = Unidad(clave=clave, tipo=_tipo_unidad(tipo))
    _apply_unidad(u, f)
    db.add(u)
    _commit(db, f"La unidad {clave} ya existe")
    return {"ok": True, "id": u.id, "clave": u.clave}


@app.post("/api/unidades/{uid}")
async def unidad_editar(uid: int, request: Request, user: dict = Depends(require_admin),
                        db: Session = Depends(get_db)) -> dict:
    u = db.get(Unidad, uid)
    if u is None:
        raise HTTPException(404, "Unidad no encontrada")
    f = await request.form()
    nueva = "".join((f.get("clave") or "").upper().split())
    if nueva and nueva != u.clave:
        if db.execute(select(Unidad).where(Unidad.clave == nueva)).scalar_one_or_none():
            raise HTTPException(400, f"La clave {nueva} ya está en uso")
        u.clave = nueva
    _apply_unidad(u, f)
    _commit(db, "La clave ya está en uso")
    return {"ok": True, "id": u.id}


@app.post("/api/unidades/{uid}/titular")
async def unidad_titular(uid: int, request: Request, user: dict = Depends(require_coordinador),
                         db: Session = Depends(get_db)) -> dict:
    """Relaciona (o cambia) el OPERADOR TITULAR de una unidad. El coordinador puede hacerlo
    sin tocar el resto de la unidad (esa edición sigue siendo de admin). operador_id vacío
    o 0 = quitar el titular."""
    u = db.get(Unidad, uid)
    if u is None:
        raise HTTPException(404, "Unidad no encontrada")
    f = await request.form()
    op_id = _pi(f.get("operador_id")) or None
    if op_id and db.get(Operador, op_id) is None:
        raise HTTPException(400, "Operador no válido")
    u.operador_asignado_id = op_id
    db.commit()
    op = db.get(Operador, op_id) if op_id else None
    log.info("Titular de la unidad %s -> operador %s", u.clave, op_id)
    return {"ok": True, "unidad": u.clave, "operador_id": u.operador_asignado_id,
            "operador_titular": op.nombre if op else None}


@app.post("/api/remolques/{rid}/titular")
async def remolque_titular(rid: int, request: Request, user: dict = Depends(require_coordinador),
                           db: Session = Depends(get_db)) -> dict:
    """Relaciona (o cambia) el OPERADOR TITULAR de un remolque (su responsable habitual). Lo
    hace el coordinador sin tocar el resto del remolque. operador_id vacío o 0 = quitar."""
    r = db.get(Remolque, rid)
    if r is None:
        raise HTTPException(404, "Remolque no encontrado")
    f = await request.form()
    op_id = _pi(f.get("operador_id")) or None
    if op_id and db.get(Operador, op_id) is None:
        raise HTTPException(400, "Operador no válido")
    r.operador_asignado_id = op_id
    db.commit()
    op = db.get(Operador, op_id) if op_id else None
    log.info("Titular del remolque %s -> operador %s", r.eco, op_id)
    return {"ok": True, "remolque": r.eco, "operador_id": r.operador_asignado_id,
            "operador_titular": op.nombre if op else None}


@app.delete("/api/unidades/{uid}")
def unidad_borrar(uid: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Borra la unidad si no hay hechos colgando de ella; si los hay, la da de baja.

    Antes esto miraba 3 de las 10 claves foráneas (viajes, termo, escaneos) y, si esas tres
    daban cero, hacía el borrado duro igualmente. Las otras siete seguían apuntando, así que
    Postgres lo rechazaba y el usuario recibía un 500: ni se borraba ni se daba de baja. Con
    el catálogo cargado le pasaba a 35 de las 55 unidades, porque TODAS tienen alias.
    """
    u = db.get(Unidad, uid)
    if u is None:
        raise HTTPException(404, "Unidad no encontrada")
    retienen = _retienen_de_verdad(db, "unidades", uid)
    if retienen:
        u.activo = False
        registrar_actividad(db, accion="unidad_baja", usuario=user, entidad="unidad",
                            entidad_id=uid, meta={"clave": u.clave, "retienen": retienen},
                            commit=False)
        db.commit()
        # `viajes` y `escaneos` se conservan porque la pantalla ya los nombraba.
        return {"ok": True, "baja": True, "retienen": retienen,
                "viajes": retienen.get("viajes", 0),
                "escaneos": retienen.get("escaneos_motor", 0)}
    clave = u.clave
    suelto = _desenganchar(db, "unidades", uid)
    registrar_actividad(db, accion="unidad_eliminada", usuario=user, entidad="unidad",
                        entidad_id=uid, meta={"clave": clave, "desenganchado": suelto},
                        commit=False)
    db.delete(u)
    db.commit()
    return {"ok": True, "eliminado": True, "desenganchado": suelto}


def _remolque_dict(r: Remolque) -> dict:
    return {
        "id": r.id, "eco": r.eco, "eco_nuevo": r.eco_nuevo, "marca": r.marca, "anio": r.anio,
        "placa": r.placa, "placas_nuevas": r.placas_nuevas, "serie": r.serie,
        "serie_thermo": r.serie_thermo, "descripcion": r.descripcion,
        "es_dolly": r.es_dolly, "usa_combustible": r.usa_combustible, "activo": r.activo,
        # La medida decide si puede ir en pareja: dos remolques sólo se enganchan si los
        # dos son de 40 pies. Va al cliente para que el selector lo aplique al marcar.
        "medida_pies": r.medida_pies,
        "operador_asignado_id": r.operador_asignado_id,
        "operador_titular": r.operador_titular.nombre if r.operador_titular else None,
    }


def _apply_remolque(r: Remolque, f) -> None:
    for k in ("eco_nuevo", "marca", "placa", "placas_nuevas", "serie", "serie_thermo", "descripcion"):
        if k in f:
            setattr(r, k, _pstr(f, k))
    if "anio" in f:
        r.anio = _pi(f.get("anio"))
    if "es_dolly" in f:
        r.es_dolly = _pbool(f, "es_dolly")
    if "usa_combustible" in f:
        r.usa_combustible = _pbool(f, "usa_combustible")
    if "activo" in f:
        r.activo = _pbool(f, "activo")


@app.get("/api/remolques")
# LECTURA con require_gestion (incluye al gerente), igual que /api/operadores. Antes
# era require_coordinador y dejaba fuera al gerente: su formulario de "Levantar viaje"
# pedía este catálogo, recibía 403 y lo tragaba en silencio (catch vacío), así que la
# lista de remolques salía VACÍA y no se les podían enganchar remolques a los viajes.
# Las mutaciones (crear/editar/titular) siguen restringidas al coordinador y al admin.
def remolques(user: dict = Depends(require_gestion), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(select(Remolque).order_by(Remolque.eco)).scalars().all()
    return [_remolque_dict(r) for r in filas]


@app.post("/api/remolques")
async def remolque_crear(request: Request, user: dict = Depends(require_admin),
                         db: Session = Depends(get_db)) -> dict:
    f = await request.form()
    eco = (f.get("eco") or "").strip().upper()
    if not eco:
        raise HTTPException(400, "El ECO es obligatorio")
    if db.execute(select(Remolque).where(Remolque.eco == eco)).scalar_one_or_none():
        raise HTTPException(400, f"El remolque {eco} ya existe")
    r = Remolque(eco=eco)
    _apply_remolque(r, f)
    db.add(r)
    _commit(db, f"El remolque {eco} ya existe")
    return {"ok": True, "id": r.id, "eco": r.eco}


@app.post("/api/remolques/{rid}")
async def remolque_editar(rid: int, request: Request, user: dict = Depends(require_admin),
                          db: Session = Depends(get_db)) -> dict:
    r = db.get(Remolque, rid)
    if r is None:
        raise HTTPException(404, "Remolque no encontrado")
    f = await request.form()
    nuevo = (f.get("eco") or "").strip().upper()
    if nuevo and nuevo != r.eco:
        if db.execute(select(Remolque).where(Remolque.eco == nuevo)).scalar_one_or_none():
            raise HTTPException(400, f"El ECO {nuevo} ya está en uso")
        r.eco = nuevo
    _apply_remolque(r, f)
    _commit(db, "El ECO ya está en uso")
    return {"ok": True, "id": r.id}


@app.delete("/api/remolques/{rid}")
def remolque_borrar(rid: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Igual que las unidades. Aquí no se comprobaba NADA, así que los 87 daban un 500."""
    r = db.get(Remolque, rid)
    if r is None:
        raise HTTPException(404, "Remolque no encontrado")
    retienen = _retienen_de_verdad(db, "remolques", rid)
    if retienen:
        r.activo = False
        registrar_actividad(db, accion="remolque_baja", usuario=user, entidad="remolque",
                            entidad_id=rid, meta={"eco": r.eco, "retienen": retienen},
                            commit=False)
        db.commit()
        return {"ok": True, "baja": True, "retienen": retienen}
    eco = r.eco
    suelto = _desenganchar(db, "remolques", rid)
    registrar_actividad(db, accion="remolque_eliminado", usuario=user, entidad="remolque",
                        entidad_id=rid, meta={"eco": eco, "desenganchado": suelto},
                        commit=False)
    db.delete(r)
    db.commit()
    return {"ok": True, "eliminado": True, "desenganchado": suelto}


@app.get("/api/operadores")
def operadores(user: dict = Depends(require_gestion), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(
        select(Operador, func.count(Viaje.id), func.round(func.avg(Viaje.rto_real).cast(Numeric), 3))
        .outerjoin(Viaje, and_(Viaje.operador_id == Operador.id, VIGENTE))
        .group_by(Operador.id).order_by(func.count(Viaje.id).desc()).limit(500)
    ).all()
    cuentas = {oid: (u, ua) for oid, u, ua in db.execute(
        select(Usuario.operador_id, Usuario.username, Usuario.ultimo_acceso)
        .where(Usuario.operador_id.isnot(None))).all()}
    return [
        {"id": o.id, "nombre": o.nombre, "numero": o.numero, "viajes": n,
         "rto_real_prom": float(rto) if rto is not None else None,
         "estatus": o.estatus, "activo": o.activo, "tiene_foto": bool(o.foto),
         "provisional": o.provisional, "rol": o.rol, "telefono": o.telefono,
         "tiene_cuenta": o.id in cuentas,
         "username": cuentas[o.id][0] if o.id in cuentas else None,
         "ultimo_acceso": (cuentas[o.id][1].isoformat()
                           if o.id in cuentas and cuentas[o.id][1] else None)}
        for (o, n, rto) in filas
    ]


@app.get("/api/operadores/provisionales")
def operadores_provisionales(user: dict = Depends(require_gestion),
                             db: Session = Depends(get_db)) -> list[dict]:
    """Operadores auto-creados del chat que esperan resolución humana (dar de alta o
    vincular al conductor real). Declarado ANTES de /{op_id} para que no lo capture."""
    filas = db.execute(
        select(Operador, func.count(Viaje.id), func.max(Viaje.fecha))
        .outerjoin(Viaje, Viaje.operador_id == Operador.id)
        .where(Operador.provisional.is_(True))
        .group_by(Operador.id).order_by(func.count(Viaje.id).desc())
    ).all()
    out = []
    for o, n, ult in filas:
        unidades = [c for (c,) in db.execute(
            select(Unidad.clave).join(Viaje, Viaje.unidad_id == Unidad.id)
            .where(Viaje.operador_id == o.id).distinct().limit(5))]
        out.append({"id": o.id, "nombre": o.nombre, "viajes": n,
                    "ultimo": ult.isoformat() if ult else None, "unidades": unidades})
    return out


@app.get("/api/operadores/duplicados")
def operadores_duplicados(user: dict = Depends(require_gestion),
                          db: Session = Depends(get_db)) -> list[dict]:
    """Grupos de operadores probablemente DUPLICADOS (mismo nombre des-ordenado / por
    nombre o apellidos). Para depurar el catálogo: normalmente un registro con número y 0
    viajes (del Excel) + otro con los viajes bajo el nombre libre. NO fusiona nada: solo
    sugiere; el usuario decide (puede haber homónimos distintos)."""
    from .captura import _tokens_nombre
    ops = db.execute(select(Operador.id, Operador.nombre, Operador.numero)).all()
    viajes = dict(db.execute(
        select(Viaje.operador_id, func.count(Viaje.id)).group_by(Viaje.operador_id)).all())
    data = [(i, n, nu, _tokens_nombre(n), viajes.get(i, 0)) for (i, n, nu) in ops]
    usados: set[int] = set()
    grupos: list[dict] = []
    for a in data:
        if a[0] in usados or len(a[3]) < 2:
            continue
        grupo = [b for b in data if b[0] not in usados and len(b[3]) >= 2
                 and (a[3] <= b[3] or b[3] <= a[3])]
        if len(grupo) < 2:
            continue
        for g in grupo:
            usados.add(g[0])
        # destino sugerido: preferir con número, luego más viajes, luego nombre más largo
        destino = sorted(grupo, key=lambda x: (x[2] is None, -x[4], -len(x[1])))[0]
        grupos.append({
            "destino_sugerido": destino[0],
            "miembros": [
                {"id": i, "nombre": n, "numero": nu, "viajes": v}
                for (i, n, nu, _t, v) in sorted(grupo, key=lambda x: -x[4])
            ],
        })
    grupos.sort(key=lambda g: -sum(m["viajes"] for m in g["miembros"]))
    return grupos


@app.post("/api/operadores/fusionar-lote")
async def operadores_fusionar_lote(request: Request, user: dict = Depends(require_admin),
                                   db: Session = Depends(get_db)) -> dict:
    """Fusiona varios operadores ORIGEN en uno DESTINO (mueve viajes/termo/asignaciones y
    borra los orígenes). Body: destino_id, origenes (ids separados por coma)."""
    f = await request.form()
    destino_id = _pi(f.get("destino_id"))
    destino = db.get(Operador, destino_id) if destino_id else None
    if destino is None:
        raise HTTPException(404, "Operador destino no encontrado")
    origenes = [int(x) for x in str(f.get("origenes") or "").split(",") if x.strip().isdigit()]
    movidos = 0
    for oid in origenes:
        if oid == destino.id:
            continue
        o = db.get(Operador, oid)
        if o is None:
            continue
        db.execute(update(Viaje).where(Viaje.operador_id == oid).values(operador_id=destino.id))
        db.execute(update(AuditoriaThermo).where(AuditoriaThermo.operador_id == oid)
                   .values(operador_id=destino.id))
        db.execute(update(Unidad).where(Unidad.operador_asignado_id == oid)
                   .values(operador_asignado_id=destino.id))
        db.delete(o)
        movidos += 1
    _commit(db, "No se pudo fusionar el lote")
    return {"ok": True, "destino": destino.nombre, "fusionados": movidos}


@app.post("/api/operadores/{op_id}/confirmar")
async def operador_confirmar(op_id: int, request: Request, user: dict = Depends(require_admin),
                             db: Session = Depends(get_db)) -> dict:
    """Da de alta un operador provisional como REAL (opcional: número y rol)."""
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")
    f = await request.form()
    numero = _num_empleado(f.get("numero"))
    if _numero_en_uso(db, numero, op_id):
        raise HTTPException(400, f"El número de empleado {numero} ya está en uso")
    if numero is not None:
        o.numero = numero
    if f.get("rol"):
        o.rol = _pstr(f, "rol")
    if (f.get("nombre") or "").strip():
        o.nombre = " ".join(f.get("nombre").strip().upper().split())
    o.provisional = False
    _commit(db, "No se pudo confirmar el operador")
    return {"ok": True, "id": o.id, "nombre": o.nombre}


@app.post("/api/operadores/{op_id}/fusionar")
async def operador_fusionar(op_id: int, request: Request, user: dict = Depends(require_admin),
                            db: Session = Depends(get_db)) -> dict:
    """Vincula un operador (normalmente provisional) al conductor REAL: mueve sus viajes,
    su termo y sus asignaciones al destino y borra el origen. op_id=origen, destino_id=real."""
    f = await request.form()
    destino_id = _pi(f.get("destino_id"))
    origen = db.get(Operador, op_id)
    destino = db.get(Operador, destino_id) if destino_id else None
    if origen is None or destino is None:
        raise HTTPException(404, "Operador origen o destino no encontrado")
    if origen.id == destino.id:
        raise HTTPException(400, "No se puede vincular un operador consigo mismo")
    db.execute(update(Viaje).where(Viaje.operador_id == origen.id).values(operador_id=destino.id))
    db.execute(update(AuditoriaThermo).where(AuditoriaThermo.operador_id == origen.id)
               .values(operador_id=destino.id))
    db.execute(update(Unidad).where(Unidad.operador_asignado_id == origen.id)
               .values(operador_asignado_id=destino.id))
    db.delete(origen)
    _commit(db, "No se pudo vincular el operador")
    return {"ok": True, "destino": destino.nombre, "origen_borrado": op_id}


@app.get("/api/operadores/{op_id}")
def operador_detalle(op_id: int, user: dict = Depends(require_gestion),
                     db: Session = Depends(get_db)) -> dict:
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")

    total = db.scalar(select(func.count()).select_from(Viaje).where(Viaje.operador_id == op_id))
    rto_prom = db.scalar(
        select(func.round(func.avg(Viaje.rto_real).cast(Numeric), 3)).where(Viaje.operador_id == op_id)
    )
    dif_prom = db.scalar(
        select(func.round(func.avg(Viaje.dif).cast(Numeric), 1)).where(Viaje.operador_id == op_id)
    )

    mes = func.to_char(Viaje.fecha, "YYYY-MM")
    rend = db.execute(
        select(mes, func.round(func.avg(Viaje.rto_real).cast(Numeric), 3))
        .where(Viaje.operador_id == op_id, Viaje.rto_real.isnot(None))
        .group_by(mes).order_by(mes)
    ).all()

    unids = db.execute(
        select(Unidad.clave, Unidad.tipo, func.count(Viaje.id), func.max(Viaje.fecha))
        .join(Viaje, Viaje.unidad_id == Unidad.id)
        .where(Viaje.operador_id == op_id)
        .group_by(Unidad.clave, Unidad.tipo).order_by(func.count(Viaje.id).desc())
    ).all()

    anoms = db.execute(
        select(Anomalia.id, Anomalia.tipo, Anomalia.descripcion, Anomalia.estado,
               Unidad.clave, Viaje.fecha)
        .join(Viaje, Anomalia.viaje_id == Viaje.id)
        .join(Unidad, Viaje.unidad_id == Unidad.id)
        .where(Viaje.operador_id == op_id)
        .order_by(Anomalia.creado_en.desc()).limit(50)
    ).all()

    ultimos = db.execute(
        select(Viaje).where(Viaje.operador_id == op_id)
        .order_by(Viaje.fecha.desc(), Viaje.id.desc()).limit(10)
    ).scalars().all()

    cuenta = db.scalar(select(Usuario).where(Usuario.operador_id == op_id))

    # Resumen operativo completo: viaje en curso, recargas recientes y penalizaciones.
    asig = db.scalar(
        select(AsignacionViaje).where(
            AsignacionViaje.operador_id == op_id,
            AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
        .order_by(AsignacionViaje.creada_en.desc()).limit(1))
    recargas = db.execute(
        select(SolicitudRecarga).where(SolicitudRecarga.operador_id == op_id)
        .order_by(SolicitudRecarga.creada_en.desc()).limit(6)).scalars().all()
    pen_all = db.execute(
        select(SeguimientoDescuento.fecha, SeguimientoDescuento.unidad_clave,
               SeguimientoDescuento.tipo, SeguimientoDescuento.lts)
        .join(Viaje, SeguimientoDescuento.viaje_id == Viaje.id)
        .where(Viaje.operador_id == op_id)
        .order_by(SeguimientoDescuento.aplicada_en.desc().nulls_last())).all()

    return {
        "id": o.id, "nombre": o.nombre, "numero": o.numero, "activo": o.activo,
        "cuenta": {"username": cuenta.username} if cuenta else None,
        "telefono": o.telefono, "licencia": o.licencia,
        "licencia_vence": o.licencia_vence.isoformat() if o.licencia_vence else None,
        "licencia_tipo": o.licencia_tipo, "curp": o.curp,
        "licencia_expedida": o.licencia_expedida.isoformat() if o.licencia_expedida else None,
        "licencia_doc": o.licencia_doc, "licencia_doc_mime": o.licencia_doc_mime,
        "licencia_doc_nombre": o.licencia_doc_nombre,
        "licencia_doc_en": o.licencia_doc_en.isoformat() if o.licencia_doc_en else None,
        # Días que le quedan a la licencia: negativo = ya venció. La vigencia es el único
        # dato de la ficha que caduca solo, y circular con ella vencida es una multa y una
        # póliza sin efecto. Se calcula con la fecha de la FLOTA, no con la del navegador.
        "dias_licencia": ((o.licencia_vence - fecha_flota()).days
                          if o.licencia_vence else None),
        "ingreso": o.ingreso.isoformat() if o.ingreso else None,
        "estatus": o.estatus, "rol": o.rol, "notas": o.notas, "tiene_foto": bool(o.foto),
        "viajes": total, "rto_real_prom": float(rto_prom) if rto_prom is not None else None,
        "dif_prom": float(dif_prom) if dif_prom is not None else None,
        "rend_mensual": {"labels": [r[0] for r in rend], "valores": [float(r[1]) for r in rend]},
        "unidades": [
            {"clave": c, "tipo": t.value, "viajes": n, "ultimo": f.isoformat() if f else None}
            for (c, t, n, f) in unids
        ],
        "anomalias": [
            {"id": i, "tipo": tp, "descripcion": d, "estado": e.value, "unidad": u,
             "fecha": f.isoformat() if f else None}
            for (i, tp, d, e, u, f) in anoms
        ],
        "ultimos_viajes": [_viaje_dict(v) for v in ultimos],
        "viaje_activo": _asignacion_dict(asig, db) if asig else None,
        "recargas": [_solicitud_dict(s) for s in recargas],
        "penalizaciones": {
            "n": len(pen_all),
            "total_lts": round(sum((r[3] or 0) for r in pen_all), 1),
            "recientes": [{"fecha": r[0].isoformat() if r[0] else None, "unidad": r[1],
                           "tipo": r[2], "lts": r[3]} for r in pen_all[:6]],
        },
    }


def _generar_password(n: int = 8) -> str:
    """Contraseña temporal legible (sin l/o/0/1 para no confundir al dictarla)."""
    import secrets
    alfabeto = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(n))


def _norm_username(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9._-]", "", (s or "").strip().lower())


def _verificar_clave_admin(db: Session, sesion: dict, clave: str) -> None:
    """Re-autenticación: exige la contraseña del ADMIN ACTUAL (el de la sesión) antes de una
    acción sensible (crear otro admin, cambiar/restablecer contraseñas). Aunque la sesión ya
    sea de admin, esto evita que alguien que encontró la sesión abierta cree cuentas o cambie
    claves. Se verifica del lado del servidor contra el hash guardado; la clave no se persiste."""
    clave = (clave or "").strip()
    if not clave:
        raise HTTPException(401, "Ingresa tu contraseña de administrador para confirmar.")
    yo = db.get(Usuario, sesion.get("id"))
    if yo is None or not verify_password(clave, yo.password_hash):
        raise HTTPException(403, "Contraseña de administrador incorrecta.")


def _password_personal(f) -> tuple[str, bool]:
    """Resuelve la contraseña de una cuenta de PERSONAL desde el form. Si viene vacía, genera
    una FUERTE; si viene escrita, exige que cumpla la política de fortaleza. Devuelve
    (password, generada)."""
    password = (f.get("password") or "").strip()
    if not password:
        return generar_password_fuerte(), True
    err = validar_password_fuerte(password)
    if err:
        raise HTTPException(400, err)
    return password, False


def _aplicar_perfil(u: Usuario, f, *, crear: bool) -> None:
    """Aplica los datos de perfil editables (correo, puesto, teléfono, foto) del form a la
    cuenta. correo/puesto viven en `prefs` (JSONB, pensado para datos personales editables);
    teléfono y foto tienen columna propia. En alta (`crear`) solo escribe lo que venga; en
    edición respeta 'no enviado = no tocar' y trata cadena vacía como borrar."""
    prefs = dict(u.prefs or {})
    if "correo" in f:
        correo = (f.get("correo") or "").strip()
        if correo:
            prefs["correo"] = correo
        else:
            prefs.pop("correo", None)
    if "puesto" in f:
        puesto = " ".join((f.get("puesto") or "").strip().split())
        if puesto:
            prefs["puesto"] = puesto
        else:
            prefs.pop("puesto", None)
    u.prefs = prefs or None
    if "telefono" in f:
        u.telefono = (f.get("telefono") or "").strip() or None
    foto = (f.get("foto") or "").strip()
    if foto:
        # data URI "data:image/png;base64,...." → separa mime y base64
        if foto.startswith("data:") and "," in foto:
            cab, _, b64 = foto.partition(",")
            u.foto = b64
            u.foto_mime = cab[5:].split(";")[0] or "image/jpeg"
        else:
            u.foto = foto
            u.foto_mime = u.foto_mime or "image/jpeg"
    elif "foto" in f and not crear:
        # foto enviada vacía en edición = quitar
        u.foto = None
        u.foto_mime = None


@app.post("/api/operadores/{op_id}/crear-acceso")
async def operador_crear_acceso(op_id: int, request: Request, user: dict = Depends(require_admin),
                                db: Session = Depends(get_db)) -> dict:
    """Da de alta la CUENTA de login de un operador del padrón (#4). La cuenta queda ligada a su
    ficha (rol operador) para que el sistema sepa quién captura. Si no se da contraseña, se
    genera una temporal y se devuelve UNA vez para entregársela al operador."""
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")
    ya = db.scalar(select(Usuario).where(Usuario.operador_id == op_id))
    if ya is not None:
        raise HTTPException(400, f"Este operador ya tiene una cuenta: {ya.username}")
    f = await request.form()
    username = (f.get("username") or "").strip()
    # El USUARIO ES EL NÚMERO DE EMPLEADO, tal cual: CFRUIT056 entra como CFRUIT056. Una
    # sola identidad para la persona, la misma que la gasolinera imprime en cada carga.
    # Sin número todavía —126 fichas están así— se cae al id, que al menos no se repite.
    username = _norm_username(username) if username else (o.numero or f"op{o.id}")
    if len(username) < 3:
        raise HTTPException(400, "El usuario debe tener al menos 3 caracteres")
    # Insensible a mayúsculas, igual que el login: si no lo fuera se podrían crear dos
    # cuentas que después el login no sabría distinguir.
    if db.scalar(select(Usuario).where(
            func.lower(Usuario.username) == username.lower())) is not None:
        raise HTTPException(400, f"El usuario '{username}' ya está en uso")
    password = (f.get("password") or "").strip()
    generada = False
    if not password:
        password = _generar_password()
        generada = True
    elif len(password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres")
    u = Usuario(username=username, password_hash=hash_password(password),
                nombre=o.nombre, rol="operador", operador_id=op_id,
                foto=o.foto, foto_mime=o.foto_mime,   # hereda la foto del padrón si ya existe
                prefs={"acc_enc": cifrar(password)})   # bóveda: para poder MOSTRARla luego
    db.add(u)
    db.commit()
    log.info("Acceso creado para operador %s (%s) -> usuario %s", o.id, o.nombre, username)
    return {"ok": True, "username": username, "password": password, "generada": generada,
            "operador": o.nombre}


@app.post("/api/operadores/{op_id}/reset-acceso")
async def operador_reset_acceso(op_id: int, request: Request, user: dict = Depends(require_admin),
                                db: Session = Depends(get_db)) -> dict:
    """Restablece la contraseña de la cuenta del operador (los roles no la cambian ellos mismos,
    #7). Devuelve la nueva contraseña UNA vez."""
    u = db.scalar(select(Usuario).where(Usuario.operador_id == op_id))
    if u is None:
        raise HTTPException(404, "Este operador no tiene cuenta")
    f = await request.form()
    password = (f.get("password") or "").strip()
    generada = False
    if not password:
        password = _generar_password()
        generada = True
    elif len(password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres")
    u.password_hash = hash_password(password)
    u.sesion_version = (u.sesion_version or 0) + 1   # cierra las sesiones abiertas con la vieja
    u.prefs = {**(u.prefs or {}), "acc_enc": cifrar(password)}   # bóveda: refresca el código visible
    db.commit()
    log.info("Contraseña restablecida para operador %s (usuario %s)", op_id, u.username)
    return {"ok": True, "username": u.username, "password": password, "generada": generada}


def _username_libre(db: Session, base: str, op_id: int, usados: set) -> str:
    """Devuelve un username libre para el alta masiva: prueba op<numero>, luego op<id>, luego
    op<id>-2, -3... evitando choques con la BD y con los ya asignados en este mismo lote."""
    def libre(u: str) -> bool:
        return u not in usados and db.scalar(select(Usuario.id).where(Usuario.username == u)) is None
    if libre(base):
        return base
    alt = f"op{op_id}"
    if libre(alt):
        return alt
    i = 2
    while not libre(f"{alt}-{i}"):
        i += 1
    return f"{alt}-{i}"


@app.post("/api/operadores/crear-accesos")
def operadores_crear_accesos(ids: str = "", user: dict = Depends(require_admin),
                             db: Session = Depends(get_db)) -> dict:
    """Alta MASIVA: crea la cuenta de login de los operadores ACTIVOS que aún no tienen una,
    genera usuario (op<numero>) y contraseña temporal legible, y devuelve la hoja (usuario +
    contraseña, una sola vez) para imprimir y repartir. Idempotente: no toca a los que ya
    tienen cuenta. `ids` opcional (coma) limita a ese subconjunto: el frontend procesa por
    LOTES para mostrar progreso. El hash (pbkdf2, caro) se hace en PARALELO —libera el GIL— para
    no tardar minutos con flotas grandes."""
    solo = {int(x) for x in ids.replace(" ", "").split(",") if x.isdigit()} if ids else None
    con_cuenta = set(db.scalars(
        select(Usuario.operador_id).where(Usuario.operador_id.isnot(None))).all())
    q = select(Operador).where(Operador.activo.is_(True))
    if solo is not None:
        q = q.where(Operador.id.in_(solo))
    # Orden NATURAL, no alfabético. Con el número en texto, `order_by` pone «1000» antes
    # que «56», y la hoja de accesos que se imprime de aquí saldría en un orden que nadie
    # reconoce. Por largo y luego por valor: 56 < 500 < 1000, y los alfanuméricos después.
    ops = db.execute(q.order_by(
        Operador.numero.is_(None), func.length(Operador.numero), Operador.numero,
        Operador.nombre)).scalars().all()
    usados: set = set()
    pend = []   # (operador, username, password_plano)
    for o in ops:
        if o.id in con_cuenta:
            continue
        base = o.numero or f"op{o.id}"
        username = _username_libre(db, base, o.id, usados)
        usados.add(username)
        pend.append((o, username, _generar_password()))
    hashes: list[str] = []
    if pend:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            hashes = list(ex.map(hash_password, [p[2] for p in pend]))
    creados = []
    for (o, username, password), h in zip(pend, hashes):
        db.add(Usuario(username=username, password_hash=h, nombre=o.nombre, rol="operador",
                       operador_id=o.id, foto=o.foto, foto_mime=o.foto_mime,
                       prefs={"acc_enc": cifrar(password)}))   # bóveda: código visible luego
        creados.append({"operador_id": o.id, "nombre": o.nombre, "numero": o.numero,
                        "username": username, "password": password})
    db.commit()
    log.info("Alta masiva de accesos: %s creados por %s", len(creados), user.get("username"))
    return {"ok": True, "n": len(creados), "creados": creados}


@app.post("/api/accesos/revelar")
async def accesos_revelar(request: Request, user: dict = Depends(require_admin),
                          db: Session = Depends(get_db)) -> dict:
    """Revela las claves de acceso de OPERADOR guardadas en la bóveda (cifrado reversible).
    Requiere re-autenticación (contraseña del admin actual). Solo son recuperables las cuentas
    cuyo acceso se creó/restableció con la bóveda activa; las anteriores solo tienen hash (una
    vía) y hay que restablecerlas para volver a verlas. Las cuentas de PERSONAL nunca están
    aquí: su clave es irrecuperable por diseño."""
    f = await request.form()
    _verificar_clave_admin(db, user, f.get("clave_admin"))
    us = db.execute(select(Usuario).where(
        Usuario.rol == "operador", Usuario.operador_id.isnot(None))).scalars().all()
    claves: dict[int, str] = {}
    no_recuperable: list[int] = []
    for u in us:
        blob = (u.prefs or {}).get("acc_enc")
        pw = descifrar(blob) if blob else None
        if pw is not None:
            claves[u.operador_id] = pw
        else:
            no_recuperable.append(u.operador_id)
    log.info("Revelado de %s claves de acceso por %s", len(claves), user.get("username"))
    return {"ok": True, "claves": claves, "no_recuperable": no_recuperable, "debil": vault_debil()}


# ─────────────────────────────────────────────────────────────────────────────
# Gestión de usuarios (Admin): quién tiene acceso, con qué ROL (permisos) y su último
# acceso (rastro). El alta de operadores vive en su padrón (crear-acceso); aquí se dan de
# alta y gestionan las cuentas de PERSONAL (coordinador/combustible/gerente/admin).
# ─────────────────────────────────────────────────────────────────────────────
ROLES_PERSONAL = ("admin", "coordinador", "combustible", "gerente")


def _usuario_dict(u: Usuario) -> dict:
    prefs = u.prefs or {}
    return {
        "id": u.id, "username": u.username, "nombre": u.nombre, "rol": u.rol,
        "activo": u.activo, "operador_id": u.operador_id, "tiene_foto": bool(u.foto),
        "correo": prefs.get("correo"), "puesto": prefs.get("puesto"), "telefono": u.telefono,
        "creado_en": u.creado_en.isoformat() if u.creado_en else None,
        "ultimo_acceso": u.ultimo_acceso.isoformat() if u.ultimo_acceso else None,
    }


def _no_dejar_sin_admin(db: Session, u: Usuario) -> None:
    """Evita quedarse sin NINGÚN admin activo (bloqueo total del sistema)."""
    otros = db.scalar(select(func.count()).select_from(Usuario).where(
        Usuario.rol == "admin", Usuario.activo.is_(True), Usuario.id != u.id))
    if not otros:
        raise HTTPException(400, "No puedes dejar al sistema sin ningún administrador activo")


@app.get("/api/usuarios")
def usuarios_listar(user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Todas las cuentas: quién tiene acceso, con qué rol y su último acceso (bitácora)."""
    # Solo PERSONAL: las cuentas de operador se gestionan en su Padrón (crear/reset-acceso),
    # no aquí; incluirlas duplicaba a la persona (aparecía en el Padrón y en esta lista).
    us = db.execute(select(Usuario).where(Usuario.rol.in_(ROLES_PERSONAL))
                    .order_by(Usuario.rol, Usuario.username)).scalars().all()
    return {"usuarios": [_usuario_dict(u) for u in us], "yo": user.get("id"),
            "roles": list(ROLES_PERSONAL)}


@app.post("/api/usuarios")
async def usuario_crear(request: Request, user: dict = Depends(require_admin),
                        db: Session = Depends(get_db)) -> dict:
    """Da de alta una cuenta de personal con NOMBRE real. Si no se da contraseña, se genera
    una temporal y se devuelve UNA vez para entregarla."""
    f = await request.form()
    username = _norm_username(f.get("username") or "")
    nombre = " ".join((f.get("nombre") or "").strip().split())
    rol = (f.get("rol") or "").strip().lower()
    if len(username) < 3:
        raise HTTPException(400, "El usuario debe tener al menos 3 caracteres")
    if not nombre:
        raise HTTPException(400, "Indica el nombre de la persona")
    if rol not in ROLES_PERSONAL:
        raise HTTPException(400, "Rol no válido")
    # Insensible a mayúsculas, igual que el login: si no lo fuera se podrían crear dos
    # cuentas que después el login no sabría distinguir.
    if db.scalar(select(Usuario).where(
            func.lower(Usuario.username) == username.lower())) is not None:
        raise HTTPException(400, f"El usuario '{username}' ya está en uso")
    # Dar de alta a OTRO ADMINISTRADOR exige re-autenticación del admin actual (acción sensible).
    if rol == "admin":
        _verificar_clave_admin(db, user, f.get("clave_admin"))
    password, generada = _password_personal(f)
    u = Usuario(username=username, password_hash=hash_password(password), nombre=nombre, rol=rol)
    _aplicar_perfil(u, f, crear=True)
    db.add(u); db.commit()
    log.info("Usuario dado de alta: %s (%s) rol %s por %s",
             username, nombre, rol, user.get("username"))
    return {"ok": True, "id": u.id, "username": username, "password": password, "generada": generada}


@app.post("/api/usuarios/{uid}")
async def usuario_editar(uid: int, request: Request, user: dict = Depends(require_admin),
                         db: Session = Depends(get_db)) -> dict:
    """Edita nombre, rol y estado (activo). Candados anti-bloqueo: no puedes cambiarte el rol
    ni desactivarte a ti mismo, ni dejar al sistema sin ningún admin activo."""
    u = db.get(Usuario, uid)
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    f = await request.form()
    es_yo = (uid == user.get("id"))
    if "nombre" in f and (f.get("nombre") or "").strip():
        u.nombre = " ".join(f.get("nombre").strip().split())
    nuevo_rol = (f.get("rol") or "").strip().lower()
    if nuevo_rol and nuevo_rol != u.rol:
        if es_yo:
            raise HTTPException(400, "No puedes cambiar tu propio rol")
        if nuevo_rol not in ROLES_PERSONAL and nuevo_rol != "operador":
            raise HTTPException(400, "Rol no válido")
        # Promover a alguien a ADMINISTRADOR exige re-autenticación del admin actual.
        if nuevo_rol == "admin":
            _verificar_clave_admin(db, user, f.get("clave_admin"))
        if u.rol == "admin":
            _no_dejar_sin_admin(db, u)
        u.rol = nuevo_rol
    if "activo" in f:
        activo = str(f.get("activo")).lower() in ("1", "true", "on", "si", "sí")
        if not activo:
            if es_yo:
                raise HTTPException(400, "No puedes desactivar tu propia cuenta")
            if u.rol == "admin":
                _no_dejar_sin_admin(db, u)
        u.activo = activo
    _aplicar_perfil(u, f, crear=False)
    db.commit()
    log.info("Usuario %s editado por %s", u.username, user.get("username"))
    return {"ok": True, "usuario": _usuario_dict(u)}


@app.delete("/api/usuarios/{uid}")
async def usuario_borrar(uid: int, request: Request, user: dict = Depends(require_admin),
                         db: Session = Depends(get_db)) -> dict:
    """Elimina una cuenta si nunca hizo nada; si hizo algo, la inhabilita y dice qué la retiene.

    Las 18 claves foráneas que apuntan a `usuarios` están en ON DELETE NO ACTION, así que un
    borrado duro de una cuenta con historial no es que sea mala idea: la base lo rechaza. Y
    forzarlo significaría dejar sin autor las órdenes que autorizó, que es justo lo que un
    botón no debe poder hacer.

    Lo que de verdad se quiere al "eliminar" a alguien es que no entre, y eso lo da
    `activo=False`: `autenticar` filtra por ese campo. El resto es el expediente, y el
    expediente no se borra.
    """
    u = db.get(Usuario, uid)
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    if uid == user.get("id"):
        raise HTTPException(400, "No puedes eliminar tu propia cuenta")
    f = await request.form()
    # Cortarle el acceso a otro es al menos tan sensible como cambiarle la clave, y el reset
    # sí pedía confirmación.
    _verificar_clave_admin(db, user, f.get("clave_admin"))
    if u.rol == "admin":
        _no_dejar_sin_admin(db, u)

    username, nombre = u.username, u.nombre
    retienen = _retienen_de_verdad(db, "usuarios", uid)
    if retienen:
        ya_estaba = not u.activo
        u.activo = False
        registrar_actividad(db, accion="usuario_inhabilitado", usuario=user,
                            entidad="usuario", entidad_id=uid,
                            meta={"username": username, "retienen": retienen})
        db.commit()
        log.info("Cuenta %s inhabilitada por %s (la retienen %s)",
                 username, user.get("username"), retienen)
        return {"ok": True, "inhabilitada": True, "ya_estaba": ya_estaba,
                "retienen": retienen, "username": username, "nombre": nombre}

    # La bitácora se SUELTA, no se borra: el renglón sigue contando que a las 14:32 alguien
    # inhabilitó una cuenta, sólo que ya no apunta a una fila que no existe. Antes bastaba
    # un único `login` para que la cuenta no se pudiera borrar jamás.
    suelto = _desenganchar(db, "usuarios", uid)
    registrar_actividad(db, accion="usuario_eliminado", usuario=user,
                        entidad="usuario", entidad_id=uid,
                        meta={"username": username, "nombre": nombre,
                              "desenganchado": suelto}, commit=False)
    db.delete(u)
    db.commit()
    log.info("Cuenta %s ELIMINADA por %s", username, user.get("username"))
    return {"ok": True, "eliminada": True, "username": username, "nombre": nombre,
            "desenganchado": suelto}


@app.get("/api/usuarios/{uid}/retienen")
def usuario_retienen(uid: int, user: dict = Depends(require_admin),
                     db: Session = Depends(get_db)) -> dict:
    """Qué historial cuelga de esta cuenta. Sirve para poder AVISAR antes de confirmar, en vez
    de que la persona descubra después que su "eliminar" fue en realidad un "inhabilitar"."""
    u = db.get(Usuario, uid)
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    return {"retienen": _retienen_de_verdad(db, "usuarios", uid), "activo": u.activo,
            "username": u.username, "es_yo": uid == user.get("id")}


@app.post("/api/usuarios/{uid}/reset")
async def usuario_reset(uid: int, request: Request, user: dict = Depends(require_admin),
                        db: Session = Depends(get_db)) -> dict:
    """Restablece la contraseña de una cuenta (nadie cambia la suya; el admin la gestiona).
    Devuelve la nueva contraseña UNA vez."""
    u = db.get(Usuario, uid)
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    f = await request.form()
    # Cambiar la contraseña de una cuenta de personal exige re-autenticación del admin actual.
    _verificar_clave_admin(db, user, f.get("clave_admin"))
    password, generada = _password_personal(f)
    u.password_hash = hash_password(password)
    u.sesion_version = (u.sesion_version or 0) + 1   # cierra las sesiones abiertas con la vieja
    db.commit()
    log.info("Contraseña restablecida para %s por %s", u.username, user.get("username"))
    return {"ok": True, "username": u.username, "password": password, "generada": generada}


@app.get("/api/usuarios/{uid}/foto")
def usuario_foto_ver(uid: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Foto de perfil de una cuenta (para el avatar de la lista/perfil en el panel de admin)."""
    u = db.get(Usuario, uid)
    if u is None or not u.foto:
        raise HTTPException(404, "Sin foto")
    try:
        data = base64.b64decode(u.foto)
    except (ValueError, TypeError):
        raise HTTPException(404, "Foto ilegible")
    return Response(content=data, media_type=u.foto_mime or "image/jpeg",
                    headers={"Cache-Control": "no-store"})


def _apply_operador(o: Operador, f) -> None:
    if "nombre" in f and (f.get("nombre") or "").strip():
        o.nombre = " ".join(f.get("nombre").strip().upper().split())
    if "numero" in f:
        o.numero = _num_empleado(f.get("numero"))
    for k in ("telefono", "licencia", "licencia_tipo", "estatus", "rol", "notas"):
        if k in f:
            setattr(o, k, _pstr(f, k))
    if "curp" in f:
        o.curp = _curp(f.get("curp"))
    if "licencia_vence" in f:
        o.licencia_vence = _pdate(f.get("licencia_vence"))
    if "licencia_expedida" in f:
        o.licencia_expedida = _pdate(f.get("licencia_expedida"))
    if "licencia_archivo" in f:
        _reclamar_licencia(o, f.get("licencia_archivo"), f.get("licencia_nombre"))
    if "ingreso" in f:
        o.ingreso = _pdate(f.get("ingreso"))
    if "activo" in f:
        o.activo = _pbool(f, "activo")


def _numero_en_uso(db: Session, numero: str | None, excluir_id: int | None) -> bool:
    if numero is None:
        return False
    q = select(Operador).where(Operador.numero == numero)
    if excluir_id is not None:
        q = q.where(Operador.id != excluir_id)
    return db.execute(q).scalars().first() is not None


@app.post("/api/operadores")
async def operador_crear(request: Request, user: dict = Depends(require_coordinador),
                         db: Session = Depends(get_db)) -> dict:
    """Alta de operador. La abre también el COORDINADOR: la rotación es alta y si un
    requerimiento llega con alguien que no está en el listado, el alta no puede esperar a
    un administrador. Borrar y fusionar siguen siendo de admin, que es lo destructivo."""
    f = await request.form()
    nombre = (f.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "El nombre es obligatorio")
    numero = _num_empleado(f.get("numero"))
    if _numero_en_uso(db, numero, None):
        raise HTTPException(400, f"El número {numero} ya está en uso")
    o = Operador(nombre=" ".join(nombre.upper().split()), numero=numero)
    _apply_operador(o, f)
    foto_file = f.get("foto_file")   # foto opcional adjunta en el mismo formulario de alta
    if foto_file is not None and getattr(foto_file, "filename", ""):
        o.foto, o.foto_mime = await _leer_foto(foto_file)
    db.add(o)
    _commit(db, f"El número {numero} ya está en uso")
    return {"ok": True, "id": o.id}


@app.post("/api/operadores/{op_id}")
async def operador_editar(op_id: int, request: Request, user: dict = Depends(require_admin),
                          db: Session = Depends(get_db)) -> dict:
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")
    f = await request.form()
    numero = _num_empleado(f.get("numero")) if "numero" in f else o.numero
    if _numero_en_uso(db, numero, op_id):
        raise HTTPException(400, f"El número {numero} ya está en uso")
    _apply_operador(o, f)
    _commit(db, f"El número {numero} ya está en uso")
    return {"ok": True, "id": o.id}


# Qué tablas apuntan a una dada. Se le pregunta al ESQUEMA en vez de escribir la lista a
# mano, porque una lista a mano se queda corta cada vez que alguien añade una tabla y nada
# la obliga a crecer: la de `operadores` miraba 3 de 9 claves foráneas, y por eso borrar a
# 135 de los 265 operadores devolvía un 500 en vez de darlos de baja.
_APUNTAN_A: dict[str, list[tuple[str, str]]] = {}


def _apuntan_a(tabla: str) -> list[tuple[str, str]]:
    """(tabla, columna) de cada clave foránea que apunta a `tabla`."""
    if tabla not in _APUNTAN_A:
        from sqlalchemy import inspect as _inspect
        insp = _inspect(engine)
        _APUNTAN_A[tabla] = [
            (t, fk["constrained_columns"][0])
            for t in insp.get_table_names()
            for fk in insp.get_foreign_keys(t)
            if fk["referred_table"] == tabla and fk["constrained_columns"]]
    return _APUNTAN_A[tabla]


def _quien_retiene(db: Session, tabla: str, pk: int) -> dict[str, int]:
    """Cuántas filas apuntan a esa fila, tabla por tabla. Sólo las que tienen alguna.

    Sirve para dos cosas: saber si se puede borrar de verdad, y poder DECIR qué lo retiene.
    Antes la respuesta traía un conteo de viajes y otro de unidades, y el resto era invisible.
    """
    from sqlalchemy import text as _text
    out: dict[str, int] = {}
    for t, c in _apuntan_a(tabla):
        n = db.scalar(_text(f'SELECT count(*) FROM "{t}" WHERE "{c}" = :pk'), {"pk": pk})
        if n:
            out[t] = int(n)
    return out


# ── Qué se lleva un borrado por delante, y qué lo impide ────────────────────────────────
# Las tres categorías no son una preferencia: salen de lo que cada tabla ES.
#
#   SE VA CON LA FILA · `alias_eco` es "un texto por el que se conoce a un activo" y no
#     significa nada sin él. Y `texto_norm` es ÚNICO: dejarlo huérfano reservaría ese
#     económico para siempre y nadie podría volver a darlo de alta.
#
#   SE SUELTA · la etiqueta es un holograma PEGADO y la tarjeta un plástico: existen fuera
#     del catálogo. El modelo ya tiene estado para una etiqueta sin activo ("sin_activo") y
#     el vínculo de la tarjeta nace en null a propósito. Borrarlos sería afirmar que el
#     objeto físico desapareció, que es justo lo que no sabemos.
#
#   RETIENE · todo lo demás —viajes, cargas, asientos, escaneos, solicitudes, asignaciones,
#     auditorías—. Son hechos ocurridos y borrar el activo los dejaría sin sujeto. Ahí el
#     borrado se vuelve baja, igual que ya hacían usuarios y operadores.
_ACOMPANAN: dict[str, tuple[str, ...]] = {
    "unidades": ("alias_eco",),
    "remolques": ("alias_eco",),
}
# El valor es lo que se le añade al UPDATE además de poner el vínculo en NULL. Casi
# siempre vacío; la etiqueta además cambia de estado.
_SE_SUELTAN: dict[str, dict[str, str]] = {
    "unidades": {"etiquetas_activo": ", estado='sin_activo'", "tarjetas_combustible": ""},
    "remolques": {"etiquetas_activo": ", estado='sin_activo'", "tarjetas_combustible": ""},
    # La bitácora no es autoría de nada: dice cuándo entró esta cuenta, no qué firmó. Las
    # otras 17 claves de `usuarios` sí son firmas y siguen reteniendo.
    "usuarios": {"registro_actividad": ""},
    # La unidad y el remolque que tenía asignados siguen existiendo sin él; el enlace con
    # el empleado del proveedor es un mapeo, no un hecho.
    "operadores": {"registro_actividad": "", "unidades": "", "remolques": "",
                   "empleados_proveedor": ""},
}


def _retienen_de_verdad(db: Session, tabla: str, pk: int) -> dict[str, int]:
    """Lo que IMPIDE borrar, que no es todo lo que apunta."""
    fuera = set(_ACOMPANAN.get(tabla, ())) | set(_SE_SUELTAN.get(tabla, ()))
    return {t: n for t, n in _quien_retiene(db, tabla, pk).items() if t not in fuera}


def _desenganchar(db: Session, tabla: str, pk: int) -> dict[str, int]:
    """Suelta lo que vive por su cuenta y borra lo que sólo existía para esta fila.

    Sin commit y en la misma transacción que el `delete` que viene detrás: si algo falla no
    queda una etiqueta suelta sin que se haya llegado a borrar nada.
    """
    from sqlalchemy import text as _text
    hecho: dict[str, int] = {}
    for t, c in _apuntan_a(tabla):
        if t in _SE_SUELTAN.get(tabla, ()):
            # El retoque sale de la tabla de arriba: "sin_activo" es la palabra que el
            # propio modelo usa para un holograma cuyo económico no está en el catálogo.
            extra = _SE_SUELTAN.get(tabla, {}).get(t, "")
            n = db.execute(_text(f'UPDATE "{t}" SET "{c}" = NULL{extra} WHERE "{c}" = :pk'),
                           {"pk": pk}).rowcount
            if n:
                hecho[f"{t}:sueltas"] = int(n)
        elif t in _ACOMPANAN.get(tabla, ()):
            n = db.execute(_text(f'DELETE FROM "{t}" WHERE "{c}" = :pk'), {"pk": pk}).rowcount
            if n:
                hecho[f"{t}:borrados"] = int(n)
    return hecho


@app.delete("/api/operadores/{op_id}")
def operador_borrar(op_id: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Da de baja a un operador y, con él, LE CORTA EL ACCESO.

    Que la baja no tocara la cuenta era un agujero: el operador despedido seguía entrando con
    su usuario y su clave. La ficha decía «inactivo» y la puerta seguía abierta.

    La reactivación NO devuelve el acceso sola, a propósito: restituir una llave es una
    decisión que alguien tiene que tomar a mano desde Usuarios y accesos. Se responde
    `cuenta_cortada` para que la pantalla lo pueda decir.
    """
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")

    # Primero la puerta. Va antes que nada porque es lo que de verdad importa de una baja, y
    # porque debe ocurrir tanto si el operador se borra como si sólo se desactiva.
    cuenta = db.scalar(select(Usuario).where(Usuario.operador_id == op_id))
    cortada = bool(cuenta is not None and cuenta.activo)
    if cortada:
        cuenta.activo = False

    # `todo` incluye lo que se suelta; `retienen`, sólo lo que impide borrar. Se guardan
    # los dos porque la pantalla ya nombraba las unidades a su cargo, y ésas ahora se
    # sueltan: si se leyeran de `retienen` saldría siempre 0, que parece un dato y no lo es.
    todo = _quien_retiene(db, "operadores", op_id)
    retienen = _retienen_de_verdad(db, "operadores", op_id)

    # La cuenta de la app es SUYA y no significa nada sin él, así que se va con él. Pero
    # sólo si ella misma no arrastra nada: si esa cuenta autorizó o despachó algo, manda
    # eso y el operador se queda en baja (con el acceso cortado, que es lo que importa).
    cuenta_borrada = None
    if (cuenta is not None and set(retienen) == {"usuarios"}
            and not _retienen_de_verdad(db, "usuarios", cuenta.id)):
        cuenta_borrada = cuenta.username
        _desenganchar(db, "usuarios", cuenta.id)
        db.delete(cuenta)
        db.flush()                      # para que el recuento de abajo ya no la vea
        retienen = _retienen_de_verdad(db, "operadores", op_id)

    if retienen:
        o.activo = False
        registrar_actividad(db, accion="operador_baja", usuario=user, operador_id=op_id,
                            entidad="operador", entidad_id=op_id,
                            meta={"retienen": retienen, "cuenta_cortada": cortada,
                                  "usuario": cuenta.username if cuenta else None})
        db.commit()
        return {"ok": True, "baja": True, "retienen": retienen,
                "viajes": retienen.get("viajes", 0),
                "unidades_a_cargo": todo.get("unidades", 0),
                "cuenta_cortada": cortada,
                "usuario": cuenta.username if cuenta else None}

    nombre_op = o.nombre
    suelto = _desenganchar(db, "operadores", op_id)
    if cuenta_borrada:
        suelto["cuenta_borrada"] = cuenta_borrada
    # OJO: aquí NO se pasa `operador_id`, o el renglón nuevo apuntaría al operador que
    # estamos a punto de borrar y la clave foránea lo impediría.
    registrar_actividad(db, accion="operador_eliminado", usuario=user,
                        entidad="operador", entidad_id=op_id,
                        meta={"nombre": nombre_op, "desenganchado": suelto},
                        commit=False)
    db.delete(o)   # la foto (base64) vive en la fila: se borra con el operador
    db.commit()
    return {"ok": True, "eliminado": True, "desenganchado": suelto,
            "cuenta_borrada": cuenta_borrada}


_FOTO_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


async def _leer_foto(file: UploadFile) -> tuple[str, str]:
    """Lee la imagen subida (chunks, tope 12MB, valida firma) y la devuelve como
    (base64, mime) para GUARDARLA codificada en la BD. Lanza 400 si algo falla."""
    ext = _FOTO_EXT.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Formato no soportado (usa JPG, PNG o WebP)")
    # El cliente ya comprime la foto (lado máx 1600px) antes de subir, así que en la práctica
    # llegan <1 MB. El tope alto es solo una red de seguridad si la compresión no corrió.
    limite = 12 * 1024 * 1024
    buf = bytearray()
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limite:
            raise HTTPException(400, "La imagen supera el límite de 12 MB")
    data = bytes(buf)
    if not _es_imagen(data, ext):
        raise HTTPException(400, "El archivo no parece una imagen válida")
    return base64.b64encode(data).decode("ascii"), (file.content_type or "image/jpeg")


def _espejar_foto(db: Session, *, desde_usuario: "Usuario | None" = None,
                  desde_operador: "Operador | None" = None) -> None:
    """Mantiene la MISMA foto en la cuenta (usuarios) y el operador del padrón enlazados: el
    último cambio —lo suba el operador desde su perfil o el Admin desde la ficha— se ve en
    todas las vistas. No hace commit (lo hace el endpoint que llama)."""
    if desde_usuario is not None and desde_usuario.operador_id:
        op = db.get(Operador, desde_usuario.operador_id)
        if op is not None:
            op.foto, op.foto_mime = desde_usuario.foto, desde_usuario.foto_mime
    if desde_operador is not None:
        acc = db.scalar(select(Usuario).where(Usuario.operador_id == desde_operador.id))
        if acc is not None:
            acc.foto, acc.foto_mime = desde_operador.foto, desde_operador.foto_mime


@app.post("/api/operadores/{op_id}/foto")
async def operador_foto_subir(op_id: int, file: UploadFile = File(...),
                              user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")
    o.foto, o.foto_mime = await _leer_foto(file)   # se guarda codificada en base64
    _espejar_foto(db, desde_operador=o)            # refleja la foto a la cuenta del operador
    db.commit()
    return {"ok": True}


@app.get("/api/operadores/{op_id}/foto")
def operador_foto_ver(op_id: int, user: dict = Depends(require_user), db: Session = Depends(get_db)):
    # El personal ve la de cualquiera (su trabajo); un operador, sólo la suya.
    if user.get("rol") == "operador" and _mi_operador_id(user, db) != op_id:
        raise HTTPException(403, "No es tu foto")
    o = db.get(Operador, op_id)
    if o is None or not o.foto:
        raise HTTPException(404, "Sin foto")
    try:
        data = base64.b64decode(o.foto)            # se decodifica (revierte) al consultarla
    except (ValueError, TypeError):
        raise HTTPException(404, "Foto ilegible")
    return Response(content=data, media_type=o.foto_mime or "image/jpeg",
                    headers={"Cache-Control": "no-cache"})


@app.delete("/api/operadores/{op_id}/foto")
def operador_foto_borrar(op_id: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")
    o.foto = None
    o.foto_mime = None
    _espejar_foto(db, desde_operador=o)            # también limpia la foto de su cuenta
    db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# La licencia de conducir: el documento y lo que la IA lee de él
# ─────────────────────────────────────────────────────────────────────────────
# El archivo vive EN DISCO (media_dir/licencias), como la evidencia de las solicitudes,
# y en la base queda sólo su nombre. Una licencia escaneada por 265 operadores no cabe
# en una columna de texto; un avatar de 80 px sí, y por eso ese sí está en la base.
_LIC_EXT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/jpg": "jpg",
            "image/png": "png", "image/webp": "webp"}
# Escrito aparte y no invirtiendo `_LIC_EXT`: dos MIME caen en «jpg», y al invertir el
# diccionario ganaría el último, que es el que NO es estándar (image/jpg).
_LIC_MIME = {"pdf": "application/pdf", "jpg": "image/jpeg",
             "png": "image/png", "webp": "image/webp"}
# El nombre lo inventa el servidor. Se valida contra ESTA FORMA —32 hexadecimales y una
# extensión conocida— y no contra una lista negra de «..»: lo que casa con esto no puede
# ser una ruta, mientras que una lista negra siempre se queda corta.
_LIC_NOMBRE = re.compile(r"^[0-9a-f]{32}\.(pdf|jpg|png|webp)$")
_LIC_LIMITE = 20 * 1024 * 1024
_CURP_FORMA = re.compile(r"^[A-Z]{4}[0-9]{6}[HM][A-Z]{5}[0-9A-Z]{2}$")


def _curp(v) -> str | None:
    """El CURP normalizado, o 400 si no tiene la forma. Vacío es válido: no todos lo traen."""
    s = re.sub(r"[\s-]+", "", (str(v) if v is not None else "")).upper()
    if not s:
        return None
    if not _CURP_FORMA.match(s):
        raise HTTPException(400, "El CURP no tiene la forma esperada (18 caracteres)")
    return s


def _dir_licencias() -> Path:
    d = settings.media_dir / "licencias"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ruta_licencia(nombre: str | None) -> Path | None:
    """La ruta del documento, o None si el nombre no es de los que genera el servidor."""
    if not nombre or not _LIC_NOMBRE.match(nombre):
        return None
    carpeta = _dir_licencias().resolve()
    p = (carpeta / nombre).resolve()
    return p if p.parent == carpeta and p.is_file() else None


def _es_documento(data: bytes, ext: str) -> bool:
    """La firma del archivo, no el content-type que declaró el navegador."""
    return data[:5] == b"%PDF-" if ext == "pdf" else _es_imagen(data, ext)


async def _guardar_licencia(file: UploadFile) -> tuple[str, str, str]:
    """Guarda el documento en disco y devuelve (nombre_propio, mime, nombre_original)."""
    import uuid

    ext = _LIC_EXT.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(400, "Formato no soportado (usa PDF, JPG, PNG o WebP)")
    buf = bytearray()
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > _LIC_LIMITE:
            raise HTTPException(400, "El documento supera el límite de 20 MB")
    data = bytes(buf)
    if not data:
        raise HTTPException(400, "El archivo llegó vacío")
    if not _es_documento(data, ext):
        raise HTTPException(400, f"El archivo no parece un {ext.upper()} válido")
    nombre = f"{uuid.uuid4().hex}.{ext}"
    (_dir_licencias() / nombre).write_bytes(data)
    # El nombre que traía sólo se conserva para MOSTRARLO. Nunca se usa como ruta.
    original = Path((file.filename or "licencia").replace("\\", "/")).name[:160]
    return nombre, (file.content_type or "").lower(), original


def _reclamar_licencia(o: Operador, nombre, original) -> None:
    """Ata a la ficha un documento ya subido por `/licencia/leer`.

    Se llama desde `_apply_operador`, o sea al GUARDAR: hasta entonces el archivo está en
    disco sin dueño y el barrido se lo lleva. Así, leer una licencia y arrepentirse no
    deja nada.
    """
    nombre = (str(nombre) if nombre is not None else "").strip()
    if not nombre or nombre == (o.licencia_doc or ""):
        return
    p = _ruta_licencia(nombre)
    if p is None:
        raise HTTPException(400, "El documento de la licencia ya no está disponible; vuelve a subirlo")
    anterior = _ruta_licencia(o.licencia_doc)
    o.licencia_doc = nombre
    o.licencia_doc_mime = _LIC_MIME.get(p.suffix[1:].lower())
    o.licencia_doc_nombre = (str(original).strip() or None) if original else None
    o.licencia_doc_en = datetime.now(timezone.utc)
    if anterior is not None:
        anterior.unlink(missing_ok=True)   # el que sustituye ya no le sirve a nadie


def _barrer_licencias(db: Session) -> int:
    """Borra los documentos que se leyeron y nadie guardó, pasadas 6 horas.

    Subir una licencia, ver lo que la IA propone y cerrar el formulario es una cosa normal
    de hacer. Sin este barrido, cada vez que pasa queda un documento de identidad tirado
    en el disco para siempre.
    """
    vivos = {n for (n,) in db.execute(
        select(Operador.licencia_doc).where(Operador.licencia_doc.is_not(None)))}
    corte = datetime.now().timestamp() - 6 * 3600
    n = 0
    for p in _dir_licencias().glob("*"):
        if p.name in vivos or not _LIC_NOMBRE.match(p.name):
            continue
        try:
            if p.stat().st_mtime < corte:
                p.unlink()
                n += 1
        except OSError:
            log.warning("No se pudo barrer el documento huérfano %s", p.name)
    return n


def _fecha_ia(v) -> str | None:
    """La fecha que leyó la IA, en ISO, o None si no es una fecha usable.

    Devolver None es preferible a devolver basura: un campo vacío se nota y se llena; una
    fecha inventada se queda.
    """
    s = (str(v) if v is not None else "").strip()[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


@app.post("/api/operadores/licencia/leer")
async def licencia_leer(file: UploadFile = File(...),
                        user: dict = Depends(require_coordinador),
                        db: Session = Depends(get_db)) -> dict:
    """Sube la licencia, la lee con IA y PROPONE los campos. NO toca ninguna ficha.

    Lo que devuelve se pinta en el formulario para que una persona lo vea antes de
    guardar. El documento queda en disco sin dueño hasta que alguien pulsa Guardar; si
    nadie lo hace, el barrido se lo lleva a las 6 horas.
    """
    nombre, mime, original = await _guardar_licencia(file)
    try:
        _barrer_licencias(db)
    except Exception:
        log.exception("Falló el barrido de licencias huérfanas")   # nunca rompe la lectura

    ruta = str(_dir_licencias() / nombre)
    try:
        # A UN HILO: la llamada tarda entre 5 y 10 s y esta función es async, así que
        # hacerla aquí dentro detendría el servidor entero —para todos— ese rato.
        lic = await run_in_threadpool(ai.leer_licencia, ruta)
    except Exception as e:
        log.exception("No se pudo leer la licencia %s", nombre)
        # Escribe en la base, no llama a la IA, pero sigue siendo trabajo bloqueante dentro
        # de una función `async`: al hilo, como todo lo demás de aquí.
        await run_in_threadpool(ai.registrar_uso, "licencia", None,
                                error=f"{type(e).__name__}: {e}")
        return {"ok": False, "archivo": nombre, "mime": mime, "nombre": original,
                "error": "No se pudo leer el documento. Captura los datos a mano."}

    vence = _fecha_ia(lic.vence)
    dias = None
    if vence:
        dias = (date.fromisoformat(vence) - fecha_flota()).days
    tipo = None
    if lic.federal is not None or lic.categoria:
        tipo = " ".join(x for x in (
            ("FEDERAL" if lic.federal else "ESTATAL") if lic.federal is not None else None,
            (lic.categoria or "").strip().upper() or None) if x)
    curp = re.sub(r"[\s-]+", "", (lic.curp or "")).upper()
    registrar_actividad(db, accion="licencia_leida", usuario=user,
                        entidad="operador", entidad_id=None,
                        meta={"archivo": nombre, "es_licencia": lic.es_licencia,
                              "confianza": lic.confianza})
    db.commit()
    return {
        "ok": True, "archivo": nombre, "mime": mime, "nombre": original,
        "es_licencia": lic.es_licencia, "confianza": lic.confianza, "aviso": lic.aviso,
        # Recortado al tamaño de la columna: esto lo escribió un modelo leyendo un papel
        # de fuera, no una persona de la casa. `licencia` es VARCHAR(60) y `licencia_tipo`
        # VARCHAR(30); sin el recorte, una cadena larga se rellena, se guarda y sale un 500
        # en la cara de quien sólo quería dar de alta a un conductor.
        "propuesta": {
            "nombre": " ".join((lic.nombre or "").upper().split())[:120] or None,
            "licencia": (lic.folio or "").strip()[:60] or None,
            "licencia_tipo": (tipo or "")[:30] or None,
            "licencia_expedida": _fecha_ia(lic.expedida),
            "licencia_vence": vence,
            "curp": curp if _CURP_FORMA.match(curp) else None,
        },
        # La vigencia es el único dato de la licencia que caduca solo, así que es el único
        # que el servidor se molesta en juzgar. Un tracto con licencia vencida es una multa
        # y una póliza sin efecto, no un campo mal capturado.
        "vence_en_dias": dias,
    }


@app.get("/api/operadores/{op_id}/licencia")
def operador_licencia_ver(op_id: int, user: dict = Depends(require_gestion),
                          db: Session = Depends(get_db)):
    """Sirve el documento guardado. Mismo permiso que ver la ficha: quien puede ver al
    operador puede ver su licencia; el operador NO ve la de nadie."""
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")
    p = _ruta_licencia(o.licencia_doc)
    if p is None:
        raise HTTPException(404, "Sin documento de licencia")
    return FileResponse(p, media_type=o.licencia_doc_mime or "application/pdf",
                        headers={"Cache-Control": "no-store",
                                 "Content-Disposition": "inline"})


@app.delete("/api/operadores/{op_id}/licencia")
def operador_licencia_borrar(op_id: int, user: dict = Depends(require_admin),
                             db: Session = Depends(get_db)) -> dict:
    """Quita el documento. Los campos leídos se quedan: el dato sigue siendo cierto
    aunque el escaneo ya no esté, y borrarlos obligaría a recapturarlos a mano."""
    o = db.get(Operador, op_id)
    if o is None:
        raise HTTPException(404, "Operador no encontrado")
    p = _ruta_licencia(o.licencia_doc)
    o.licencia_doc = o.licencia_doc_mime = o.licencia_doc_nombre = None
    o.licencia_doc_en = None
    registrar_actividad(db, accion="licencia_documento_borrado", usuario=user,
                        entidad="operador", entidad_id=op_id)
    db.commit()
    if p is not None:
        p.unlink(missing_ok=True)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Gráficos / estadísticas
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/viajes/{viaje_id}/reactivar")
def viaje_reactivar(viaje_id: int, user: dict = Depends(require_admin),
                    db: Session = Depends(get_db)) -> dict:
    """Deshace una retractación: el reporte se borró del grupo pero el dato era correcto.

    Existe porque un borrado en WhatsApp puede ser un dedazo, y sin esto la única salida
    sería volver a capturar el reporte a mano.
    """
    v = db.get(Viaje, viaje_id)
    if v is None:
        raise HTTPException(404, "Viaje no encontrado")
    if v.retractado_en is None:
        return {"ok": True, "ya_vigente": True}
    v.retractado_en = None
    v.retractado_motivo = None
    db.execute(delete(Anomalia).where(Anomalia.viaje_id == viaje_id,
                                      Anomalia.tipo == "reporte_retractado"))
    db.commit()
    log.info("Viaje %s reactivado manualmente desde el panel", viaje_id)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Solicitudes de recarga — ciclo de vida (Fase A de la migración a plataforma web)
# ─────────────────────────────────────────────────────────────────────────────

def _stats_cargas(db: Session, unidad_id: int | None = None,
                  remolque_id: int | None = None) -> dict | None:
    """Cuánto carga DE VERDAD este activo, según el libro del proveedor.

    `cargas_proveedor` es el ÚNICO sitio de la base donde consta un despacho individual: cada
    fila es una vez que una pistola surtió a un tanque. No confundir con `viajes.lts_real`,
    que es el consumo de un VIAJE ENTERO y vale varias cargas — la pantalla del despachador
    enseñaba ese número (876.8 L para T205) como referencia de UNA carga, cuando las cargas
    reales de esa misma unidad van de 33 a 580 L con mediana 280.

    Devuelve None cuando no hay con qué contestar. NO devuelve ceros: una unidad sin historial
    tiene que decir «no sé», que es distinto de «carga cero».
    """
    if not unidad_id and not remolque_id:
        return None
    col = CargaProveedor.unidad_id if unidad_id else CargaProveedor.remolque_id
    fila = db.execute(
        select(func.count(CargaProveedor.id),
               func.percentile_cont(0.5).within_group(CargaProveedor.litros),
               func.percentile_cont(0.25).within_group(CargaProveedor.litros),
               func.percentile_cont(0.75).within_group(CargaProveedor.litros),
               func.min(CargaProveedor.litros), func.max(CargaProveedor.litros),
               func.min(CargaProveedor.fecha_operacion), func.max(CargaProveedor.fecha_operacion))
        .where(col == (unidad_id or remolque_id),
               CargaProveedor.vigente.is_(True),
               CargaProveedor.litros > 0)).first()
    if not fila or not fila[0]:
        return None
    n, med, p25, p75, mn, mx, d0, d1 = fila
    ult = db.execute(
        select(CargaProveedor.litros, CargaProveedor.fecha_operacion,
               CargaProveedor.estacion_nombre_txt)
        .where(col == (unidad_id or remolque_id), CargaProveedor.vigente.is_(True),
               CargaProveedor.litros > 0)
        .order_by(CargaProveedor.fecha_operacion.desc(), CargaProveedor.id.desc())
        .limit(1)).first()
    return {
        "cargas": int(n),
        "mediana": round(float(med), 1),
        "p25": round(float(p25), 1), "p75": round(float(p75), 1),
        "min": round(float(mn), 1), "max": round(float(mx), 1),
        "ultima": ({"litros": round(float(ult[0]), 1),
                    "fecha": ult[1].isoformat() if ult[1] else None,
                    "estacion": ult[2]} if ult else None),
        "desde": d0.isoformat() if d0 else None,
        "hasta": d1.isoformat() if d1 else None,
        "fuente": "libro del proveedor (cargas_proveedor), despachos individuales reales",
    }


def _solicitud_dict(s: SolicitudRecarga, detalle: bool = False) -> dict:
    d = {
        "id": s.id, "estado": s.estado.value,
        "operador": s.operador.nombre if s.operador else None,
        "operador_id": s.operador_id,
        "unidad": s.unidad.clave if s.unidad else None,
        "unidad_id": s.unidad_id, "viaje_id": s.viaje_id,
        "tipo_recarga": getattr(s, "tipo_recarga", "motor"),
        "remolque_id": s.remolque_id,
        "remolque_eco": s.remolque.eco if s.remolque else None,
        # La otra mitad de un «motor y termo»: el teléfono la necesita para retomar las dos.
        "hermana_id": getattr(s, "hermana_id", None),
        "litros_solicitados": s.litros_solicitados,
        "odometro": s.odometro, "nivel_tanque": s.nivel_tanque,
        "motivo": s.motivo, "capturada_asistida": s.capturada_asistida,
        "creada_en": s.creada_en.isoformat() if s.creada_en else None,
        "folio": s.orden.folio if s.orden else None,
        # Litros que fija el coordinador (al autorizar) y combustible (al despachar). El
        # operador NO los determina; los ve como informativos.
        "litros_autorizados": s.orden.litros_autorizados if s.orden else None,
        "litros_reales": s.orden.litros_reales if s.orden else None,
        # Alerta para el coordinador: alguna foto de económico NO coincidió con lo asignado.
        "eco_alerta": any(e.coincide is False for e in s.evidencias),
    }
    if detalle:
        # Los nombres detrás de los identificadores. Se resuelven de una sola consulta, aquí,
        # porque el panel no tiene ninguna vía para traducirlos por su cuenta.
        ses = object_session(s)
        ids = {t.por_usuario_id for t in s.transiciones}
        ids.add(s.creada_por_id)
        if s.orden is not None:
            ids.update({s.orden.autorizada_por_id, s.orden.despachada_por_id})
        ids.discard(None)
        nombres: dict[int, str] = {}
        if ses is not None and ids:
            nombres = {u.id: (u.nombre or u.username)
                       for u in ses.execute(select(Usuario).where(Usuario.id.in_(ids)))
                       .scalars()}
        d["creada_por"] = nombres.get(s.creada_por_id)
        d["actualizada_en"] = s.actualizada_en.isoformat() if s.actualizada_en else None
        d["evidencias"] = [
            {"id": e.id, "tipo": e.tipo, "foto": bool(e.foto_path),
             # La dirección para verla. Antes sólo viajaba el booleano y no había ninguna
             # manera de abrir la imagen: la evidencia existía y era inconsultable.
             "foto_url": (f"/api/evidencias/{e.id}/foto" if e.foto_path else None),
             "sospechosa": bool(getattr(e, "sospechosa", False)),
             "sospecha_motivo": getattr(e, "sospecha_motivo", None),
             "valor_ia": e.valor_ia, "valor_final": e.valor_final,
             "texto_ia": e.texto_ia, "esperado": e.esperado, "coincide": e.coincide,
             "etiqueta": e.etiqueta,
             "procedencia": e.procedencia.value, "confianza": e.confianza}
            for e in s.evidencias
        ]
        d["transiciones"] = [
            {"de": t.estado_anterior, "a": t.estado_nuevo,
             "usuario_id": t.por_usuario_id, "usuario": nombres.get(t.por_usuario_id),
             "nota": t.nota,
             "momento": t.momento.isoformat() if t.momento else None,
             "cambios": t.cambios}
            for t in s.transiciones
        ]
        if s.orden:
            # La mitad del despacho faltaba: se sabía qué se autorizó y no quién surtió el
            # diésel, cuándo, ni contra qué factura. Es justo la mitad que el coordinador
            # necesita cuando lo cargado no coincide con lo autorizado.
            d["orden"] = {"folio": s.orden.folio,
                          "litros_autorizados": s.orden.litros_autorizados,
                          "litros_reales": s.orden.litros_reales,
                          "litros_sugeridos": s.orden.litros_sugeridos,
                          "sugerencia_motivo": s.orden.sugerencia_motivo,
                          "autorizada_por": nombres.get(s.orden.autorizada_por_id),
                          "autorizada_en": s.orden.autorizada_en.isoformat()
                          if s.orden.autorizada_en else None,
                          "despachada_por": nombres.get(s.orden.despachada_por_id),
                          "despachada_en": s.orden.despachada_en.isoformat()
                          if s.orden.despachada_en else None,
                          "factura_id": s.orden.factura_id}
    return d


@app.get("/api/solicitudes")
def solicitudes_listar(
    estado: str | None = None,
    limite: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Bandeja de solicitudes. El operador ve SOLO las suyas; los demás ven la flota.
    Combustible ve solo las que ya están autorizadas en adelante."""
    rol = user.get("rol")

    # El alcance del ROL se arma UNA vez y lo usan la lista y los conteos. Antes los
    # conteos hacían su propio `group by` sobre la tabla entera, así que al chofer le
    # decían 18 —las de toda la flota, con 12 borradores ajenos dentro— aunque su lista
    # viniera vacía. Un número equivocado, y además estado que no le toca ver.
    alcance = []
    if rol == "operador":
        # Solo lo propio: se filtra por el operador ligado a su cuenta.
        oper = db.scalar(select(Usuario.operador_id).where(Usuario.id == user.get("id")))
        alcance.append(SolicitudRecarga.operador_id == oper if oper else false())
    elif rol == "combustible":
        alcance.append(SolicitudRecarga.estado.in_(
            (EstadoSolicitud.AUTORIZADA, EstadoSolicitud.DESPACHADA,
             EstadoSolicitud.FACTURADA, EstadoSolicitud.EN_DISCREPANCIA,
             EstadoSolicitud.CONCILIADA)))

    q = select(SolicitudRecarga).where(*alcance)
    if estado:
        try:
            q = q.where(SolicitudRecarga.estado == EstadoSolicitud(estado))
        except ValueError:
            raise HTTPException(400, "Estado inválido")

    filas = db.execute(
        q.order_by(SolicitudRecarga.creada_en.desc()).limit(limite)).scalars().all()
    # Los conteos llevan el alcance del rol pero NO el filtro de estado: son los que pintan
    # todas las pestañas a la vez, así que acotarlos a la pestaña abierta las dejaría a las
    # demás en cero.
    conteos = dict(db.execute(
        select(SolicitudRecarga.estado, func.count(SolicitudRecarga.id))
        .where(*alcance).group_by(SolicitudRecarga.estado)).all())
    total = sum(conteos.values())
    return {
        "conteos": {e.value: n for e, n in conteos.items()},
        "solicitudes": [_solicitud_dict(s) for s in filas],
        # Cuántas hay de verdad y si esto viene recortado. Sin esto la lista se corta en
        # silencio: una devuelta vieja se cae de la ventana y desaparece de TODAS las
        # pestañas —«Todas» incluida— sin que nada lo diga ni forma de llegar a ella.
        "total": total,
        "limite": limite,
        "truncado": len(filas) >= limite,
    }


@app.get("/api/solicitudes/{sol_id}")
def solicitud_detalle(sol_id: int, user: dict = Depends(require_user),
                      db: Session = Depends(get_db)) -> dict:
    """Detalle completo para el modal de validación: foto, valor de la IA, procedencia de
    cada campo y las anomalías, todo en una sola vista."""
    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Solicitud no encontrada")
    rol = user.get("rol")
    if rol == "operador":
        oper = db.scalar(select(Usuario.operador_id).where(Usuario.id == user.get("id")))
        if s.operador_id != oper:
            raise HTTPException(403, "No es tu solicitud")
    elif rol == "combustible":
        # Mismo alcance que su bandeja: solo lo que ya está autorizado en adelante. Antes
        # el detalle no filtraba y combustible podía leer borradores/enviadas ajenas a su
        # trabajo enumerando ids.
        if s.estado in (EstadoSolicitud.BORRADOR, EstadoSolicitud.ENVIADA,
                        EstadoSolicitud.EN_VALIDACION, EstadoSolicitud.DEVUELTA,
                        EstadoSolicitud.RECHAZADA):
            raise HTTPException(403, "Aún no está autorizada para despacho")
    d = _solicitud_dict(s, detalle=True)
    # El panorama del combustible: se calcula sólo en el detalle, que es una solicitud a la
    # vez. En el listado saldría una consulta de escáner por fila.
    d["tanque"] = rendimiento.panorama(db, s)
    return d


@app.post("/api/solicitudes")
async def solicitud_crear(request: Request, user: dict = Depends(require_operador),
                          db: Session = Depends(get_db)) -> dict:
    """Crea una solicitud. El operador la crea para sí mismo; el coordinador puede crearla
    en NOMBRE de un operador (captura asistida) sin romper la trazabilidad."""
    f = await request.form()
    rol = user.get("rol")

    # Idempotencia de la cola offline: si ya existe una solicitud con este uuid (reintento
    # de sincronización tras caerse la red), se devuelve esa misma en vez de duplicar.
    client_uuid = (f.get("client_uuid") or "").strip() or None
    if client_uuid:
        prev = db.scalar(select(SolicitudRecarga).where(
            SolicitudRecarga.client_uuid == client_uuid))
        if prev is not None:
            # También la hermana: sin esto, un reintento en modo «ambos» recuperaba la del
            # motor y dejaba huérfana la del termo, que es justo lo que este camino evita.
            return {"ok": True, "id": prev.id, "estado": prev.estado.value, "duplicada": True,
                    "id_termo": prev.hermana_id}

    operador_id = _pi(f.get("operador_id"))
    asistida = False
    if rol == "operador":
        # Se ignora cualquier operador_id del formulario: siempre es él mismo.
        operador_id = db.scalar(select(Usuario.operador_id).where(Usuario.id == user.get("id")))
        if operador_id is None:
            raise HTTPException(400, "Tu cuenta no está ligada a un operador del padrón")
        # REGLA (#2): el operador NO puede solicitar combustible si su coordinador no ha
        # LEVANTADO su viaje (no basta la unidad habitual/titular: se exige asignación ACTIVA).
        asig = db.scalar(
            select(AsignacionViaje).where(
                AsignacionViaje.operador_id == operador_id,
                AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
            .order_by(AsignacionViaje.creada_en.desc()).limit(1))
        if asig is None:
            raise HTTPException(409, "No tienes un viaje asignado. Pídele a tu coordinador que "
                                     "levante tu viaje antes de solicitar combustible.")
        # El operador NO elige la unidad ni fija los litros: la unidad la establece el viaje
        # asignado y los litros los determinan la autorización/despacho.
        unidad_id = asig.unidad_id
        litros_solicitados = None
    else:
        if operador_id is not None:
            asistida = True   # el coordinador captura por otro
        unidad_id = _pi(f.get("unidad_id"))
        litros_solicitados = _pf(f.get("litros_solicitados"))

    tipo_recarga = (f.get("tipo_recarga") or "motor").strip().lower()
    # 'ambos' NO es un tipo de recarga: es la declaración de que se van a hacer las DOS.
    # Se convierte aquí en dos solicitudes hermanas y no sobrevive a esta línea, porque un
    # registro con los litros de motor y termo juntos no se puede volver a separar.
    ambos = tipo_recarga == "ambos"
    if tipo_recarga not in ("motor", "termo", "ambos"):
        tipo_recarga = "motor"
    if ambos:
        uni = db.get(Unidad, unidad_id) if unidad_id else None
        if uni is None or uni.tipo != TipoUnidad.CAMION:
            raise HTTPException(
                400, "Sólo un camión recarga motor y termo a la vez: en un tracto el termo "
                     "va en el remolque y se solicita con el económico del remolque.")
        tipo_recarga = "motor"
    # El viaje del que sale, CONGELADO aquí. Si mañana se le levanta otro, esta solicitud
    # sigue midiéndose contra el suyo.
    _asig = db.scalar(select(AsignacionViaje.id)
                      .where(AsignacionViaje.operador_id == operador_id,
                             AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
                      .order_by(AsignacionViaje.creada_en.desc()).limit(1)) if operador_id else None
    s = SolicitudRecarga(
        estado=EstadoSolicitud.BORRADOR,
        creada_por_id=user.get("id"),
        operador_id=operador_id,
        asignacion_id=_asig,
        unidad_id=unidad_id,
        remolque_id=_pi(f.get("remolque_id")),
        viaje_id=_pi(f.get("viaje_id")),
        litros_solicitados=litros_solicitados,
        tipo_recarga=tipo_recarga,
        odometro=_pf(f.get("odometro")),
        nivel_tanque=_pf(f.get("nivel_tanque")),
        capturada_asistida=asistida,
        client_uuid=client_uuid,
    )
    db.add(s)
    db.flush()
    db.add(TransicionSolicitud(
        solicitud_id=s.id, estado_anterior=None, estado_nuevo="borrador",
        por_usuario_id=user.get("id"),
        nota="Creada por el coordinador (asistida)" if asistida else "Creada"))

    hermana = None
    if ambos:
        # El termo del camión lleva el MISMO económico pero se carga aparte, así que es su
        # propia solicitud con sus propios litros y su propia evidencia (las horas del termo).
        # `client_uuid` no se copia: es la llave anti-duplicado de la cola sin conexión del
        # teléfono, y repetirla haría que una de las dos se descartara por duplicada.
        hermana = SolicitudRecarga(
            estado=EstadoSolicitud.BORRADOR,
            creada_por_id=user.get("id"),
            operador_id=operador_id,
            unidad_id=unidad_id,
            remolque_id=_pi(f.get("remolque_id")),
            viaje_id=_pi(f.get("viaje_id")),
            litros_solicitados=litros_solicitados,
            tipo_recarga="termo",
            capturada_asistida=asistida,
        )
        db.add(hermana)
        db.flush()
        # El vínculo, en las dos direcciones y desde el primer momento.
        hermana.hermana_id = s.id
        s.hermana_id = hermana.id
        db.add(TransicionSolicitud(
            solicitud_id=hermana.id, estado_anterior=None, estado_nuevo="borrador",
            por_usuario_id=user.get("id"),
            nota=f"Creada junto con la solicitud {s.id}: el operador declaró que carga "
                 f"motor y termo del mismo económico"))

    db.commit()
    if hermana is not None:
        return {"ok": True, "id": s.id, "estado": s.estado.value,
                "id_termo": hermana.id, "ambos": True}
    return {"ok": True, "id": s.id, "estado": s.estado.value}


@app.get("/api/evidencias/{ev_id}/foto")
def evidencia_foto(ev_id: int, user: dict = Depends(require_user),
                   db: Session = Depends(get_db)):
    """Devuelve la foto de una evidencia.

    Quien autoriza litros tiene que poder ver el odómetro que los justifica. El operador ve
    las suyas; coordinación, gerencia y admin ven las de cualquiera, que es justamente su
    trabajo. No se sirve desde /static a propósito: son fotos de la operación y pasan por la
    sesión como cualquier otro dato.
    """
    ev = db.get(EvidenciaRecarga, ev_id)
    if ev is None or not ev.foto_path:
        raise HTTPException(404, "Esa evidencia no tiene foto")
    if user.get("rol") == "operador":
        s = db.get(SolicitudRecarga, ev.solicitud_id)
        mio = db.scalar(select(Usuario.operador_id).where(Usuario.id == user.get("id")))
        if s is None or s.operador_id != mio:
            raise HTTPException(403, "No es tu evidencia")
    ruta = ruta_media(ev.foto_path)
    if ruta is None:
        # El registro dice que hay foto y el disco dice que no. Es un 404 honesto: mentir con
        # una imagen vacía haría creer que la evidencia está y no lo está.
        raise HTTPException(404, "La foto ya no está en el disco del servidor")
    return FileResponse(ruta, media_type=mimetypes.guess_type(ruta.name)[0] or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/solicitudes/{sol_id}/qr")
async def solicitud_qr(sol_id: int, request: Request,
                       user: dict = Depends(require_operador),
                       db: Session = Depends(get_db)) -> dict:
    """Registra el económico leído de un QR pegado al activo.

    Sustituye a fotografiar el número y hacérselo leer a la IA, y es mejor por dos motivos
    medibles: el texto llega EXACTO —no hay foto borrosa ni número mal interpretado— y no
    gasta una llamada facturable por cada económico de cada solicitud.

    Lo que NO cambia es la verificación: el activo que resuelve el código se compara contra
    el que el viaje tiene asignado, igual que se comparaba la lectura de la foto. Escanear
    el sticker de otra unidad se marca para el coordinador exactamente igual que antes.
    """
    f = await request.form()
    texto = (f.get("texto") or "").strip()
    esperado = (f.get("esperado") or "").strip().upper() or None
    etiqueta = ((f.get("etiqueta") or "").strip()[:60]) or None
    if not texto:
        raise HTTPException(400, "El lector no devolvió ningún código")

    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Solicitud no encontrada")
    # El operador sólo toca lo suyo, y sólo mientras la solicitud se está armando: la
    # misma puerta que el endpoint de evidencia por foto, porque esto es una evidencia más.
    if user.get("rol") == "operador":
        oper = db.scalar(select(Usuario.operador_id).where(Usuario.id == user.get("id")))
        if s.operador_id != oper:
            raise HTTPException(403, "No es tu solicitud")
    if s.estado not in (EstadoSolicitud.BORRADOR, EstadoSolicitud.DEVUELTA):
        raise HTTPException(409, "La solicitud ya no admite cambios de evidencia")

    r = catalogo.resolver_qr(db, texto)
    leido = catalogo.contenido_qr(texto).upper() or None

    # El nombre del activo al que resolvió: es lo que se compara y lo que se le enseña a la
    # persona. Si el código no resuelve, se conserva el texto crudo para poder investigarlo.
    nombre = None
    if r.unidad_id:
        u = db.get(Unidad, r.unidad_id)
        nombre = u.clave if u else None
    elif r.remolque_id:
        rem = db.get(Remolque, r.remolque_id)
        nombre = rem.eco if rem else None

    coincide = None
    aviso = None
    if esperado is not None:
        coincide = bool(nombre and catalogo.norm_eco(nombre) == catalogo.norm_eco(esperado))
        if not coincide:
            aviso = (f"El QR corresponde a {nombre or 'un activo desconocido'} y el viaje "
                     f"tiene asignado {esperado}. Se marca para revisión del coordinador.")
    if not r.ok:
        aviso = "; ".join(r.discrepancias or ["el código no corresponde a ningún activo"])

    ev = EvidenciaRecarga(
        solicitud_id=sol_id, tipo="eco", foto_path=None,
        texto_ia=nombre or leido, esperado=esperado, coincide=coincide,
        etiqueta=etiqueta,
        # No es MANUAL ni salió de la IA: llega exacto del lector.
        procedencia=Procedencia.ESCANEADA,
        confianza=None,
    )
    db.add(ev)
    registrar_actividad(db, accion="evidencia_qr", usuario=user,
                        entidad="solicitud", entidad_id=sol_id,
                        meta={"codigo": leido, "resolvio": nombre, "via": r.via,
                              "coincide": coincide})
    db.commit()
    return {"ok": True, "id": ev.id, "activo": nombre, "via": r.via,
            "confianza": r.confianza, "coincide": coincide, "aviso": aviso,
            "resuelve": r.ok}


@app.post("/api/solicitudes/{sol_id}/evidencia")
async def solicitud_evidencia(
    sol_id: int,
    tipo: str = Form(...),
    file: UploadFile = File(...),
    esperado: str | None = Form(None),   # ECO esperado (para verificar el número económico)
    etiqueta: str | None = Form(None),   # nombre legible del slot ("ECO Remolque 1", etc.)
    user: dict = Depends(require_operador),
    db: Session = Depends(get_db),
) -> dict:
    """Sube una foto de instrumento a una solicitud y la LEE con IA en el momento.

    Aquí la IA por fin trabaja con contexto: la unidad ya se conoce, así que puede validar
    el valor leído contra la lectura anterior antes de aceptarlo. Devuelve lo que leyó para
    que quien está parado frente al tablero confirme o corrija en dos segundos.
    """
    from . import ai
    from .validacion import _ultimo_odometro

    if tipo not in ("odometro", "kilometraje", "nivel", "termo", "comprobante", "eco", "horas_motor"):
        raise HTTPException(400, "Tipo de evidencia inválido")
    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Solicitud no encontrada")
    # El operador solo toca lo suyo, y solo mientras la solicitud aún se está armando.
    if user.get("rol") == "operador":
        oper = db.scalar(select(Usuario.operador_id).where(Usuario.id == user.get("id")))
        if s.operador_id != oper:
            raise HTTPException(403, "No es tu solicitud")
    if s.estado not in (EstadoSolicitud.BORRADOR, EstadoSolicitud.DEVUELTA):
        raise HTTPException(409, "La solicitud ya no admite cambios de evidencia")

    # Guardar la foto en disco (leer_imagen recibe una ruta).
    b64, mime = await _leer_foto(file)
    ext = _FOTO_EXT.get(mime, "jpg")
    # uuid y no la hora en segundos: dos fotos del mismo tipo en el mismo segundo («ECO
    # Remolque 1» y «ECO Remolque 2», o un doble toque) se pisaban y las dos evidencias
    # acababan apuntando al mismo archivo.
    import uuid
    ruta = settings.media_dir / f"sol{sol_id}_{tipo}_{uuid.uuid4().hex}.{ext}"
    ruta.write_bytes(base64.b64decode(b64))

    # Pista de contexto para la IA: la unidad y el odómetro anterior, si los hay.
    pista = None
    ultimo_odo = None
    if s.unidad_id:
        u = db.get(Unidad, s.unidad_id)
        ultimo_odo = _ultimo_odometro(db, s.unidad_id, fecha_flota(), None)
        pista = f"unidad {u.clave if u else s.unidad_id}"
        if ultimo_odo:
            pista += f", último odómetro conocido {ultimo_odo:.0f}"

    valor_ia = None
    texto_ia = None
    coincide = None
    confianza = None
    aviso = None
    sospechosa, sospecha_motivo = False, None
    esp = (esperado or "").strip().upper() or None
    objetivo = {"eco": "eco", "kilometraje": "kilometraje", "odometro": "odometro"}.get(tipo)
    try:
        # A UN HILO. Esta función es `async`, así que corre SOBRE el bucle de eventos, y
        # uvicorn arranca sin `--workers`: un solo proceso, un solo bucle. Leer la foto aquí
        # dentro congelaba la aplicación entera —los cinco paneles, todos los usuarios— los
        # 5-10 s que tarda la llamada (ver el mismo arreglo en `licencia_leer`). Y éste es el
        # camino más transitado que hay: cada foto de cada chofer al cerrar el turno.
        lectura = await run_in_threadpool(ai.leer_imagen, str(ruta),
                                          pista=pista, objetivo=objetivo)
        confianza = lectura.confianza
        # Autenticidad, que es otra pregunta distinta de la legibilidad: una foto de la
        # pantalla de otro teléfono se lee perfecta y el dato es de otro día.
        sospechosa = bool(getattr(lectura, "es_recaptura", False))
        sospecha_motivo = (getattr(lectura, "motivo_sospecha", None) or "").strip() or None
        if sospecha_motivo:
            sospechosa = True
        elif sospechosa:
            sospecha_motivo = "La imagen parece la foto de una pantalla, no del instrumento."
        if not getattr(lectura, "es_instrumento", True):
            sospechosa = True
            sospecha_motivo = sospecha_motivo or "La foto no parece un instrumento del camión."

        if tipo == "eco":
            # Lectura de TEXTO: el número económico rotulado, y su verificación.
            texto_ia = (lectura.eco_rotulado or "").strip().upper() or None
            if esp is not None:
                # `None` es "no se pudo leer" y `False` es "es otro activo": son preguntas
                # distintas y antes se contestaban con el mismo False. Una foto ilegible
                # acusaba de económico equivocado a una unidad correcta, y encendía la
                # alerta del coordinador (`eco_alerta` mira `coincide is False`).
                coincide = (texto_ia == esp) if texto_ia else None
                if coincide is False:
                    aviso = (f"El económico leído ({texto_ia}) NO coincide con el "
                             f"asignado ({esp}). Se marca para revisión del coordinador.")
                elif coincide is None:
                    aviso = (f"No se pudo leer el económico en la foto. Escanea la etiqueta "
                             f"del activo o captura {esp} a mano.")
        else:
            campo = {"odometro": "odometro", "kilometraje": "kilometraje_viaje",
                     "nivel": "nivel_tanque", "termo": "horas_termo", "comprobante": "lts_real",
                     "horas_motor": "horas_motor"}[tipo]
            valor_ia = getattr(lectura, campo, None)
            # Validación de rango EN EL MOMENTO: un odómetro que retrocede se avisa aquí.
            if tipo == "odometro" and valor_ia is not None and ultimo_odo and valor_ia < ultimo_odo:
                aviso = (f"El odómetro leído ({valor_ia:.0f}) es MENOR al anterior "
                         f"({ultimo_odo:.0f}). Revisa la foto o el valor.")
        # Se le dice al operador en el momento, que es cuando puede repetir la foto. Al
        # coordinador le llega igual en la evidencia, aunque el operador la ignore.
        if sospecha_motivo and not aviso:
            aviso = sospecha_motivo
    except Exception:
        log.exception("Fallo leyendo la evidencia de la solicitud %s", sol_id)
        # El operador NO teclea el odómetro: sale siempre de la cámara (regla del
        # dueño, 20-sep-2026). Si la foto no se deja leer, se repite; y si aun así no
        # sale, el número lo pone el coordinador desde la misma foto.
        aviso = "No se pudo leer la foto automáticamente. Repítela enfocando el número."

    # La lectura ACERTADA también cuenta: hasta hoy sólo la CORRECCIÓN copiaba el valor a la
    # solicitud, así que el odómetro de la cabecera se llenaba únicamente cuando alguien
    # enmendaba a la IA — leerlo bien dejaba el campo vacío.
    # Pero NO se copia lo que la propia función acaba de declarar dudoso: si la foto parece la
    # foto de otra pantalla, el número se guarda en la evidencia —con su marca— y no se
    # asciende a dato de la solicitud sin que una persona lo mire.
    if valor_ia is not None and not sospechosa:
        if tipo == "odometro":
            s.odometro = valor_ia
        elif tipo == "nivel":
            s.nivel_tanque = valor_ia
    ev = EvidenciaRecarga(
        # Sólo el nombre: la ruta absoluta deja de existir al mover la base de servidor.
        solicitud_id=sol_id, tipo=tipo, foto_path=ruta.name,
        valor_ia=valor_ia, valor_final=valor_ia,   # arranca igual; se ajusta si corrigen
        texto_ia=texto_ia, esperado=esp if tipo == "eco" else None, coincide=coincide,
        etiqueta=((etiqueta or "").strip()[:60] or None),
        procedencia=(Procedencia.IA_ACEPTADA if (valor_ia is not None or texto_ia is not None)
                     else Procedencia.MANUAL),
        confianza=confianza,
        sospechosa=sospechosa, sospecha_motivo=sospecha_motivo,
    )
    db.add(ev)
    db.commit()
    return {"ok": True, "evidencia_id": ev.id, "tipo": tipo, "etiqueta": ev.etiqueta,
            "valor_ia": valor_ia, "texto_ia": texto_ia, "coincide": coincide,
            "confianza": confianza, "aviso": aviso}


# Hasta cuándo se puede corregir una lectura. Quien revisa (coordinador, gerencia) llega
# hasta el momento en que se fijan los litros: después, la decisión ya se tomó contra este
# número. El OPERADOR se cierra antes, y a propósito: los otros dos escritores de evidencia
# —subir la foto y escanear el QR— exigen BORRADOR o DEVUELTA, así que dejarle corregir con
# la solicitud ya enviada le permitiría mover el número sin poder cambiar la foto de la que
# sale. El dato se separaría de su propia prueba.
_EVIDENCIA_EDITABLE = {EstadoSolicitud.BORRADOR, EstadoSolicitud.ENVIADA,
                       EstadoSolicitud.EN_VALIDACION, EstadoSolicitud.DEVUELTA}
_EVIDENCIA_EDITABLE_OPERADOR = {EstadoSolicitud.BORRADOR, EstadoSolicitud.DEVUELTA}


@app.post("/api/solicitudes/{sol_id}/evidencia/{ev_id}")
def evidencia_corregir(sol_id: int, ev_id: int, valor: float = Form(...),
                       user: dict = Depends(require_user),
                       db: Session = Depends(get_db)) -> dict:
    """Corrige el valor que leyó la IA. Guarda que fue corregido a mano: ese dato mide
    qué tan confiable es la lectura automática por tipo de instrumento.

    Lo puede hacer el operador (sólo en SUS solicitudes) y también quien coordina, gerencia o
    administra: es el coordinador quien mira la foto al lado del número antes de autorizar
    litros, y hasta hoy veía un odómetro mal leído sin poder tocarlo. La corrección de un
    tercero deja rastro de quién la hizo — cambiar el dato de otra persona no puede ser
    anónimo.
    """
    ev = db.get(EvidenciaRecarga, ev_id)
    if ev is None or ev.solicitud_id != sol_id:
        raise HTTPException(404, "Evidencia no encontrada")
    rol = user.get("rol")
    # Propiedad: un operador solo corrige evidencia de SUS solicitudes.
    if rol == "operador":
        s0 = db.get(SolicitudRecarga, sol_id)
        if s0 is None or s0.operador_id != _mi_operador_id(user, db):
            raise HTTPException(403, "No es tu solicitud")
    elif rol not in ("coordinador", "gerente", "admin"):
        # `combustible` no corrige lecturas: cuando la orden llega a él ya está autorizada, y
        # mover el odómetro entonces cambiaría la base de una decisión ya tomada.
        raise HTTPException(403, "Tu rol no puede corregir evidencia")
    # Y no basta con QUIÉN: importa CUÁNDO. La misma razón que le cierra la puerta a
    # combustible se la cierra a todos en cuanto la solicitud se autoriza, porque los litros
    # se fijaron CONTRA esta lectura. Después del despacho el diésel ya está en el tanque:
    # cambiar el odómetro entonces no corrige un dato, falsea el expediente de una decisión
    # tomada — y el rendimiento km/L que sale de ahí deja de cuadrar con nada.
    s_est = db.get(SolicitudRecarga, sol_id)
    abierto = _EVIDENCIA_EDITABLE_OPERADOR if rol == "operador" else _EVIDENCIA_EDITABLE
    if s_est is not None and s_est.estado not in abierto:
        # El mensaje NO manda a ninguna puerta: desde rechazada, conciliada y anulada no sale
        # ninguna transición, así que sugerir un remedio ahí sería mentirle a quien lo lee.
        # Al operador sí se le puede decir algo útil, porque devolver la solicitud existe.
        if rol == "operador":
            raise HTTPException(
                409, "Ya enviaste esta solicitud. Pídele a tu coordinador que te la devuelva "
                     "si necesitas corregir la lectura.")
        raise HTTPException(
            409, f"La solicitud ya está {s_est.estado.value}: la lectura quedó fijada cuando "
                 f"se autorizaron los litros y ya no se puede modificar.")
    anterior = ev.valor_final if ev.valor_final is not None else ev.valor_ia
    ev.valor_final = valor
    # Si la IA había leído algo y el valor cambia, es una corrección; si no leyó, es manual.
    if ev.valor_ia is None:
        ev.procedencia = Procedencia.MANUAL
    elif abs(ev.valor_ia - valor) > 1e-9:
        ev.procedencia = Procedencia.IA_CORREGIDA
    else:
        # Vuelta atrás: el valor coincide otra vez con lo que leyó la IA, así que ya no hay
        # corrección que declarar. Sin esta rama la etiqueta se quedaba pegada.
        ev.procedencia = Procedencia.IA_ACEPTADA
    # Copiar el valor final a la solicitud según el tipo.
    s = db.get(SolicitudRecarga, sol_id)
    if s is not None:
        if ev.tipo == "odometro":
            s.odometro = valor
        elif ev.tipo == "nivel":
            s.nivel_tanque = valor
    # Sólo se anota si el valor CAMBIÓ. Guardar el mismo número no es una corrección, y
    # anotarlo llena la bitácora de ruido: un fallo del panel hizo que alguien pulsara
    # «Guardar» cinco veces seguidas y quedaron cinco «correcciones» de 958387 a 958387.
    cambio = anterior is None or abs(float(anterior) - float(valor)) > 1e-9
    if rol != "operador" and cambio:
        registrar_actividad(
            db, accion="evidencia_corregida", usuario=user.get("id"), rol=rol,
            entidad="evidencia", entidad_id=ev.id,
            meta={"solicitud": sol_id, "tipo": ev.tipo,
                  "antes": anterior, "despues": valor})
    db.commit()
    return {"ok": True, "valor_final": valor, "procedencia": ev.procedencia.value,
            "anterior": anterior}


def _eco_de_la_hermana(s: SolicitudRecarga, db: Session) -> bool:
    """¿La solicitud hermana ya verificó el económico de esta unidad?

    Sólo cuenta una evidencia que CUADRÓ (`coincide is True`): si el económico de la hermana
    no coincidía, o no se pudo leer, este camión sigue sin verificar y la exigencia se
    mantiene. Y se exige la misma unidad, para que el parentesco no sirva de atajo si algún
    día las dos solicitudes acabaran apuntando a activos distintos.
    """
    if not getattr(s, "hermana_id", None) or s.unidad_id is None:
        return False
    h = db.get(SolicitudRecarga, s.hermana_id)
    if h is None or h.unidad_id != s.unidad_id:
        return False
    return any(e.tipo == "eco" and e.coincide is True for e in (h.evidencias or []))


def _evidencia_faltante(s: SolicitudRecarga, db: Session) -> list[str]:
    """Evidencia que le falta a la solicitud del operador para poder ENVIARSE (#1: todos los
    datos cubiertos). Se derivan los slots esperados del viaje asignado: odómetro + kilometraje
    + económico de la unidad + un económico por cada remolque enganchado (si es tracto)."""
    tipos = [e.tipo for e in s.evidencias]
    faltan: list[str] = []
    n_eco = sum(1 for t in tipos if t == "eco")
    # Recarga de TERMO (diésel del equipo de frío): otra combinación de evidencia — económico
    # de la unidad + horas del termo; NO lleva odómetro/kilometraje (esos son del motor).
    if getattr(s, "tipo_recarga", "motor") == "termo":
        if "termo" not in tipos:
            faltan.append("horas del termo")
        # El económico puede venir de la HERMANA. En un «carga motor y termo» el operador
        # escanea el sticker una sola vez —es el mismo económico, es el mismo camión— y esa
        # evidencia se guarda en la solicitud del motor. Exigirle a la del termo su propia
        # copia la volvía imposible de enviar: un 400 fijo, no intermitente.
        if s.unidad is not None and n_eco < 1 and not _eco_de_la_hermana(s, db):
            faltan.append("económico")
        return faltan
    # Recarga de MOTOR (por defecto).
    # No basta con que la foto exista: tiene que haber dado un NÚMERO. Antes se comprobaba
    # sólo el tipo, así que una foto ilegible cubría el requisito y la solicitud llegaba al
    # coordinador con el odómetro en nulo — y él autorizaba litros sin saber los kilómetros.
    # El valor puede venir de la IA o de una corrección posterior: `valor_final` arranca
    # igual que `valor_ia` y es lo que queda tras corregir.
    if not any(e.tipo == "odometro" and (e.valor_final if e.valor_final is not None
                                         else e.valor_ia) is not None
               for e in s.evidencias):
        faltan.append("odómetro legible" if "odometro" in tipos else "odómetro")
    # El kilometraje YA NO se pide aparte: el dueño declaró que el odómetro acumulado del
    # tablero es el mismo dato, en tracto y en camión. Eran dos fotos del mismo instrumento.
    # Se sigue ACEPTANDO la evidencia de tipo 'kilometraje' para no invalidar lo ya capturado.
    # Un TRACTO NO lleva horómetro de motor: lo declaró el dueño de la flota el 1-sep-2026.
    # Antes se exigía, y era una foto imposible de tomar que dejaba la solicitud incompleta
    # para siempre. Las horas SÍ existen en el termo, y el termo va en el remolque (en un
    # tracto) o pegado a la unidad (en un camión), nunca en el motor del tracto.
    # Económico OBLIGATORIO para verificar la unidad en TODA combinación: SIEMPRE el de la
    # unidad motriz (camión o tracto) + uno por cada remolque enganchado (si es tracto).
    esperado_eco = 1 if s.unidad is not None else 0   # la motriz siempre lleva su económico
    if s.operador_id is not None and s.unidad is not None and s.unidad.tipo == TipoUnidad.TRACTO:
        asig = db.scalar(
            select(AsignacionViaje).where(
                AsignacionViaje.operador_id == s.operador_id,
                AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
            .order_by(AsignacionViaje.creada_en.desc()).limit(1))
        if asig and asig.remolque_ids:
            esperado_eco += min(2, len(asig.remolque_ids))
    if n_eco < esperado_eco:
        faltan.append(f"económico ({n_eco} de {esperado_eco})")
    return faltan


@app.post("/api/solicitudes/{sol_id}/transicion")
async def solicitud_transicion(sol_id: int, request: Request,
                               user: dict = Depends(require_user),
                               db: Session = Depends(get_db)) -> dict:
    """Mueve una solicitud a un estado nuevo (enviar, validar, devolver, rechazar, anular).
    La autorización con folio tiene su propio endpoint. La máquina de estados valida que el
    salto sea legal y que el rol pueda hacerlo."""
    from .solicitudes import EstadoSolicitud as ES
    from .solicitudes import TransicionInvalida, transicionar

    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Solicitud no encontrada")
    f = await request.form()
    destino_raw = (f.get("destino") or "").strip()
    try:
        destino = ES(destino_raw)
    except ValueError:
        raise HTTPException(400, f"Estado destino inválido: {destino_raw!r}")

    # Blindaje: los estados con efectos colaterales (folio, litros_reales, factura) SOLO se
    # alcanzan por su endpoint dedicado, que es el que captura y valida esos datos. Por esta
    # vía genérica se hacen únicamente las transiciones de revisión/manuales. Sin esto, un
    # rol con permiso al estado destino podría saltarse /autorizar, /despachar o /conciliar.
    if destino in (ES.AUTORIZADA, ES.DESPACHADA, ES.FACTURADA):
        raise HTTPException(409, f"'{destino.value}' se realiza por su acción dedicada "
                                 "(autorizar / despachar / facturar), no por esta vía.")
    # Propiedad: un operador solo puede mover SUS propias solicitudes.
    if user.get("rol") == "operador" and s.operador_id != _mi_operador_id(user, db):
        raise HTTPException(403, "No es tu solicitud")

    # REGLA (#1): el operador no puede ENVIAR con datos a medias. Debe cubrir TODA la evidencia
    # del viaje (odómetro, kilometraje y los económicos de la unidad y sus remolques).
    if destino == ES.ENVIADA and user.get("rol") == "operador":
        faltan = _evidencia_faltante(s, db)
        if faltan:
            raise HTTPException(400, "Antes de enviar cubre toda la evidencia. Falta: "
                                     + ", ".join(faltan))

    try:
        transicionar(db, s, destino, user, nota=(f.get("motivo") or None))
    except TransicionInvalida as e:
        raise HTTPException(409, str(e))
    db.commit()
    return {"ok": True, "estado": s.estado.value}


@app.post("/api/solicitudes/{sol_id}/despachar")
async def solicitud_despachar(sol_id: int, request: Request,
                              user: dict = Depends(require_combustible),
                              db: Session = Depends(get_db)) -> dict:
    """Combustible registra el despacho: los litros REALES cargados (que no siempre igualan
    los autorizados) y quién los cargó. Mueve la solicitud a DESPACHADA. Solo desde
    AUTORIZADA."""
    from .solicitudes import EstadoSolicitud as ES
    from .solicitudes import TransicionInvalida, transicionar

    s = db.get(SolicitudRecarga, sol_id)
    if s is None or s.orden is None:
        raise HTTPException(404, "Solicitud u orden no encontrada")
    f = await request.form()
    litros = _pf_litros(f.get("litros_reales"), "los litros realmente cargados")

    cambios = {"litros_autorizados": s.orden.litros_autorizados, "litros_reales": litros}
    try:
        transicionar(db, s, ES.DESPACHADA, user,
                     nota=f"Despachados {litros:g} L", cambios=cambios)
    except TransicionInvalida as e:
        raise HTTPException(409, str(e))
    s.orden.litros_reales = litros
    s.orden.despachada_por_id = user.get("id")
    s.orden.despachada_en = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "estado": s.estado.value, "litros_reales": litros}


def _ordenes_del_dia(db: Session, unidad_id: int,
                     excluir_solicitud: int | None = None,
                     tipo_recarga: str | None = None) -> list[dict]:
    """Órdenes ya autorizadas HOY para esta unidad, en el día de la flota.

    El día se cierra en la zona de la flota y no en UTC: con seis horas de diferencia, una
    autorización de las 19:00 caería «mañana» y el freno no vería la carga de la tarde.
    Se descartan las anuladas y las rechazadas: no cuenta lo que no llegó a surtirse.
    """
    hoy = fecha_flota()
    ini = datetime.combine(hoy, time.min).replace(tzinfo=_tz()).astimezone(timezone.utc)
    q = (select(OrdenDespacho.folio, OrdenDespacho.litros_autorizados,
                OrdenDespacho.litros_reales, OrdenDespacho.autorizada_en,
                SolicitudRecarga.id, SolicitudRecarga.estado)
         .join(SolicitudRecarga, SolicitudRecarga.id == OrdenDespacho.solicitud_id)
         .where(SolicitudRecarga.unidad_id == unidad_id,
                OrdenDespacho.autorizada_en >= ini,
                SolicitudRecarga.estado.notin_([EstadoSolicitud.ANULADA,
                                                EstadoSolicitud.RECHAZADA])))
    if excluir_solicitud:
        q = q.where(SolicitudRecarga.id != excluir_solicitud)
    # El motor y el termo son DOS TANQUES. Un camión que recarga «ambos» genera dos
    # solicitudes hermanas, y sin este filtro la segunda disparaba el aviso siempre por una
    # carga que no es del mismo depósito.
    if tipo_recarga:
        q = q.where(SolicitudRecarga.tipo_recarga == tipo_recarga)
    return [{"folio": f, "litros": la, "litros_reales": lr, "solicitud": sid,
             "estado": est.value if est else None,
             "hora": a.astimezone(_tz()).strftime("%H:%M") if a else None}
            for f, la, lr, a, sid, est in db.execute(q).all()]


@app.get("/api/solicitudes/{sol_id}/motivo")
def solicitud_motivo(sol_id: int, destino: str = Query(""),
                     user: dict = Depends(require_gestion),
                     db: Session = Depends(get_db)) -> dict:
    """Qué motivo PROPONE la IA para devolver, rechazar o anular. La persona decide.

    Devolver una solicitud sin decir qué corregir obliga al operador a adivinar, y adivinar
    en la carretera cuesta un viaje. Esto le da al coordinador un borrador con el defecto
    concreto ya señalado; puede reescribirlo entero antes de enviarlo.

    Nunca falla hacia arriba: si la IA no contesta se devuelve `motivo` vacío y el formulario
    queda como estaba, en blanco, para que se escriba a mano.
    """
    from . import ai

    if destino not in ("devuelta", "rechazada", "anulada"):
        raise HTTPException(400, "Ese destino no lleva motivo propuesto")
    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Solicitud no encontrada")

    datos = {
        "destino": destino,
        "estado_actual": s.estado.value if s.estado else None,
        "unidad": s.unidad.clave if s.unidad else None,
        "tipo_recarga": getattr(s, "tipo_recarga", "motor"),
        "litros_solicitados": s.litros_solicitados,
        "odometro": s.odometro,
        "tanque": rendimiento.panorama(db, s),
        "ya_autorizado_hoy": (_ordenes_del_dia(db, s.unidad_id, excluir_solicitud=s.id,
                                               tipo_recarga=getattr(s, "tipo_recarga", "motor"))
                              if s.unidad_id else []),
        # Sólo lo que describe si la evidencia sirve; ni rutas de fichero ni ids.
        "evidencias": [{"tipo": e.tipo, "etiqueta": e.etiqueta, "texto_leido": e.texto_ia,
                        "esperado": e.esperado, "coincide": e.coincide,
                        "valor_leido": e.valor_ia, "valor_final": e.valor_final,
                        "sospechosa": bool(getattr(e, "sospechosa", False)),
                        "sospecha": getattr(e, "sospecha_motivo", None)}
                       for e in (s.evidencias or [])],
    }
    try:
        m = ai.sugerir_motivo(datos)
    except Exception:
        log.exception("Falló el motivo propuesto para la solicitud %s", sol_id)
        return {"motivo": "", "base": "sin_datos", "confianza": "baja"}
    return {"motivo": m.motivo, "base": m.base, "confianza": m.confianza}


@app.get("/api/solicitudes/{sol_id}/sugerencia")
def solicitud_sugerencia(sol_id: int, user: dict = Depends(require_gestion),
                         db: Session = Depends(get_db)) -> dict:
    """Cuántos litros PROPONE la IA autorizar. La persona decide; esto sólo le da un punto
    de partida con el que discutir en vez de una caja vacía.

    Se apoya en dos cosas y en ese orden: las CARGAS ANTERIORES REALES de ese activo (el
    libro del proveedor, el único sitio donde consta un despacho individual) y, sólo como
    contexto, la estimación del VIAJE, que es de otra escala —el viaje entero vale varias
    cargas—. NO se apoya en el tanque: la capacidad no existe como dato por unidad y la aguja
    no se fotografía nunca, así que «cuánto cabe» sería aritmética sobre dos suposiciones.
    """
    from . import ai

    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Solicitud no encontrada")

    ctx = solicitud_contexto(sol_id, user, db)
    tipo = getattr(s, "tipo_recarga", "motor")
    # Para el termo mandan las cargas del REMOLQUE: no recorre los kilómetros del tracto.
    cargas = (ctx.get("cargas_remolque") if tipo == "termo" else None) or ctx.get("cargas_unidad")
    datos = {
        "tipo_recarga": tipo,
        "unidad": (ctx.get("unidad") or {}).get("clave"),
        # Si ya se autorizó hoy, la IA tiene que saberlo: proponer la carga completa otra vez
        # sería proponer el doble del día.
        "ya_autorizado_hoy": (_ordenes_del_dia(db, s.unidad_id, excluir_solicitud=s.id,
                                              tipo_recarga=tipo)
                              if s.unidad_id else []),
        "cargas_anteriores": cargas,
        "esperado": ctx.get("esperado"),
        # El panorama del tanque: rendimiento del escáner, lo recorrido desde la última
        # lectura, lo que el viaje debería gastar y el techo del 2%. Sin esto la IA proponía
        # a ciegas en cuanto la unidad no tenía cargas anteriores.
        "tanque": rendimiento.panorama(db, s),
        # A la IA se le manda el km EFECTIVO: proponía litros sobre una distancia que el
        # coordinador ya había corregido, y con el tope calculado sobre esa misma base.
        "viaje": ({"km": ((ctx.get("asignacion") or {}).get("km_destino")
                          if (ctx.get("asignacion") or {}).get("km_destino") is not None
                          else (ctx.get("asignacion") or {}).get("km_estimado")),
                   "km_ajustados_a_mano": bool((ctx.get("asignacion") or {}).get("km_modificado")),
                   "origen": (ctx.get("asignacion") or {}).get("origen"),
                   "destino": (ctx.get("asignacion") or {}).get("destino")}
                  if ctx.get("asignacion") else None),
    }
    try:
        sug = ai.sugerir_litros(datos)
    except Exception:
        # Sin IA no se inventa un número: se devuelve lo que sí está medido para que el
        # coordinador decida con datos a la vista. Un formulario en blanco es peor.
        log.exception("No se pudo sugerir litros para la solicitud %s", sol_id)
        return {"litros": None, "base": "sin_ia", "confianza": "baja",
                "justificacion": "No se pudo consultar a la IA. Los datos de abajo son reales "
                                 "y están medidos: decide con ellos.",
                "datos": datos, "recortada": False}

    litros = sug.litros
    recortada = None
    # Tope aritmético sobre lo que dijo el modelo: ningún activo ha cargado nunca más de su
    # máximo histórico ni menos de su mínimo, así que una propuesta fuera de ahí no se
    # acepta tal cual. Se recorta y se DICE que se recortó.
    if litros is not None and cargas:
        if litros > cargas["max"]:
            recortada, litros = f"la IA propuso {litros:g} L y esta unidad nunca ha cargado más de {cargas['max']:g}", cargas["max"]
        elif litros < cargas["min"]:
            recortada, litros = f"la IA propuso {litros:g} L y esta unidad nunca ha cargado menos de {cargas['min']:g}", cargas["min"]
    # Y NUNCA por encima del techo. El recorte de arriba sube la propuesta hasta el mínimo
    # histórico de la unidad, y en un viaje corto ese mínimo puede pasarse del tope: la IA
    # lo tiene como techo duro (ai.py) y el recorte aritmético se lo saltaba por detrás,
    # ofreciendo con un botón un número que el propio sistema considera excesivo.
    _tope = (datos.get("tanque") or {}).get("tope")
    if litros is not None and _tope is not None and litros > _tope:
        recortada = (f"{recortada}; y se bajó al tope de {_tope:g} L" if recortada
                     else f"la propuesta pasaba del tope de {_tope:g} L")
        litros = _tope
    return {
        "litros": litros,
        "base": sug.base,
        "confianza": sug.confianza,
        "justificacion": sug.justificacion,
        "razonamiento": sug.razonamiento,
        "recortada": recortada,
        "datos": datos,
    }


@app.post("/api/solicitudes/{sol_id}/autorizar")
async def solicitud_autorizar(sol_id: int, request: Request,
                              user: dict = Depends(require_gestion),
                              db: Session = Depends(get_db)) -> dict:
    """El coordinador autoriza: se genera la orden de despacho con su folio automático.
    Solo desde EN_VALIDACION."""
    from .solicitudes import TransicionInvalida, autorizar, transicionar

    s = db.get(SolicitudRecarga, sol_id)
    if s is None:
        raise HTTPException(404, "Solicitud no encontrada")
    f = await request.form()
    # El coordinador FIJA los litros al autorizar (el operador ya no los aporta): obligatorio.
    litros = _pf_litros(f.get("litros"), "los litros a autorizar")
    # Freno a la SEGUNDA autorización del día para la misma unidad. No es un muro: el 46% de
    # los días una unidad carga más de una vez y eso es operación normal —153 de 472 cargas
    # reales son de menos de 100 L—. Lo que faltaba era que alguien lo mirara: hasta hoy no
    # existía ninguna consulta que comprobara si esa unidad ya tenía una orden autorizada hoy,
    # así que se podía autorizar la misma banda dos y tres veces sin que nada lo mencionara.
    if s.unidad_id and not (f.get("confirmar") or "").strip():
        previas = _ordenes_del_dia(db, s.unidad_id, excluir_solicitud=s.id,
                                   tipo_recarga=getattr(s, "tipo_recarga", "motor"))
        if previas:
            total = sum(o["litros"] or 0 for o in previas)
            raise HTTPException(409, {
                "codigo": "doble_carga_hoy",
                "mensaje": (f"Esta unidad ya tiene {len(previas)} "
                            f"orden{'es' if len(previas) > 1 else ''} de hoy por {total:g} L "
                            f"en total ({', '.join(o['folio'] for o in previas)}). "
                            f"Si es una recarga adicional, confírmalo."),
                "ordenes": previas,
                "litros_hoy": round(total, 2),
            })
    # SE TOMA AQUÍ, en la misma transacción. El grafo sólo deja AUTORIZADA desde
    # EN_VALIDACION, y hasta hoy ese salto lo daba el panel con una llamada aparte,
    # decidiendo si hacerlo según SU LISTA CACHEADA. Tres defectos, que son el mismo:
    #
    #   · el operador corrige una devuelta y la reenvía; la fila real pasa a ENVIADA pero
    #     la caché seguía diciendo «devuelta», no se daba el salto, y autorizar devolvía
    #     un 409 del que no se salía desde la pantalla;
    #   · eran dos escrituras sin vuelta atrás: la primera podía quedar hecha y la segunda
    #     no, dejando la solicitud tomada y sin autorizar, en silencio;
    #   · y al declinar el aviso de doble carga la primera ya estaba hecha, así que el
    #     siguiente intento chocaba contra ella para siempre.
    #
    # Va DESPUÉS del freno de la doble carga a propósito: tomarla antes dejaría la
    # solicitud tomada al declinar, que es justo lo que se viene a cerrar. Y `transicionar`
    # no hace commit, así que esto y la autorización viven o mueren juntas.
    if s.estado == EstadoSolicitud.ENVIADA:
        try:
            transicionar(db, s, EstadoSolicitud.EN_VALIDACION, user,
                         nota="Tomada al autorizar")
        except TransicionInvalida as e:
            raise HTTPException(409, str(e))
    # Lo que la IA había propuesto viaja de vuelta desde el panel: se guarda AL LADO de lo
    # autorizado para poder preguntar después si a la propuesta se le hizo caso.
    try:
        orden = autorizar(db, s, user, litros=litros,
                          sugeridos=_pf(f.get("litros_sugeridos")),
                          sugerencia_motivo=(f.get("sugerencia_motivo") or None))
    except TransicionInvalida as e:
        raise HTTPException(409, str(e))
    db.commit()
    # El tope que REGÍA viaja de vuelta, leído DE LA ORDEN y no recalculado: así la
    # respuesta no puede discrepar de lo que quedó escrito. El panel lo usa para decirlo
    # en el aviso de cierre, que es la otra mitad de «avisa y deja constancia».
    excedido = (orden.tope_litros is not None
                and orden.litros_autorizados is not None
                and orden.litros_autorizados > orden.tope_litros)
    return {"ok": True, "estado": s.estado.value, "folio": orden.folio,
            "tope": orden.tope_litros, "estimado": orden.tope_estimado,
            "tope_motivo": orden.tope_motivo, "excedido": excedido}


@app.post("/api/coordinador/solicitud")
async def coordinador_solicitud(request: Request, user: dict = Depends(require_coordinador),
                                db: Session = Depends(get_db)) -> dict:
    """El coordinador LEVANTA una solicitud de combustible y la AUTORIZA en el mismo acto:
    elige unidad y litros (el operador es opcional; si no lo indica se toma el titular de la
    unidad). La solicitud entra DIRECTO a la cola de Combustible (AUTORIZADA, con su folio),
    sin pasar por la captura del operador. Queda marcada como asistida para no romper la
    trazabilidad. Recorre BORRADOR→ENVIADA→EN_VALIDACION→AUTORIZADA por la máquina de estados."""
    from .solicitudes import TransicionInvalida, autorizar, transicionar

    f = await request.form()
    unidad_id = _pi(f.get("unidad_id"))
    litros = _pf_litros(f.get("litros"), "los litros a autorizar")
    operador_id = _pi(f.get("operador_id"))
    nota = (f.get("nota") or "").strip() or None
    u = db.get(Unidad, unidad_id) if unidad_id else None
    if u is None:
        raise HTTPException(400, "Unidad no válida")
    if litros is None or litros <= 0:
        raise HTTPException(400, "Indica los litros a autorizar")
    if operador_id is not None and db.get(Operador, operador_id) is None:
        raise HTTPException(400, "Operador no válido")
    if operador_id is None:
        operador_id = u.operador_asignado_id   # titular de la unidad (si tiene)

    # El mismo freno que en /autorizar: este formulario autoriza por dentro, así que sin esto
    # era la puerta de atrás por la que se podía duplicar la carga del día sin ver el aviso.
    if not (f.get("confirmar") or "").strip():
        previas = _ordenes_del_dia(db, unidad_id, tipo_recarga="motor")
        if previas:
            total = sum(o["litros"] or 0 for o in previas)
            raise HTTPException(409, {
                "codigo": "doble_carga_hoy",
                "mensaje": (f"Esta unidad ya tiene {len(previas)} "
                            f"orden{'es' if len(previas) > 1 else ''} de hoy por {total:g} L "
                            f"({', '.join(o['folio'] for o in previas)}). "
                            f"Si es una recarga adicional, confírmalo."),
                "ordenes": previas, "litros_hoy": round(total, 2)})
    # El viaje del que sale, CONGELADO aquí. Si mañana se le levanta otro, esta solicitud
    # sigue midiéndose contra el suyo.
    _asig = db.scalar(select(AsignacionViaje.id)
                      .where(AsignacionViaje.operador_id == operador_id,
                             AsignacionViaje.estado == EstadoAsignacion.ACTIVA)
                      .order_by(AsignacionViaje.creada_en.desc()).limit(1)) if operador_id else None
    s = SolicitudRecarga(
        estado=EstadoSolicitud.BORRADOR, creada_por_id=user.get("id"),
        operador_id=operador_id, unidad_id=unidad_id, asignacion_id=_asig,
        litros_solicitados=litros, capturada_asistida=True)
    db.add(s)
    db.flush()
    db.add(TransicionSolicitud(
        solicitud_id=s.id, estado_anterior=None, estado_nuevo="borrador",
        por_usuario_id=user.get("id"), nota="Solicitud levantada por el coordinador"))
    try:
        transicionar(db, s, EstadoSolicitud.ENVIADA, user, nota=nota or "Levantada por el coordinador")
        transicionar(db, s, EstadoSolicitud.EN_VALIDACION, user)
        orden = autorizar(db, s, user, litros=litros)
    except TransicionInvalida as e:
        raise HTTPException(409, str(e))
    db.commit()
    log.info("Coordinador levantó solicitud %s (unidad %s, %.0f L) -> %s",
             s.id, u.clave, litros, orden.folio)
    return {"ok": True, "id": s.id, "estado": s.estado.value, "folio": orden.folio,
            "operador_id": operador_id}


@app.get("/api/coordinador/resumen")
def coordinador_resumen(user: dict = Depends(require_gestion), db: Session = Depends(get_db)) -> dict:
    """Datos duros para los gráficos del resumen del coordinador: composición de la flota
    (pastel), viajes por mes por tipo (áreas), consumo por unidad (barras) y km vs rendimiento
    (dispersión). Compara Tractos vs Camiones vs Remolques donde el dato lo permite."""
    # Composición de la flota (unidades activas por tipo + remolques). Sin VIGENTE: eso filtra
    # viajes, no unidades.
    n_tracto = db.scalar(select(func.count()).select_from(Unidad)
                         .where(Unidad.tipo == TipoUnidad.TRACTO, Unidad.activo.is_(True))) or 0
    n_camion = db.scalar(select(func.count()).select_from(Unidad)
                         .where(Unidad.tipo == TipoUnidad.CAMION, Unidad.activo.is_(True))) or 0
    n_rem = db.scalar(select(func.count()).select_from(Remolque)
                      .where(Remolque.activo.is_(True))) or 0
    composicion = {"Tractos": n_tracto, "Camiones": n_camion, "Remolques": n_rem}

    # Viajes por mes por tipo (últimos 12 meses) — para el gráfico de áreas.
    mes = func.to_char(Viaje.fecha, "YYYY-MM")
    filas = db.execute(
        select(mes, Unidad.tipo, func.count(Viaje.id))
        .join(Unidad, Viaje.unidad_id == Unidad.id)
        .where(Viaje.fecha.isnot(None), VIGENTE)
        .group_by(mes, Unidad.tipo)
    ).all()
    meses = sorted({f[0] for f in filas})[-12:]
    trac = {m: 0 for m in meses}
    cam = {m: 0 for m in meses}
    for m, tipo, n in filas:
        if m not in trac:
            continue
        if tipo == TipoUnidad.TRACTO:
            trac[m] = n
        else:
            cam[m] = n
    mensual = {"labels": meses, "Tractos": [trac[m] for m in meses], "Camiones": [cam[m] for m in meses]}

    # Consumo de litros por unidad (top 8), con su tipo — barras coloreadas por tipo.
    cons = db.execute(
        select(Unidad.clave, Unidad.tipo, func.round(func.sum(Viaje.lts_real).cast(Numeric), 0))
        .join(Viaje, Viaje.unidad_id == Unidad.id).where(Viaje.lts_real.isnot(None), VIGENTE)
        .group_by(Unidad.clave, Unidad.tipo).order_by(func.sum(Viaje.lts_real).desc()).limit(8)
    ).all()
    consumo = [{"unidad": c, "tipo": t.value, "litros": float(l) if l is not None else 0}
               for (c, t, l) in cons]

    # Dispersión km vs rendimiento real, por tipo (muestra de los viajes más recientes).
    disp = db.execute(
        select(Viaje.kilometros, Viaje.rto_real, Unidad.tipo)
        .join(Unidad, Viaje.unidad_id == Unidad.id)
        .where(Viaje.rto_real.isnot(None), Viaje.kilometros.isnot(None),
               Viaje.kilometros > 0, VIGENTE)
        .order_by(Viaje.fecha.desc()).limit(400)
    ).all()
    dispersion = [{"km": float(k), "rto": float(r), "tipo": t.value} for (k, r, t) in disp]

    return {"composicion": composicion, "mensual": mensual,
            "consumo": consumo, "dispersion": dispersion}


@app.get("/api/conciliacion")
def conciliacion_motor(
    anio: int | None = Query(None, ge=2000, le=2100),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Cruce entre lo que midió el MOTOR y lo que dice el COMPROBANTE, por año y por unidad."""
    from . import conciliacion as conc

    if anio is None:
        anio = db.scalar(select(func.max(func.extract("year", Viaje.fecha))))
        anio = int(anio) if anio else fecha_flota().year
    d = conc.cruce_por_unidad(db, anio)
    return {
        "anio": anio,
        "por_anio": conc.cruce_por_anio(db),
        "unidades": d["unidades"],
        "totales": d["totales"],
        "descartadas": d["descartadas"],
        "ratio_max": d["ratio_max"],
        "termo": conc.horas_termo(db),
        "escaneos": db.scalar(select(func.count(EscaneoMotor.id))) or 0,
    }


@app.get("/api/conciliacion/escaneos")
def conciliacion_escaneos(
    user: dict = Depends(require_user), db: Session = Depends(get_db),
) -> list[dict]:
    """Detalle fino: cada PDF de escaneo con su telemetría y su período."""
    from . import conciliacion as conc

    escaneos = db.execute(
        select(EscaneoMotor).order_by(EscaneoMotor.periodo_fin.desc())
    ).scalars().all()
    return [conc.conciliar(db, e) for e in escaneos]


# ─────────────────────────────────────────────────────────────────────────────
# Lecturas de motor: el historial por unidad, el PDF y la puerta de entrada
# ─────────────────────────────────────────────────────────────────────────────
# El PDF vive en disco con un nombre DERIVADO DEL ID de la fila. No hay ningún nombre de
# fuera tocando el disco, así que aquí no hace falta el validador de forma que sí lleva la
# licencia: el nombre no puede ser otra cosa.
_ESC_NOMBRE = re.compile(r"^esc\d+\.pdf$")
_ESC_LIMITE = 15 * 1024 * 1024


def _dir_escaneos() -> Path:
    d = settings.media_dir / "escaneos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ruta_escaneo(nombre: str | None) -> Path | None:
    if not nombre or not _ESC_NOMBRE.match(nombre):
        return None
    p = _dir_escaneos() / nombre
    return p if p.is_file() else None


def alertas_de_serie(serie: "list[EscaneoMotor]", unidad=None) -> dict[int, dict]:
    """Qué lecturas de una unidad merecen una mirada, y POR QUÉ.

    Recibe la serie entera de la unidad y no una lectura suelta, porque las dos reglas
    necesitan contexto: el rendimiento se juzga contra lo que esa misma unidad venía
    haciendo, y sin la serie no hay «venía haciendo».

    LAS DOS REGLAS, Y LO QUE SUSTITUYEN:

    1. RALENTÍ, medido en LITROS y no en tiempo. El escáner da las dos cosas y sólo una se
       puede accionar: la flota pasa el 37.9% del tiempo parada, pero eso son 3,316 L de
       42,227 —el 7.9% del diésel—, porque parado se queman uno o dos litros por hora y
       rodando muchos más. El umbral viejo (35% del TIEMPO) estaba POR DEBAJO de la
       mediana de la flota, así que marcaba a la mayoría: 24 de 43 lecturas.

    2. RENDIMIENTO contra su PROPIO HISTORIAL PREVIO. Antes era contra
       `Unidad.rendimiento_objetivo`, vacío en 64 de 65 unidades: se encendió 0 veces.
       «Previo» es literal —sólo las lecturas anteriores a ésta—, porque una referencia
       que incluye la lectura que juzga la arrastra consigo y nunca la marca.

    El piso de km es el mismo del motor de referencia: un período de 143 km no mide un
    rendimiento, y sin el piso las tres caídas mayores de la base son justo eso.

    Si la unidad trae `rendimiento_objetivo` o `pct_tolerancia`, esa decisión humana manda
    sobre la derivada, igual que en `validacion.py`.
    """
    from .rendimiento import llave_periodo

    piso = settings.piso_km_escaneo
    objetivo = getattr(unidad, "rendimiento_objetivo", None)
    tol = getattr(unidad, "pct_tolerancia", None)
    tol = abs(tol) if tol is not None else settings.caida_rendimiento_alerta

    orden = sorted((e for e in serie if e.periodo_fin),
                   key=lambda e: (e.periodo_fin, e.id))
    out: dict[int, dict] = {}
    for i, e in enumerate(orden):
        # El historial PREVIO, con la misma ventana que usa la referencia: hacia atrás
        # hasta `km_serie_escaneo` y sin salirse de `dias_serie_escaneo`. Un período de
        # hace un año no dice lo que la unidad «venía haciendo».
        km_prev = lts_prev = 0.0
        vistos: set = set()
        for p in reversed(orden[:i]):
            if (e.periodo_fin - p.periodo_fin).days > settings.dias_serie_escaneo:
                break
            if not p.km or not p.litros:
                continue
            k = llave_periodo(p)          # un período duplicado no cuenta dos veces
            if k in vistos:
                continue
            vistos.add(k)
            km_prev += p.km
            lts_prev += p.litros
            if km_prev >= settings.km_serie_escaneo:
                break

        medible = bool(e.km and e.km >= piso)
        ref = origen = None
        if objetivo:
            ref, origen = objetivo, "el objetivo de la unidad"
        elif km_prev >= piso and lts_prev > 0:
            ref = km_prev / lts_prev
            origen = f"lo que venía haciendo ({km_prev:,.0f} km antes de esto)"
        caida = (e.rendimiento / ref - 1) if (ref and e.rendimiento) else None

        motivos = []
        rend_bajo = bool(medible and caida is not None and caida <= -tol)
        if rend_bajo:
            motivos.append(f"rinde {abs(caida) * 100:.0f}% menos que {origen}: "
                           f"{e.rendimiento:.2f} contra {ref:.2f} km/L")

        frac = ((e.lts_ralenti / e.litros)
                if (e.lts_ralenti is not None and e.litros) else None)
        ralenti_alto = bool(medible and frac is not None
                            and frac >= settings.ralenti_frac_alerta)
        if ralenti_alto:
            motivos.append(f"quemó el {frac * 100:.0f}% de su diésel parado "
                           f"({e.lts_ralenti:,.0f} L de {e.litros:,.0f})")

        out[e.id] = {
            "alerta": bool(motivos), "motivos": motivos,
            "rend_bajo": rend_bajo, "ralenti_alto": ralenti_alto,
            "referencia": round(ref, 3) if ref else None,
            "referencia_origen": origen,
            "caida": round(caida, 4) if caida is not None else None,
            "frac_ralenti": round(frac, 4) if frac is not None else None,
            # `medible` distingue «va bien» de «este período es demasiado corto para
            # decir nada». Sin ese matiz, 143 km sin alerta parecen 143 km correctos.
            "medible": medible,
        }
    # Las que no tienen `periodo_fin` no entran en el orden y se quedarían fuera del dict.
    for e in serie:
        out.setdefault(e.id, {"alerta": False, "motivos": [], "rend_bajo": False,
                              "ralenti_alto": False, "referencia": None,
                              "referencia_origen": None, "caida": None,
                              "frac_ralenti": None, "medible": False})
    return out


@app.get("/api/combustible/motor/historial")
def motor_historial(user: dict = Depends(require_ver_combustible),
                    db: Session = Depends(get_db)) -> dict:
    """El historial de lecturas AGRUPADO POR UNIDAD, camiones y tractos.

    Incluye a propósito las unidades SIN ninguna lectura. Listar sólo las medidas deja
    creer que la flota está cubierta, y hoy no lo está: los camiones no tienen ni una.
    """
    por_unidad: dict[int, list[EscaneoMotor]] = {}
    for e in db.execute(select(EscaneoMotor).order_by(
            EscaneoMotor.periodo_fin.desc(), EscaneoMotor.id.desc())).scalars():
        por_unidad.setdefault(e.unidad_id, []).append(e)

    # Las activas, más cualquiera que tenga lecturas aunque ya esté de baja: su historial
    # no deja de ser cierto porque la unidad se haya ido.
    unis = db.execute(select(Unidad).where(
        or_(Unidad.activo.is_(True), Unidad.id.in_(list(por_unidad) or [-1])))
        .order_by(Unidad.clave)).scalars().all()

    filas, n_alertas = [], 0
    for u in unis:
        es = por_unidad.get(u.id, [])
        # La alerta se calcula sobre la SERIE de la unidad, no lectura a lectura: el
        # rendimiento se juzga contra lo que esa misma unidad venía haciendo antes.
        alertas = alertas_de_serie(es, u)
        lecturas = []
        for e in es:
            a = alertas.get(e.id, {})
            if a.get("alerta"):
                n_alertas += 1
            lecturas.append({
                "id": e.id, "formato": e.formato, "archivo": e.archivo,
                "periodo_inicio": e.periodo_inicio.isoformat() if e.periodo_inicio else None,
                "periodo_fin": e.periodo_fin.isoformat() if e.periodo_fin else None,
                "km": e.km, "litros": e.litros, "rendimiento": e.rendimiento,
                "pct_ralenti": e.pct_ralenti, "lts_ralenti": e.lts_ralenti,
                "odometro_total": e.odometro_total, "analisis": e.analisis,
                "tiene_pdf": _ruta_escaneo(e.pdf) is not None,
                "alerta": bool(a.get("alerta")),
                "rend_bajo": a.get("rend_bajo", False),
                "ralenti_alto": a.get("ralenti_alto", False),
                "motivos": a.get("motivos", []),
                "referencia": a.get("referencia"),
                "referencia_origen": a.get("referencia_origen"),
                "caida": a.get("caida"),
                "frac_ralenti": a.get("frac_ralenti"),
                # Distingue «va bien» de «este período es demasiado corto para decir nada».
                "medible": a.get("medible", False),
            })
        km = sum(e.km or 0 for e in es)
        lts = sum(e.litros or 0 for e in es)
        filas.append({
            "id": u.id, "clave": u.clave, "tipo": u.tipo.value if u.tipo else None,
            "activa": u.activo, "objetivo": u.rendimiento_objetivo,
            "n": len(es), "km": km, "litros": lts,
            # El rendimiento del conjunto es km TOTALES entre litros TOTALES, no el
            # promedio de los km/L de cada lectura: un período de 80 km no pesa lo mismo
            # que uno de 5,000 y promediarlos los iguala.
            "rendimiento": (km / lts) if lts else None,
            "ultima": lecturas[0]["periodo_fin"] if lecturas else None,
            "rend_ultima": lecturas[0]["rendimiento"] if lecturas else None,
            "con_pdf": sum(1 for x in lecturas if x["tiene_pdf"]),
            "lecturas": lecturas,
        })

    def resumen(cond) -> dict:
        g = [f for f in filas if cond(f)]
        return {"unidades": len(g), "con_lecturas": sum(1 for f in g if f["n"]),
                "lecturas": sum(f["n"] for f in g)}

    return {
        "unidades": filas,
        "resumen": {
            "escaneos": sum(f["n"] for f in filas),
            "con_pdf": sum(f["con_pdf"] for f in filas),
            "n_alertas": n_alertas,
            # Los umbrales viajan al cliente para que la pantalla pueda EXPLICAR la
            # alerta con los números de verdad, en vez de repetirlos a mano.
            "umbral_ralenti": settings.ralenti_frac_alerta,
            "umbral_caida": settings.caida_rendimiento_alerta,
            "piso_km": settings.piso_km_escaneo,
            "no_medibles": sum(1 for f in filas for x in f["lecturas"]
                               if not x.get("medible")),
            "tractos": resumen(lambda f: f["tipo"] == "TRACTO"),
            "camiones": resumen(lambda f: f["tipo"] == "CAMION"),
        },
    }


@app.get("/api/motor/escaneos/{esc_id}/pdf")
def motor_escaneo_pdf(esc_id: int, user: dict = Depends(require_ver_combustible),
                      db: Session = Depends(get_db)):
    """El reporte tal cual salió de la computadora del camión."""
    e = db.get(EscaneoMotor, esc_id)
    if e is None:
        raise HTTPException(404, "Esa lectura no existe")
    p = _ruta_escaneo(e.pdf)
    if p is None:
        raise HTTPException(404, "Esa lectura se importó sin guardar el PDF")
    return FileResponse(p, media_type="application/pdf", headers={
        "Cache-Control": "no-store",
        # El nombre con el que llegó, para que al descargarlo se reconozca.
        "Content-Disposition": f'inline; filename="{_ascii_nombre(e.archivo)}"'})


def _ascii_nombre(s: str | None) -> str:
    """Un nombre de fichero seguro para una cabecera HTTP (ASCII, sin comillas)."""
    import unicodedata
    t = unicodedata.normalize("NFKD", s or "reporte.pdf")
    t = "".join(c for c in t if 32 <= ord(c) < 127 and c not in '"\\')
    return t[:120] or "reporte.pdf"


@app.post("/api/motor/escaneos")
async def motor_escaneos_cargar(files: list[UploadFile] = File(...),
                                user: dict = Depends(require_combustible),
                                db: Session = Depends(get_db)) -> dict:
    """Carga PDF de escaneo: importa los que faltan y le engancha el papel a los que ya están.

    Se puede soltar la carpeta entera. Cada archivo cae en una de cinco cestas y TODAS se
    devuelven con nombre y apellido, porque un contador a secas obligaría a abrir la base
    para entender qué pasó:
      nuevo       se importó la lectura (y se guardó su PDF)
      adjuntado   la lectura ya estaba; ahora además tiene su PDF
      ya          la lectura ya estaba y ya tenía PDF
      repetido    es un período que YA está, guardado con otro nombre de archivo
      rechazado   no se pudo leer, o nombra una unidad que no está en el catálogo
    """
    import uuid

    from .escaneo import leer_pdf, llave_odometro

    claves = {c: i for i, c in db.execute(select(Unidad.id, Unidad.clave)).all()}
    ya_arch = {a: (i, p) for a, i, p in db.execute(
        select(EscaneoMotor.archivo, EscaneoMotor.id, EscaneoMotor.pdf))}
    odo_vistos: dict[tuple, str] = {}
    for uid, odo, km, arch in db.execute(select(
            EscaneoMotor.unidad_id, EscaneoMotor.odometro_total,
            EscaneoMotor.km, EscaneoMotor.archivo)):
        k = llave_odometro(uid, odo, km)
        if k is not None:
            odo_vistos.setdefault(k, arch)

    tmp = _dir_escaneos() / ".entrando"
    tmp.mkdir(exist_ok=True)
    reporte: list[dict] = []
    # (fila, bytes) de lo que se va a escribir a disco DESPUÉS del commit: si la
    # transacción se cae, no queda ni un PDF suelto sin fila que lo explique.
    pendientes: list[tuple] = []
    unidades_tocadas: set[int] = set()

    for f in files:
        nombre = Path((f.filename or "reporte.pdf").replace("\\", "/")).name[:200]
        def anota(estado, detalle=None, **extra):
            reporte.append({"archivo": nombre, "estado": estado,
                            "detalle": detalle, **extra})
        crudo = await f.read(_ESC_LIMITE + 1)
        if len(crudo) > _ESC_LIMITE:
            anota("rechazado", "Supera el límite de 15 MB")
            continue
        if not crudo:
            anota("rechazado", "El archivo llegó vacío (0 bytes)")
            continue
        if crudo[:5] != b"%PDF-":
            anota("rechazado", "No es un PDF")
            continue

        # Si ya está importado, lo único que falta es el papel.
        if nombre in ya_arch:
            esc_id, pdf = ya_arch[nombre]
            if _ruta_escaneo(pdf) is not None:
                anota("ya", "Ya estaba importado y ya tenía su PDF", escaneo_id=esc_id)
            else:
                e = db.get(EscaneoMotor, esc_id)
                if e is None:
                    anota("rechazado", "La lectura desapareció mientras se cargaba")
                    continue
                e.pdf, e.pdf_en = f"esc{esc_id}.pdf", datetime.now(timezone.utc)
                pendientes.append((e, crudo))
                anota("adjuntado", "Ya estaba importado; ahora además tiene su PDF",
                      escaneo_id=esc_id)
            continue

        ruta = tmp / f"{uuid.uuid4().hex}.pdf"
        try:
            ruta.write_bytes(crudo)
            # A un hilo: son 163 archivos en el peor caso y `pypdf` es CPU; hacerlo aquí
            # dentro dejaría el servidor sin atender a nadie mientras dura.
            d = await run_in_threadpool(leer_pdf, str(ruta))
        except Exception as e:
            anota("rechazado", str(e)[:160])
            continue
        finally:
            ruta.unlink(missing_ok=True)

        uid = claves.get(d["unidad"])
        if uid is None:
            # NO se da de alta la unidad. Escribir en el catálogo sin que nadie lo apruebe
            # es justo lo que el maestro de placas existe para no hacer.
            anota("rechazado", f"La unidad {d['unidad']} no está en el catálogo; "
                               f"date de alta primero", unidad=d["unidad"])
            continue
        k = llave_odometro(uid, d.get("odometro_total"), d.get("km"))
        if k is not None and k in odo_vistos:
            anota("repetido", f"Mismo período que «{odo_vistos[k]}»: las dos lecturas "
                              f"cierran en el kilómetro {k[1]:,.2f}", unidad=d["unidad"])
            continue

        e = EscaneoMotor(
            unidad_id=uid, archivo=nombre, formato=d["formato"],
            motor=d.get("motor"), serie_motor=d.get("serie_motor"),
            periodo_inicio=d.get("periodo_inicio"), periodo_fin=d["periodo_fin"],
            odometro_total=d.get("odometro_total"), lts_total=d.get("lts_total"),
            lts_ralenti_total=d.get("lts_ralenti_total"),
            km=d["km"], litros=d["litros"], rendimiento=d.get("rendimiento"),
            tiempo=d.get("tiempo"), lts_ralenti=d.get("lts_ralenti"),
            pct_ralenti=d.get("pct_ralenti"), tiempo_ralenti=d.get("tiempo_ralenti"),
            vel_max=d.get("vel_max"), vel_prom=d.get("vel_prom"),
            rpm_prom=d.get("rpm_prom"), rpm_max=d.get("rpm_max"),
            carga_prom=d.get("carga_prom"), km_crucero=d.get("km_crucero"),
            km_top_gear=d.get("km_top_gear"), frenadas=d.get("frenadas"),
            paradas_panico=d.get("paradas_panico"))
        db.add(e)
        db.flush()                       # hace falta el id para nombrar su PDF
        e.pdf, e.pdf_en = f"esc{e.id}.pdf", datetime.now(timezone.utc)
        pendientes.append((e, crudo))
        ya_arch[nombre] = (e.id, e.pdf)
        if k is not None:
            odo_vistos[k] = nombre
        unidades_tocadas.add(uid)
        anota("nuevo", None, escaneo_id=e.id, unidad=d["unidad"],
              periodo_fin=d["periodo_fin"].isoformat() if d.get("periodo_fin") else None,
              km=d.get("km"), litros=d.get("litros"))

    # El inicio del período no viene en los Cummins: se encadena con el fin del anterior
    # de la MISMA unidad. Sólo se recorren las unidades que esta carga tocó.
    inferidos = 0
    for uid in unidades_tocadas:
        serie = db.execute(select(EscaneoMotor).where(EscaneoMotor.unidad_id == uid)
                           .order_by(EscaneoMotor.periodo_fin, EscaneoMotor.id)).scalars().all()
        for prev, act in zip(serie, serie[1:]):
            if act.periodo_inicio is None:
                act.periodo_inicio = prev.periodo_fin
                inferidos += 1

    cuenta = {k: sum(1 for r in reporte if r["estado"] == k)
              for k in ("nuevo", "adjuntado", "ya", "repetido", "rechazado")}
    if cuenta["nuevo"] or cuenta["adjuntado"]:
        registrar_actividad(db, accion="escaneos_cargados", usuario=user,
                            entidad="escaneo_motor", meta=cuenta)
    _commit(db, "Ese reporte ya estaba cargado")

    # Los PDF se escriben AHORA, con la transacción ya cerrada.
    for e, crudo in pendientes:
        try:
            (_dir_escaneos() / e.pdf).write_bytes(crudo)
        except OSError:
            log.exception("No se pudo guardar el PDF del escaneo %s", e.id)
    return {"ok": True, "cuenta": cuenta, "inferidos": inferidos,
            "total": db.scalar(select(func.count(EscaneoMotor.id))) or 0,
            "archivos": reporte}


@app.get("/api/uso-ia")
def uso_ia(
    dias: int = Query(30, ge=1, le=365),
    user: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Cuánto se ha consumido de IA: llamadas, tokens y dinero, por día y por operación.

    Solo administradores: es información de facturación, no de operación.
    """
    from .ai import PRECIOS_USD
    from .models import UsoIA

    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    base = UsoIA.momento >= desde

    tot = db.execute(select(
        func.count(UsoIA.id), func.sum(UsoIA.tokens_entrada), func.sum(UsoIA.tokens_salida),
        func.sum(UsoIA.costo_usd), func.min(UsoIA.momento),
    ).where(base)).one()

    por_op = db.execute(
        select(UsoIA.operacion, func.count(UsoIA.id), func.sum(UsoIA.tokens_entrada),
               func.sum(UsoIA.tokens_salida), func.sum(UsoIA.costo_usd))
        .where(base).group_by(UsoIA.operacion).order_by(func.sum(UsoIA.costo_usd).desc())
    ).all()

    dia = func.to_char(UsoIA.momento, "YYYY-MM-DD")
    por_dia = db.execute(
        select(dia, func.count(UsoIA.id), func.sum(UsoIA.costo_usd))
        .where(base).group_by(dia).order_by(dia)
    ).all()

    fallos = db.scalar(select(func.count(UsoIA.id)).where(base, UsoIA.ok.is_(False)))
    return {
        "dias": dias,
        "totales": {"llamadas": tot[0] or 0, "tokens_entrada": int(tot[1] or 0),
                    "tokens_salida": int(tot[2] or 0), "costo_usd": float(tot[3] or 0),
                    "desde": tot[4].isoformat() if tot[4] else None,
                    "fallidas": fallos or 0},
        "por_operacion": [{"operacion": o, "llamadas": n, "tokens_entrada": int(e or 0),
                           "tokens_salida": int(s or 0), "costo_usd": float(c or 0)}
                          for o, n, e, s, c in por_op],
        "por_dia": [{"dia": d, "llamadas": n, "costo_usd": float(c or 0)} for d, n, c in por_dia],
        "modelo_actual": settings.anthropic_model,
        "precios_usd_por_millon": PRECIOS_USD,
    }


@app.get("/api/bitacora")
def bitacora_nueva(
    desde: date | None = None,
    hasta: date | None = None,
    unidad: str | None = None,
    limite: int = Query(200, ge=1, le=2000),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Bitácora en el formato nuevo del cliente (30 columnas), ya calculada."""
    from . import bitacora as bit

    return {
        "columnas": [{"campo": c, "titulo": t} for c, t in bit.COLUMNAS],
        "filas": bit.filas(db, desde, hasta, unidad, limite),
        # Por la fuente única, no por el ajuste crudo: `precios_por_anio` puede fijar otro
        # precio para el año en curso y leer `settings.precio_litro` se lo saltaba.
        "precio_litro": precio_del_litro(),
        "reserva_litros": settings.reserva_litros,
    }


@app.get("/api/bitacora.xlsx")
def bitacora_xlsx(
    desde: date | None = None,
    hasta: date | None = None,
    unidad: str | None = None,
    limite: int = Query(2000, ge=1, le=20000),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    """La misma bitácora como .xlsx, con los encabezados EXACTOS del archivo del cliente
    para que puedan pegarla en su hoja sin reacomodar columnas."""
    import io

    import openpyxl

    from . import bitacora as bit

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BITACORA"
    ws.append([t for _, t in bit.COLUMNAS])
    for f in ws[1]:
        f.font = openpyxl.styles.Font(bold=True)
    for fila in bit.filas(db, desde, hasta, unidad, limite):
        ws.append([fila.get(c) for c, _ in bit.COLUMNAS])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="bitacora.xlsx"'},
    )


@app.get("/api/reporte-ejecutivo")
def reporte_ejecutivo(
    anio: int | None = Query(None, ge=2000, le=2100),
    comparar: bool = Query(True),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Reporte ejecutivo de combustible: rendimiento vs ideal, ralentí, operadores y dinero."""
    from . import reporte as rep

    if anio is None:
        anio = db.scalar(select(func.max(func.extract("year", Viaje.fecha)))) or fecha_flota().year
        anio = int(anio)
    return rep.generar(db, anio, anio - 1 if comparar else None)


@app.get("/api/reporte-ejecutivo/anios")
def reporte_anios(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[int]:
    """Años con datos suficientes para generar el reporte."""
    filas = db.execute(
        select(func.extract("year", Viaje.fecha))
        .where(Viaje.kilometros > 0, Viaje.lts_real > 0)
        .group_by(func.extract("year", Viaje.fecha))
        .order_by(func.extract("year", Viaje.fecha).desc())
    ).scalars().all()
    return [int(a) for a in filas]


@app.get("/api/stats/rendimiento-unidad")
def stat_rendimiento_unidad(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    filas = db.execute(
        select(Unidad.clave, func.round(func.avg(Viaje.rto_real).cast(Numeric), 3), func.count(Viaje.id))
        .join(Viaje, Viaje.unidad_id == Unidad.id).where(Viaje.rto_real.isnot(None), VIGENTE)
        .group_by(Unidad.clave).having(func.count(Viaje.id) >= 5)
        .order_by(func.count(Viaje.id).desc()).limit(15)
    ).all()
    return {"labels": [f[0] for f in filas], "valores": [float(f[1]) for f in filas]}


@app.get("/api/stats/viajes-mes")
def stat_viajes_mes(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    mes = func.to_char(Viaje.fecha, "YYYY-MM")
    filas = db.execute(
        select(mes, func.count(Viaje.id), func.round(func.sum(Viaje.lts_real).cast(Numeric), 0))
        .where(VIGENTE).group_by(mes).order_by(mes)
    ).all()
    return {
        "labels": [f[0] for f in filas],
        "viajes": [f[1] for f in filas],
        "litros": [float(f[2]) if f[2] is not None else 0 for f in filas],
    }


@app.get("/api/stats/tipos")
def stat_tipos(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    por_tipo = {t.value: db.scalar(select(func.count()).select_from(Unidad).where(Unidad.tipo == t)) for t in TipoUnidad}
    anoms = db.execute(select(Anomalia.tipo, func.count()).group_by(Anomalia.tipo)).all()
    return {"unidades": por_tipo, "anomalias": {a[0]: a[1] for a in anoms}}


@app.get("/api/stats/scanner-vs-real")
def stat_scanner_vs_real(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(
        select(Unidad.clave,
               func.round(func.avg(Viaje.rto).cast(Numeric), 3),
               func.round(func.avg(Viaje.rto_real).cast(Numeric), 3),
               func.count(Viaje.id))
        .join(Viaje, Viaje.unidad_id == Unidad.id)
        .where(Viaje.rto_real.isnot(None), Viaje.rto.isnot(None), VIGENTE)
        .group_by(Unidad.clave).having(func.count(Viaje.id) >= 5)
        .order_by(func.count(Viaje.id).desc()).limit(10)
    ).all()
    return [
        {"unidad": c, "rto": float(s) if s is not None else None,
         "rto_real": float(r) if r is not None else None, "viajes": n}
        for (c, s, r, n) in filas
    ]


@app.get("/api/stats/consumo-unidad")
def stat_consumo_unidad(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(
        select(Unidad.clave, func.round(func.sum(Viaje.lts_real).cast(Numeric), 0))
        .join(Viaje, Viaje.unidad_id == Unidad.id).where(Viaje.lts_real.isnot(None), VIGENTE)
        .group_by(Unidad.clave).order_by(func.sum(Viaje.lts_real).desc()).limit(8)
    ).all()
    return [{"unidad": c, "litros": float(l) if l is not None else 0} for (c, l) in filas]


@app.get("/api/stats/heatmap-anomalias")
def stat_heatmap_anomalias(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    mes = func.to_char(Viaje.fecha, "YYYY-MM")
    filas = db.execute(
        select(Unidad.clave, mes, func.count(Anomalia.id))
        .join(Viaje, Anomalia.viaje_id == Viaje.id)
        .join(Unidad, Viaje.unidad_id == Unidad.id)
        .where(Viaje.fecha.isnot(None))
        .group_by(Unidad.clave, mes)
    ).all()
    meses = sorted({f[1] for f in filas})[-12:]
    por_unidad: dict[str, dict[str, int]] = {}
    for clave, m, n in filas:
        por_unidad.setdefault(clave, {})[m] = n
    # El total debe cuadrar con la suma de las celdas mostradas (misma ventana de meses).
    def _tot(d: dict[str, int]) -> int:
        return sum(d.get(m, 0) for m in meses)
    top = sorted(por_unidad, key=lambda u: -_tot(por_unidad[u]))[:12]
    return {
        "meses": meses,
        "filas": [
            {"unidad": u, "total": _tot(por_unidad[u]),
             "celdas": [por_unidad[u].get(m, 0) for m in meses]}
            for u in top
        ],
    }


@app.get("/api/stats/ranking-operador-unidad")
def stat_ranking(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(
        select(Operador.nombre, Unidad.clave, Unidad.tipo,
               func.round(func.avg(Viaje.rto_real).cast(Numeric), 3), func.count(Viaje.id))
        .join(Viaje, Viaje.operador_id == Operador.id)
        .join(Unidad, Viaje.unidad_id == Unidad.id)
        .where(Viaje.rto_real.isnot(None), VIGENTE)
        .group_by(Operador.nombre, Unidad.clave, Unidad.tipo)
        .having(func.count(Viaje.id) >= 5)
        .order_by(func.avg(Viaje.rto_real).desc()).limit(10)
    ).all()
    return [
        {"operador": op, "unidad": c, "tipo": t.value,
         "rto_real": float(r) if r is not None else None, "viajes": n}
        for (op, c, t, r, n) in filas
    ]


@app.get("/api/thermo")
def thermo(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(select(AuditoriaThermo).order_by(AuditoriaThermo.id.desc()).limit(200)).scalars().all()
    return [
        {"unidad": t.unidad.clave if t.unidad else None,
         "operador": t.operador.nombre if t.operador else None,
         "remolque": t.remolque, "mes": t.mes,
         "hrs_inic": t.hrs_inic, "hrs_fin": t.hrs_fin, "horas_trab": t.horas_trab,
         "litros_thermo": t.litros_thermo, "rto_thermo": t.rto_thermo}
        for t in filas
    ]


def _contexto_flota(db: Session, desde: date | None = None, hasta: date | None = None) -> dict:
    """Resumen agregado de la flota que alimenta el análisis (admin) y las consultas (gerente).
    Si se pasa desde/hasta (date), acota TODO al periodo (viajes por Viaje.fecha, anomalías por
    su fecha de detección). Sin fechas = todo el histórico (comportamiento previo)."""
    vf = []
    if desde:
        vf.append(Viaje.fecha >= desde)
    if hasta:
        vf.append(Viaje.fecha <= hasta)

    def _vf(q):
        return q.where(*vf) if vf else q

    total = db.scalar(_vf(select(func.count()).select_from(Viaje))) or 0
    rango = db.execute(_vf(select(func.min(Viaje.fecha), func.max(Viaje.fecha)))).one()
    rto_global = db.scalar(_vf(select(func.round(func.avg(Viaje.rto_real).cast(Numeric), 3))))
    # En rangos cortos exigir 10 viajes por unidad deja la lista vacía; se baja a 3 con periodo.
    min_viajes = 3 if (desde or hasta) else 10
    peores = db.execute(
        select(Unidad.clave, func.round(func.avg(Viaje.rto_real).cast(Numeric), 3), func.count(Viaje.id))
        .join(Viaje, Viaje.unidad_id == Unidad.id).where(Viaje.rto_real.isnot(None), *vf)
        .group_by(Unidad.clave).having(func.count(Viaje.id) >= min_viajes)
        .order_by(func.avg(Viaje.rto_real)).limit(8)
    ).all()
    af = []
    if desde:
        af.append(Anomalia.creado_en >= desde)
    if hasta:
        af.append(Anomalia.creado_en <= hasta)
    anoms_q = select(Anomalia.tipo, func.count())
    if af:
        anoms_q = anoms_q.where(*af)
    anoms = db.execute(anoms_q.group_by(Anomalia.tipo)).all()
    ctx = {
        "total_viajes": total,
        "unidades": db.scalar(select(func.count()).select_from(Unidad)) or 0,
        "operadores": db.scalar(select(func.count()).select_from(Operador)) or 0,
        "anomalias_pendientes": db.scalar(select(func.count()).select_from(Anomalia)
                                          .where(Anomalia.estado == EstadoAnomalia.PENDIENTE)) or 0,
        "rango_fechas": [rango[0].isoformat() if rango[0] else None,
                         rango[1].isoformat() if rango[1] else None],
        "rendimiento_global_km_l": float(rto_global or 0),
        "unidades_menor_rendimiento": [{"unidad": p[0], "km_l": float(p[1]), "viajes": p[2]} for p in peores],
        "anomalias_por_tipo": {a[0]: a[1] for a in anoms},
    }
    if desde or hasta:
        ctx["periodo_pedido"] = {"desde": desde.isoformat() if desde else None,
                                 "hasta": hasta.isoformat() if hasta else None}
    return ctx


@app.post("/api/consulta")
async def consulta_ia(request: Request, user: dict = Depends(require_rol("gerente", "admin")),
                      db: Session = Depends(get_db)) -> dict:
    """El gerente pregunta a la IA sobre los datos de la flota (consultas puntuales, NO
    reportes). Es de solo lectura: no cambia ningún dato de la operación."""
    f = await request.form()
    pregunta = (f.get("pregunta") or "").strip()[:500]
    if not pregunta:
        raise HTTPException(400, "Escribe una pregunta")
    contexto = _contexto_flota(db)
    try:
        respuesta = await run_in_threadpool(ai.consulta, pregunta, contexto)
    except Exception:
        log.exception("Fallo en la consulta IA del gerente")
        raise HTTPException(502, "No se pudo generar la respuesta")
    return {"ok": True, "pregunta": pregunta, "respuesta": respuesta}


@app.post("/api/analisis")
async def analisis(request: Request, user: dict = Depends(require_admin),
                   db: Session = Depends(get_db)) -> dict:
    """Genera un análisis de la flota con Claude y lo guarda. Tiene efecto secundario
    (llamada facturable + inserción), por eso es POST, no GET. Acepta `desde`/`hasta` (ISO
    YYYY-MM-DD) opcionales para acotar el análisis a un periodo."""
    f = await request.form()
    d0 = _export_fecha((f.get("desde") or "").strip())
    d1 = _export_fecha((f.get("hasta") or "").strip())
    if d0 and d1 and d0 > d1:
        raise HTTPException(400, "El rango es inválido: 'desde' es posterior a 'hasta'.")
    resumen = _contexto_flota(db, d0, d1)
    if not resumen["total_viajes"]:
        raise HTTPException(400, "No hay viajes en el periodo elegido para analizar.")
    # A un hilo, por lo mismo: el prompt de aquí es el informe entero, así que la
    # congelación era más larga que la de una foto aunque se pida mucho menos.
    texto = await run_in_threadpool(ai.analizar_flota, resumen)
    # Se guarda automáticamente para conservar el historial/contexto de reportes.
    rep = AnalisisReporte(texto=texto, resumen=resumen)
    db.add(rep)
    db.commit()
    return {"id": rep.id, "analisis": texto, "resumen": resumen,
            "creado_en": rep.creado_en.isoformat() if rep.creado_en else None}


@app.get("/api/analisis/historial")
def analisis_historial(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    filas = db.execute(
        select(AnalisisReporte).order_by(AnalisisReporte.id.desc()).limit(30)
    ).scalars().all()
    return [
        {"id": r.id, "creado_en": r.creado_en.isoformat() if r.creado_en else None,
         "total_viajes": (r.resumen or {}).get("total_viajes"),
         "rango_fechas": (r.resumen or {}).get("rango_fechas")}
        for r in filas
    ]


@app.get("/api/analisis/{rep_id}")
def analisis_por_id(rep_id: int, user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    r = db.get(AnalisisReporte, rep_id)
    if r is None:
        raise HTTPException(404, "Reporte no encontrado")
    return {"id": r.id, "analisis": r.texto, "resumen": r.resumen,
            "creado_en": r.creado_en.isoformat() if r.creado_en else None}


@app.delete("/api/analisis/{rep_id}")
def analisis_borrar(rep_id: int, user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    r = db.get(AnalisisReporte, rep_id)
    if r is None:
        raise HTTPException(404, "Reporte no encontrado")
    db.delete(r)
    db.commit()
    return {"ok": True, "eliminado": True}


@app.get("/api/stats/anomalias-precision")
def stat_anomalias_precision(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Precisión de cada regla: confirmadas (reales) vs rechazadas (falsas alarmas)."""
    filas = db.execute(
        select(Anomalia.tipo, Anomalia.estado, func.count()).group_by(Anomalia.tipo, Anomalia.estado)
    ).all()
    clave = {EstadoAnomalia.PENDIENTE: "pendientes", EstadoAnomalia.CONFIRMADA: "confirmadas",
             EstadoAnomalia.RECHAZADA: "rechazadas"}
    por_tipo: dict[str, dict] = {}
    for tipo, estado, n in filas:
        d = por_tipo.setdefault(tipo, {"pendientes": 0, "confirmadas": 0, "rechazadas": 0})
        d[clave[estado]] = n
    tipos = []
    tot = {"pendientes": 0, "confirmadas": 0, "rechazadas": 0}
    for tipo, d in por_tipo.items():
        resueltas = d["confirmadas"] + d["rechazadas"]
        tipos.append({"tipo": tipo, "categoria": CATEGORIA_ANOMALIA.get(tipo, "base"), **d,
                      "resueltas": resueltas, "total": d["pendientes"] + resueltas,
                      "precision": round(d["confirmadas"] / resueltas, 3) if resueltas else None})
        for k in tot:
            tot[k] += d[k]
    tipos.sort(key=lambda t: -t["total"])
    res_tot = tot["confirmadas"] + tot["rechazadas"]
    return {
        "tipos": tipos,
        "totales": {**tot, "resueltas": res_tot, "total": tot["pendientes"] + res_tot,
                    "precision": round(tot["confirmadas"] / res_tot, 3) if res_tot else None},
    }


@app.post("/api/stats/anomalias-diagnostico")
def stat_anomalias_diagnostico(user: dict = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Claude lee la precisión por regla y recomienda qué reglas afinar (llamada facturable -> POST)."""
    prec = stat_anomalias_precision(user, db)
    if not prec["tipos"]:
        raise HTTPException(400, "Aún no hay anomalías para diagnosticar.")
    texto = ai.analizar_anomalias(prec)
    return {"analisis": texto, "resumen": prec}


# ─────────────────────────────────────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/config")
def config_get(user: dict = Depends(require_user)) -> dict:
    return {
        "tolerancia_pct": settings.tolerancia_pct,
        "umbral_sigma": settings.umbral_sigma,
        "bot_confirmar": settings.bot_confirmar,
        "modelo_ia": settings.anthropic_model,
        "grupo_jid": settings.group_jid,
        "usuario": user.get("username"),
    }


@app.post("/api/cambiar-password")
def cambiar_password(user: dict = Depends(require_user)) -> dict:
    """DESHABILITADO por política (#7): ningún rol cambia su contraseña desde la app. La
    gestiona el administrador (alta/reseteo por script)."""
    raise HTTPException(403, "El cambio de contraseña lo gestiona el administrador.")


@app.get("/api/perfil")
def perfil_ver(user: dict = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    u = db.get(Usuario, user["id"])
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    return {"username": u.username, "nombre": u.nombre, "rol": u.rol,
            "telefono": u.telefono, "prefs": u.prefs or {}, "tiene_foto": bool(u.foto)}


@app.post("/api/perfil")
async def perfil_actualizar(request: Request, user: dict = Depends(require_user),
                            db: Session = Depends(get_db)) -> dict:
    """Actualiza la información personal y las preferencias del PROPIO usuario. NO permite
    cambiar el nombre/apellidos ni el rol (#7: esos los fija el administrador)."""
    import json
    import re
    u = db.get(Usuario, user["id"])
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    f = await request.form()
    if "telefono" in f:
        tel = (f.get("telefono") or "").strip()[:30]
        u.telefono = tel or None
    if "prefs" in f:
        try:
            raw = json.loads(f.get("prefs") or "{}")
        except (ValueError, TypeError):
            raise HTTPException(400, "Preferencias inválidas")
        if not isinstance(raw, dict):
            raise HTTPException(400, "Preferencias inválidas")
        # Lista blanca: solo personalización visual (color de acento y tema).
        prefs = dict(u.prefs or {})
        if "accent" in raw:
            accent = str(raw.get("accent") or "").strip()
            if accent:
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
                    raise HTTPException(400, "Color inválido")
                prefs["accent"] = accent.upper()
            else:
                prefs.pop("accent", None)
        if "tema" in raw:
            tema = str(raw.get("tema") or "").strip()
            if tema in ("light", "dark", "system"):
                prefs["tema"] = tema
        u.prefs = prefs or None
    db.commit()
    return {"ok": True, "telefono": u.telefono, "prefs": u.prefs or {}}


@app.post("/api/perfil/foto")
async def perfil_foto_subir(file: UploadFile = File(...), user: dict = Depends(require_user),
                            db: Session = Depends(get_db)) -> dict:
    """El usuario sube su propia foto de perfil (se comprime en el cliente antes de subir)."""
    u = db.get(Usuario, user["id"])
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    u.foto, u.foto_mime = await _leer_foto(file)
    _espejar_foto(db, desde_usuario=u)             # refleja la foto al padrón (ficha, detalles)
    db.commit()
    return {"ok": True}


@app.get("/api/perfil/foto")
def perfil_foto_ver(user: dict = Depends(require_user), db: Session = Depends(get_db)):
    u = db.get(Usuario, user["id"])
    if u is None or not u.foto:
        raise HTTPException(404, "Sin foto")
    try:
        data = base64.b64decode(u.foto)
    except (ValueError, TypeError):
        raise HTTPException(404, "Foto ilegible")
    return Response(content=data, media_type=u.foto_mime or "image/jpeg",
                    headers={"Cache-Control": "no-store"})


# ─────────────────────────────────────────────────────────────────────────────
# Webhook de Evolution (no protegido — lo llama Evolution API)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/eventos/fallidos")
def eventos_fallidos(
    estado: str = Query("fallido", pattern="^(fallido|pendiente|omitido|todos)$"),
    limite: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Eventos con problema (rastro recuperable): se pueden reprocesar.

    Incluye también los 'pendiente' atascados: un evento que lleva intentos y sigue sin
    procesarse está igual de perdido que uno fallido, y antes no se veía en ningún lado.
    """
    q = select(EventoWhatsapp)
    if estado == "todos":
        q = q.where(EventoWhatsapp.estado_proceso.in_(("fallido", "pendiente", "omitido")))
    else:
        q = q.where(EventoWhatsapp.estado_proceso == estado)
    filas = db.execute(q.order_by(EventoWhatsapp.id.desc()).limit(limite)).scalars().all()

    conteos = dict(db.execute(
        select(EventoWhatsapp.estado_proceso, func.count(EventoWhatsapp.id))
        .group_by(EventoWhatsapp.estado_proceso)
    ).all())
    return {
        "conteos": {k: int(v) for k, v in conteos.items()},
        "eventos": [
            {"id": e.id, "estado": e.estado_proceso, "tipo": e.tipo_mensaje,
             "texto": (e.texto or "")[:200],
             "participante": (e.participante or "").split("@")[0], "intentos": e.intentos,
             "recibido": e.recibido_en.isoformat() if e.recibido_en else None,
             "tiene_media": bool(e.media_path), "error": e.error}
            for e in filas
        ],
    }


@app.post("/api/eventos/{evento_id}/reprocesar")
def evento_reprocesar(evento_id: int, user: dict = Depends(require_admin),
                      db: Session = Depends(get_db)) -> dict:
    """Reencola un evento fallido para volver a intentarlo (reinicia contador).

    Solo se avisa al grupo si ese reporte NUNCA llegó a confirmarse. Reprocesar algo que
    ya estaba procesado (p. ej. para re-correr una regla corregida) no debe mandarle al
    cliente un segundo acuse de un reporte de hace horas.
    """
    e = db.get(EventoWhatsapp, evento_id)
    if e is None:
        raise HTTPException(404, "Evento no encontrado")
    ya_confirmado = e.estado_proceso == "procesado"

    # Si el evento traía foto y no se pudo bajar, se reintenta la descarga AHORA: sin esto
    # el reproceso volvía a correr sobre un evento sin foto y fallaba igual.
    media_recuperada = False
    if not e.media_path:
        from .webhook import reintentar_media
        try:
            nueva = reintentar_media(e)
            if nueva:
                e.media_path = nueva
                media_recuperada = True
        except Exception:
            log.exception("Fallo al reintentar la media del evento %s", evento_id)

    e.estado_proceso = "pendiente"
    e.intentos = 0
    e.error = None
    db.commit()
    from .captura import encolar
    encolar(evento_id, acusar=not ya_confirmado)
    return {"ok": True, "acusa_al_grupo": not ya_confirmado,
            "media_recuperada": media_recuperada}


def _verificar_webhook(request: Request) -> None:
    """Si WEBHOOK_TOKEN está configurado, exige el header X-Webhook-Token. Evita que
    alguien inyecte viajes falsos (o queme la factura de IA) si el puerto se expone.
    Vacío = sin verificación (uso local)."""
    import hmac
    if settings.webhook_token and not hmac.compare_digest(
            (request.headers.get("x-webhook-token") or "").encode(),
            settings.webhook_token.encode()):
        raise HTTPException(401, "webhook no autorizado")


@app.post("/webhook")
async def webhook(request: Request) -> dict:
    _verificar_webhook(request)
    payload = await request.json()
    await run_in_threadpool(_despachar, payload)
    return {"ok": True}


@app.post("/webhook/{evento}")
async def webhook_por_evento(evento: str, request: Request) -> dict:
    _verificar_webhook(request)
    payload = await request.json()
    await run_in_threadpool(_despachar, payload)
    return {"ok": True}


def _despachar(payload: dict) -> None:
    # Con el bot retirado (cierre de Fase A) el webhook queda inerte: acusa recibo para no
    # dejar a Evolution reintentando, pero NO procesa nada. La captura es por la app web.
    if not settings.bot_whatsapp_activo:
        return
    evento = (payload.get("event") or "").lower()
    if evento == "messages.upsert":
        try:
            procesar_messages_upsert(payload)
        except Exception:
            log.exception("Error procesando messages.upsert")
    elif evento == "messages.delete":
        # Evolution también emite el borrado como evento propio (además del protocolMessage
        # REVOKE dentro de messages.upsert). Se atienden los dos: según la versión y el
        # dispositivo llega por una vía o por la otra, y perder un borrado significa dejar
        # contando un reporte que su autor ya retiró.
        from . import retractacion

        d = payload.get("data") or {}
        mid = (d.get("key") or {}).get("id") or d.get("id")
        if mid:
            try:
                retractacion.por_borrado(mid, (d.get("key") or {}).get("participant"))
            except Exception:
                log.exception("Error procesando el borrado del mensaje %s", mid)
    elif evento == "connection.update":
        log.info("Estado de conexión de WhatsApp: %s", (payload.get("data") or {}).get("state"))
    elif evento == "qrcode.updated":
        log.info("QR actualizado — escanéalo desde el manager: http://localhost:8080/manager")
    else:
        log.debug("Evento no manejado: %s", evento)
