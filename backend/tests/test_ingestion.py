import json
from types import SimpleNamespace

from app import mqtt_client
from app.models import CleaningCycle, DeviceState, PanelReading


def msg(topic, payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return SimpleNamespace(topic=topic, payload=raw)


def test_sensor_reading_stored(db):
    mqtt_client.on_message(
        None, None, msg("sensors/test", {"v": 18, "i_ma": 500, "temp": 30, "hum": 40})
    )
    r = db.query(PanelReading).one()
    assert r.panel_type == "test" and r.power == 9.0 and r.humidity == 40


def test_malformed_payloads_do_not_crash(db):
    mqtt_client.on_message(None, None, msg("sensors/test", b"not json"))
    mqtt_client.on_message(None, None, msg("sensors/test", {"v": 18}))  # missing i_ma
    assert db.query(PanelReading).count() == 0


def test_cleaning_status_transitions(db):
    db.add(CleaningCycle(pre_loss=12))
    db.commit()
    mqtt_client.on_message(None, None, msg("cleaning/status", {"cycle_id": 1, "state": "running"}))
    db.expire_all()
    assert db.get(CleaningCycle, 1).status == "running"
    mqtt_client.on_message(None, None, msg("cleaning/status", {"cycle_id": 1, "state": "done"}))
    db.expire_all()
    cyc = db.get(CleaningCycle, 1)
    assert cyc.status == "verifying" and cyc.completed_at is not None


def test_sensor_message_updates_device_heartbeat(db):
    mqtt_client.on_message(None, None, msg("sensors/reference", {"v": 18, "i_ma": 400}))
    first = db.get(DeviceState, "reference")
    assert first.healthy is True and first.last_seen is not None
    t1 = first.last_seen
    mqtt_client.on_message(None, None, msg("sensors/reference", {"v": 18, "i_ma": 400}))
    db.expire_all()
    assert db.get(DeviceState, "reference").last_seen >= t1
    assert db.query(DeviceState).count() == 1


def test_negative_current_is_clamped(db):
    mqtt_client.on_message(None, None, msg("sensors/test", {"v": 18, "i_ma": -3.0}))
    assert db.query(PanelReading).one().power == 0.0
