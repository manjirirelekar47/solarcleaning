import json
from pathlib import Path

import pytest
from app import vision
from app.config import settings

from tests import b_images as imgs


@pytest.fixture(autouse=True)
def _no_model(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "model_path", Path(str(tmp_path / "missing.pt")))
    monkeypatch.setattr(settings, "severity_map_path", Path(str(tmp_path / "no_map.json")))
    monkeypatch.setattr(settings, "allow_stub_decisions", False)
    vision.get_bundle(reload=True)
    yield
    vision.get_bundle(reload=True)


def test_load_image_applies_exif_rotation():
    img = vision.load_image(imgs.rotated_bytes(200, 100))
    assert img.size == (100, 200)  # width and height swapped


def test_load_image_rejects_garbage_and_empty():
    with pytest.raises(vision.ImageError):
        vision.load_image(b"not an image")
    with pytest.raises(vision.ImageError):
        vision.load_image(b"")


def test_quality_accepts_panel_and_rejects_bad_frames():
    assert vision.assess_quality(imgs.panel_image()) is None
    assert "dark" in vision.assess_quality(vision.load_image(imgs.dark_bytes()))
    assert "blank" in vision.assess_quality(vision.load_image(imgs.blank_bytes()))
    assert "sky" in vision.assess_quality(vision.load_image(imgs.sky_bytes()))


def test_quality_rejects_tiny_image():
    from PIL import Image

    assert "small" in vision.assess_quality(Image.new("RGB", (32, 32), (10, 10, 10)))


def test_stub_mode_is_flagged_and_never_used():
    _, result = vision.analyze_bytes(imgs.panel_bytes())
    assert result.model == "stub"
    assert result.model_version is None
    assert result.used is False
    assert "stub" in result.note
    assert result.severity_pct is None  # no calibration map


def test_stub_can_be_allowed_explicitly(monkeypatch):
    monkeypatch.setattr(settings, "allow_stub_decisions", True)
    _, result = vision.analyze_bytes(imgs.panel_bytes())
    assert result.used is True


def test_bad_frame_is_stored_but_flagged_unused(monkeypatch):
    monkeypatch.setattr(settings, "allow_stub_decisions", True)
    _, result = vision.analyze_bytes(imgs.sky_bytes())
    assert result.used is False
    assert "sky" in result.note


def _fake_cnn(monkeypatch, label, conf, probs):
    monkeypatch.setattr(
        vision, "get_bundle", lambda reload=False: {"version": "t1", "classes": list(probs)}
    )
    monkeypatch.setattr(vision, "_classify_cnn", lambda img, b: (label, conf, probs))


def test_low_confidence_cnn_result_is_unused(monkeypatch):
    monkeypatch.setattr(settings, "min_conf", float("0.7"))
    _fake_cnn(monkeypatch, "dusty", 0.55, {"clean": 0.45, "dusty": 0.55})
    _, result = vision.analyze_bytes(imgs.panel_bytes())
    assert result.model == "cnn" and result.model_version == "t1"
    assert result.used is False and "low confidence" in result.note


def test_confident_cnn_result_is_used_and_severity_is_expected_value(monkeypatch):
    monkeypatch.setattr(settings, "min_conf", float("0.7"))
    _fake_cnn(monkeypatch, "dusty", 0.9, {"clean": 0.1, "dusty": 0.9})
    _, result = vision.analyze_bytes(imgs.panel_bytes())
    assert result.used is True and result.note is None
    assert result.severity_score == pytest.approx(45.0)  # 0.9 * 50


def test_severity_map_interpolates_and_clips(monkeypatch, tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"breakpoints": [[0, 0], [50, 8], [100, 30]]}))
    monkeypatch.setattr(settings, "severity_map_path", Path(str(path)))
    assert vision.calibrated_severity(25) == pytest.approx(4.0)
    assert vision.calibrated_severity(200) == pytest.approx(30.0)


def test_bad_severity_map_is_ignored(monkeypatch, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"breakpoints": [[10, 1], [10, 2]]}))
    monkeypatch.setattr(settings, "severity_map_path", Path(str(path)))
    assert vision.calibrated_severity(10) is None
    path.write_text("{not json")
    monkeypatch.setattr(settings, "severity_map_path", Path(str(path)))
    assert vision.calibrated_severity(10) is None
