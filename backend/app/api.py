"""REST API consumed by the dashboard."""

import io
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mqtt_client
from .config import settings
from .db import get_db
from .decision_engine import active_cycle, current_electrical_loss, trigger_clean
from .models import CleaningCycle, ImageCapture, PanelReading, SoilingEvent
from .vision import load_classifier

router = APIRouter()
classifier = load_classifier()


def latest(db, model, col=None):
    return db.scalars(select(model).order_by((col or model.timestamp).desc())).first()


def _event(ev):
    return ev and {
        "combined_loss": ev.combined_loss,
        "electrical_loss": ev.electrical_loss,
        "cnn_severity": ev.cnn_severity,
        "level": ev.alert_level,
        "action": ev.action,
        "at": ev.timestamp,
    }


def _cycle(c):
    return {
        "id": c.id,
        "status": c.status,
        "result": c.result,
        "pre": c.pre_loss,
        "post": c.post_loss,
        "triggered_at": c.triggered_at,
        "completed_at": c.completed_at,
    }


@router.get("/status")
def status(db: Session = Depends(get_db)):
    ev, img = latest(db, SoilingEvent), latest(db, ImageCapture)
    cyc = latest(db, CleaningCycle, CleaningCycle.id)
    ref = db.scalars(
        select(PanelReading)
        .where(PanelReading.panel_type == "reference")
        .order_by(PanelReading.timestamp.desc())
    ).first()
    return {
        "event": _event(ev),
        "image": img
        and {
            "url": "/media/" + Path(img.image_path).name,
            "class": img.soiling_class,
            "confidence": img.confidence,
            "at": img.timestamp,
        },
        "env": ref and {"temp": ref.temp, "humidity": ref.humidity},
        "cleaning": cyc and _cycle(cyc),
    }


@router.get("/decision")
def decision(db: Session = Depends(get_db)):
    return _event(latest(db, SoilingEvent))


@router.get("/readings")
def readings(limit: int = 100, panel: str | None = None, db: Session = Depends(get_db)):
    q = select(PanelReading).order_by(PanelReading.timestamp.desc()).limit(limit)
    if panel:
        q = q.where(PanelReading.panel_type == panel)
    return [
        {
            "t": r.timestamp,
            "panel": r.panel_type,
            "v": r.voltage,
            "i": r.current,
            "p": r.power,
            "temp": r.temp,
            "hum": r.humidity,
        }
        for r in reversed(db.scalars(q).all())
    ]


@router.get("/soiling-events")
def events(limit: int = 200, db: Session = Depends(get_db)):
    rows = db.scalars(select(SoilingEvent).order_by(SoilingEvent.timestamp.desc()).limit(limit))
    return [
        {"t": r.timestamp, "loss": r.combined_loss, "level": r.alert_level}
        for r in reversed(rows.all())
    ]


@router.get("/alerts")
def alerts(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(SoilingEvent)
        .where(SoilingEvent.alert_level != "ok")
        .order_by(SoilingEvent.timestamp.desc())
        .limit(limit)
    ).all()
    return [
        {"t": r.timestamp, "level": r.alert_level, "loss": r.combined_loss, "action": r.action}
        for r in rows
    ]


@router.get("/cleaning-cycles")
def cleaning_cycles(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.scalars(select(CleaningCycle).order_by(CleaningCycle.id.desc()).limit(limit)).all()
    return [_cycle(c) for c in rows]


@router.get("/images")
def images(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.scalars(select(ImageCapture).order_by(ImageCapture.timestamp.desc()).limit(limit))
    return [
        {
            "id": i.id,
            "url": "/media/" + Path(i.image_path).name,
            "class": i.soiling_class,
            "confidence": i.confidence,
            "severity": i.severity_score,
            "t": i.timestamp,
        }
        for i in rows.all()
    ]


@router.post("/images")
async def upload_image(file: UploadFile, db: Session = Depends(get_db)):
    data = await file.read()
    try:
        fmt = (Image.open(io.BytesIO(data)).format or "JPEG").lower()
        Image.open(io.BytesIO(data)).verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Not a valid image file")
    label, conf, sev = classifier.predict(data)
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    path = settings.image_dir / f"{int(time.time())}_{uuid.uuid4().hex[:6]}.{fmt}"
    path.write_bytes(data)
    img = ImageCapture(
        image_path=str(path), soiling_class=label, confidence=conf, severity_score=sev
    )
    db.add(img)
    db.commit()
    db.refresh(img)
    return {"id": img.id, "class": label, "confidence": conf, "severity": sev}


@router.post("/trigger-clean")
def manual_clean(db: Session = Depends(get_db)):
    if active_cycle(db):
        raise HTTPException(status_code=409, detail="A cleaning cycle is already in progress")
    cyc = trigger_clean(db, mqtt_client.client, current_electrical_loss(db) or 0.0)
    return {"ok": True, "cycle_id": cyc.id}
