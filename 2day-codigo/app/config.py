from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    database_url: str
    evolution_url: str = "http://localhost:8080"
    evolution_apikey: str
    evolution_instance: str = "combustible"
    group_jid: str = ""
    media_dir: Path = BASE_DIR / ".." / "data" / "media"

    # Contacto del coordinador para el botón "Contactar" de la app del operador.
    # Número de WhatsApp en formato internacional, SOLO dígitos (sin +, espacios ni
    # guiones), p.ej. 5215512345678. Vacío = el botón no se muestra. Se edita en el .env.
    coordinador_nombre: str = "Coordinación"
    coordinador_whatsapp: str = ""

    # IA (Anthropic)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # Despacho: techo de litros autorizables sobre lo que el viaje debería gastar.
    # NO es una tolerancia de medición (esa es `tolerancia_pct`): limita una cantidad
    # que la empresa entrega, así que se cumple por decreto y no depende del ruido.
    tope_despacho_pct: float = 0.02
    # Un escaneo que cubre menos que esto no es una medición de carretera: es arranque
    # en frío y maniobras. Hay extracciones de 124, 143 y 172 km en la flota.
    piso_km_escaneo: float = 500.0

    # Cuántos días hacia atrás se juntan las lecturas del escáner para formar UNA
    # referencia. Los períodos son consecutivos y no se pisan, así que juntarlos es medir
    # sobre una ventana más larga, no promediar. Hoy los 43 escaneos de la base caben en
    # 17 días y esto no recorta nada; existe para que dentro de un año la referencia no
    # arrastre lecturas de un motor que ya no es el mismo.
    dias_serie_escaneo: int = 90

    # Tope de kilómetros que se juntan hacia atrás para formar UNA referencia. No es un
    # punto óptimo: es un freno. Se midió el error de cada tope contra una vara
    # independiente —el km/l que dicen TODOS los viajes de la unidad según la computadora
    # del motor, quitando los viajes gemelos de los escaneos para que no sea circular— y
    # el error baja de 12.4% (sólo la última lectura) a 8.8%, tocando fondo a partir de
    # unos 5,000 km. De ahí en adelante da igual, así que esto no recorta nada hoy: existe
    # para que dentro de dos años una unidad muy escaneada no arrastre medio historial.
    #
    # Un tope de 3,000 km parecía prudente y era peor (9.6%): en 4 de los 10 pares con
    # varias lecturas devolvía EXACTAMENTE la última, que es lo que se venía a corregir.
    km_serie_escaneo: float = 8000.0
    # Días sin actividad tras los cuales un viaje se da por abandonado y se cierra solo.
    # NO es «cuánto lleva abierto»: un viaje legítimo de esta flota duró 14 días. Es
    # cuánto lleva sin que pase nada, que es otra cosa. Medidos 18 huecos de inactividad
    # dentro de viajes vivos: mediana 0.0 días y máximo 7.5. Esto es el doble del mayor
    # hueco real, y la evidencia es corta (tres semanas de operación): súbelo si la flota
    # hace rutas más largas. Un viaje con solicitudes vivas NO se cierra nunca, pase el
    # tiempo que pase.
    viaje_inactivo_dias: int = 14

    # Validación (Etapa 2)
    # DOS UMBRALES DISTINTOS, y conviene no confundirlos nunca más:
    #   · `tope_despacho_pct` (2%) limita los litros que se AUTORIZAN. Acota una cantidad
    #     que la empresa entrega, así que se cumple por decreto.
    #   · `tolerancia_pct` (esto) compara lo cargado contra lo que el motor dice haber
    #     quemado. Son dos instrumentos, y ahí un 2% choca con el ruido de la medición:
    #     marcaba el 88.9% de los viajes capturados, con lo que `desviacion_alta` dejaba de
    #     señalar una excepción para señalarlo casi todo.
    # El 2% estuvo aquí del 16 al 20-sep-2026 mientras se aclaraba a cuál de los dos se
    # refería el dueño. Vuelve al 5% ahora que el 2% tiene su sitio propio.
    tolerancia_pct: float = 0.05
    umbral_sigma: float = 3.0

    # Umbrales de anomalías nuevas
    rto_min_tracto: float = 1.2      # banda física de rendimiento (km/l) para tractos
    rto_max_tracto: float = 5.5
    rto_min_camion: float = 1.5      # banda física para camiones (C)
    rto_max_camion: float = 6.0
    carga_fantasma_lts_min: float = 50.0   # litros mínimos para sospechar carga fantasma
    carga_fantasma_km_max: float = 20.0    # km máximos para sospechar carga fantasma
    sifoneo_ventana: int = 8               # nº de viajes de la ventana para sifoneo
    sifoneo_umbral: float = 0.10           # exceso acumulado (Σdif/Σscanner) para marcar sifoneo
    vel_max_tope: float = 110.0            # tope absoluto de velocidad (km/h)
    ralenti_piso: float = 25.0             # piso absoluto de ralentí para marcar
    # La asignación operador↔unidad del catálogo es nominal: en esta flota la rotación es
    # alta y casi ningún viaje lo hace el titular, así que avisarlo sería ruido constante.
    avisar_operador_no_asignado: bool = False

    # ── Costeo del combustible ──────────────────────────────────────────────
    # UNA sola fuente de precios para todo el sistema. La bitácora usaba `precio_litro` y
    # el reporte ejecutivo una tabla propia: los mismos 805,141 L valían $22.1M en una
    # pantalla y $23.3M en otra, y cambiar el .env no movía el reporte.
    precio_litro: float = 27.1551          # $/L vigente (para el año en curso y los futuros)
    # Precio por año, para valuar cada período con el que de verdad costó. Formato
    # "2025:26.50,2026:29.00" en el .env; vacío = usar siempre `precio_litro`.
    #
    # DE DÓNDE SALE EL 27.1551 (3-sep-2026): no es una estimación. Es el precio ponderado
    # real de julio de 2026 —$3,218,987.99 entre 118,541.021 L— calculado sobre las 639
    # cargas que los dos proveedores facturaron y que están cargadas verbatim en
    # `cargas_proveedor`. Se comprueba con:
    #     SELECT round(sum(importe)::numeric / sum(litros)::numeric, 4)
    #     FROM cargas_proveedor WHERE vigente;
    # Es un precio BRUTO, con IVA e IEPS incluidos, porque `importe` es el total facturado;
    # para una flota el IVA es acreditable, así que el costo económico neto es menor. El
    # desglose solo existe para Xyga (313 de las 639 filas), y por eso no se usa aquí.
    #
    # Antes decía 2026:29.00, que venía de tres facturas de PRUEBA que ya se borraron. Cada
    # informe salía un 6.4% inflado: unos $218,700 de más solo en julio.
    precios_por_anio: str = "2025:26.50,2026:27.1551"

    # ── Los dos umbrales de la alerta de LECTURAS DE MOTOR ───────────────────
    # Medidos sobre los 43 escaneos de la base, no elegidos por gusto.
    #
    # RALENTÍ. El escáner da dos cosas distintas y sólo una se puede accionar: el
    # PORCENTAJE DE TIEMPO parado (mediana de la flota 37.9%) y los LITROS quemados
    # parado (3,316 L de 42,227 = 7.9% del diésel). No son lo mismo, porque parado se
    # queman uno o dos litros por hora y rodando muchos más: T147 pasa el 39% del tiempo
    # parado y eso son 62 L, mientras que T203 pasa el 54% y quema el 18% de su diésel.
    # Un aviso sobre el tiempo marca al que espera en el andén; uno sobre los litros marca
    # al que quema combustible. Con 0.15 se marcan 3 de los 35 períodos medibles —los que
    # queman 448, 230 y 143 L parados—, que es una lista con la que se puede hacer algo.
    ralenti_frac_alerta: float = 0.15
    #
    # RENDIMIENTO. Cuánto tiene que caer un período contra lo que esa MISMA unidad venía
    # haciendo antes. Con 0.10 se marcan 3 de los 16 períodos comparables (19%).
    # Comprobado que las caídas NO son cambios de configuración: de las 23 comparaciones
    # sólo 2 cambian de FULL a SENCILLO o al revés, y ninguna de las marcadas. Se puede
    # ajustar por unidad con `Unidad.pct_tolerancia`, igual que en la validación de viajes.
    caida_rendimiento_alerta: float = 0.10
    reserva_litros: float = 200.0          # reserva que se busca mantener en el tanque

    # Dashboard / auth
    # SIN valor por defecto, por el mismo motivo que `admin_password` de dos líneas abajo y
    # con una consecuencia peor: con esta llave se FIRMAN las cookies de sesión, así que
    # quien la conozca se fabrica una de admin y entra sin contraseña. Antes valía
    # "cambia-esto-en-produccion", y pydantic no se quejaba: un despliegue que no cargara el
    # .env —otro directorio de trabajo, un contenedor mal montado— caía en silencio a una
    # cadena pública. Ahora el arranque falla, que es lo correcto: un secreto conocido nunca
    # debe ser el respaldo silencioso de nada.
    session_secret: str
    admin_user: str = "admin"
    # Frenos del login (ver app/limites.py). La ventana va por ORIGEN, no por usuario:
    # bloquear la cuenta tras N fallos le regala a cualquiera la forma de dejar fuera al
    # administrador tecleando mal ocho veces.
    login_max_fallos: int = 8            # fallos del mismo origen antes del castigo
    login_ventana_seg: int = 300         # en cuánto tiempo se cuentan esos fallos
    login_castigo_seg: int = 300         # cuánto dura el castigo
    # Verificaciones de contraseña a la vez. Cada una cuesta ~250 ms de CPU; con este tope
    # el gasto máximo queda acotado pase lo que pase, venga de donde venga.
    login_simultaneos: int = 4
    # La cookie de sesión sale marcada `Secure`: el navegador deja de mandarla por HTTP. Los
    # navegadores tratan localhost como contexto seguro, así que esto NO rompe las pruebas
    # locales. Se deja conmutable por si alguna vez hace falta servir por HTTP plano.
    cookie_segura: bool = True
    # La documentación automática (/docs, /redoc, /openapi.json) regala el mapa completo de
    # la API a cualquiera que tenga la URL. Apagada por defecto; se enciende para desarrollar.
    docs_abiertas: bool = False
    # SIN valor por defecto a propósito: una contraseña escrita en el código fuente es
    # pública (va al repositorio, a los respaldos y a cualquier copia del proyecto). Si
    # está vacía, el arranque genera una aleatoria y la escribe UNA vez en el log.
    admin_password: str = ""

    # Bot de WhatsApp: enviar acuse por cada viaje capturado
    bot_confirmar: bool = True

    # RETIRO DEL BOT (cierre de Fase A de la migración a plataforma web). Con esto en False
    # el asistente de WhatsApp queda INERTE: no procesa mensajes entrantes ni rehidrata la
    # cola al arrancar. La captura pasa por la aplicación web. Es un interruptor reversible
    # a propósito —no se borró el código— para poder reactivarlo durante la transición.
    bot_whatsapp_activo: bool = False

    # Zona horaria de la flota: la FECHA del viaje se calcula con la hora local de la
    # flota a partir de cuándo se recibió el mensaje (no del reloj de procesamiento, que
    # puede ir en otro día por reintentos o por correr en un VPS en UTC).
    zona_horaria: str = "America/Mexico_City"

    # ── Mapas ────────────────────────────────────────────────────────────────
    # Clave de Google Maps. Vacía = el sistema sigue con OpenStreetMap/OSRM (lo de
    # hoy), así que la app NUNCA queda sin mapa por falta de clave.
    # Se usan DOS claves distintas a propósito:
    #  · google_maps_browser_key -> la carga el navegador para DIBUJAR el mapa. Queda
    #    a la vista (es inevitable en Maps JS), así que debe restringirse por dominio
    #    en Google Cloud y limitarse SOLO a "Maps JavaScript API".
    #  · google_maps_api_key -> la usa el SERVIDOR para rutas y direcciones. Nunca
    #    sale de aquí; es la que puede generar gasto, por eso se mantiene aparte.
    # Si solo se define una, se usa esa para ambas cosas.
    google_maps_api_key: str = ""
    google_maps_browser_key: str = ""
    # Tope diario de peticiones de ruta, como red de seguridad contra un cobro
    # sorpresa. 0 = sin tope.
    google_maps_max_rutas_dia: int = 500

    # Token opcional del webhook: si se define, /webhook exige el header X-Webhook-Token.
    # Vacío = sin verificación (uso local). Al activarlo, configurar el mismo header en el
    # webhook de Evolution. Protege contra inyección de viajes falsos si el puerto se expone.
    webhook_token: str = ""


