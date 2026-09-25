"""Engine, session factory and the ``get_db`` dependency.

The app runs on SQLite locally and on Postgres when deployed, so the engine is built from
whatever ``SANCTUM_DATABASE_URL`` holds rather than assuming one backend.
"""
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("SANCTUM_DATABASE_URL", "sqlite:///./sanctum.db")

# Hosted providers still hand out ``postgres://`` URLs, a scheme SQLAlchemy 2 dropped, and a
# bare ``postgresql://`` resolves to psycopg2 rather than the psycopg 3 driver we install.
POSTGRES_ALIASES = {"postgres", "postgresql"}
POSTGRES_DRIVER = "postgresql+psycopg"


def normalize_database_url(url: str) -> URL:
    """Parse ``url``, mapping legacy Postgres schemes onto the driver that is installed.

    An explicit driver (``postgresql+psycopg2://``) is left alone so it stays overridable.
    """
    parsed = make_url(url)
    if parsed.drivername in POSTGRES_ALIASES:
        parsed = parsed.set(drivername=POSTGRES_DRIVER)
    return parsed


def engine_options(url: URL) -> dict:
    """Backend-specific engine arguments.

    ``check_same_thread`` is a sqlite3 connect argument that other drivers reject, and hosted
    Postgres closes idle connections, so those pools are checked before a connection is used.
    """
    if url.get_backend_name() == "sqlite":
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


url = normalize_database_url(DATABASE_URL)
engine = create_engine(url, **engine_options(url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
