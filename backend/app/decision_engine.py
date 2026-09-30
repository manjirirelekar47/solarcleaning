"""The automation brain: combine signals, decide, trigger cleaning, verify the result.

decide() is a pure function so the threshold table is trivially testable.
"""

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from .config import settings
from .loss import combined_loss, electrical_loss_pct
from .models import (
    CleaningCycle,
    DeviceState,
    ImageCapture,
    PanelReading,
    SoilingEvent,
    now,
)
from .notify import raise_alert
from .weather import max_rain_probability_24h

MIN_SAMPLES = 3


@dataclass
class Decision:
    level: str
    action: str
    reason: str = ""


def decide(loss: float, rain_prob: int | None, cleaning_active: bool) -> Decision:
    if loss < 5:
        return Decision("ok", "none", f"loss {loss:.1f}% is below 5%")
    if loss < 10:
        return Decision("watch", "notify", f"loss {loss:.1f}% is in the 5-10% watch band")
    if loss < 20:
        if rain_prob is not None and rain_prob >= settings.rain_threshold_pct:
            return Decision(
                "clean_recommended",
                "defer",
                f"loss {loss:.1f}%, cleaning deferred: {rain_prob}% chance of rain in 24 h",
            )
        act = "wait" if cleaning_active else "clean"
        return Decision("clean_recommended", act, f"loss {loss:.1f}% is at or above 10%")
    act = "wait" if cleaning_active else "clean"
    return Decision("critical", act, f"loss {loss:.1f}% is at or above 20% (rain is ignored)")


def net_benefit_inr(loss_pct: float) -> float:
    """Value of the energy a clean panel would recover over the horizon, minus cleaning cost.

    Informational only: the thresholds in decide() drive the action.
    """
    saved_kwh = (
        loss_pct / 100 * settings.farm_rated_kw * settings.peak_sun_hours
    ) * settings.benefit_horizon_days
    return round(saved_kwh * settings.tariff_inr_per_kwh - settings.cleaning_cost_inr, 2)


def last_cleaning_completed(db) -> datetime | None:
    return db.scalars(
        select(CleaningCycle.completed_at)
        .where(CleaningCycle.completed_at.is_not(None))
        .order_by(CleaningCycle.completed_at.desc())
    ).first()


def median_power(db, panel: str, since: datetime) -> float | None:
    vals = db.scalars(
        select(PanelReading.power).where(
            PanelReading.panel_type == panel, PanelReading.timestamp >= since
        )
    ).all()
    return statistics.median(vals) if len(vals) >= MIN_SAMPLES else None


def electrical_loss_since(db, since: datetime) -> float | None:
    t, r = median_power(db, "test", since), median_power(db, "reference", since)
    return None if t is None or r is None else electrical_loss_pct(t, r, settings.baseline_ratio)


def current_electrical_loss(db) -> float | None:
    """5-minute median, but never reaching back before the last completed cleaning
    (otherwise pre-clean readings would keep the loss high and cause a cleaning loop)."""
    since = now() - timedelta(minutes=5)
    done = last_cleaning_completed(db)
    return electrical_loss_since(db, max(since, done) if done else since)


def cleaning_is_active(db) -> bool:
    return (
        db.scalars(select(CleaningCycle).where(CleaningCycle.status != "complete")).first()
        is not None
    )


def trigger_clean(db, mqtt, pre_loss: float) -> CleaningCycle:
    cyc = CleaningCycle(pre_loss=pre_loss)
    db.add(cyc)
    db.commit()
    mqtt.publish(
        "cleaning/trigger",
        json.dumps({"cycle_id": cyc.id, "duration_s": settings.clean_duration_s}),
        qos=1,
    )
    return cyc


