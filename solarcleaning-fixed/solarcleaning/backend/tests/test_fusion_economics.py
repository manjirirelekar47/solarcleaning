"""A6 (gated fusion) and A7 (cost-benefit) tests."""

import pytest
from app import alerts as alerts_mod
from app import decision_engine as de
from app import economics
from app.config import settings
from app.models import Alert, ImageCapture, SoilingEvent
from test_decision_engine import FakeMqtt, add_readings
from test_reliability import touch_both


@pytest.fixture()
def quiet(monkeypatch):
    sent = []
    monkeypatch.setattr(de, "max_rain_probability_24h", lambda: None)
    monkeypatch.setattr(alerts_mod, "notify", sent.append)
    monkeypatch.setattr(settings, "settle_s", 20)
    return sent


def add_image(db, cls, score):
    db.add(
        ImageCapture(image_path="x.jpg", soiling_class=cls, confidence=0.9, severity_score=score)
    )
    db.commit()


def run_once(db, ref_w, test_w, cls, score):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=test_w, ref_w=ref_w, age_s=1)
    add_image(db, cls, score)
    de.evaluate_once(db, mqtt)
    return mqtt, db.query(SoilingEvent).order_by(SoilingEvent.id.desc()).first()


# ---------------- A6 ----------------
def test_shadow_electrical_high_cnn_clean_is_notify_only(db, quiet):
    mqtt, ev = run_once(db, ref_w=10, test_w=7.0, cls="clean", score=0)  # 30% electrical
    assert mqtt.sent == []
    assert (ev.action, ev.reason) == ("notify", "electrical_only")
    assert ev.electrical_loss == pytest.approx(30.0) and ev.cnn_severity == 0
    assert db.query(Alert).filter(Alert.kind == "cleaning").one().level != "ok"


def test_misclassified_clean_panel_cnn_dusty_electrical_zero_is_notify_only(db, quiet):
    mqtt, ev = run_once(db, ref_w=10, test_w=10.0, cls="dusty", score=80)
    assert mqtt.sent == []
    assert ev.action in ("none", "notify")  # never "clean"
    assert ev.electrical_loss == pytest.approx(0.0)


def test_cnn_drives_level_but_meter_disagrees_gives_cnn_only(db, quiet, monkeypatch):
    monkeypatch.setattr(settings, "severity_loss_factor", 1.0)  # severity 80 -> 80% "loss"
    mqtt, ev = run_once(db, ref_w=10, test_w=9.5, cls="dusty", score=80)  # electrical 5%
    assert mqtt.sent == [] and (ev.action, ev.reason) == ("notify", "cnn_only")


def test_both_signals_high_cleans(db, quiet):
    mqtt, ev = run_once(db, ref_w=10, test_w=7.0, cls="dusty", score=50)
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]
    assert ev.action == "clean" and ev.reason is None


def test_calibrated_severity_pct_is_used_when_present(db, quiet):
    db.add(
        ImageCapture(
            image_path="x.jpg",
            soiling_class="dusty",
            confidence=0.9,
            severity_score=50,
            severity_pct=22.0,
        )
    )
    db.commit()
    touch_both(db)
    add_readings(db, test_w=7.0, ref_w=10.0, age_s=1)
    de.evaluate_once(db, FakeMqtt())
    ev = db.query(SoilingEvent).one()
    assert ev.cnn_severity == 22.0
    assert ev.combined_loss == pytest.approx(0.6 * 22.0 + 0.4 * 30.0)


def test_no_fresh_image_falls_back_to_electrical_only_and_can_clean(db, quiet):
    mqtt = FakeMqtt()
    touch_both(db)
    add_readings(db, test_w=7.5, ref_w=10.0, age_s=1)
    de.evaluate_once(db, mqtt)
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]


# ---------------- A7 ----------------
@pytest.fixture()
def plant_1mw(monkeypatch):
    for k, v in dict(
        plant_kwp=1000.0,
        peak_sun_hours=5.0,
        tariff_inr_per_kwh=8.0,
        water_cost_per_clean=2000.0,
        labor_cost_per_clean=500.0,
        pump_w=30.0,
        clean_duration_s=20,
        dry_days_horizon=3.0,
        rain_threshold_pct=60,
    ).items():
        monkeypatch.setattr(settings, k, v)


def test_worked_example_1mw_plant(plant_1mw):
    """12% loss on a 1 MW plant: 5000 kWh/day x 12% x INR 8 = INR 4,800 per day."""
    assert economics.daily_energy_kwh() == 5000
    assert economics.benefit_inr(12.0, 1.0) == pytest.approx(4800.0)
    assert economics.cleaning_cost_inr() == pytest.approx(2500.0, abs=0.01)
    assert economics.net_benefit_inr(12.0, None) == pytest.approx(4800 * 3 - 2500, abs=0.01)
    assert economics.net_benefit_inr(12.0, 80) == pytest.approx(4800 - 2500, abs=0.01)


def test_small_loss_or_imminent_rain_does_not_pay(plant_1mw):
    assert economics.net_benefit_inr(1.0, 80) < 0  # 1% loss, rain tomorrow: ~INR 400 vs 2,500
    assert economics.net_benefit_inr(10.0, None) > 0


def test_small_plant_not_cleaned_when_cost_exceeds_benefit(db, quiet, monkeypatch):
    monkeypatch.setattr(settings, "plant_kwp", 0.02)  # the 20 W demo panel, real water cost
    monkeypatch.setattr(settings, "water_cost_per_clean", 50.0)
    mqtt, ev = run_once(db, ref_w=10, test_w=7.0, cls="dusty", score=50)
    assert mqtt.sent == []
    assert (ev.action, ev.reason) == ("notify", "cost_exceeds_benefit")
    assert ev.net_benefit_inr < 0


def test_same_situation_on_big_plant_is_cleaned_and_net_benefit_stored(db, quiet, plant_1mw):
    mqtt, ev = run_once(db, ref_w=10, test_w=7.0, cls="dusty", score=50)
    assert [t for t, _ in mqtt.sent] == ["cleaning/trigger"]
    assert ev.net_benefit_inr > 0
