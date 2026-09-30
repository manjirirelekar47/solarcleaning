import pytest
from app import models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def no_debounce_by_default(monkeypatch):
    """Existing tests expect an immediate clean; A4 tests set confirm_n explicitly."""
    from app.config import settings

    monkeypatch.setattr(settings, "confirm_n", 1)


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


@pytest.fixture()
def db(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture()
def client(session_factory, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "image_dir", tmp_path)

    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override
    yield TestClient(app)  # no `with`: lifespan (MQTT, scheduler) is not started in tests
    app.dependency_overrides.clear()
