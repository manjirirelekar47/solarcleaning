import json
from datetime import timedelta

import pytest
from app import decision_engine as de
from app.config import settings
from app.decision_engine import decide
from app.loss import combined_loss, electrical_loss_pct
from app.models import CleaningCycle, PanelReading, now


@pytest.mark.parametrize(
    "loss,rain,active,level,action",
    [
        (4.99, None, False, "ok", "none"),
        (5.0, None, False, "watch", "notify"),
        (9.99, 90, False, "watch", "notify"),
        (10.0, None, False, "clean_recommended", "clean"),
        (15.0, 80, False, "clean_recommended", "defer"),  # rain deferral
        (15.0, 59, False, "clean_recommended", "clean"),  # just under rain threshold
        (15.0, None, True, "clean_recommended", "wait"),  # cleaning already running
        (20.0, 100, False, "critical", "clean"),  # critical ignores rain
        (25.0, None, True, "critical", "wait"),
    ],
)
def test_decide(loss, rain, active, level, action):
    d = decide(loss, rain, active)
    assert (d.level, d.action) == (level, action)


def test_electrical_loss():
    assert electrical_loss_pct(8.0, 10.0) == pytest.approx(20.0)
    assert electrical_loss_pct(0.0, 0.01) == 0.0  # night guard
    assert electrical_loss_pct(11.0, 10.0) == 0.0  # never negative
    assert electrical_loss_pct(4.0, 5.0, baseline=0.8) == pytest.approx(0.0)  # baseline absorbed


def test_combined_weights():
    assert combined_loss(50, 10) == pytest.approx(34.0)  # .6*50 + .4*10
    assert combined_loss(None, 10) == 10  # no image


class FakeMqtt:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos=0):
        self.sent.append((topic, json.loads(payload)))


def add_readings(db, test_w, ref_w, n=5, age_s=0):
    ts = now() - timedelta(seconds=age_s)
    for _ in range(n):
        db.add(
            PanelReading(
                panel_type="test", voltage=18, current=test_w / 18, power=test_w, timestamp=ts
            )
        )
        db.add(
            PanelReading(
                panel_type="reference", voltage=18, current=ref_w / 18, power=ref_w, timestamp=ts
            )
        )
    db.commit()


@pytest.fixture()
def quiet(monkeypatch):
    alerts = []
    monkeypatch.setattr(de, "max_rain_probability_24h", lambda: None)
    monkeypatch.setattr(de, "notify", alerts.append)
    monkeypatch.setattr(settings, "settle_s", 0)
    return alerts


def test_full_cycle_trigger_verify(db, quiet, monkeypatch):
    """Rising loss -> trigger -> no double trigger -> cleaning -> verification."""
    mqtt = FakeMqtt()
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=30)  # 25% loss -> critical (pre-clean)

    de.evaluate_once(db, mqtt)
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]
    cyc = db.get(CleaningCycle, mqtt.sent[0][1]["cycle_id"])
    assert cyc.status == "triggered" and cyc.pre_loss == pytest.approx(25.0)

    de.evaluate_once(db, mqtt)  # cycle still active -> must not trigger again
    assert len(mqtt.sent) == 1

    # mqtt "done" arrives 10 s ago; old dirty readings are KEPT in the table on purpose:
    # verification must ignore anything before the settle window.
    cyc.status, cyc.completed_at = "verifying", now() - timedelta(seconds=10)
    monkeypatch.setattr(settings, "settle_s", 5)
    db.commit()
    add_readings(db, test_w=9.9, ref_w=10.0, age_s=1)  # clean again, after settling
    de.verify_cycles(db)
    db.refresh(cyc)
    assert (cyc.status, cyc.result) == ("complete", "success")
    assert cyc.post_loss == pytest.approx(1.0)
    assert any("critical" in a for a in quiet)  # level-change alert fired


def test_verify_waits_for_settle_time(db, quiet, monkeypatch):
    monkeypatch.setattr(settings, "settle_s", 60)
    add_readings(db, 9.9, 10.0)
    cyc = CleaningCycle(pre_loss=20, status="verifying", completed_at=now())
    db.add(cyc)
    db.commit()
    de.verify_cycles(db)
    assert cyc.status == "verifying"


def test_stale_cycle_is_expired(db, quiet):
    old = CleaningCycle(pre_loss=20, status="running", triggered_at=now() - timedelta(hours=1))
    db.add(old)
    db.commit()
    de.expire_stale_cycles(db)
    assert (old.status, old.result) == ("complete", "insufficient")
    assert de.active_cycle(db) is None


def test_verify_needs_post_clean_samples(db, quiet):
    """Dirty pre-clean readings only -> stay in verifying instead of a false 'insufficient'."""
    add_readings(db, 7.5, 10.0, age_s=30)
    cyc = CleaningCycle(pre_loss=25, status="verifying", completed_at=now() - timedelta(seconds=10))
    db.add(cyc)
    db.commit()
    de.verify_cycles(db)
    assert cyc.status == "verifying"
