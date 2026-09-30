"""REST API consumed by the dashboard and the capture tool.

/images  /images/latest  /images/{id}/file  /status  /loss-trend  /readings  /alerts
/device-health  /soiling-events  /cleaning-cycles  /decision  /trigger-clean (X-API-Key)
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from . import mqtt_client, vision
from .config import settings
from .db import get_db
from .decision_engine import cleaning_is_active, current_electrical_loss, trigger_clean
from .models import (
    Alert,
    CleaningCycle,
    DeviceState,
    ImageCapture,
    PanelReading,
    SoilingEvent,
    now,
)
from .security import require_api_key

router = APIRouter()

RANGES = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}
CONTEXTS = {"routine", "post_clean", "manual"}
MAX_BUCKETS = 5000
_BUCKET_RE = re.compile(r"^(\d+)([smh])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600}


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def aligned_start(at: datetime, span: timedelta, bucket_s: int) -> datetime:
    """Window start snapped down to a bucket boundary so buckets stay put between requests."""
    seconds = int((at - span - EPOCH).total_seconds())
    return EPOCH + timedelta(seconds=seconds - seconds % bucket_s)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- pure helpers


def parse_bucket(text: str) -> int:
    m = _BUCKET_RE.match(text.strip().lower())
    if not m or int(m.group(1)) <= 0:
        raise HTTPException(status_code=422, detail="bucket must look like 30s, 15m or 1h")
    return int(m.group(1)) * _UNIT_S[m.group(2)]


def parse_range(text: str) -> timedelta:
    if text not in RANGES:
        raise HTTPException(status_code=422, detail=f"range must be one of {sorted(RANGES)}")
    return RANGES[text]


def check_bucket_count(span: timedelta, bucket_s: int) -> None:
    if span.total_seconds() / bucket_s > MAX_BUCKETS:
        raise HTTPException(status_code=422, detail="bucket too small for this range")


def bucketize(
    rows: Iterable[tuple], start: datetime, bucket_s: int, columns: list[str]
) -> list[dict]:
    """Average each column per time bucket. rows are (timestamp, v1, v2, ...); None is skipped."""
    sums: dict[int, list[float]] = {}
    counts: dict[int, list[int]] = {}
    n = len(columns)
    for row in rows:
        ts = row[0]
        idx = int((ts - start).total_seconds() // bucket_s)
        if idx < 0:
            continue
        s = sums.setdefault(idx, [0.0] * n)
        c = counts.setdefault(idx, [0] * n)
        for i in range(n):
            v = row[i + 1]
            if v is not None:
                s[i] += float(v)
                c[i] += 1
    out = []
    for idx in sorted(sums):
        item: dict = {"t": iso(start + timedelta(seconds=idx * bucket_s))}
        for i, name in enumerate(columns):
            item[name] = round(sums[idx][i] / counts[idx][i], 3) if counts[idx][i] else None
        out.append(item)
    return out


def image_dict(img: ImageCapture) -> dict:
    return {
        "id": img.id,
        "timestamp": iso(img.timestamp),
        "label": img.label,
        "confidence": img.confidence,
        "severity_score": img.severity_score,
        "severity_pct": img.severity_pct,
        "model": img.model,
        "context": img.context,
        "used": bool(img.used),
        "note": img.note,
        "url": f"/images/{img.id}/file",
    }


# ---------------------------------------------------------------- images


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks, size = [], 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status_code=413, detail=f"image larger than {limit // (1024 * 1024)} MB"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/images", status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    context: str = Form("routine"),
    db: Session = Depends(get_db),
) -> dict:
    if context not in CONTEXTS:
        raise HTTPException(status_code=422, detail=f"context must be one of {sorted(CONTEXTS)}")
    data = await _read_limited(file, settings.max_upload_bytes)
    try:
        img, result = await run_in_threadpool(vision.analyze_bytes, data)
    except vision.ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    folder = settings.images_dir
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    await run_in_threadpool(img.save, folder / filename, "JPEG", quality=90)

    row = ImageCapture(
        filename=filename,
        label=result.label,
        confidence=result.confidence,
        severity_score=result.severity_score,
        severity_pct=result.severity_pct,
        model=result.model,
        context=context,
        used=result.used,
        note=result.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return image_dict(row)


@router.get("/images/latest")
def latest_image(db: Session = Depends(get_db)) -> dict:
    row = (
        db.query(ImageCapture).order_by(desc(ImageCapture.timestamp), desc(ImageCapture.id)).first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no images yet")
    return image_dict(row)


@router.get("/images/{image_id}/file")
def image_file(image_id: int, db: Session = Depends(get_db)) -> FileResponse:
    row = db.get(ImageCapture, image_id)
    if row is None or not row.filename:
        raise HTTPException(status_code=404, detail="image not found")
    path = settings.images_dir / Path(row.filename).name  # basename only: no traversal
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image file missing on disk")
    return FileResponse(path, media_type="image/jpeg")


# ---------------------------------------------------------------- status


def _device_health(rows: list) -> str:
    if not rows:
        return "unknown"
    healthy = [bool(r.healthy) for r in rows]
    if all(healthy):
        return "ok"
    return "offline" if not any(healthy) else "degraded"


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict:
    event = (
        db.query(SoilingEvent).order_by(desc(SoilingEvent.timestamp), desc(SoilingEvent.id)).first()
    )
    last_reading = db.query(func.max(PanelReading.timestamp)).scalar()
    image = (
        db.query(ImageCapture).order_by(desc(ImageCapture.timestamp), desc(ImageCapture.id)).first()
    )
    active = cleaning_is_active(db)
    info = vision.model_info()
    return {
        "level": event.level if event else None,
        "action": event.action if event else None,
        "combined_loss": event.combined_loss if event else None,
        "electrical_loss": event.electrical_loss if event else None,
        "cnn_severity": event.cnn_severity if event else None,
        "reason": event.reason if event else None,
        "net_benefit_inr": event.net_benefit_inr if event else None,
        "event_time": iso(event.timestamp) if event else None,
        "data_age_s": (
            None if last_reading is None else round((now() - last_reading).total_seconds(), 1)
        ),
        "device_health": _device_health(db.query(DeviceState).all()),
        "model": image.model if image else info["model"],
        "model_version": info["model_version"],
        "cleaning_active": active,
        "latest_image": image_dict(image) if image else None,
    }


# ---------------------------------------------------------------- trends


@router.get("/loss-trend")
def loss_trend(
    range: str = Query("24h"),
    bucket: str = Query("15m"),
    db: Session = Depends(get_db),
) -> dict:
    span, bucket_s = parse_range(range), parse_bucket(bucket)
    check_bucket_count(span, bucket_s)
    start = aligned_start(now(), span, bucket_s)
    rows = (
        db.query(
            SoilingEvent.timestamp,
            SoilingEvent.combined_loss,
            SoilingEvent.electrical_loss,
            SoilingEvent.cnn_severity,
        )
        .filter(SoilingEvent.timestamp >= start)
        .order_by(SoilingEvent.timestamp)
        .all()
    )
    points = bucketize(rows, start, bucket_s, ["combined", "electrical", "cnn"])
    cycles = (
        db.query(CleaningCycle)
        .filter(CleaningCycle.triggered_at >= start)
        .order_by(CleaningCycle.triggered_at)
        .all()
    )
    cleanings = [
        {"t": iso(c.triggered_at), "pre": c.pre_loss, "post": c.post_loss, "result": c.result}
        for c in cycles
    ]
    return {"range": range, "bucket_s": bucket_s, "points": points, "cleanings": cleanings}


@router.get("/readings")
def readings(
    panel: str = Query("both"),
    bucket: str = Query("1m"),
    range: str = Query("1h"),
    db: Session = Depends(get_db),
) -> dict:
    if panel not in {"test", "reference", "both"}:
        raise HTTPException(status_code=422, detail="panel must be test, reference or both")
    span, bucket_s = parse_range(range), parse_bucket(bucket)
    check_bucket_count(span, bucket_s)
    start = aligned_start(now(), span, bucket_s)
    wanted = ["test", "reference"] if panel == "both" else [panel]
    merged: dict[str, dict] = {}
    for source in wanted:
        rows = (
            db.query(PanelReading.timestamp, PanelReading.power)
            .filter(PanelReading.panel_type == source, PanelReading.timestamp >= start)
            .order_by(PanelReading.timestamp)
            .all()
        )
        for point in bucketize(rows, start, bucket_s, ["power_w"]):
            merged.setdefault(point["t"], {"t": point["t"]})[source] = point["power_w"]
    points = [merged[k] for k in sorted(merged)]
    for p in points:
        for source in wanted:
            p.setdefault(source, None)
    return {"range": range, "bucket_s": bucket_s, "points": points}


# ---------------------------------------------------------------- alerts and devices


@router.get("/alerts")
def alerts(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(Alert).order_by(desc(Alert.timestamp), desc(Alert.id)).limit(limit).all()
    return [
        {
            "id": a.id,
            "timestamp": iso(a.timestamp),
            "kind": a.kind,
            "level": a.level,
            "message": a.message,
            "cycle_id": a.cycle_id,
        }
        for a in rows
    ]


@router.get("/device-health")
def device_health(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(DeviceState).order_by(DeviceState.source).all()
    return [
        {
            "source": d.source,
            "last_seen": iso(d.last_seen),
            "healthy": bool(d.healthy),
            "fault": d.fault,
        }
        for d in rows
    ]


# ---------------------------------------------------------------- history lists and hardware


@router.get("/images")
def list_images(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)) -> list[dict]:
    q = db.query(ImageCapture).order_by(desc(ImageCapture.timestamp), desc(ImageCapture.id))
    return [image_dict(i) for i in q.limit(limit).all()]


@router.get("/soiling-events")
def soiling_events(limit: int = Query(200, ge=1, le=5000), db: Session = Depends(get_db)) -> list:
    rows = db.query(SoilingEvent).order_by(desc(SoilingEvent.timestamp)).limit(limit).all()
    return [
        {"t": iso(r.timestamp), "loss": r.combined_loss, "level": r.level, "action": r.action}
        for r in reversed(rows)
    ]


@router.get("/decision")
def decision(db: Session = Depends(get_db)) -> dict | None:
    e = db.query(SoilingEvent).order_by(desc(SoilingEvent.timestamp), desc(SoilingEvent.id)).first()
    return e and {
        "t": iso(e.timestamp),
        "level": e.level,
        "action": e.action,
        "reason": e.reason,
        "combined_loss": e.combined_loss,
        "net_benefit_inr": e.net_benefit_inr,
    }


@router.get("/cleaning-cycles")
def cleaning_cycles(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)) -> list:
    rows = db.query(CleaningCycle).order_by(desc(CleaningCycle.id)).limit(limit).all()
    return [
        {
            "id": c.id,
            "triggered_at": iso(c.triggered_at),
            "completed_at": iso(c.completed_at),
            "status": c.status,
            "pre": c.pre_loss,
            "post": c.post_loss,
            "result": c.result,
        }
        for c in rows
    ]


@router.post("/trigger-clean", dependencies=[Depends(require_api_key)])
def manual_clean(db: Session = Depends(get_db)) -> dict:
    if cleaning_is_active(db):
        raise HTTPException(status_code=409, detail="A cleaning cycle is already running")
    cyc = trigger_clean(db, mqtt_client.client, current_electrical_loss(db) or 0.0)
    return {"ok": True, "cycle_id": cyc.id}
