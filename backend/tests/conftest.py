import os

os.environ["DB_URL"] = "sqlite:///:memory:"  # must be set before the app is imported
os.environ["ENABLE_BACKGROUND"] = "0"

import pytest
from app import models  # noqa: F401
from app.db import Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))


@pytest.fixture
def fake_mqtt():
    return FakeMqtt()
