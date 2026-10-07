from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_url = settings.database_url
_args: dict = {}
# Supabase exige TLS. Si la cadena no dice nada, se pide aquí para no depender de que
# quien la pegue en el panel se acuerde de `?sslmode=require`.
if "supabase" in _url and "sslmode=" not in _url:
    _args["sslmode"] = "require"
# El pooler de Supabase en modo TRANSACCIÓN (puerto 6543) reparte cada transacción a una
# conexión distinta, y las sentencias preparadas de psycopg acaban en una conexión que no
# las conoce. Se apagan sólo ahí; el modo SESIÓN (5432) es el recomendado y no lo necesita.
if ":6543" in _url:
    _args["prepare_threshold"] = None

engine = create_engine(
    _url,
    pool_pre_ping=True,
    # El pooler de Supabase limita los clientes por proyecto (unos 15 en el plan pequeño).
    # Un solo proceso con 5 + 5 cabe holgado y deja sitio para una consola o un pg_dump.
    pool_size=5,
    max_overflow=5,
    # Se reciclan antes de que el pooler cierre por inactividad una conexión que el pool
    # aún creía viva.
    pool_recycle=300,
    connect_args=_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
