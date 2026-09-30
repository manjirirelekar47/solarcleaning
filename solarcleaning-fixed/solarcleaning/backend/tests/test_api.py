"""Core API behaviour: health, empty database, upload, alerts, manual cleaning."""

import io

import pytest
from app import api
from app.config import settings
from app.models import Alert, CleaningCycle, PanelReading, SoilingEvent
from PIL import Image


def jpeg(color=(30, 60, 120), size=128):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG")
    return buf.getvalue()


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_status_on_empty_db_does_not_crash(client):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["level"] is None and body["latest_image"] is None
    assert body["device_health"] == "unknown" and body["cleaning_active"] is False


def test_empty_list_endpoints(client):
    for path in ("/soiling-events", "/alerts", "/cleaning-cycles", "/images", "/device-health"):
        assert client.get(path).json() == []
    assert client.get("/decision").json() is None
    assert client.get("/images/latest").status_code == 404


def test_upload_stores_image_and_shows_it_in_status(client):
    r = client.post("/images", files={"file": ("a.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 201
    body = r.json()
    assert body["context"] == "routine" and body["model"] == "stub"
    assert body["used"] is False  # the stub model never drives decisions
    status = client.get("/status").json()
    assert status["latest_image"]["id"] == body["id"]
    assert client.get(body["url"]).status_code == 200


def test_upload_rejects_non_image(client):
    r = client.post("/images", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 422


def test_status_reflects_the_latest_event(client, db):
    db.add(PanelReading(panel_type="reference", voltage=18, current=0.4, power=7, temp=30))
    db.add(
        SoilingEvent(
            combined_loss=12,
            electrical_loss=10,
            alert_level="clean_recommended",
            action="clean",
            reason="cnn_only",
            net_benefit_inr=250.0,
        )
    )
    db.commit()
    s = client.get("/status").json()
    assert (s["level"], s["action"], s["reason"]) == ("clean_recommended", "clean", "cnn_only")
    assert s["net_benefit_inr"] == 250.0 and s["data_age_s"] is not None
    assert client.get("/alerts").json() == []  # an event is not an alert, only transitions are
    db.add(Alert(kind="level_change", level="clean_recommended", message="level changed"))
    db.commit()
    row = client.get("/alerts").json()[0]
    assert (row["kind"], row["level"], row["message"]) == (
        "level_change",
        "clean_recommended",
        "level changed",
    )


@pytest.fixture()
def keyed(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "s3cret")
    return {"X-API-Key": "s3cret"}


def test_manual_clean_needs_the_api_key(client, monkeypatch):
    monkeypatch.setattr(api.mqtt_client.client, "publish", lambda *a, **k: None)
    monkeypatch.setattr(settings, "api_key", "s3cret")
    assert client.post("/trigger-clean").status_code == 401
    assert client.post("/trigger-clean", headers={"X-API-Key": "nope"}).status_code == 401


def test_manual_clean_fails_closed_without_a_configured_key(client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    assert client.post("/trigger-clean", headers={"X-API-Key": "anything"}).status_code == 503


def test_manual_clean_blocked_while_cycle_active(client, db, monkeypatch, keyed):
    sent = []
    monkeypatch.setattr(api.mqtt_client.client, "publish", lambda *a, **k: sent.append(a))
    assert client.post("/trigger-clean", headers=keyed).status_code == 200
    assert len(sent) == 1 and sent[0][0] == "cleaning/trigger"
    assert client.post("/trigger-clean", headers=keyed).status_code == 409
    assert db.query(CleaningCycle).count() == 1
    assert client.get("/cleaning-cycles").json()[0]["status"] == "triggered"