def latest_image_severity(db) -> float | None:
    """Severity of the newest USABLE image from the last 24 h taken AFTER the last cleaning.

    Images flagged used=False (stub model, low confidence, dark/blank/sky frame) never count.
    Uses the calibrated loss-% when a severity map exists, else the raw 0-100 score.
    """
    img = db.scalars(
        select(ImageCapture)
        .where(ImageCapture.used.is_(True))
        .order_by(ImageCapture.timestamp.desc())
    ).first()
    if not img or now() - img.timestamp >= timedelta(hours=24):
        return None
    done = last_cleaning_completed(db)
    if done and img.timestamp < done:
        return None  # photo shows the pre-clean panel
    return img.severity_pct if img.severity_pct is not None else img.severity_score


def devices_unhealthy(db) -> list[str]:
    return [d.source for d in db.scalars(select(DeviceState)).all() if not d.healthy]


def refresh_device_health(db):
    """Mark a source offline when it stops reporting, and alert on each transition."""
    limit = timedelta(seconds=settings.device_timeout_s)
    for dev in db.scalars(select(DeviceState)).all():
        stale = dev.last_seen is None or now() - dev.last_seen > limit
        if stale and dev.healthy:
            dev.healthy, dev.fault = False, f"no data for more than {settings.device_timeout_s}s"
            db.commit()
            raise_alert(db, "device_offline", "critical", f"{dev.source} panel sensor offline")
        elif not stale and not dev.healthy:
            dev.healthy, dev.fault = True, None
            db.commit()
            raise_alert(db, "device_recovered", "ok", f"{dev.source} panel sensor is back")


def evaluate_once(db, mqtt):
    elec = current_electrical_loss(db)
    if elec is None:
        return
    sev = latest_image_severity(db)
    loss = combined_loss(sev, elec)
    d = decide(loss, max_rain_probability_24h(), cleaning_is_active(db))

    down = devices_unhealthy(db)
    if d.action == "clean" and down:  # never spray on data from a dead sensor
        d = Decision(d.level, "blocked", f"cleaning blocked: {', '.join(down)} sensor offline")

    prev = db.scalars(select(SoilingEvent).order_by(SoilingEvent.timestamp.desc())).first()
    db.add(
        SoilingEvent(
            combined_loss=loss,
            electrical_loss=elec,
            cnn_severity=sev,
            level=d.level,
            action=d.action,
            reason=d.reason,
            net_benefit_inr=net_benefit_inr(loss),
        )
    )
    db.commit()
    if d.action == "clean":
        trigger_clean(db, mqtt, loss)
    if d.level != (prev.level if prev else "ok"):
        raise_alert(
            db,
            "level_change",
            d.level,
            f"Solar soiling level: {d.level} (loss {loss:.1f}%, action: {d.action})",
        )


def verify_cycles(db):
    """After cleaning finishes and the panel has settled, compare loss before vs after.

    post_loss uses electrical loss only (readings received since the cleaning finished).
    """
    for cyc in db.scalars(select(CleaningCycle).where(CleaningCycle.status == "verifying")).all():
        if now() - cyc.completed_at < timedelta(seconds=settings.settle_s):
            continue
        post = electrical_loss_since(db, cyc.completed_at)
        if post is None:
            continue
        cyc.post_loss, cyc.status = post, "complete"
        cyc.result = "success" if post < 5 else "insufficient"
        db.commit()
        raise_alert(
            db,
            "cleaning_result",
            "ok" if cyc.result == "success" else "watch",
            f"Cleaning #{cyc.id} {cyc.result}: {cyc.pre_loss:.1f}% -> {post:.1f}%",
            cyc.id,
        )


def expire_stale_cycles(db):
    """Backend restart / lost MQTT message must not block cleaning forever."""
    cutoff = now() - timedelta(seconds=settings.stale_cycle_s)
    for cyc in db.scalars(
        select(CleaningCycle).where(
            CleaningCycle.status != "complete", CleaningCycle.triggered_at < cutoff
        )
    ).all():
        stuck = cyc.status
        cyc.status, cyc.result, cyc.completed_at = "complete", "insufficient", now()
        db.commit()
        raise_alert(
            db,
            "cleaning_timeout",
            "watch",
            f"Cleaning #{cyc.id} timed out (stuck in '{stuck}'), marked insufficient",
            cyc.id,
        )
