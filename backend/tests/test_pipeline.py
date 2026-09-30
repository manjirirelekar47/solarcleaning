"""evaluate -> trigger -> cleaning -> verify, driven by hand-made readings."""

import json
from datetime import timedelta

from app import decision_engine as de
from app.models import (
    Alert,
    CleaningCycle,
    DeviceState,
    ImageCapture,
    PanelReading,
    SoilingEvent,
    now,
)


def add_readings(db, test_w, ref_w, n=5, age_s=10):
    for i in range(n):
        t = now() - timedelta(seconds=age_s + i)
        for panel, w in (("test", test_w), ("reference", ref_w)):
            db.add(PanelReading(panel_type=panel, voltage=18, current=w / 18, power=w, timestamp=t))
    db.commit()


def setup_quiet(monkeypatch, rain=None):
    monkeypatch.setattr(de, "max_rain_probability_24h", lambda: rain)
    sent = []
    monkeypatch.setattr("app.notify.notify", sent.append)  # raise_alert still writes Alert rows
    return sent


def test_no_data_does_nothing(db, fake_mqtt, monkeypatch):
    setup_quiet(monkeypatch)
    de.evaluate_once(db, fake_mqtt)
    assert db.query(SoilingEvent).count() == 0


def test_clean_panel_is_ok(db, fake_mqtt, monkeypatch):
    setup_quiet(monkeypatch)
    add_readings(db, 10, 10)
    de.evaluate_once(db, fake_mqtt)
    ev = db.query(SoilingEvent).one()
    assert (ev.level, ev.action) == ("ok", "none")
    assert fake_mqtt.published == []


def test_dirty_panel_triggers_once(db, fake_mqtt, monkeypatch):
    sent = setup_quiet(monkeypatch)
    add_readings(db, 8.5, 10)  # 15 % electrical loss
    de.evaluate_once(db, fake_mqtt)
    de.evaluate_once(db, fake_mqtt)  # second pass must NOT trigger again (cycle active)
    assert len(fake_mqtt.published) == 1
    topic, payload, _ = fake_mqtt.published[0]
    assert topic == "cleaning/trigger" and json.loads(payload)["cycle_id"] == 1
    assert db.query(CleaningCycle).count() == 1
    assert len(sent) == 1  # alert only on level change


def test_rain_defers_cleaning(db, fake_mqtt, monkeypatch):
    setup_quiet(monkeypatch, rain=80)
    add_readings(db, 8.5, 10)
    de.evaluate_once(db, fake_mqtt)
    assert db.query(SoilingEvent).one().action == "defer"
    assert fake_mqtt.published == []


def test_full_cycle_verifies_success(db, fake_mqtt, monkeypatch):
    sent = setup_quiet(monkeypatch)
    add_readings(db, 8.0, 10, age_s=200)  # dirty, old
    de.evaluate_once(db, fake_mqtt)
    cyc = db.get(CleaningCycle, 1)
    cyc.status, cyc.completed_at = "verifying", now() - timedelta(seconds=60)
    db.commit()
    for panel in ("test", "reference"):  # clean readings after the cleaning finished
        for i in range(4):
            db.add(
                PanelReading(
                    panel_type=panel,
                    voltage=18,
                    current=0.5,
                    power=9.9 if panel == "test" else 10,
                    timestamp=now() - timedelta(seconds=10 + i),
                )
            )
    db.commit()
    de.verify_cycles(db)
    db.refresh(cyc)
    assert (cyc.status, cyc.result) == ("complete", "success")
    assert cyc.post_loss < 5
    assert any("success" in m for m in sent)


def test_verify_waits_for_settle_time(db, monkeypatch):
    setup_quiet(monkeypatch)
    db.add(CleaningCycle(pre_loss=15, status="verifying", completed_at=now()))
    db.commit()
    de.verify_cycles(db)
    assert db.get(CleaningCycle, 1).status == "verifying"


