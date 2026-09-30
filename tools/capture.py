"""Automatic image capture: routine frames plus one post-cleaning frame per cycle.

Sources (pick one):
  --source-url http://PHONE_IP:8080/shot.jpg   phone running an IP-webcam app
  --camera 0                                   USB webcam (needs opencv-python-headless)
  --folder tools/test_frames                   cycle through saved images (simulator demos)

Examples:
  python tools/capture.py --api http://localhost:8000 --folder tools/test_frames --interval 60 \
      --mqtt-host localhost --mqtt-user backend --mqtt-password-env MQTT_PASSWORD
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

import requests

MAX_BYTES = 5 * 1024 * 1024
log = logging.getLogger("capture")


class FrameSource(Protocol):
    def grab(self) -> bytes: ...


class UrlSource:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url, self.timeout = url, timeout

    def grab(self) -> bytes:
        resp = requests.get(self.url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content


class FolderSource:
    def __init__(self, folder: Path):
        exts = {".jpg", ".jpeg", ".png"}
        files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in exts)
        if not files:
            raise ValueError(f"no .jpg/.png images in {folder}")
        self._cycle = itertools.cycle(files)

    def grab(self) -> bytes:
        return next(self._cycle).read_bytes()


class CameraSource:
    def __init__(self, index: int):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("--camera needs: pip install opencv-python-headless") from exc
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera {index}")

    def grab(self) -> bytes:
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("camera returned no frame")
        ok, buf = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("could not encode frame")
        return buf.tobytes()


def parse_status(payload: bytes | str) -> str | None:
    """Turn an MQTT payload into a lowercase status word. Accepts JSON or plain text."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return text.lower()
    if isinstance(data, dict):
        value = data.get("status") or data.get("state")
        return str(value).lower() if value else None
    return str(data).lower()


def post_image(
    api: str, data: bytes, context: str, session=requests, retries: int = 3, backoff_s: float = 2.0
) -> dict:
    """POST one frame to /images. Retries transient failures; refuses files over 5 MB."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"frame is {len(data)} bytes, over the 5 MB upload limit")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.post(
                f"{api.rstrip('/')}/images",
                files={"file": ("frame.jpg", data, "image/jpeg")},
                data={"context": context},
                timeout=30,
            )
            if resp.status_code == 201:
                return resp.json()
            if 400 <= resp.status_code < 500:  # our fault (bad image, too big): do not retry
                raise ValueError(f"server rejected frame: {resp.status_code} {resp.text[:200]}")
            last = RuntimeError(f"server error {resp.status_code}")
        except requests.RequestException as exc:
            last = exc
        if attempt < retries:
            time.sleep(backoff_s * attempt)
    raise RuntimeError(f"upload failed after {retries} attempts: {last}")


class PostCleanScheduler:
    """After cleaning/status = done, wait settle_s, then post ONE post_clean image."""

    DONE = {"done", "completed", "complete", "finished"}

    def __init__(self, capture_and_post, settle_s: float):
        self._run, self.settle_s = capture_and_post, settle_s
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_status(self, payload: bytes | str) -> bool:
        """Return True if a post-clean capture was scheduled."""
        if parse_status(payload) not in self.DONE:
            return False
        with self._lock:
            if self._timer is not None and self._timer.is_alive():
                return False  # already waiting: one image per cycle
            self._timer = threading.Timer(self.settle_s, self._safe_run)
            self._timer.daemon = True
            self._timer.start()
        return True

    def _safe_run(self) -> None:
        try:
            self._run()
        except Exception:
            log.exception("post_clean capture failed")

    def wait(self, timeout: float | None = None) -> None:
        t = self._timer
        if t is not None:
            t.join(timeout)


def start_mqtt(args, scheduler: PostCleanScheduler):  # pragma: no cover - needs a broker
    import paho.mqtt.client as mqtt

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:  # paho-mqtt 1.x
        client = mqtt.Client()
    if args.mqtt_user:
        client.username_pw_set(args.mqtt_user, os.getenv(args.mqtt_password_env, ""))

    def on_connect(c, userdata, flags, rc, *extra):
        log.info("MQTT connected (rc=%s); subscribing to %s", rc, args.mqtt_topic)
        c.subscribe(args.mqtt_topic)

    def on_message(c, userdata, msg):
        if scheduler.on_status(msg.payload):
            log.info("Cleaning done: post_clean capture in %.0fs", scheduler.settle_s)

    client.on_connect, client.on_message = on_connect, on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(args.mqtt_host, args.mqtt_port)
    client.loop_start()
    return client


def build_source(args) -> FrameSource:
    if args.source_url:
        return UrlSource(args.source_url)
    if args.camera is not None:
        return CameraSource(args.camera)
    return FolderSource(Path(args.folder))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--source-url")
    src.add_argument("--camera", type=int)
    src.add_argument("--folder")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--interval", type=float, default=300.0, help="seconds between routine frames")
    ap.add_argument("--settle-s", type=float, default=30.0, help="wait after cleaning is done")
    ap.add_argument("--mqtt-host")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--mqtt-topic", default="cleaning/status")
    ap.add_argument("--mqtt-user")
    ap.add_argument("--mqtt-password-env", default="MQTT_PASSWORD")
    ap.add_argument("--once", action="store_true", help="send one routine frame and exit")
    args = ap.parse_args(argv)
    if args.interval <= 0 or args.settle_s < 0:
        ap.error("--interval must be > 0 and --settle-s >= 0")
    return args


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    source = build_source(args)

    def capture_and_post(context: str) -> None:
        result = post_image(args.api, source.grab(), context)
        log.info(
            "Posted %s frame id=%s used=%s note=%s",
            context,
            result.get("id"),
            result.get("used"),
            result.get("note"),
        )

    scheduler = PostCleanScheduler(lambda: capture_and_post("post_clean"), args.settle_s)
    if args.mqtt_host:
        start_mqtt(args, scheduler)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.is_set():
        try:
            capture_and_post("routine")
        except Exception as exc:  # keep running through camera or network hiccups
            log.warning("routine capture failed: %s", exc)
        if args.once:
            scheduler.wait()
            break
        stop.wait(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
