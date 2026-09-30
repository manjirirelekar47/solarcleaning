"""Synthetic test images shared by Member B's tests (not collected by pytest)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image


def _to_bytes(img: Image.Image, fmt: str = "JPEG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def panel_image(size: int = 256, seed: int = 0) -> Image.Image:
    """Dark-blue cells with bright grid lines and sensor noise: looks like a PV panel."""
    rng = np.random.default_rng(seed)
    arr = np.full((size, size, 3), (25, 45, 110), dtype=np.float32)
    arr += rng.normal(0, 8, arr.shape)
    step = size // 8
    arr[::step, :, :] = 200
    arr[:, ::step, :] = 200
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))


def panel_bytes(**kw) -> bytes:
    return _to_bytes(panel_image(**kw))


def sky_bytes(size: int = 256) -> bytes:
    """Smooth blue gradient with no edges: a photo of the sky, not a panel."""
    ramp = np.linspace(140, 240, size).astype("uint8")
    arr = np.zeros((size, size, 3), dtype="uint8")
    arr[..., 0] = 60
    arr[..., 1] = 120
    arr[..., 2] = ramp[None, :]
    return _to_bytes(Image.fromarray(arr))


def dark_bytes(size: int = 256) -> bytes:
    return _to_bytes(Image.fromarray(np.full((size, size, 3), 5, dtype="uint8")))


def blank_bytes(size: int = 256) -> bytes:
    return _to_bytes(Image.fromarray(np.full((size, size, 3), 128, dtype="uint8")))


def rotated_bytes(width: int = 200, height: int = 100) -> bytes:
    """Landscape pixels tagged EXIF orientation=6 (rotate 90 CW): should load as portrait."""
    img = panel_image(size=max(width, height)).crop((0, 0, width, height))
    exif = Image.Exif()
    exif[0x0112] = 6
    return _to_bytes(img, exif=exif)
