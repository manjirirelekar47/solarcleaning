"""Measure BASELINE_RATIO. Run with BOTH panels physically clean.

Usage (from repo root, simulator or ESP32 publishing): python tools/calibrate.py [--minutes 5]
Paste the printed value into .env as BASELINE_RATIO.
"""

import argparse
import statistics
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import SessionLocal  # noqa: E402
from app.models import PanelReading, now  # noqa: E402
from sqlalchemy import select  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=int, default=5)
args = ap.parse_args()

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
