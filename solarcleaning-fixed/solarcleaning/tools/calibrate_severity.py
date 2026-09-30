"""Fit CNN severity to measured electrical loss and write severity_map.json.

The CNN gives a raw 0-100 severity. This script learns how that raw number relates to the
power loss you actually measured, using a monotone (isotonic) fit, and writes breakpoints
that backend/app/vision.py interpolates to produce `severity_pct`.

Example:
  python tools/calibrate_severity.py --db postgresql+psycopg://solar:solar@localhost:5432/solar \
      --out ml/models/severity_map.json --baseline-ratio 0.98

Tables read (columns): image_captures(timestamp, label, severity_score, used),
panel_readings(timestamp, panel_type, power).
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import statistics
import sys
from datetime import datetime, timedelta

import numpy as np

FIXED_SEVERITY = {"clean": 0.0, "dusty": 50.0, "bird_drop": 60.0, "mixed": 80.0}  # = vision.py
PAIR_TOLERANCE_S = 5.0


# ---------------------------------------------------------------- pure functions


def to_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", ""))


def isotonic_fit(x: list[float], y: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Non-decreasing least-squares fit (pool adjacent violators).

    Duplicate x values are averaged first. Returns strictly increasing xs and fitted ys.
    """
    if len(x) != len(y) or not x:
        raise ValueError("x and y must be non-empty and the same length")
    groups: dict[float, list[float]] = {}
    for xi, yi in zip(x, y):
        groups.setdefault(float(xi), []).append(float(yi))
    xs = sorted(groups)
    blocks = [[statistics.fmean(groups[v]), float(len(groups[v])), 1] for v in xs]  # mean, w, n
    merged: list[list[float]] = []
    for blk in blocks:
        merged.append(blk)
        while len(merged) > 1 and merged[-2][0] > merged[-1][0]:
            b = merged.pop()
            a = merged.pop()
            w = a[1] + b[1]
            merged.append([(a[0] * a[1] + b[0] * b[1]) / w, w, a[2] + b[2]])
    ys: list[float] = []
    for mean, _, n in merged:
        ys.extend([mean] * int(n))
    return np.array(xs), np.array(ys)


