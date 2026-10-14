from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def _url_de_la_base() -> URL:
    """La URL de conexión, desde DATABASE_URL o desde DB_HOST/DB_USER/DB_PASSWORD/DB_NAME.

    Si DATABASE_URL viene mal escrita se falla con un mensaje que dice QUÉ falta, sin
    repetir la cadena: el error de SQLAlchemy citaba un trozo de ella, y ese trozo era la
    contraseña, que acababa escrita en los logs del panel.
    """
    if settings.database_url:
        try:
            url = make_url(settings.database_url)
        except (ArgumentError, ValueError):
            raise RuntimeError(
                "DATABASE_URL está mal escrita. El formato es "
                "postgresql+psycopg://USUARIO:CONTRASEÑA@HOST:5432/BASE  — revisa que esté la "
                "parte @HOST:PUERTO y que no haya espacios. Si la contraseña lleva @ : / o #, "
                "usa mejor las variables DB_HOST, DB_USER, DB_PASSWORD y DB_NAME.") from None
        # `postgres://` (lo que dan Supabase, Prisma y casi todos) no es un nombre de dialecto
        # de SQLAlchemy, y `postgresql://` cargaría psycopg2, que no está instalado.
        if url.drivername in ("postgres", "postgresql"):
            url = url.set(drivername="postgresql+psycopg")
        # `?schema=public` es de Prisma; psycopg lo rechaza como parámetro desconocido.
        return url.difference_update_query(["schema"])
    if settings.db_host and settings.db_user:
        return URL.create("postgresql+psycopg", username=settings.db_user,
                          password=settings.db_password, host=settings.db_host,
                          port=settings.db_port, database=settings.db_name)
    raise RuntimeError("Falta la base de datos: define DATABASE_URL, o bien DB_HOST, DB_USER, "
                       "DB_PASSWORD y DB_NAME.")


_url = _url_de_la_base()
_args: dict = {}
# Supabase en la nube exige TLS. Si la cadena no dice nada, se pide aquí para no depender de
# que quien la pegue en el panel se acuerde de `?sslmode=require`. Sólo para los dominios de
# supabase.com: un Supabase propio en la red interna (p.ej. `supabase_crm-db`) no usa TLS, y
# exigirlo ahí impediría conectar.
if (_url.host or "").endswith("supabase.com") and "sslmode" not in _url.query:
    _args["sslmode"] = "require"
# El pooler de Supabase en modo TRANSACCIÓN (puerto 6543) reparte cada transacción a una
# conexión distinta, y las sentencias preparadas de psycopg acaban en una conexión que no
# las conoce. Se apagan sólo ahí; el modo SESIÓN (5432) es el recomendado y no lo necesita.
if _url.port == 6543:
    _args["prepare_threshold"] = None
# Sin esto, una base inalcanzable (host mal escrito, otra red) deja el arranque colgado sin
# decir nada y el despliegue no termina nunca. Con 10 s falla con un error que se lee en el log.
_args.setdefault("connect_timeout", 10)

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
