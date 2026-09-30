import io

from app.models import Alert, CleaningCycle, PanelReading, SoilingEvent
from PIL import Image


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "PNG")
    return buf.getvalue()


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_status_on_empty_db_does_not_crash(client):
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json() == {"event": None, "image": None, "env": None, "cleaning": None}


def test_empty_list_endpoints(client):
    for path in ("/readings", "/soiling-events", "/alerts", "/cleaning-cycles", "/images"):
        assert client.get(path).json() == []
    assert client.get("/decision").json() is None


def test_upload_image_classifies_and_shows_in_status(client):
    r = client.post("/images", files={"file": ("dusty.png", png("white"), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["class"] == "dusty" and body["severity"] > 20
    s = client.get("/status").json()
    assert s["image"]["url"].startswith("/media/") and s["image"]["class"] == "dusty"


def test_upload_rejects_non_image(client):
    r = client.post("/images", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400


def test_status_reflects_event_and_env(client, db):
    db.add(
        PanelReading(panel_type="reference", voltage=18, current=0.4, power=7, temp=30, humidity=45)
    )
    db.add(
        SoilingEvent(
            combined_loss=12,
            electrical_loss=10,
            cnn_severity=None,
            alert_level="clean_recommended",
            action="clean",
        )
    )
    db.commit()
    s = client.get("/status").json()
    assert s["event"]["level"] == "clean_recommended" and s["env"]["temp"] == 30
    assert client.get("/alerts").json() == []  # A9: an event is not an alert, only transitions are
    db.add(Alert(kind="level_change", level="clean_recommended", message="level changed"))
    db.commit()
    row = client.get("/alerts").json()[0]
    assert (row["kind"], row["level"], row["message"]) == (
        "level_change",
        "clean_recommended",
        "level changed",
    )


def test_manual_clean_blocked_while_cycle_active(client, db, monkeypatch):
    from app import api

    sent = []
    monkeypatch.setattr(api.mqtt_client.client, "publish", lambda *a, **k: sent.append(a))
    assert client.post("/trigger-clean").status_code == 200
    assert len(sent) == 1 and sent[0][0] == "cleaning/trigger"
    assert client.post("/trigger-clean").status_code == 409
    assert db.query(CleaningCycle).count() == 1
    assert client.get("/cleaning-cycles").json()[0]["status"] == "triggered"
