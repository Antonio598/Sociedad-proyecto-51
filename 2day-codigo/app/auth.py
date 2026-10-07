"""Autenticación del dashboard: hash de contraseñas (stdlib) y usuario admin inicial."""

import hashlib
import hmac
import logging
import os
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Usuario

log = logging.getLogger("combustible.auth")

_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── Fortaleza de contraseñas (cuentas de PERSONAL, de alto privilegio) ──────────
# Alfabetos sin caracteres ambiguos (l/I/1, o/O/0) para que una clave se pueda dictar sin
# confundir. Los símbolos se limitan a un set "teclado-amable" que existe en teclados de
# celular y no rompe URLs/logs.
_MAY = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_MIN = "abcdefghijkmnpqrstuvwxyz"
_NUM = "23456789"
_SIM = "!@#$%&*+=?"
_ALFABETO_FUERTE = _MAY + _MIN + _NUM + _SIM


def generar_password_fuerte(n: int = 14) -> str:
    """Contraseña ALEATORIA fuerte que GARANTIZA mayúscula, minúscula, dígito y símbolo.

    Para cuentas de personal (admin/coordinador/…): son de alto privilegio, se muestran una
    vez y se copian, así que se prioriza fortaleza sobre lo dictable. n≥8 siempre.
    """
    n = max(8, n)
    obligatorios = [secrets.choice(_MAY), secrets.choice(_MIN),
                    secrets.choice(_NUM), secrets.choice(_SIM)]
    resto = [secrets.choice(_ALFABETO_FUERTE) for _ in range(n - len(obligatorios))]
    chars = obligatorios + resto
    # Barajado con secrets (Fisher-Yates) para no fijar la posición de los obligatorios.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def validar_password_fuerte(pwd: str) -> str | None:
    """Valida una contraseña ESCRITA a mano para cuentas de personal. Devuelve un mensaje de
    error (str) si no cumple, o None si es válida. Regla: ≥8, con mayúscula, minúscula, dígito
    y símbolo (los mismos criterios que garantiza el generador)."""
    pwd = pwd or ""
    if len(pwd) < 8:
        return "La contraseña debe tener al menos 8 caracteres"
    if not any(c.isupper() for c in pwd):
        return "Debe incluir al menos una mayúscula"
    if not any(c.islower() for c in pwd):
        return "Debe incluir al menos una minúscula"
    if not any(c.isdigit() for c in pwd):
        return "Debe incluir al menos un número"
    if not any(not c.isalnum() for c in pwd):
        return "Debe incluir al menos un símbolo (p.ej. ! @ # $ % & * + = ?)"
    return None


# Hash señuelo contra el que se verifica cuando el usuario NO existe. Se calcula una vez al
# importar, sobre una contraseña aleatoria que nadie conoce ni necesita conocer: su único
# trabajo es costar exactamente lo mismo que un hash de verdad.
_SEÑUELO = hash_password(secrets.token_hex(16))


def autenticar(session: Session, username: str, password: str) -> Usuario | None:
    """Comprueba usuario y contraseña. Tarda lo mismo exista la cuenta o no.

    POR QUÉ EL SEÑUELO. Antes, si el usuario no existía, `verify_password` ni se llamaba y la
    respuesta salía en ~14 ms; si existía, pagaba las 200.000 iteraciones y tardaba ~300 ms.
    Esa diferencia es un oráculo: cronometrando el login se sabe qué cuentas existen sin
    acertar ni una contraseña. Con los nombres de operador siendo `op<numero>`, eso enumera la
    flota entera. Ahora el caso "no existe" verifica contra un hash de mentira, así que cuesta
    lo mismo y el reloj no dice nada.

    Esto NO sustituye a un límite de intentos: cierra la fuga de información, no el abuso.
    """
    # SIN distinguir mayúsculas. Hasta que el usuario pasó a ser el número de empleado
    # todos eran dígitos y la distinción no existía; con letras, quien teclea
    # «cfruit056» desde un teléfono que capitaliza recibiría «usuario o contraseña
    # incorrectos» y acabaría bloqueado por el limitador de intentos.
    filas = session.execute(
        select(Usuario).where(func.lower(Usuario.username) == (username or "").lower(),
                              Usuario.activo.is_(True))).scalars().all()
    # Si alguna vez hubiera dos cuentas que sólo se distinguen por mayúsculas, NO se
    # elige una: adivinar cuál es la buena sería peor que no dejar entrar.
    u = filas[0] if len(filas) == 1 else None
    if u is None:
        verify_password(password, _SEÑUELO)     # se paga el mismo coste y se descarta
        return None
    return u if verify_password(password, u.password_hash) else None


# Contraseñas que alguna vez estuvieron escritas en el código de este proyecto. Cualquiera
# con una copia del repositorio las conoce, así que aunque hoy vivan en el .env siguen
# siendo públicas. Se avisa en cada arranque hasta que se cambien.
_COMPROMETIDAS = {"combustible2026"}


def password_comprometida() -> bool:
    """¿La contraseña del admin es una de las que estuvieron en el código?"""
    return settings.admin_password in _COMPROMETIDAS


def crear_admin_si_falta() -> None:
    """Crea el usuario admin inicial si no hay usuarios en la BD.

    Si no se configuró ADMIN_PASSWORD se genera una aleatoria y se registra UNA vez en el
    log: es preferible a un valor por defecto conocido, que en la práctica nadie cambia.
    """
    if password_comprometida():
        log.error(
            "SEGURIDAD: la contraseña de %s es una que estuvo escrita en el código fuente "
            "y por lo tanto es pública. Cámbiala desde el panel (Perfil) o con "
            "ADMIN_PASSWORD en backend/.env.", settings.admin_user)

    with SessionLocal() as s:
        existe = s.execute(select(Usuario.id).limit(1)).first()
        if existe:
            return
        pwd = settings.admin_password
        generada = False
        if not pwd:
            pwd = secrets.token_urlsafe(16)
            generada = True
        u = Usuario(
            username=settings.admin_user,
            password_hash=hash_password(pwd),
            nombre="Administrador",
            rol="admin",
        )
        s.add(u)
        s.commit()
        if generada:
            log.warning("Usuario admin inicial creado: %s — contraseña generada: %s\n"
                        "Anótala AHORA: no se vuelve a mostrar. Cámbiala al entrar.",
                        settings.admin_user, pwd)
        else:
            log.info("Usuario admin inicial creado: %s (cambia la contraseña)",
                     settings.admin_user)
