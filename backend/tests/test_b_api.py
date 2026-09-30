import time
from datetime import timedelta
from pathlib import Path

import pytest
from app import api as api_module
from app import vision
from app.api import aligned_start, bucketize
from app.config import settings
from app.db import Base, get_db
from app.models import Alert, CleaningCycle, DeviceState, PanelReading, SoilingEvent
from app.models import now as utc_now
from app.security import require_api_key
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests import b_images as imgs


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def client(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "images_dir", Path(str(tmp_path / "images")))
    monkeypatch.setattr(settings, "model_path", Path(str(tmp_path / "missing.pt")))
    monkeypatch.setattr(settings, "severity_map_path", Path(str(tmp_path / "none.json")))
    monkeypatch.setattr(settings, "allow_stub_decisions", False)
    monkeypatch.setattr(settings, "max_upload_mb", 5)
    vision.get_bundle(reload=True)

    app = FastAPI()
    app.include_router(api_module.router)

    def override():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _add(session_factory, *objs):
    with session_factory() as db:
        db.add_all(objs)
        db.commit()


def _upload(client, data, context="routine", name="frame.jpg"):
    return client.post(
        "/images", files={"file": (name, data, "image/jpeg")}, data={"context": context}
    )


# ------------------------------------------------------------------ images


def test_upload_stores_row_and_flags_stub_unused(client):
    r = _upload(client, imgs.panel_bytes())
    assert r.status_code == 201
    body = r.json()
    assert body["model"] == "stub" and body["used"] is False
    assert body["context"] == "routine"
    file_resp = client.get(body["url"])
    assert file_resp.status_code == 200 and file_resp.headers["content-type"] == "image/jpeg"
    assert client.get("/images/latest").json()["id"] == body["id"]


def test_dark_and_sky_frames_are_stored_but_unused(client):
    for data, word in ((imgs.dark_bytes(), "dark"), (imgs.sky_bytes(), "sky")):
        body = _upload(client, data).json()
        assert body["used"] is False and word in body["note"]


def test_post_clean_context_is_kept(client):
    assert (
        _upload(client, imgs.panel_bytes(), context="post_clean").json()["context"] == "post_clean"
    )


def test_bad_uploads_are_rejected(client, monkeypatch):
    assert _upload(client, b"plain text, not an image").status_code == 422
    assert _upload(client, imgs.panel_bytes(), context="nonsense").status_code == 422
    monkeypatch.setattr(settings, "max_upload_mb", int("1"))
    assert _upload(client, b"\x00" * (1024 * 1024 + 10)).status_code == 413


def test_latest_image_404_when_empty_and_file_404_for_unknown(client):
    assert client.get("/images/latest").status_code == 404
    assert client.get("/images/999/file").status_code == 404


# ------------------------------------------------------------------ status


def test_status_on_empty_database(client):
    body = client.get("/status").json()
    assert body["level"] is None and body["data_age_s"] is None
    assert body["device_health"] == "unknown" and body["model"] == "stub"
    assert body["cleaning_active"] is False and body["latest_image"] is None


def test_status_reports_latest_event_health_and_data_age(client, session_factory):
    now = utc_now()
    _add(
        session_factory,
        SoilingEvent(
            timestamp=now - timedelta(minutes=5), level="watch", action="notify", combined_loss=6.0
        ),
        SoilingEvent(
            timestamp=now,
            level="clean_recommended",
            action="clean",
            combined_loss=12.5,
            electrical_loss=14.0,
            cnn_severity=9.0,
            reason="both signals high",
            net_benefit_inr=420.0,
        ),
        PanelReading(
            timestamp=now - timedelta(seconds=20),
            panel_type="test",
            voltage=18.0,
            current=0.1,
            power=3.0,
        ),
        DeviceState(source="test", last_seen=now, healthy=True),
        DeviceState(source="reference", last_seen=now, healthy=False, fault="no data"),
        CleaningCycle(pre_loss=10.0, triggered_at=now, status="running"),
    )
    body = client.get("/status").json()
    assert body["level"] == "clean_recommended" and body["net_benefit_inr"] == 420.0
    assert body["reason"] == "both signals high"
    assert 15 <= body["data_age_s"] <= 30
    assert body["device_health"] == "degraded"
    assert body["cleaning_active"] is True


# ------------------------------------------------------------------ trends


def test_bucketize_averages_and_skips_none():
    start = utc_now().replace(microsecond=0)
    rows = [
        (start + timedelta(seconds=1), 10.0, None),
        (start + timedelta(seconds=2), 20.0, 4.0),
        (start + timedelta(seconds=70), 30.0, None),
        (start - timedelta(seconds=5), 99.0, 99.0),  # before start: ignored
    ]
    out = bucketize(rows, start, 60, ["a", "b"])
    assert [p["a"] for p in out] == [15.0, 30.0]
    assert [p["b"] for p in out] == [4.0, None]


