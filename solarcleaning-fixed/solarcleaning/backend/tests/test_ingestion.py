import json
from types import SimpleNamespace

import pytest
from app import mqtt_client
from app.models import CleaningCycle, PanelReading


@pytest.fixture(autouse=True)
def use_test_db(session_factory, monkeypatch):
    monkeypatch.setattr(mqtt_client, "SessionLocal", session_factory)


def msg(topic, payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return SimpleNamespace(topic=topic, payload=raw)


def test_sensor_reading_is_stored(db):
    mqtt_client.on_message(
        None, None, msg("sensors/test", {"v": 18.0, "i_ma": 500, "temp": 30, "hum": 40})
    )
    row = db.query(PanelReading).one()
    assert (row.panel_type, row.current) == ("test", 0.5)
    assert row.power == pytest.approx(9.0)


def test_missing_temp_is_allowed(db):
    mqtt_client.on_message(None, None, msg("sensors/reference", {"v": 18.0, "i_ma": 400}))
    assert db.query(PanelReading).one().temp is None


@pytest.mark.parametrize("bad", [b"{not json", b"{}", b'{"v": 1}'])
def test_malformed_payload_is_ignored(db, bad):
    mqtt_client.on_message(None, None, msg("sensors/test", bad))
    assert db.query(PanelReading).count() == 0


def test_cleaning_status_transitions(db):
    db.add(CleaningCycle(pre_loss=15))
    db.commit()
    mqtt_client.on_message(None, None, msg("cleaning/status", {"cycle_id": 1, "state": "running"}))
    assert db.get(CleaningCycle, 1).status == "running"
    mqtt_client.on_message(None, None, msg("cleaning/status", {"cycle_id": 1, "state": "done"}))
    db.expire_all()
    cyc = db.get(CleaningCycle, 1)
    assert cyc.status == "verifying" and cyc.completed_at is not None


def test_unknown_cycle_id_is_ignored(db):
    mqtt_client.on_message(None, None, msg("cleaning/status", {"cycle_id": 99, "state": "done"}))
