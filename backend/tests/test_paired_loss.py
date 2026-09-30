"""A5 (paired ratios, temperature correction) and A12 (time-of-day baseline) tests."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from app import decision_engine as de
from app.config import settings
from app.loss import baseline_at, hourly_baselines, pair_ratios, paired_electrical_loss
from test_decision_engine import add_readings

T0 = datetime(2026, 9, 30, 6, 30, tzinfo=timezone.utc)  # 12:00 in Asia/Kolkata


def series(powers, start=T0, step_s=10, temp=None):
    return [(start + timedelta(seconds=i * step_s), p, temp) for i, p in enumerate(powers)]


# ---------------- A5 ----------------
def test_transient_cloud_on_one_panel_does_not_create_false_loss():
    ref = series([10.0] * 9)
    test = series([8.0] * 4 + [4.0] + [8.0] * 4)  # clean baseline 0.8, one sample hit by a cloud
    assert paired_electrical_loss(test, ref, baseline=0.8) == pytest.approx(0.0)


def test_pairing_beats_median_of_powers_when_irradiance_moves():
    """Reference sampled all morning, test only in the second half: powers differ because of the
    sun, not soiling. Paired ratios see a true 20% loss; median-of-powers would say ~33%."""
    ref_p = [10 - 0.5 * i for i in range(10)]
    ref = series(ref_p)
    test = series([0.8 * p for p in ref_p[5:]], start=T0 + timedelta(seconds=50))
    assert paired_electrical_loss(test, ref) == pytest.approx(20.0)


def test_low_reference_power_returns_none():
    assert paired_electrical_loss(series([0.0] * 5), series([0.01] * 5)) is None


def test_unpaired_samples_are_ignored():
    ref = series([10.0] * 3)
    test = series([8.0] * 3, start=T0 + timedelta(seconds=120))  # far outside pair tolerance
    assert pair_ratios(test, ref) == [] and paired_electrical_loss(test, ref) is None


def test_min_pairs_enforced():
    ref, test = series([10.0] * 2), series([8.0] * 2)
    assert paired_electrical_loss(test, ref, min_pairs=3) is None
    assert paired_electrical_loss(test, ref, min_pairs=2) == pytest.approx(20.0)


def test_small_timestamp_offset_still_pairs():
    ref = series([10.0] * 5)
    test = [(ts + timedelta(seconds=1), p, None) for ts, p, _ in series([8.0] * 5)]
    assert paired_electrical_loss(test, ref) == pytest.approx(20.0)


def test_temperature_correction(monkeypatch):
    ref = series([10.0] * 5, temp=30.0)
    test = series([9.6] * 5, temp=40.0)  # 4% lower purely because it is 10 C hotter
    assert paired_electrical_loss(test, ref) == pytest.approx(4.0)  # off by default
    monkeypatch.setattr(settings, "temp_coeff", -0.004)
    assert paired_electrical_loss(test, ref) == pytest.approx(0.0, abs=1e-6)


def test_current_electrical_loss_uses_pairs(db):
    add_readings(db, test_w=8.0, ref_w=10.0, age_s=1)
    assert de.current_electrical_loss(db, min_samples=3) == pytest.approx(20.0)
    assert de.current_electrical_loss(db, since=de.now() + timedelta(seconds=5)) is None


# ---------------- A12 ----------------
def test_baseline_falls_back_without_a_table(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "baseline_hourly_path", tmp_path / "missing.json")
    monkeypatch.setattr(settings, "baseline_ratio", 0.97)
    assert baseline_at(T0) == 0.97


def test_baseline_uses_local_hour_and_falls_back_for_unknown_hours(tmp_path, monkeypatch):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"12": 0.90, "8": 0.95}))
    monkeypatch.setattr(settings, "baseline_hourly_path", f)
    monkeypatch.setattr(settings, "baseline_ratio", 1.0)
    assert baseline_at(T0) == 0.90  # 06:30 UTC = 12:00 IST
    assert baseline_at(T0 + timedelta(hours=2)) == 1.0  # 14:00 IST was never calibrated


def test_baseline_table_reloads_when_the_file_changes(tmp_path, monkeypatch):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"12": 0.90}))
    monkeypatch.setattr(settings, "baseline_hourly_path", f)
    assert baseline_at(T0) == 0.90
    f.write_text(json.dumps({"12": 0.85}))
    import os

    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    assert baseline_at(T0) == 0.85


def test_hourly_baseline_absorbs_a_daily_drift():
    """Clean-panel ratio drifts 1.00 -> 0.94 across the day; a per-hour table removes the error."""
    test, ref = [], []
    for hour, ratio in [(6, 1.00), (12, 0.97), (18, 0.94)]:  # UTC hours -> IST 11:30, 17:30, 23:30
        start = datetime(2026, 9, 30, hour, 0, tzinfo=timezone.utc)
        ref += series([10.0] * 4, start=start)
        test += series([10.0 * ratio] * 4, start=start)
    table = hourly_baselines(test, ref)
    assert table == {"11": 1.0, "17": 0.97, "23": 0.94}
    for hour, ratio in table.items():
        ts = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)
        assert paired_electrical_loss(
            series([10 * ratio] * 3, start=ts), series([10.0] * 3, start=ts), baseline=ratio
        ) == pytest.approx(0.0)


def test_hourly_baseline_skips_thin_hours():
    ref, test = series([10.0] * 2), series([9.5] * 2)
    assert hourly_baselines(test, ref, min_pairs=3) == {}
