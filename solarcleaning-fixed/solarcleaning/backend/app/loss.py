"""Electrical loss vs. the clean reference panel, and the combined CNN + electrical score."""

import json
import statistics
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from .config import settings

# A sample is (timestamp, power_w, temp_c or None)
Sample = tuple[datetime, float, float | None]


def electrical_loss_pct(p_test: float, p_ref: float, baseline: float = 1.0) -> float | None:
    """Loss vs. a clean panel. baseline = P_test/P_ref measured when both are clean.

    Returns None (unknown) when the reference gives no usable light signal (night, heavy
    overcast). 0.0 would read as "no loss", which is a different statement.
    """
    if p_ref < settings.min_ref_power_w:
        return None
    ratio = (p_test / p_ref) / baseline
    return max(0.0, min(100.0, (1 - ratio) * 100))


def combined_loss(severity: float | None, electrical: float) -> float:
    if severity is None:  # no recent image: rely on electrical only
        return electrical
    return 0.6 * severity + 0.4 * electrical


# ---------------- A5: paired ratios ----------------
def _pairs(test: list[Sample], ref: list[Sample]) -> list[tuple[datetime, float]]:
    out = []
    for ts, p_t, temp_t in test:
        if not ref:
            break
        r_ts, p_r, temp_r = min(ref, key=lambda s: abs((s[0] - ts).total_seconds()))
        if abs((r_ts - ts).total_seconds()) > settings.pair_tolerance_s:
            continue
        if p_r < settings.min_ref_power_w:
            continue
        if settings.temp_coeff and temp_t is not None and temp_r is not None:
            p_t = p_t / (1 + settings.temp_coeff * (temp_t - temp_r))
        out.append((ts, p_t / p_r))
    return out


def pair_ratios(test: list[Sample], ref: list[Sample]) -> list[float]:
    """test/reference power ratio for every test sample that has a reference sample within
    pair_tolerance_s. Pairs whose reference is too dark are dropped (night / heavy overcast).

    Optional temperature correction (temp_coeff != 0): the test power is referred to the
    reference panel's temperature before dividing.
    """
    return [ratio for _, ratio in _pairs(test, ref)]


def paired_electrical_loss(
    test: list[Sample], ref: list[Sample], baseline: float = 1.0, min_pairs: int = 1
) -> float | None:
    """Median of per-pair ratios, so one sample where a cloud or bird hit a single panel cannot
    move the result. None when there are too few usable pairs (night, no data)."""
    ratios = pair_ratios(test, ref)
    if len(ratios) < min_pairs or not ratios:
        return None
    ratio = statistics.median(ratios) / baseline
    return max(0.0, min(100.0, (1 - ratio) * 100))


# ---------------- A12: time-of-day baseline ----------------
@lru_cache(maxsize=4)
def _load_hourly(path: str, mtime: float) -> dict[str, float]:
    with open(path) as f:
        return {str(k): float(v) for k, v in json.load(f).items()}


def baseline_at(ts: datetime) -> float:
    """Baseline P_test/P_ref for the hour of `ts` (local time). Falls back to the single
    BASELINE_RATIO when there is no hourly table or that hour was never calibrated."""
    path = settings.baseline_hourly_path
    try:
        table = _load_hourly(str(path), path.stat().st_mtime)
    except (OSError, ValueError):
        return settings.baseline_ratio
    hour = ts.astimezone(ZoneInfo(settings.timezone)).hour
    return table.get(str(hour), settings.baseline_ratio)


def hourly_baselines(test: list[Sample], ref: list[Sample], min_pairs: int = 3) -> dict[str, float]:
    """Median clean-panel ratio per local hour, from samples taken while both panels were clean.
    Hours with fewer than `min_pairs` usable pairs are left out (they use BASELINE_RATIO)."""
    zone = ZoneInfo(settings.timezone)
    buckets: dict[int, list[float]] = {}
    for ts, ratio in _pairs(test, ref):
        buckets.setdefault(ts.astimezone(zone).hour, []).append(ratio)
    return {
        str(h): round(statistics.median(v), 4)
        for h, v in sorted(buckets.items())
        if len(v) >= min_pairs
    }
