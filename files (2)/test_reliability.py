"""A1 (re-trigger loop), A3 (device health, sensor sanity, night) regression tests."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app import alerts as alerts_mod
from app import decision_engine as de
from app import health, mqtt_client
from app.config import settings
from app.loss import electrical_loss_pct
from app.models import (
    Alert,
    CleaningCycle,
    DeviceState,
    ImageCapture,
    PanelReading,
    SoilingEvent,
    now,
)
from test_decision_engine import FakeMqtt, add_readings


@pytest.fixture()
def quiet(monkeypatch):
    sent = []
    monkeypatch.setattr(de, "max_rain_probability_24h", lambda: None)
    monkeypatch.setattr(de, "notify", sent.append)
    monkeypatch.setattr(alerts_mod, "notify", sent.append)
    monkeypatch.setattr(settings, "settle_s", 20)
    monkeypatch.setattr(settings, "min_clean_interval_s", 600)
    monkeypatch.setattr(settings, "max_cleans_per_day", 6)
    monkeypatch.setattr(settings, "offline_after_s", 30)
    return sent


def add_image(db, age_s, severity=50.0):
    db.add(
        ImageCapture(
            image_path="x.jpg",
            soiling_class="dusty",
            confidence=0.9,
            severity_score=severity,
            timestamp=now() - timedelta(seconds=age_s),
        )
    )
    db.commit()


def finished_cycle(db, triggered_ago_s, completed_ago_s):
    cyc = CleaningCycle(
        pre_loss=25,
        post_loss=1,
        status="complete",
        result="success",
        triggered_at=now() - timedelta(seconds=triggered_ago_s),
        completed_at=now() - timedelta(seconds=completed_ago_s),
    )
    db.add(cyc)
    db.commit()
    return cyc


def touch_both(db, fault=None):
    for src in health.SOURCES:
        health.touch(db, src, fault)
    db.commit()


# ---------------- A1 ----------------
def test_no_retrigger_on_stale_data_right_after_clean(db, quiet):
    """The critical bug: dirty readings + dusty image still in the table after a clean."""
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=30)  # dirty, from BEFORE the clean
    add_image(db, age_s=40)  # stale "dusty" image, also before the clean
    finished_cycle(db, triggered_ago_s=60, completed_ago_s=10)  # settle window still open
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == []


def test_old_image_ignored_after_settle(db, quiet):
    """Settle window over, fresh clean readings, but the only image predates the clean."""
    mqtt = FakeMqtt()
    touch_both(db)
    finished_cycle(db, triggered_ago_s=700, completed_ago_s=100)  # cooldown over, settled
    add_image(db, age_s=200)  # dusty image from before the clean
    add_readings(db, test_w=9.9, ref_w=10.0, age_s=1)  # panel is clean now
    de.evaluate_once(db, mqtt)
    ev = db.query(SoilingEvent).one()
    assert mqtt.sent == [] and ev.cnn_severity is None and ev.alert_level == "ok"


def test_cooldown_blocks_second_clean_and_logs_once(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    finished_cycle(db, triggered_ago_s=300, completed_ago_s=250)  # inside 600 s cooldown
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)  # genuinely dirty again
    de.evaluate_once(db, mqtt)
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == []
    assert [e.reason for e in db.query(SoilingEvent)] == ["cooldown", "cooldown"]
    blocked = db.query(Alert).filter(Alert.kind == "cleaning").all()
    assert len(blocked) == 1 and "cooldown" in blocked[0].message  # logged once, not per tick


def test_daily_cap_enforced_and_logged(db, quiet, monkeypatch):
    monkeypatch.setattr(settings, "max_cleans_per_day", 2)
    monkeypatch.setattr(settings, "min_clean_interval_s", 60)
    mqtt = FakeMqtt()
    touch_both(db)
    finished_cycle(db, triggered_ago_s=7200, completed_ago_s=7100)
    finished_cycle(db, triggered_ago_s=3600, completed_ago_s=3500)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == []
    assert db.query(SoilingEvent).one().reason == "daily_cap"
    assert "daily_cap" in db.query(Alert).one().message


def test_clean_allowed_when_limits_not_hit(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    finished_cycle(db, triggered_ago_s=3600, completed_ago_s=3500)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    de.evaluate_once(db, mqtt)
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]


# ---------------- A3 ----------------
def test_night_is_unknown_not_zero_loss(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=0.0, ref_w=0.01, age_s=1)
    de.evaluate_once(db, mqtt)
    de.evaluate_once(db, mqtt)
    ev = db.query(SoilingEvent).one()  # one event per state change, not one per tick
    assert (ev.alert_level, ev.reason, ev.electrical_loss) == (
        "unknown",
        "insufficient_light",
        None,
    )
    assert mqtt.sent == []


def test_device_offline_alerts_once_and_blocks_clean(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)  # would normally clean
    db.get(DeviceState, "test").last_seen = now() - timedelta(seconds=45)
    db.commit()
    for _ in range(3):
        de.evaluate_once(db, mqtt)
    assert mqtt.sent == []
    rows = db.query(Alert).all()
    assert len(rows) == 1 and rows[0].kind == "device" and "offline" in rows[0].message
    assert db.query(SoilingEvent).one().reason == "device_unhealthy"


def test_device_recovery_writes_one_alert(db, quiet):
    touch_both(db)
    db.get(DeviceState, "test").last_seen = now() - timedelta(seconds=45)
    db.commit()
    health.evaluate_health(db)
    health.touch(db, "test", None)
    db.commit()
    assert health.evaluate_health(db) == []
    assert [a.level for a in db.query(Alert).order_by(Alert.id)] == ["critical", "ok"]


def test_disconnected_test_panel_does_not_trigger_clean(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=0.0, ref_w=10.0, age_s=1)  # 100% "loss" from a dead sensor
    de.evaluate_once(db, mqtt)
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == []
    assert db.query(SoilingEvent).one().reason == "sensor_fault"
    assert len(db.query(Alert).filter(Alert.kind == "sensor_fault").all()) == 1


def test_reference_reading_zero_blocks_clean(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=9.0, ref_w=0.0, age_s=1)
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == [] and db.query(SoilingEvent).one().reason == "sensor_fault"


@pytest.mark.parametrize(
    "v,a,ok",
    [(18.0, 0.5, True), (0.2, 0.0, True), (18.0, -0.2, False), (30.0, 0.5, False),
     (-1.0, 0.1, False), (float("nan"), 0.1, False)],
)  # fmt: skip
def test_check_reading(v, a, ok):
    assert (health.check_reading(v, a) is None) is ok


def test_implausible_reading_is_not_stored_and_marks_fault(db, session_factory, monkeypatch):
    monkeypatch.setattr(mqtt_client, "SessionLocal", session_factory)

    def msg(topic, payload):
        return SimpleNamespace(topic=topic, payload=json.dumps(payload).encode())

    mqtt_client.on_message(None, None, msg("sensors/test", {"v": 18.0, "i_ma": -500}))
    assert db.query(PanelReading).count() == 0
    assert "negative current" in db.get(DeviceState, "test").fault
    mqtt_client.on_message(None, None, msg("sensors/test", {"v": 18.0, "i_ma": 500}))
    db.expire_all()
    assert db.query(PanelReading).count() == 1 and db.get(DeviceState, "test").fault is None


def test_firmware_fault_message_marks_sensor_fault(db, session_factory, monkeypatch):
    monkeypatch.setattr(mqtt_client, "SessionLocal", session_factory)
    raw = json.dumps({"source": "test", "fault": "ina219_not_found"}).encode()
    mqtt_client.on_message(None, None, SimpleNamespace(topic="sensors/fault", payload=raw))
    assert db.get(DeviceState, "test").fault == "ina219_not_found"


def test_device_rejected_trigger_is_failed_not_insufficient(db, session_factory, monkeypatch):
    monkeypatch.setattr(mqtt_client, "SessionLocal", session_factory)
    db.add(CleaningCycle(pre_loss=15))
    db.commit()
    raw = json.dumps({"cycle_id": 1, "state": "rejected"}).encode()
    mqtt_client.on_message(None, None, SimpleNamespace(topic="cleaning/status", payload=raw))
    db.expire_all()
    cyc = db.get(CleaningCycle, 1)
    assert (cyc.status, cyc.result) == ("complete", "failed")


def test_late_done_does_not_revive_completed_cycle(db, session_factory, monkeypatch):
    monkeypatch.setattr(mqtt_client, "SessionLocal", session_factory)
    db.add(CleaningCycle(pre_loss=15, status="complete", result="insufficient"))
    db.commit()
    raw = json.dumps({"cycle_id": 1, "state": "done"}).encode()
    mqtt_client.on_message(None, None, SimpleNamespace(topic="cleaning/status", payload=raw))
    db.expire_all()
    assert db.get(CleaningCycle, 1).status == "complete"


def test_electrical_loss_none_below_min_reference():
    assert electrical_loss_pct(0.0, 0.01) is None
    assert electrical_loss_pct(8.0, 10.0) == pytest.approx(20.0)
