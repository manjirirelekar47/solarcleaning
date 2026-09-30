from app import api, mqtt_client
from app.config import settings
from app.main import app
from app.models import CleaningCycle, now
from fastapi.testclient import TestClient

client = TestClient(app)  # not used as a context manager -> no MQTT / scheduler


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_empty_lists():
    for path in ("/images", "/soiling-events", "/alerts", "/cleaning-cycles", "/device-health"):
        assert client.get(path).json() == []
    assert client.get("/decision").json() is None
    assert client.get("/status").status_code == 200


def test_manual_trigger_needs_key_and_publishes(fake_mqtt, monkeypatch):
    monkeypatch.setattr(mqtt_client, "client", fake_mqtt)
    monkeypatch.setattr(settings, "api_key", "k")
    assert client.post("/trigger-clean").status_code == 401
    r = client.post("/trigger-clean", headers={"X-API-Key": "k"})
    assert r.json()["ok"] is True
    assert fake_mqtt.published[0][0] == "cleaning/trigger"
    assert client.get("/cleaning-cycles").json()[0]["status"] == "triggered"


def test_manual_trigger_returns_409_while_a_cycle_is_active(db, fake_mqtt, monkeypatch):
    monkeypatch.setattr(mqtt_client, "client", fake_mqtt)
    monkeypatch.setattr(settings, "api_key", "k")
    db.add(CleaningCycle(pre_loss=10, status="running", triggered_at=now()))
    db.commit()
    assert client.post("/trigger-clean", headers={"X-API-Key": "k"}).status_code == 409
    assert fake_mqtt.published == []


def test_upload_uses_uuid_filename_and_lists_image(tmp_path, monkeypatch):
    import io

    from PIL import Image

    monkeypatch.setattr(settings, "images_dir", tmp_path)
    monkeypatch.setattr(api.vision.settings, "model_path", tmp_path / "none.pt")
    api.vision.get_bundle(reload=True)
    buf = io.BytesIO()
    Image.new("RGB", (128, 128), (200, 200, 200)).save(buf, "JPEG")
    r = client.post("/images", files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 201
    assert len(list(tmp_path.iterdir())) == 1
    assert client.get("/images").json()[0]["id"] == r.json()["id"]