settings = Settings()
# Las rutas relativas del .env se anclan a backend/, no al cwd del proceso
if not settings.media_dir.is_absolute():
    settings.media_dir = BASE_DIR / settings.media_dir
settings.media_dir = settings.media_dir.resolve()
settings.media_dir.mkdir(parents=True, exist_ok=True)


def ruta_media(guardado: str | None) -> Path | None:
    """La ruta en disco de una foto guardada en la BD (`foto_path`, `media_path`).

    Las filas nuevas guardan SÓLO el nombre del archivo, que se resuelve contra `media_dir`.
    Las viejas guardan la ruta absoluta de la máquina donde se subieron —p.ej.
    `C:\\Users\\...\\data\\media\\x.jpg`—, que no existe al mover la base a otro servidor:
    si esa ruta no está, se busca el mismo nombre en `media_dir`. Devuelve None si no hay
    archivo; nunca sale de `media_dir` salvo para una ruta absoluta vieja que sí existe.
    """
    if not guardado:
        return None
    p = Path(guardado)
    if p.is_absolute() and p.is_file():
        return p
    # El nombre se saca a mano para que una ruta de Windows también se entienda en Linux.
    nombre = guardado.replace("\\", "/").rsplit("/", 1)[-1]
    if not nombre or nombre in (".", ".."):
        return None
    candidato = settings.media_dir / nombre
    return candidato if candidato.is_file() else None


def _tz():
    from datetime import timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(settings.zona_horaria)
    except (ZoneInfoNotFoundError, KeyError):
        return timezone.utc


def fecha_flota(dt=None):
    """Fecha (date) en la zona horaria de la flota. Si se pasa un datetime (p.ej. cuándo
    se recibió el mensaje), lo convierte; si no, usa el instante actual. Un datetime naive
    se asume en UTC (así se guardan los recibido_en en la BD)."""
    from datetime import datetime, timezone
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz()).date()


def precio_del_litro(anio: int | None = None) -> float:
    """Precio del diésel para ese año. ÚNICA fuente para todo el sistema.

    Si el año no tiene precio propio se usa `precio_litro` (el vigente). Antes cada módulo
    traía su tabla y dos entregables que el cliente recibe juntos valuaban el mismo
    combustible con precios distintos.
    """
    if anio is not None:
        for parte in (settings.precios_por_anio or "").split(","):
            parte = parte.strip()
            if not parte or ":" not in parte:
                continue
            a, _, p = parte.partition(":")
            try:
                if int(a) == anio:
                    return float(p)
            except ValueError:
                continue
    return settings.precio_litro
