from contextlib import contextmanager
from functools import lru_cache
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    url = settings().database_url
    options = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    result = create_engine(url, **options)
    if url.startswith("sqlite"):

        @event.listens_for(result, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

    return result


def new_session():
    return sessionmaker(engine(), expire_on_commit=False)()


def get_db():
    db = new_session()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope():
    db = new_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
