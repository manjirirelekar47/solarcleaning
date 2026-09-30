"""A4: debounce (CONFIRM_N) and level hysteresis regression tests."""

from datetime import timedelta

import pytest
from app import alerts as alerts_mod
from app import decision_engine as de
from app.config import settings
from app.models import Alert, PanelReading, SoilingEvent, now
from test_decision_engine import FakeMqtt, add_readings
from test_reliability import touch_both


@pytest.fixture()
def quiet(monkeypatch):
    sent = []
    monkeypatch.setattr(de, "max_rain_probability_24h", lambda: None)
    monkeypatch.setattr(alerts_mod, "notify", sent.append)
    monkeypatch.setattr(settings, "settle_s", 20)
    monkeypatch.setattr(settings, "confirm_n", 3)
    monkeypatch.setattr(settings, "hysteresis_pct", 1.0)
    return sent


def set_readings(db, test_w, ref_w):
    db.query(PanelReading).delete()
    db.commit()
    add_readings(db, test_w=test_w, ref_w=ref_w, age_s=1)


def events(db):
    return db.query(SoilingEvent).order_by(SoilingEvent.id).all()


# ---------------- debounce ----------------
def test_clean_fires_only_on_the_nth_consecutive_evaluation(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)  # 25% loss, no image
    de.evaluate_once(db, mqtt)
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == []
    assert [(e.action, e.reason) for e in events(db)] == [("wait", "confirming")] * 2
    de.evaluate_once(db, mqtt)
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]
    assert events(db)[-1].action == "clean"


def test_short_shadow_does_not_trigger_clean(db, quiet):
    """A shadow lasting one or two evaluations is confirmed away before the pump fires."""
    mqtt = FakeMqtt()
    touch_both(db)
    set_readings(db, test_w=7.5, ref_w=10.0)  # shadow: 25% "loss"
    de.evaluate_once(db, mqtt)
    de.evaluate_once(db, mqtt)
    set_readings(db, test_w=9.95, ref_w=10.0)  # shadow passes
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == [] and events(db)[-1].alert_level == "ok"


def test_streak_restarts_after_a_recovery(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    set_readings(db, test_w=7.5, ref_w=10.0)
    de.evaluate_once(db, mqtt)
    de.evaluate_once(db, mqtt)
    set_readings(db, test_w=9.95, ref_w=10.0)
    de.evaluate_once(db, mqtt)  # breaks the streak
    set_readings(db, test_w=7.5, ref_w=10.0)
    de.evaluate_once(db, mqtt)  # streak is 1 again, not 3
    assert mqtt.sent == []
    assert events(db)[-1].reason == "confirming"


def test_stale_confirming_events_do_not_count(db, quiet):
    """Two 'confirming' events from an hour ago (e.g. before an outage) carry no weight."""
    mqtt = FakeMqtt()
    touch_both(db)
    for _ in range(2):
        db.add(
            SoilingEvent(
                combined_loss=25,
                electrical_loss=25,
                cnn_severity=None,
                alert_level="critical",
                action="wait",
                reason="confirming",
                timestamp=now() - timedelta(hours=1),
            )
        )
    db.commit()
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    de.evaluate_once(db, mqtt)
    assert mqtt.sent == [] and events(db)[-1].reason == "confirming"


def test_confirming_writes_no_alert_rows(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    for _ in range(2):
        de.evaluate_once(db, mqtt)
    assert db.query(Alert).filter(Alert.kind == "cleaning").count() == 0  # confirming: no alert


def test_confirm_n_one_cleans_immediately(db, quiet, monkeypatch):
    monkeypatch.setattr(settings, "confirm_n", 1)
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    de.evaluate_once(db, mqtt)
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]


def test_held_signals_are_not_debounced_into_a_clean(db, quiet):
    """Gates still win: a shadow (meter high, CNN clean) never reaches the confirm step."""
    from app.models import ImageCapture

    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.0, ref_w=10.0, age_s=1)
    db.add(
        ImageCapture(image_path="x.jpg", soiling_class="clean", confidence=0.9, severity_score=0)
    )
    db.commit()
    for _ in range(4):
        de.evaluate_once(db, mqtt)
    assert mqtt.sent == [] and events(db)[-1].reason == "electrical_only"


# ---------------- hysteresis ----------------
@pytest.mark.parametrize(
    "loss,prev,level",
    [
        (5.0, "ok", "watch"),  # rising: immediate at the threshold
        (4.5, "watch", "watch"),  # inside the band: hold
        (3.99, "watch", "ok"),  # cleared the band: drop
        (9.5, "clean_recommended", "clean_recommended"),
        (8.9, "clean_recommended", "watch"),
        (19.5, "critical", "critical"),
        (18.9, "critical", "clean_recommended"),
        (3.0, "critical", "ok"),  # a big drop still lands on the right level
        (12.0, "unknown", "clean_recommended"),  # unknown / missing history: raw level
        (12.0, None, "clean_recommended"),
    ],
)
def test_held_level(loss, prev, level):
    assert de.held_level(loss, prev) == level


def test_loss_hovering_at_5_percent_changes_level_once():
    level, changes = "ok", 0
    for loss in [4.9, 5.1, 4.9, 5.2, 4.6, 5.0, 4.4]:
        new = de.decide(loss, None, False, level).level
        changes += new != level
        level = new
    assert changes == 1 and level == "watch"


def test_hysteresis_never_triggers_a_clean_below_the_threshold():
    d = de.decide(9.5, None, False, prev_level="clean_recommended")
    assert (d.level, d.action) == ("clean_recommended", "notify")  # held level, no pump
    d = de.decide(19.5, None, False, prev_level="critical")
    assert (d.level, d.action) == ("critical", "clean")  # 19.5 is still >= 10: real clean


def test_flapping_loss_sends_one_level_notification(db, quiet, monkeypatch):
    """End to end: scripted losses around 5% give one 'level' message, not one per crossing."""
    losses = iter([4.9, 5.1, 4.8, 5.2, 4.7])
    monkeypatch.setattr(de, "combined_loss", lambda sev, elec: next(losses))
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=9.5, ref_w=10.0, age_s=1)
    for _ in range(5):
        de.evaluate_once(db, mqtt)
    assert [e.alert_level for e in events(db)] == ["ok", "watch", "watch", "watch", "watch"]
    assert len([m for m in quiet if "soiling level" in m]) == 1