def pearson(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def electrical_loss_at(
    t: datetime,
    test: list[tuple[datetime, float]],
    ref: list[tuple[datetime, float]],
    window_s: float,
    min_ref_w: float,
    baseline_ratio: float,
) -> float | None:
    """Median of per-pair test/reference ratios around t, as a loss percent (0-100)."""
    if not test or not ref:
        return None
    ref_times = [r[0] for r in ref]
    lo, hi = t - timedelta(seconds=window_s), t + timedelta(seconds=window_s)
    ratios = []
    for ts, p_test in test:
        if not (lo <= ts <= hi):
            continue
        i = bisect.bisect_left(ref_times, ts)
        candidates = [j for j in (i - 1, i) if 0 <= j < len(ref)]
        if not candidates:
            continue
        j = min(candidates, key=lambda k: abs((ref_times[k] - ts).total_seconds()))
        if abs((ref_times[j] - ts).total_seconds()) > PAIR_TOLERANCE_S:
            continue
        if ref[j][1] >= min_ref_w:
            ratios.append(p_test / ref[j][1])
    if not ratios:
        return None
    loss = (1.0 - statistics.median(ratios) / baseline_ratio) * 100.0
    return float(min(max(loss, 0.0), 100.0))


def build_samples(images, test, ref, window_s, min_ref_w, baseline_ratio):
    """images: (timestamp, label, raw_severity). Returns (raw, fixed, loss) lists."""
    raw, fixed, loss = [], [], []
    for ts, label, sev in images:
        e = electrical_loss_at(ts, test, ref, window_s, min_ref_w, baseline_ratio)
        if e is None or sev is None:
            continue
        raw.append(float(sev))
        fixed.append(FIXED_SEVERITY.get(label, 0.0))
        loss.append(e)
    return raw, fixed, loss


def cross_validated_predictions(raw: list[float], loss: list[float], folds: int = 5) -> list[float]:
    """Out-of-fold calibrated predictions, so the reported correlation is not in-sample."""
    preds = [float("nan")] * len(raw)
    for k in range(folds):
        tr = [i for i in range(len(raw)) if i % folds != k]
        te = [i for i in range(len(raw)) if i % folds == k]
        if not tr or len({raw[i] for i in tr}) < 2:
            continue
        xs, ys = isotonic_fit([raw[i] for i in tr], [loss[i] for i in tr])
        for i in te:
            preds[i] = float(np.interp(raw[i], xs, ys))
    return preds


def calibrate(raw, fixed, loss, min_points: int = 15) -> dict:
    if len(raw) < min_points:
        raise ValueError(
            f"only {len(raw)} paired samples; need at least {min_points}. "
            "Log more images together with panel readings, or keep the fixed severity table."
        )
    xs, ys = isotonic_fit(raw, loss)
    if len(xs) < 2:
        raise ValueError("all raw severities are identical: cannot fit a curve")
    calibrated = [float(np.interp(r, xs, ys)) for r in raw]
    cv = cross_validated_predictions(raw, loss)
    keep = [i for i, p in enumerate(cv) if p == p]  # drop NaN
    return {
        "breakpoints": [[round(float(a), 3), round(float(b), 3)] for a, b in zip(xs, ys)],
        "n_samples": len(raw),
        "corr_fixed_table": pearson(fixed, loss),
        "corr_calibrated_in_sample": pearson(calibrated, loss),
        "corr_calibrated_cross_validated": pearson([cv[i] for i in keep], [loss[i] for i in keep]),
    }


# ---------------------------------------------------------------- database access


def load_from_db(db_url: str, only_cnn: bool):
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    img_sql = (
        "SELECT timestamp, label, severity_score FROM image_captures "
        "WHERE used = :used AND severity_score IS NOT NULL"
        + (" AND model = 'cnn'" if only_cnn else "")
        + " ORDER BY timestamp"
    )
    with engine.connect() as conn:
        images = [(to_dt(r[0]), r[1], r[2]) for r in conn.execute(text(img_sql), {"used": True})]
        readings = conn.execute(
            text("SELECT timestamp, panel_type, power FROM panel_readings ORDER BY timestamp")
        ).fetchall()
    test = [(to_dt(r[0]), float(r[2])) for r in readings if r[1] == "test" and r[2] is not None]
    ref = [(to_dt(r[0]), float(r[2])) for r in readings if r[1] == "reference" and r[2] is not None]
    return images, test, ref


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--db", default=os.getenv("DB_URL"), help="SQLAlchemy URL")
    ap.add_argument("--out", default="ml/models/severity_map.json")
    ap.add_argument("--window-s", type=float, default=120.0)
    ap.add_argument("--min-ref-w", type=float, default=0.5)
    ap.add_argument(
        "--baseline-ratio",
        type=float,
        default=1.0,
        help="test/reference ratio when both panels are clean",
    )
    ap.add_argument("--min-points", type=int, default=15)
    ap.add_argument("--all-models", action="store_true", help="include stub-model images")
    args = ap.parse_args(argv)
    if not args.db:
        print("error: pass --db or set DB_URL", file=sys.stderr)
        return 2
    if args.baseline_ratio <= 0:
        print("error: --baseline-ratio must be positive", file=sys.stderr)
        return 2

    images, test, ref = load_from_db(args.db, only_cnn=not args.all_models)
    raw, fixed, loss = build_samples(
        images, test, ref, args.window_s, args.min_ref_w, args.baseline_ratio
    )
    try:
        result = calibrate(raw, fixed, loss, args.min_points)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=2)
    fmt = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
    print(f"Wrote {args.out} from {result['n_samples']} samples")
    print(f"  correlation, fixed 0/50/60 table : {fmt(result['corr_fixed_table'])}")
    print(f"  correlation, calibrated (in-sample): {fmt(result['corr_calibrated_in_sample'])}")
    print(
        f"  correlation, calibrated (5-fold CV): {fmt(result['corr_calibrated_cross_validated'])}"
    )
    print("Quote the cross-validated figure on the slide.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