def test_loss_trend_returns_buckets_and_cleaning_markers(client, session_factory):
    now = utc_now()
    base = aligned_start(now, timedelta(hours=2), 900) + timedelta(seconds=30)  # inside a bucket
    _add(
        session_factory,
        SoilingEvent(
            timestamp=base,
            level="watch",
            action="notify",
            combined_loss=8.0,
            electrical_loss=9.0,
            cnn_severity=5.0,
        ),
        SoilingEvent(
            timestamp=base + timedelta(minutes=1),
            level="watch",
            action="notify",
            combined_loss=10.0,
            electrical_loss=None,
            cnn_severity=7.0,
        ),
        CleaningCycle(
            triggered_at=now - timedelta(hours=1),
            status="complete",
            completed_at=now,
            result="success",
            pre_loss=18.0,
            post_loss=2.0,
        ),
        SoilingEvent(
            timestamp=now - timedelta(days=3), level="ok", action="none", combined_loss=1.0
        ),
    )
    day = client.get("/loss-trend", params={"range": "24h", "bucket": "15m"}).json()
    assert day["bucket_s"] == 900 and len(day["points"]) == 1
    p = day["points"][0]
    assert p["combined"] == 9.0 and p["electrical"] == 9.0 and p["cnn"] == 6.0
    assert day["cleanings"] == [
        {"t": day["cleanings"][0]["t"], "pre": 18.0, "post": 2.0, "result": "success"}
    ]
    week = client.get("/loss-trend", params={"range": "7d", "bucket": "1h"}).json()
    assert len(week["points"]) == 2  # the 3-day-old event is inside 7 days


def test_trend_parameter_validation(client):
    assert client.get("/loss-trend", params={"range": "1y"}).status_code == 422
    assert client.get("/loss-trend", params={"bucket": "abc"}).status_code == 422
    assert client.get("/loss-trend", params={"bucket": "0m"}).status_code == 422
    assert client.get("/loss-trend", params={"range": "7d", "bucket": "1s"}).status_code == 422
    assert client.get("/readings", params={"panel": "sun"}).status_code == 422


def test_loss_trend_7d_is_fast_on_a_large_table(client, session_factory):
    now = utc_now()
    rows = [
        {
            "timestamp": now - timedelta(seconds=10 * i),
            "level": "ok",
            "action": "none",
            "combined_loss": float(i % 20),
            "electrical_loss": float(i % 15),
            "cnn_severity": 1.0,
        }
        for i in range(60_480)  # 7 days at one event per 10 s
    ]
    with session_factory() as db:
        db.bulk_insert_mappings(SoilingEvent, rows)
        db.commit()
    t0 = time.perf_counter()
    r = client.get("/loss-trend", params={"range": "7d", "bucket": "15m"})
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200 and len(r.json()["points"]) > 600
    assert elapsed < 1.0, f"took {elapsed:.2f}s"


def test_readings_merge_test_and_reference(client, session_factory):
    base = aligned_start(utc_now(), timedelta(minutes=10), 60) + timedelta(seconds=5)
    _add(
        session_factory,
        PanelReading(timestamp=base, panel_type="test", voltage=18.0, current=0.1, power=2.0),
        PanelReading(timestamp=base, panel_type="reference", voltage=18.0, current=0.1, power=3.0),
        PanelReading(
            timestamp=base + timedelta(seconds=20),
            panel_type="test",
            voltage=18.0,
            current=0.1,
            power=4.0,
        ),
    )
    both = client.get("/readings", params={"panel": "both", "bucket": "1m", "range": "1h"}).json()
    assert len(both["points"]) == 1
    assert both["points"][0]["test"] == 3.0 and both["points"][0]["reference"] == 3.0
    only = client.get("/readings", params={"panel": "test"}).json()
    assert "reference" not in only["points"][0] and only["points"][0]["test"] == 3.0


# ------------------------------------------------------------------ alerts, devices, security


def test_alerts_newest_first_and_limited(client, session_factory):
    now = utc_now()
    _add(
        session_factory,
        *[
            Alert(
                timestamp=now - timedelta(minutes=i),
                kind="level_change",
                level="watch",
                message=f"m{i}",
            )
            for i in range(5)
        ],
    )
    body = client.get("/alerts", params={"limit": 3}).json()
    assert [a["message"] for a in body] == ["m0", "m1", "m2"]
    assert client.get("/alerts", params={"limit": 0}).status_code == 422
    assert client.get("/alerts", params={"limit": 501}).status_code == 422


def test_device_health_lists_sources(client, session_factory):
    now = utc_now()
    _add(
        session_factory,
        DeviceState(source="test", last_seen=now, healthy=True),
        DeviceState(source="reference", last_seen=None, healthy=False, fault="offline"),
    )
    body = client.get("/device-health").json()
    assert [d["source"] for d in body] == ["reference", "test"]
    assert body[0]["fault"] == "offline" and body[0]["last_seen"] is None


@pytest.fixture()
def keyed_client():
    app = FastAPI()

    @app.post("/trigger-clean", dependencies=[Depends(require_api_key)])
    def trigger():
        return {"started": True}

    return TestClient(app)


def test_api_key_required(keyed_client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "s3cret")
    assert keyed_client.post("/trigger-clean").status_code == 401
    assert keyed_client.post("/trigger-clean", headers={"X-API-Key": "wrong"}).status_code == 401
    ok = keyed_client.post("/trigger-clean", headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200 and ok.json() == {"started": True}


def test_api_key_fails_closed_when_unconfigured(keyed_client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    assert keyed_client.post("/trigger-clean", headers={"X-API-Key": "anything"}).status_code == 503
