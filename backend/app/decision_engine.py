"""Automation brain: combined loss -> decision -> cleaning trigger -> post-clean verification."""

import json
import statistics
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from .config import settings
from .loss import combined_loss, electrical_loss_pct
from .models import CleaningCycle, ImageCapture, PanelReading, SoilingEvent, aware, now
from .notify import notify
from .weather import max_rain_probability_24h


@dataclass
class Decision:
    level: str
    action: str


def decide(loss: float, rain_prob: int | None, cleaning_active: bool) -> Decision:
    if loss < 5:
        return Decision("ok", "none")
    if loss < 10:
        return Decision("watch", "notify")
    if loss < 20:
        if rain_prob is not None and rain_prob >= settings.rain_threshold_pct:
            return Decision("clean_recommended", "defer")
        return Decision("clean_recommended", "wait" if cleaning_active else "clean")
    return Decision("critical", "wait" if cleaning_active else "clean")


def median_power(db, panel: str, minutes: int = 5, since=None, min_samples: int = 1):
    """Median power over the last `minutes`, or over everything since `since` if given."""
    since = since or now() - timedelta(minutes=minutes)
    vals = db.scalars(
        select(PanelReading.power).where(
            PanelReading.panel_type == panel, PanelReading.timestamp >= since
        )
    ).all()
    return statistics.median(vals) if len(vals) >= min_samples else None


def current_electrical_loss(db, since=None, min_samples: int = 1):
    t = median_power(db, "test", since=since, min_samples=min_samples)
    r = median_power(db, "reference", since=since, min_samples=min_samples)
    return None if t is None or r is None else electrical_loss_pct(t, r, settings.baseline_ratio)


def active_cycle(db):
    return db.scalars(select(CleaningCycle).where(CleaningCycle.status != "complete")).first()


def trigger_clean(db, mqtt, pre_loss: float) -> CleaningCycle:
    cyc = CleaningCycle(pre_loss=pre_loss)
    db.add(cyc)
    db.commit()
    payload = {"cycle_id": cyc.id, "duration_s": settings.clean_duration_s}
    mqtt.publish("cleaning/trigger", json.dumps(payload), qos=1)
    return cyc


def expire_stale_cycles(db) -> None:
    """A cycle stuck in triggered/running/verifying would block all future cleaning."""
    limit = timedelta(seconds=settings.cycle_timeout_s)
    for cyc in db.scalars(select(CleaningCycle).where(CleaningCycle.status != "complete")):
        if now() - aware(cyc.triggered_at) > limit:
            cyc.status, cyc.result, cyc.completed_at = "complete", "insufficient", now()
            db.commit()
            notify(f"Cleaning #{cyc.id} timed out without verification")


def evaluate_once(db, mqtt) -> None:
    elec = current_electrical_loss(db)
    if elec is None:
        return
    img = db.scalars(select(ImageCapture).order_by(ImageCapture.timestamp.desc())).first()
    fresh = img is not None and now() - aware(img.timestamp) < timedelta(hours=24)
    sev = img.severity_score if fresh else None
    loss = combined_loss(sev, elec)
    d = decide(loss, max_rain_probability_24h(), active_cycle(db) is not None)

    prev = db.scalars(select(SoilingEvent).order_by(SoilingEvent.timestamp.desc())).first()
    db.add(
        SoilingEvent(
            combined_loss=loss,
            electrical_loss=elec,
            cnn_severity=sev,
            alert_level=d.level,
            action=d.action,
        )
    )
    db.commit()
    if d.action == "clean":
        trigger_clean(db, mqtt, loss)
    if d.level != (prev.alert_level if prev else "ok"):
        notify(f"Solar soiling level: {d.level} (loss {loss:.1f}%, action: {d.action})")


def verify_cycles(db) -> None:
    """Once cleaning is done and the panel has settled, compare loss before vs after.

    Only readings taken AFTER the settle window count; a plain 5-minute median would still
    contain pre-clean (dirty) readings and report every cleaning as insufficient.
    """
    for cyc in db.scalars(select(CleaningCycle).where(CleaningCycle.status == "verifying")):
        ready_at = aware(cyc.completed_at) + timedelta(seconds=settings.settle_s)
        if now() < ready_at:
            continue
        post = current_electrical_loss(db, since=ready_at, min_samples=3)
        if post is None:  # not enough post-clean data yet
            continue
        cyc.post_loss, cyc.status = post, "complete"
        cyc.result = "success" if post < 5 else "insufficient"
        db.commit()
        notify(f"Cleaning #{cyc.id} {cyc.result}: {cyc.pre_loss:.1f}% -> {post:.1f}%")
