"""Bóveda reversible para claves de acceso de OPERADORES (no personal).

El problema: las contraseñas se guardan con pbkdf2 (una vía) — es IMPOSIBLE recuperarlas.
Para las cuentas de PERSONAL (coordinador/combustible/gerente/admin) eso es lo correcto y
se queda así: solo se pueden restablecer. Pero el admin pidió VER en la tabla la clave de
cada OPERADOR (se dicta/imprime y se reparte a choferes; es de baja sensibilidad y de todos
modos se entrega en papel). Para eso hace falta un guardado REVERSIBLE, no un hash.

Diseño (solo stdlib, igual que auth.py — sin dependencias nuevas):
  - Cifrado autenticado "encrypt-then-MAC" con keystream derivado por HMAC-SHA256.
  - La clave maestra se DERIVA de `settings.session_secret` (ya persistente): no hace falta
    un secreto nuevo en el .env, y así la clave del vault NO viaja en la BD (un volcado de la
    BD no basta para descifrar; hace falta también el secreto del servidor).
  - keystream_i = HMAC(k_enc, nonce || i);  ct = pt XOR keystream;  tag = HMAC(k_mac, nonce||ct)
  - Formato en la BD: base64url(nonce[16] || ct || tag[32]).

NO es para datos de alta sensibilidad (para eso iría AES-GCM de `cryptography`); es
adecuado para códigos de acceso cortos que además se imprimen. El acceso a descifrar está
además protegido por re-autenticación del admin en el endpoint.
"""

import base64
import hashlib
import hmac
import os

from .config import settings

_NONCE = 16
_TAG = 32
_DEFECTO = "cambia-esto-en-produccion"


def _claves() -> tuple[bytes, bytes]:
    """Deriva (k_enc, k_mac) del secreto de sesión. Etiquetas distintas por dominio."""
    maestro = (settings.session_secret or _DEFECTO).encode()
    k_enc = hmac.new(maestro, b"acceso-vault-enc-v1", hashlib.sha256).digest()
    k_mac = hmac.new(maestro, b"acceso-vault-mac-v1", hashlib.sha256).digest()
    return k_enc, k_mac


def _keystream(k_enc: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(k_enc, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def cifrar(texto: str) -> str:
    """Cifra un texto y devuelve un blob base64url para guardar en la BD."""
    k_enc, k_mac = _claves()
    nonce = os.urandom(_NONCE)
    data = texto.encode()
    ks = _keystream(k_enc, nonce, len(data))
    ct = bytes(a ^ b for a, b in zip(data, ks))
    tag = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ct + tag).decode()


def descifrar(blob: str) -> str | None:
    """Descifra un blob de `cifrar`. Devuelve None si el MAC no cuadra o el blob es inválido
    (p.ej. se cifró con OTRO session_secret: la clave cambió y ese código ya no es legible)."""
    try:
        raw = base64.urlsafe_b64decode((blob or "").encode())
    except (ValueError, TypeError):
        return None
    if len(raw) < _NONCE + _TAG:
        return None
    nonce, ct, tag = raw[:_NONCE], raw[_NONCE:-_TAG], raw[-_TAG:]
    k_enc, k_mac = _claves()
    esperado = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(esperado, tag):
        return None
    try:
        return bytes(a ^ b for a, b in zip(ct, _keystream(k_enc, nonce, len(ct)))).decode()
    except UnicodeDecodeError:
        return None


def vault_debil() -> bool:
    """¿La clave del vault se deriva del secreto por defecto? (aviso de seguridad para la UI)."""
    return (settings.session_secret or _DEFECTO) == _DEFECTO
