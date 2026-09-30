"""Image quality checks, soiling classification and severity calibration.

Two modes:
  * "cnn"  : a trained MobileNetV3-small checkpoint is loaded from MODEL_PATH.
  * "stub" : no model available. A crude contrast heuristic labels the image, but the result
             is flagged unused so it can never drive a cleaning decision.
"""

from __future__ import annotations

import io
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import settings

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard

DEFAULT_CLASSES = ["clean", "dusty", "bird_drop", "mixed"]
# Fixed class -> severity table (0 to 100). Used when no calibration map exists.
FIXED_SEVERITY = {"clean": 0.0, "dusty": 50.0, "bird_drop": 60.0, "mixed": 80.0}
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


class ImageError(ValueError):
    """The bytes are not a usable image."""


@dataclass
class Analysis:
    model: str  # "cnn" or "stub"
    model_version: str | None
    label: str
    confidence: float
    severity_score: float  # raw 0..100
    severity_pct: float | None  # calibrated expected loss %, None without a map
    used: bool
    note: str | None
    probs: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------- image loading


def load_image(data: bytes) -> Image.Image:
    """Decode bytes, fix phone rotation from EXIF, return an RGB image."""
    if not data:
        raise ImageError("empty upload")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageError(f"cannot decode image: {exc}") from exc
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


# ---------------------------------------------------------------- quality checks


def assess_quality(img: Image.Image) -> str | None:
    """Return None if the frame looks usable, else a short reason it should be ignored."""
    w, h = img.size
    if min(w, h) < 64:
        return "image too small"
    small = img.resize((128, 128))
    arr = np.asarray(small, dtype=np.float32)
    gray = arr.mean(axis=2)
    if gray.mean() < 30:
        return "frame too dark"
    if gray.std() < 6:
        return "frame blank"
    # Sky-like frame: strongly blue, smooth, almost no edges (panels show cell grid lines).
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    blue_frac = float(np.mean((b > r + 30) & (b > g + 10)))
    grad = float(np.abs(np.diff(gray, axis=0)).mean() + np.abs(np.diff(gray, axis=1)).mean()) / 2
    if blue_frac > 0.7 and grad < 4.0:
        return "no panel visible (sky-like frame)"
    return None


# ---------------------------------------------------------------- severity mapping

_map_lock = threading.Lock()
_severity_cache: dict[str, Any] = {"path": None, "mtime": None, "xs": None, "ys": None}


def _load_severity_map() -> tuple[np.ndarray, np.ndarray] | None:
    path = Path(settings.severity_map_path)
    if not path.is_file():
        return None
    mtime = path.stat().st_mtime
    with _map_lock:
        if _severity_cache["path"] == str(path) and _severity_cache["mtime"] == mtime:
            xs, ys = _severity_cache["xs"], _severity_cache["ys"]
        else:
            try:
                pts = json.loads(path.read_text())["breakpoints"]
                xs = np.array([p[0] for p in pts], dtype=float)
                ys = np.array([p[1] for p in pts], dtype=float)
                if len(xs) < 2 or np.any(np.diff(xs) <= 0):
                    raise ValueError("breakpoints must have 2+ strictly increasing x values")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("Ignoring bad severity map %s: %s", path, exc)
                return None
            _severity_cache.update(path=str(path), mtime=mtime, xs=xs, ys=ys)
    return xs, ys


def calibrated_severity(raw: float) -> float | None:
    """Map raw 0..100 severity to an expected power-loss percent, or None if no map exists."""
    loaded = _load_severity_map()
    if loaded is None:
        return None
    xs, ys = loaded
    return float(np.clip(np.interp(raw, xs, ys), 0.0, 100.0))


# ---------------------------------------------------------------- CNN loading

_model_lock = threading.Lock()
_model_state: dict[str, Any] = {"loaded": False, "bundle": None}


def _build_net(num_classes: int):  # pragma: no cover - needs torch
    import torch.nn as nn
    from torchvision import models

    net = models.mobilenet_v3_small(weights=None)
    net.classifier[3] = nn.Linear(net.classifier[3].in_features, num_classes)
    return net


