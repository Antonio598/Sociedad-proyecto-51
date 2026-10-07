"""Crea o actualiza un usuario del panel con su rol.

    python -m scripts.crear_usuario coordinador --rol coordinador --nombre "Juan Perez"
    python -m scripts.crear_usuario jromero --rol operador --operador 1042
    python -m scripts.crear_usuario despacho --rol combustible --nombre "Bomba interna"

Los cuatro roles: admin, coordinador, combustible, operador.
Una cuenta de OPERADOR debe ligarse a su ficha del padrón (--operador NÚMERO), para que el
sistema sepa quién captura sin que él lo escriba.

La contraseña se pide por consola (no se pasa como argumento: quedaría en el historial del
shell y en la lista de procesos).
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Operador, Usuario  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("username")
ap.add_argument("--rol", choices=["admin", "coordinador", "combustible", "operador", "gerente"],
                default="coordinador")
ap.add_argument("--nombre", default=None)
ap.add_argument("--operador", type=int, default=None,
                help="NÚMERO del operador del padrón a ligar (obligatorio si --rol operador)")
a = ap.parse_args()

with SessionLocal() as s:
    operador_id = None
    if a.rol == "operador":
        if a.operador is None:
            sys.exit("Un operador debe ligarse a su ficha: usa --operador NÚMERO.")
        op = s.execute(
            select(Operador).where(Operador.numero == a.operador)).scalar_one_or_none()
        if op is None:
            sys.exit(f"No existe operador con número {a.operador} en el padrón.")
        operador_id = op.id
        if a.nombre is None:
            a.nombre = op.nombre   # hereda el nombre de la ficha
        # Un operador ya ligado a otra cuenta es un error de captura, no algo a pisar.
        otra = s.execute(select(Usuario).where(
            Usuario.operador_id == operador_id, Usuario.username != a.username)).scalars().first()
        if otra is not None:
            sys.exit(f"El operador {op.nombre} ya tiene cuenta: {otra.username!r}.")
    elif a.operador is not None:
        sys.exit("--operador solo aplica a cuentas de rol operador.")

    pwd = getpass.getpass(f"Contraseña para {a.username}: ")
    if len(pwd) < 8:
        sys.exit("La contraseña debe tener al menos 8 caracteres.")
    if pwd != getpass.getpass("Confírmala: "):
        sys.exit("No coinciden.")

    u = s.execute(select(Usuario).where(Usuario.username == a.username)).scalar_one_or_none()
    if u is None:
        u = Usuario(username=a.username, password_hash=hash_password(pwd),
                    nombre=a.nombre, rol=a.rol, operador_id=operador_id, activo=True)
        s.add(u)
        accion = "creado"
    else:
        u.password_hash = hash_password(pwd)
        u.rol = a.rol
        u.operador_id = operador_id
        if a.nombre:
            u.nombre = a.nombre
        u.activo = True
        accion = "actualizado"
    s.commit()
    liga = f" (ligado al operador #{a.operador})" if operador_id else ""
    print(f"Usuario {a.username!r} {accion} con rol {a.rol}{liga}.")
