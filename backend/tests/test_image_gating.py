"""B2: images flagged unused (dark frame, low confidence, stub model) never drive decisions."""

import pytest
from app import alerts as alerts_mod
from app import decision_engine as de
from app.config import settings
from app.models import ImageCapture, SoilingEvent
from test_decision_engine import FakeMqtt, add_readings
from test_reliability import touch_both


@pytest.fixture()
def quiet(monkeypatch):
    monkeypatch.setattr(de, "max_rain_probability_24h", lambda: None)
    monkeypatch.setattr(alerts_mod, "notify", lambda *_: None)
    monkeypatch.setattr(settings, "settle_s", 20)


def add_image(db, used, cls="dusty", score=50.0, context="routine", note=None):
    db.add(
        ImageCapture(
            image_path="x.jpg",
            soiling_class=cls,
            confidence=0.9,
            severity_score=score,
            used=used,
            note=note,
            context=context,
        )
    )
    db.commit()


def evaluate(db):
    mqtt = FakeMqtt()
    de.evaluate_once(db, mqtt)
    return mqtt, db.query(SoilingEvent).order_by(SoilingEvent.id.desc()).first()


def test_unused_image_is_ignored_and_engine_falls_back_to_the_meter(db, quiet):
    touch_both(db)
    add_readings(db, test_w=7.0, ref_w=10, age_s=1)  # 30% electrical loss
    add_image(db, used=False, cls="dusty", score=99.0, note="frame too dark")
    _, ev = evaluate(db)
    assert ev.cnn_severity is None  # the bad frame contributed nothing


def test_used_image_is_taken_into_account(db, quiet):
    touch_both(db)
    add_readings(db, test_w=7.0, ref_w=10, age_s=1)
    add_image(db, used=True, cls="dusty", score=50.0)
    mqtt, ev = evaluate(db)
    assert ev.cnn_severity == 50.0
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]


def test_newer_unused_image_does_not_hide_an_older_usable_one(db, quiet):
    touch_both(db)
    add_readings(db, test_w=7.0, ref_w=10, age_s=1)
    add_image(db, used=True, cls="dusty", score=50.0)
    add_image(db, used=False, cls="clean", score=0.0, note="low confidence")  # newer, ignored
    _, ev = evaluate(db)
    assert ev.cnn_severity == 50.0
