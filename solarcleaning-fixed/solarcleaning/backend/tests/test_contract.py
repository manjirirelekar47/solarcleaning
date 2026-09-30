"""The dashboard's TypeScript types (dashboard/src/types.ts) must match what the API returns."""

import io
import re
from datetime import timedelta
from pathlib import Path

from app.models import Alert, CleaningCycle, PanelReading, SoilingEvent, now
from PIL import Image

TYPES = (Path(__file__).resolve().parents[2] / "dashboard" / "src" / "types.ts").read_text()


def ts_keys(interface: str) -> set[str]:
    body = re.search(rf"export interface {interface} \{{(.*?)\n\}}", TYPES, re.S).group(1)
    return set(re.findall(r"^\s+(\w+)\??:", body, re.M))


def test_status_and_image_shapes_match_types(client, db):
    buf = io.BytesIO()
    Image.new("RGB", (128, 128), (10, 20, 30)).save(buf, "JPEG")
    assert client.post("/images", files={"file": ("a.jpg", buf.getvalue())}).status_code == 201
    db.add(SoilingEvent(combined_loss=1.0, electrical_loss=1.0, alert_level="ok", action="none"))
    db.commit()
    body = client.get("/status").json()
    assert set(body) == ts_keys("Status")
    assert set(body["latest_image"]) == ts_keys("ImageInfo")


def test_alert_trend_and_readings_shapes_match_types(client, db):
    t = now()
    db.add(Alert(kind="level_change", level="ok", message="m", timestamp=t))
    db.add(SoilingEvent(combined_loss=1.0, electrical_loss=1.0, alert_level="ok", action="none"))
    db.add(CleaningCycle(pre_loss=9.0, post_loss=1.0, status="complete", result="success"))
    for panel in ("test", "reference"):
        db.add(
            PanelReading(
                panel_type=panel,
                voltage=18,
                current=0.3,
                power=5.0,
                timestamp=t - timedelta(seconds=5),
            )
        )
    db.commit()
    assert set(client.get("/alerts").json()[0]) == ts_keys("AlertRow")
    trend = client.get("/loss-trend?range=1h&bucket=1m").json()
    assert set(trend) == ts_keys("LossTrend")
    assert set(trend["points"][0]) == ts_keys("TrendPoint")
    assert set(trend["cleanings"][0]) == ts_keys("CleaningMarker")
    readings = client.get("/readings").json()
    assert set(readings) == ts_keys("Readings")
    assert set(readings["points"][0]) == ts_keys("ReadingPoint")
