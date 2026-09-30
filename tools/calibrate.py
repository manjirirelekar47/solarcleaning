"""Measure BASELINE_RATIO. Run with BOTH panels physically clean.

Usage (from repo root, simulator or ESP32 publishing):
  python tools/calibrate.py [--minutes 5]          single value: paste into .env as BASELINE_RATIO
  python tools/calibrate.py --hourly [--hours 24]  A12: one ratio per hour of the day, written to
                                                   ml/artifacts/baseline_hourly.json (used
                                                   automatically; hours without data fall back to
                                                   BASELINE_RATIO)
"""

import argparse
import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.loss import hourly_baselines  # noqa: E402
from app.models import PanelReading, aware, now  # noqa: E402
from sqlalchemy import select  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=int, default=5)
ap.add_argument("--hourly", action="store_true", help="per-hour baseline table (A12)")
ap.add_argument("--hours", type=int, default=24, help="history used by --hourly")
args = ap.parse_args()

if args.hourly:
    since = now() - timedelta(hours=args.hours)
    with SessionLocal() as db:
        rows = db.execute(
            select(
                PanelReading.panel_type,
                PanelReading.timestamp,
                PanelReading.power,
                PanelReading.temp,
            ).where(PanelReading.timestamp >= since)
        ).all()
    test = [(aware(ts), p, tmp) for kind, ts, p, tmp in rows if kind == "test"]
    ref = [(aware(ts), p, tmp) for kind, ts, p, tmp in rows if kind == "reference"]
    table = hourly_baselines(test, ref)
    if not table:
        sys.exit(f"Not enough daylight data in the last {args.hours} h to build a table.")
    settings.baseline_hourly_path.parent.mkdir(parents=True, exist_ok=True)
    settings.baseline_hourly_path.write_text(json.dumps(table, indent=2))
    for hour, ratio in table.items():
        print(f"{int(hour):02d}:00  {ratio:.4f}")
    print(f"wrote {settings.baseline_hourly_path}")
    sys.exit(0)

since = now() - timedelta(minutes=args.minutes)
with SessionLocal() as db:

    def med(panel):
        q = select(PanelReading.power).where(
            PanelReading.panel_type == panel, PanelReading.timestamp >= since
        )
        vals = db.scalars(q).all()
        return statistics.median(vals) if vals else None

    t, r = med("test"), med("reference")

if t is None or r is None or r < 0.05:
    sys.exit(f"Not enough daylight data in the last {args.minutes} min (test={t}, ref={r}).")
print(f"median P_test={t:.3f} W, P_ref={r:.3f} W")
print(f"BASELINE_RATIO={t / r:.4f}")
