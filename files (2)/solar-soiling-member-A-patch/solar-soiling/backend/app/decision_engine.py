"""Automation brain: combined loss -> decision -> cleaning trigger -> post-clean verification."""

import json
import statistics
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from . import health
from .alerts import record_alert
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


def panel_medians(db, since=None, min_samples: int = 1):
    t = median_power(db, "test", since=since, min_samples=min_samples)
    r = median_power(db, "reference", since=since, min_samples=min_samples)
    return t, r


def current_electrical_loss(db, since=None, min_samples: int = 1):
    """None when there is no data OR no usable light (night); never a fake 0."""
    t, r = panel_medians(db, since=since, min_samples=min_samples)
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


def last_completed_cycle(db):
    return db.scalars(
        select(CleaningCycle)
        .where(CleaningCycle.completed_at.is_not(None))
        .order_by(CleaningCycle.completed_at.desc())
    ).first()


def clean_block_reason(db) -> str | None:
    """A1: cooldown since the last trigger, and a rolling 24 h cap. None = cleaning allowed."""
    last = db.scalars(select(CleaningCycle).order_by(CleaningCycle.triggered_at.desc())).first()
    if last and now() - aware(last.triggered_at) < timedelta(seconds=settings.min_clean_interval_s):
        return "cooldown"
    day_ago = now() - timedelta(hours=24)
    n = db.scalar(
        select(func.count()).select_from(CleaningCycle).where(CleaningCycle.triggered_at >= day_ago)
    )
    if n >= settings.max_cleans_per_day:
        return "daily_cap"
    return None


def _record_state(db, prev, reason: str, alert_msg: str | None = None) -> None:
    """Loss is unknown (night, sensor fault, device down): store ONE event per state change."""
    if prev is not None and prev.reason == reason:
        return
    db.add(
        SoilingEvent(
            combined_loss=None,
            electrical_loss=None,
            cnn_severity=None,
            alert_level="unknown",
            action="none",
            reason=reason,
        )
    )
    db.commit()
    if alert_msg:
        record_alert(db, "sensor_fault", "critical", alert_msg)


def evaluate_once(db, mqtt) -> None:
    prev = db.scalars(select(SoilingEvent).order_by(SoilingEvent.timestamp.desc())).first()

    # A3: a dead or offline device must never lead to a clean.
    if health.evaluate_health(db):
        _record_state(db, prev, "device_unhealthy")
        return

    # A1: only data taken after the last cycle has settled may count.
    start, cutoff = now() - timedelta(minutes=5), None
    last = last_completed_cycle(db)
    if last is not None:
        cutoff = aware(last.completed_at) + timedelta(seconds=settings.settle_s)
        start = max(start, cutoff)

    t, r = panel_medians(db, since=start, min_samples=3)
    if t is None or r is None:  # nothing usable yet (settling after a clean, or no data)
        return
    fault = health.cross_check(t, r)
    if fault:
        _record_state(db, prev, "sensor_fault", f"Sensor fault: {fault}. Auto-clean paused.")
        return
    elec = electrical_loss_pct(t, r, settings.baseline_ratio)
    if elec is None:  # reference too dark to compare
        _record_state(db, prev, "insufficient_light")
        return

    img = db.scalars(select(ImageCapture).order_by(ImageCapture.timestamp.desc())).first()
    fresh = img is not None and now() - aware(img.timestamp) < timedelta(hours=24)
    if fresh and cutoff is not None and aware(img.timestamp) <= cutoff:
        fresh = False  # image predates the last clean: a new one is required
    sev = img.severity_score if fresh else None
    loss = combined_loss(sev, elec)
    d = decide(loss, max_rain_probability_24h(), active_cycle(db) is not None)

    reason = None
    if d.action == "clean":
        reason = clean_block_reason(db)
        if reason:
            d = Decision(d.level, "wait")

    db.add(
        SoilingEvent(
            combined_loss=loss,
            electrical_loss=elec,
            cnn_severity=sev,
            alert_level=d.level,
            action=d.action,
            reason=reason,
        )
    )
    db.commit()
    if reason and (prev is None or prev.reason != reason):
        record_alert(
            db,
            "cleaning",
            d.level,
            f"Auto-clean blocked ({reason}): loss {loss:.1f}%, "
            f"min interval {settings.min_clean_interval_s} s, "
            f"cap {settings.max_cleans_per_day}/day",
        )
    if d.action == "clean":
        trigger_clean(db, mqtt, loss)
    prev_level = prev.alert_level if prev else "ok"
    if d.level != prev_level and not (prev_level == "unknown" and d.level == "ok"):
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