def test_old_image_ignored_after_cleaning(db, fake_mqtt, monkeypatch):
    setup_quiet(monkeypatch)
    db.add(
        ImageCapture(
            filename="x",
            label="mixed",
            confidence=1,
            severity_score=80,
            timestamp=now() - timedelta(minutes=10),
        )
    )
    db.add(
        CleaningCycle(
            pre_loss=20,
            status="complete",
            result="success",
            completed_at=now() - timedelta(minutes=5),
        )
    )
    db.commit()
    assert de.latest_image_severity(db) is None  # pre-clean photo must not re-trigger cleaning


def test_stale_cycle_expires(db, monkeypatch):
    setup_quiet(monkeypatch)
    db.add(CleaningCycle(pre_loss=12, status="running", triggered_at=now() - timedelta(hours=1)))
    db.commit()
    de.expire_stale_cycles(db)
    cyc = db.get(CleaningCycle, 1)
    assert (cyc.status, cyc.result) == ("complete", "insufficient")


def test_alerts_are_stored_in_the_database(db, fake_mqtt, monkeypatch):
    setup_quiet(monkeypatch)
    add_readings(db, 8.5, 10)
    de.evaluate_once(db, fake_mqtt)
    a = db.query(Alert).one()
    assert (a.kind, a.level) == ("level_change", "clean_recommended")


def test_event_records_reason_and_net_benefit(db, fake_mqtt, monkeypatch):
    setup_quiet(monkeypatch, rain=80)
    add_readings(db, 8.5, 10)
    de.evaluate_once(db, fake_mqtt)
    ev = db.query(SoilingEvent).one()
    assert "80%" in ev.reason and "deferred" in ev.reason
    assert ev.net_benefit_inr == de.net_benefit_inr(ev.combined_loss)


def test_net_benefit_formula():
    # 20 % of 1 kW * 5 h * 1 day = 1 kWh * Rs 8 = Rs 8 minus Rs 10 cleaning cost
    assert de.net_benefit_inr(20) == -2.0
    assert de.net_benefit_inr(50) == 10.0


def test_unusable_images_never_drive_decisions(db, monkeypatch):
    setup_quiet(monkeypatch)
    db.add(
        ImageCapture(
            filename="x",
            label="mixed",
            confidence=0.5,
            severity_score=80,
            model="stub",
            used=False,
            note="stub model: not used for decisions",
        )
    )
    db.commit()
    assert de.latest_image_severity(db) is None
    db.add(
        ImageCapture(
            filename="y",
            label="dusty",
            confidence=0.9,
            severity_score=50,
            severity_pct=12.0,
            model="cnn",
            used=True,
        )
    )
    db.commit()
    assert de.latest_image_severity(db) == 12.0  # calibrated value preferred over raw score


def test_raw_score_used_without_calibration(db, monkeypatch):
    setup_quiet(monkeypatch)
    db.add(
        ImageCapture(
            filename="y", label="dusty", confidence=0.9, severity_score=50, model="cnn", used=True
        )
    )
    db.commit()
    assert de.latest_image_severity(db) == 50


def test_offline_device_blocks_automatic_cleaning(db, fake_mqtt, monkeypatch):
    setup_quiet(monkeypatch)
    add_readings(db, 8.5, 10)
    db.add(DeviceState(source="reference", last_seen=now() - timedelta(minutes=2), healthy=False))
    db.commit()
    de.evaluate_once(db, fake_mqtt)
    ev = db.query(SoilingEvent).one()
    assert ev.action == "blocked" and "offline" in ev.reason
    assert fake_mqtt.published == [] and db.query(CleaningCycle).count() == 0


def test_device_health_transitions_raise_alerts(db, monkeypatch):
    setup_quiet(monkeypatch)
    db.add(DeviceState(source="test", last_seen=now() - timedelta(minutes=5), healthy=True))
    db.commit()
    de.refresh_device_health(db)
    assert db.get(DeviceState, "test").healthy is False
    de.refresh_device_health(db)  # no duplicate alert while it stays offline
    assert db.query(Alert).filter_by(kind="device_offline").count() == 1
    db.get(DeviceState, "test").last_seen = now()
    db.commit()
    de.refresh_device_health(db)
    assert db.get(DeviceState, "test").healthy is True
    assert db.query(Alert).filter_by(kind="device_recovered").count() == 1
