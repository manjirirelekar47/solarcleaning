"""SQLAlchemy engine, session factory and declarative base."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings

_kwargs: dict = {"pool_pre_ping": True}
if settings.db_url.startswith("sqlite"):  # used by the test-suite
    _kwargs = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in settings.db_url:
        _kwargs["poolclass"] = StaticPool

engine = create_engine(settings.db_url, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db
