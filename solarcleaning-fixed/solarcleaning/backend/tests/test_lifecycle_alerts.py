"""A8 (cleaning lifecycle, one retry, manual inspection) and A9 (alert on every transition)."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app import alerts as alerts_mod
from app import decision_engine as de
from app import mqtt_client
from app.config import settings
from app.models import Alert, CleaningCycle, ImageCapture, PanelReading, SoilingEvent, now
from test_decision_engine import FakeMqtt, add_readings
from test_reliability import touch_both


@pytest.fixture()
def quiet(monkeypatch):
    sent = []
    monkeypatch.setattr(de, "max_rain_probability_24h", lambda: None)
    monkeypatch.setattr(alerts_mod, "notify", sent.append)
    monkeypatch.setattr(settings, "settle_s", 20)
    monkeypatch.setattr(settings, "min_clean_interval_s", 60)
    return sent


def set_readings(db, test_w, ref_w):
    db.query(PanelReading).delete()
    db.commit()
    add_readings(db, test_w=test_w, ref_w=ref_w, age_s=1)


def alerts_of(db, level=None, kind=None):
    q = db.query(Alert)
    if level:
        q = q.filter(Alert.level == level)
    if kind:
        q = q.filter(Alert.kind == kind)
    return q.order_by(Alert.id).all()


def end_cycle(db, cyc, test_w):
    """The pump 'finished' 30 s ago (past the settle window); panel now reads test_w vs 10 W."""
    cyc.status, cyc.triggered_at = "verifying", now() - timedelta(seconds=120)
    cyc.completed_at = now() - timedelta(seconds=30)
    db.commit()
    set_readings(db, test_w=test_w, ref_w=10.0)
    de.verify_cycles(db)
    db.refresh(cyc)


def start_clean(db, mqtt):
    before = len(mqtt.sent)
    de.evaluate_once(db, mqtt)
    if len(mqtt.sent) == before:
        return None
    return db.get(CleaningCycle, mqtt.sent[-1][1]["cycle_id"])


# ---------------- A8 ----------------
def test_timeout_is_failed_not_insufficient_and_asks_for_inspection(db, quiet):
    db.add(CleaningCycle(pre_loss=20, status="running", triggered_at=now() - timedelta(hours=1)))
    db.commit()
    de.expire_stale_cycles(db)
    de.expire_stale_cycles(db)
    cyc = db.get(CleaningCycle, 1)
    assert (cyc.status, cyc.result) == ("complete", "failed")
    rows = alerts_of(db, level="inspection")
    assert (
        len(rows) == 1 and rows[0].cycle_id == 1 and "Manual inspection needed" in rows[0].message
    )


def test_device_rejection_is_failed_with_one_alert(db, session_factory, monkeypatch, quiet):
    monkeypatch.setattr(mqtt_client, "SessionLocal", session_factory)
    db.add(CleaningCycle(pre_loss=15))
    db.commit()
    raw = json.dumps({"cycle_id": 1, "state": "rejected"}).encode()
    mqtt_client.on_message(None, None, SimpleNamespace(topic="cleaning/status", payload=raw))
    db.expire_all()
    assert db.get(CleaningCycle, 1).result == "failed"
    assert len(alerts_of(db, level="inspection")) == 1


def test_failed_cycle_blocks_auto_clean_until_panel_is_ok(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    de.evaluate_once(db, mqtt)  # first clean goes out
    cyc = db.get(CleaningCycle, mqtt.sent[0][1]["cycle_id"])
    cyc.triggered_at = now() - timedelta(hours=1)
    db.commit()
    de.expire_stale_cycles(db)  # -> failed + inspection alert
    cyc.completed_at = now() - timedelta(seconds=30)  # settle window is over
    db.commit()
    set_readings(db, test_w=7.5, ref_w=10.0)
    for _ in range(3):
        de.evaluate_once(db, mqtt)
    assert len(mqtt.sent) == 1  # no second spray
    assert (
        db.query(SoilingEvent).order_by(SoilingEvent.id.desc()).first().reason
        == "manual_inspection"
    )
    assert len(alerts_of(db, level="inspection")) == 1  # not one per tick


def test_insufficient_first_attempt_is_retried_once_as_attempt_2(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    c1 = start_clean(db, mqtt)
    assert c1.attempt == 1
    end_cycle(db, c1, test_w=7.5)  # stuck relay: still dirty
    assert c1.result == "insufficient"
    assert len(alerts_of(db, level="warning")) == 1 and not alerts_of(db, level="inspection")
    c2 = start_clean(db, mqtt)
    assert c2 is not None and c2.attempt == 2


def test_stuck_relay_one_retry_then_single_manual_inspection_alert(db, quiet):
    """The simulator --fault stuck_relay scenario, step by step."""
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    c1 = start_clean(db, mqtt)
    end_cycle(db, c1, test_w=7.5)
    c2 = start_clean(db, mqtt)
    end_cycle(db, c2, test_w=7.5)
    assert (c2.attempt, c2.result) == (2, "insufficient")
    for _ in range(4):
        assert start_clean(db, mqtt) is None  # no third spray
    assert len(mqtt.sent) == 2
    assert len(alerts_of(db, level="inspection")) == 1  # a single manual-inspection alert
    assert "after retry" in alerts_of(db, level="inspection")[0].message
    last = db.query(SoilingEvent).order_by(SoilingEvent.id.desc()).first()
    assert (last.action, last.reason) == ("notify", "manual_inspection")
    assert not [a for a in alerts_of(db, kind="cleaning") if "held" in a.message]


def test_inspection_hold_clears_when_the_panel_reads_ok_again(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    end_cycle(db, start_clean(db, mqtt), test_w=7.5)
    end_cycle(db, start_clean(db, mqtt), test_w=7.5)
    assert start_clean(db, mqtt) is None  # held
    set_readings(db, test_w=9.95, ref_w=10.0)
    de.evaluate_once(db, mqtt)  # someone cleaned it by hand: level ok
    set_readings(db, test_w=7.5, ref_w=10.0)
    c = start_clean(db, mqtt)
    assert c is not None and c.attempt == 1  # a fresh incident, not attempt 3


def test_manual_clean_after_inspection_alert_releases_the_hold(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    end_cycle(db, start_clean(db, mqtt), test_w=7.5)
    end_cycle(db, start_clean(db, mqtt), test_w=7.5)
    assert de.inspection_hold(db)
    de.trigger_clean(db, mqtt, 20.0, attempt=1)  # POST /trigger-clean
    assert not de.inspection_hold(db)


def test_retry_respects_daily_cap(db, quiet, monkeypatch):
    monkeypatch.setattr(settings, "max_cleans_per_day", 1)
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    end_cycle(db, start_clean(db, mqtt), test_w=7.5)
    assert start_clean(db, mqtt) is None  # cap of 1 already used: no retry
    assert db.query(SoilingEvent).order_by(SoilingEvent.id.desc()).first().reason == "daily_cap"


def test_success_ends_the_incident(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    c1 = start_clean(db, mqtt)
    end_cycle(db, c1, test_w=9.95)
    assert c1.result == "success" and alerts_of(db, level="ok", kind="cleaning")
    assert de.next_attempt(db) == 1


def test_late_retry_window_starts_a_new_incident(db, quiet, monkeypatch):
    monkeypatch.setattr(settings, "retry_window_s", 100)
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    c1 = start_clean(db, mqtt)
    end_cycle(db, c1, test_w=7.5)
    c1.completed_at = now() - timedelta(seconds=500)
    db.commit()
    assert de.next_attempt(db) == 1


def _post_clean_image(db, cls, score, age_s=5):
    img = ImageCapture(
        image_path="p.jpg",
        soiling_class=cls,
        confidence=0.9,
        severity_score=score,
        context="post_clean",
        timestamp=now() - timedelta(seconds=age_s),
    )
    db.add(img)
    db.commit()
    return img


def test_post_clean_image_can_overrule_a_good_electrical_reading(db, quiet):
    touch_both(db)
    cyc = CleaningCycle(pre_loss=25, status="verifying")
    db.add(cyc)
    db.commit()
    img = _post_clean_image(db, "dusty", 80)  # 80 * 0.3 = 24% expected loss from the image
    end_cycle(db, cyc, test_w=9.95)  # electrical says ~0.5%
    assert cyc.result == "insufficient" and cyc.post_image_id == img.id
    assert cyc.post_loss == pytest.approx(0.6 * 24 + 0.4 * 0.5)


def test_clean_post_image_confirms_success_and_old_images_are_ignored(db, quiet):
    touch_both(db)
    cyc = CleaningCycle(pre_loss=25, status="verifying")
    db.add(cyc)
    db.commit()
    _post_clean_image(db, "dusty", 80, age_s=3600)  # taken long before the cycle finished
    img = _post_clean_image(db, "clean", 0)
    end_cycle(db, cyc, test_w=9.95)
    assert cyc.result == "success" and cyc.post_image_id == img.id


def test_verification_without_an_image_uses_electrical_only(db, quiet):
    touch_both(db)
    cyc = CleaningCycle(pre_loss=25, status="verifying")
    db.add(cyc)
    db.commit()
    end_cycle(db, cyc, test_w=9.95)
    assert cyc.post_image_id is None and cyc.post_loss == pytest.approx(0.5)


# ---------------- A9 ----------------
def test_one_ongoing_level_is_one_alert_not_one_per_tick(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=9.3, ref_w=10.0, age_s=1)  # 7% loss: watch
    for _ in range(6):
        de.evaluate_once(db, mqtt)
    rows = alerts_of(db)
    assert [(r.kind, r.level) for r in rows] == [("level_change", "watch")]
    assert db.query(SoilingEvent).count() == 6  # events keep logging; alerts do not


def test_cleaning_start_and_result_each_write_a_row(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    c1 = start_clean(db, mqtt)
    started = alerts_of(db, level="info", kind="cleaning")
    assert len(started) == 1 and started[0].cycle_id == c1.id and "attempt 1" in started[0].message
    end_cycle(db, c1, test_w=9.95)
    done = alerts_of(db, level="ok", kind="cleaning")
    assert len(done) == 1 and done[0].cycle_id == c1.id and "success" in done[0].message


def test_alerts_endpoint_matches_the_notifications(client, db, quiet):
    """GET /alerts and Telegram carry the same transitions, in the same words."""
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    c1 = start_clean(db, mqtt)
    end_cycle(db, c1, test_w=9.95)
    set_readings(db, test_w=9.95, ref_w=10.0)
    de.evaluate_once(db, mqtt)  # level back to ok
    rows = client.get("/alerts?limit=50").json()
    assert [r["message"] for r in reversed(rows)] == quiet
    assert [r["kind"] for r in reversed(rows)] == [
        "level_change", "cleaning", "cleaning", "level_change"
    ]  # fmt: skip


def test_no_alert_row_for_every_non_ok_event(db, quiet):
    """The old /alerts listed every non-ok soiling event; it must be empty when nothing changed."""
    db.add(
        SoilingEvent(
            combined_loss=12, electrical_loss=12, cnn_severity=None,
            alert_level="clean_recommended", action="clean",
        )
    )  # fmt: skip
    db.commit()
    assert de.next_attempt(db) == 1
    assert alerts_of(db) == []
