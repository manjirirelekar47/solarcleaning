import io

from app import vision
from app.config import settings
from PIL import Image


def img_bytes(color):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, "PNG")
    return buf.getvalue()


def test_stub_dark_is_clean_bright_is_dusty():
    stub = vision.StubClassifier()
    assert stub.predict(img_bytes("black"))[0] == "clean"
    label, _, sev = stub.predict(img_bytes("white"))
    assert label == "dusty" and 0 <= sev <= 100


def test_loader_falls_back_to_stub_without_model(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "model_path", tmp_path / "missing.pt")
    assert isinstance(vision.load_classifier(), vision.StubClassifier)