def _load_bundle() -> dict | None:
    path = Path(settings.model_path)
    if not path.is_file():
        return None
    try:  # pragma: no cover - needs torch
        import torch

        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        classes = list(ckpt["classes"])
        net = _build_net(len(classes))
        net.load_state_dict(ckpt["state_dict"])
        net.eval()
        return {
            "net": net,
            "classes": classes,
            "version": str(ckpt.get("model_version", "unknown")),
            "img_size": int(ckpt.get("img_size", 224)),
            "mean": tuple(ckpt.get("mean", IMAGENET_MEAN)),
            "std": tuple(ckpt.get("std", IMAGENET_STD)),
        }
    except Exception as exc:  # missing torch, bad file, shape mismatch
        log.warning("CNN model not loaded (%s). Falling back to stub.", exc)
        return None


def get_bundle(reload: bool = False) -> dict | None:
    with _model_lock:
        if reload or not _model_state["loaded"]:
            _model_state["bundle"] = _load_bundle()
            _model_state["loaded"] = True
        return _model_state["bundle"]


def model_info() -> dict:
    b = get_bundle()
    return {
        "model": "cnn" if b else "stub",
        "model_version": b["version"] if b else None,
        "classes": b["classes"] if b else DEFAULT_CLASSES,
    }


# ---------------------------------------------------------------- classification


def _classify_cnn(img: Image.Image, bundle: dict) -> tuple[str, float, dict[str, float]]:
    import torch  # pragma: no cover - needs torch

    size = bundle["img_size"]
    arr = np.asarray(img.resize((size, size)), dtype=np.float32) / 255.0
    arr = (arr - np.array(bundle["mean"], dtype=np.float32)) / np.array(
        bundle["std"], dtype=np.float32
    )
    tensor = torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0)
    with torch.no_grad():
        probs = torch.softmax(bundle["net"](tensor), dim=1)[0].tolist()
    prob_map = dict(zip(bundle["classes"], probs))
    label = max(prob_map, key=prob_map.get)
    return label, float(prob_map[label]), prob_map


def _classify_stub(img: Image.Image) -> tuple[str, float, dict[str, float]]:
    gray = np.asarray(img.resize((128, 128)).convert("L"), dtype=np.float32)
    haze = 1.0 - min(float(gray.std()) / 64.0, 1.0)  # dust lowers contrast
    label = "dusty" if haze > 0.6 else "clean"
    return label, 0.5, {label: 0.5}


def _raw_severity(prob_map: dict[str, float]) -> float:
    total = sum(prob_map.values()) or 1.0
    return float(sum(p / total * FIXED_SEVERITY.get(c, 0.0) for c, p in prob_map.items()))


def analyze_image(img: Image.Image) -> Analysis:
    """Classify one decoded image. Never raises for a decodable image."""
    s = settings
    bundle = get_bundle()
    mode = "cnn" if bundle else "stub"
    version = bundle["version"] if bundle else None

    if bundle:
        label, conf, probs = _classify_cnn(img, bundle)
    else:
        label, conf, probs = _classify_stub(img)
    raw = _raw_severity(probs) if bundle else FIXED_SEVERITY.get(label, 0.0)
    pct = calibrated_severity(raw)

    used, note = True, None
    problem = assess_quality(img)
    if problem:
        used, note = False, problem
    elif mode == "stub":
        # The stub's fixed 0.5 confidence is meaningless, so only the explicit switch matters.
        if not s.allow_stub_decisions:
            used, note = False, "stub model: not used for decisions"
    elif conf < s.min_conf:
        used, note = False, f"low confidence ({conf:.2f} < {s.min_conf:.2f})"

    return Analysis(
        model=mode,
        model_version=version,
        label=label,
        confidence=round(conf, 4),
        severity_score=round(raw, 2),
        severity_pct=None if pct is None else round(pct, 2),
        used=used,
        note=note,
        probs={k: round(v, 4) for k, v in probs.items()},
    )


def analyze_bytes(data: bytes) -> tuple[Image.Image, Analysis]:
    """Decode + analyze. Raises ImageError if the bytes are not an image."""
    img = load_image(data)
    return img, analyze_image(img)
