"""Frenos del login: contra la fuerza bruta y contra el agotamiento de CPU.

SON DOS PROBLEMAS DISTINTOS Y LLEVAN DOS FRENOS DISTINTOS.

  · Fuerza bruta — alguien prueba contraseñas. Se corta con una VENTANA por origen: pasados
    N fallos en M minutos, ese origen deja de ser atendido durante un rato.

  · Agotamiento de CPU — cada intento cuesta ~250 ms de pbkdf2 (200.000 iteraciones), así
    que una petición de 300 bytes compra un cuarto de segundo de procesador. Medido: con seis
    peticiones por segundo el panel entero pasa de 12 ms a más de 3 s. Eso NO lo arregla la
    ventana, porque un atacante reparte los intentos entre muchos orígenes. Se corta con un
    TOPE DE SIMULTANEIDAD: si ya hay N verificaciones en curso, la siguiente se rechaza sin
    gastar un ciclo.

POR QUÉ LA VENTANA VA POR ORIGEN Y NO POR USUARIO. Bloquear la cuenta tras N fallos suena
mejor y es peor: le entrega a cualquiera la forma de dejar fuera al administrador tecleando
mal ocho veces. Limitar el ORIGEN molesta a quien ataca y no a quien trabaja. Los fallos sí
se cuentan por usuario, pero sólo para dejarlos en la bitácora: contar no es bloquear.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from starlette.requests import Request

from .config import settings

log = logging.getLogger("combustible.limites")

# ── la ventana por origen ──────────────────────────────────────────────────────
_ventanas: dict[str, deque[float]] = {}
_castigos: dict[str, float] = {}
_lock = threading.Lock()

# Cota de memoria: sin esto, un atacante con IPs distintas hace crecer el diccionario sin
# final. Al llegar al tope se tira lo más viejo, que es justo lo que ya no frena a nadie.
_MAX_ORIGENES = 4096


def ip_cliente(request: Request) -> str:
    """De dónde viene la petición, de verdad.

    `X-Forwarded-For` lo pone cualquiera, así que sólo se hace caso cuando quien se conecta
    es el propio equipo: por el túnel, el agente vive en loopback y es la única forma de
    distinguir a los visitantes. Si la conexión llega de fuera, manda la dirección real y la
    cabecera se ignora — de lo contrario un atacante se inventaría un origen nuevo por
    intento y la ventana no frenaría nada.
    """
    par = request.client.host if request.client else ""
    if par in ("127.0.0.1", "::1", "localhost"):
        reenviado = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if reenviado:
            return reenviado[:45]
    return par or "desconocido"


def _podar(cola: deque[float], ahora: float, ventana: float) -> None:
    while cola and ahora - cola[0] > ventana:
        cola.popleft()


def espera_login(origen: str) -> int:
    """Segundos que este origen debe esperar. 0 si puede intentarlo."""
    ahora = time.monotonic()
    with _lock:
        hasta = _castigos.get(origen)
        if hasta is not None:
            if ahora < hasta:
                return int(hasta - ahora) + 1
            del _castigos[origen]
    return 0


def anotar_fallo(origen: str) -> int:
    """Cuenta un intento fallido. Devuelve los segundos de castigo si se acaba de pasar."""
    ahora = time.monotonic()
    with _lock:
        if len(_ventanas) >= _MAX_ORIGENES:
            for viejo in list(_ventanas)[:_MAX_ORIGENES // 4]:
                _ventanas.pop(viejo, None)
        cola = _ventanas.setdefault(origen, deque())
        _podar(cola, ahora, settings.login_ventana_seg)
        cola.append(ahora)
        if len(cola) >= settings.login_max_fallos:
            _castigos[origen] = ahora + settings.login_castigo_seg
            cola.clear()
            log.warning("LOGIN: origen %s bloqueado %s s tras %s fallos",
                        origen, settings.login_castigo_seg, settings.login_max_fallos)
            return settings.login_castigo_seg
    return 0


def limpiar_origen(origen: str) -> None:
    """Un acierto borra la cuenta: quien entra bien no arrastra sus errores de tecleo."""
    with _lock:
        _ventanas.pop(origen, None)
        _castigos.pop(origen, None)


# ── el tope de simultaneidad del hashing ───────────────────────────────────────
class _Aforo:
    """Cuántas verificaciones de contraseña caben a la vez. No espera: rechaza.

    Esperar sería peor que rechazar. Una cola de peticiones aguantando su turno retiene hilos
    del servidor, y el ataque conseguiría lo mismo por otra puerta: el panel dejaría de
    responder igual. Rechazar de inmediato deja el procesador libre para quien sí trabaja.
    """

    def __init__(self, tope: int) -> None:
        self.tope = max(1, tope)
        self.dentro = 0
        self.cerrojo = threading.Lock()

    def entrar(self) -> bool:
        with self.cerrojo:
            if self.dentro >= self.tope:
                return False
            self.dentro += 1
            return True

    def salir(self) -> None:
        with self.cerrojo:
            self.dentro = max(0, self.dentro - 1)


_aforo = _Aforo(settings.login_simultaneos)


class aforo_login:
    """`with aforo_login() as hay_sitio:` — False si el servidor ya está ocupado hasheando."""

    def __enter__(self) -> bool:
        self.entre = _aforo.entrar()
        return self.entre

    def __exit__(self, *exc) -> None:
        if self.entre:
            _aforo.salir()
