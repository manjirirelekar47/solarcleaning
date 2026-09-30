"""Automation brain: combined loss -> decision -> cleaning trigger -> post-clean verification."""

import json
import statistics
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from . import economics, health
from .alerts import record_alert
from .config import settings
from .loss import baseline_at, combined_loss, paired_electrical_loss
from .models import (
    Alert,
    CleaningCycle,
    ImageCapture,
    PanelReading,
    SoilingEvent,
    aware,
    now,
)
from .weather import max_rain_probability_24h

CONFIRMING = "confirming"  # A4: wants to clean, waiting for confirm_n evaluations
INSPECTION = "manual_inspection"  # A8: auto-clean stops until a human looks (or the panel is ok)


@dataclass
class Decision:
    level: str
    action: str


# A4: level ladder. Rising is immediate; falling needs loss to clear the threshold by
# `hysteresis_pct`, so a loss hovering at 5/10/20% does not flap between levels.
LEVELS = ("ok", "watch", "clean_recommended", "critical")
LEVEL_THRESHOLDS = (5.0, 10.0, 20.0)  # loss % at which levels 1, 2, 3 begin


def level_index(loss: float) -> int:
    return sum(loss >= t for t in LEVEL_THRESHOLDS)


def held_level(loss: float, prev_level: str | None) -> str:
    """Alert level for `loss`, sticky on the way down (A4 hysteresis)."""
    idx = level_index(loss)
    prev = LEVELS.index(prev_level) if prev_level in LEVELS else 0
    if prev > idx:
        idx = prev
        while idx > 0 and loss < LEVEL_THRESHOLDS[idx - 1] - settings.hysteresis_pct:
            idx -= 1
    return LEVELS[idx]


def decide(
    loss: float, rain_prob: int | None, cleaning_active: bool, prev_level: str | None = None
) -> Decision:
    """Hysteresis shapes the reported level only. The ACTION always follows the raw
    thresholds, so a held level can never trigger a clean below its threshold."""
    level = held_level(loss, prev_level)
    if loss < 5:
        return Decision(level, "none")
    if loss < 10:
        return Decision(level, "notify")
    if loss < 20:
        if rain_prob is not None and rain_prob >= settings.rain_threshold_pct:
            return Decision(level, "defer")
        return Decision(level, "wait" if cleaning_active else "clean")
    return Decision(level, "wait" if cleaning_active else "clean")


def median_power(db, panel: str, minutes: int = 5, since=None, min_samples: int = 1):
    """Median power over the last `minutes`, or over everything since `since` if given."""
    since = since or now() - timedelta(minutes=minutes)
    vals = db.scalars(
        select(PanelReading.power).where(
            PanelReading.panel_type == panel, PanelReading.timestamp >= since
        )
    ).all()
    return statistics.median(vals) if len(vals) >= min_samples else None


def recent_power(db, panel: str, n: int = 3):
    """Median of the last n raw readings (None if fewer). Catches a sensor that just died,
    which the 5-minute median hides while it is still a mix of good and zero samples."""
    vals = db.scalars(
        select(PanelReading.power)
        .where(PanelReading.panel_type == panel)
        .order_by(PanelReading.timestamp.desc(), PanelReading.id.desc())
        .limit(n)
    ).all()
    return statistics.median(vals) if len(vals) >= n else None


def panel_medians(db, since=None, min_samples: int = 1):
    t = median_power(db, "test", since=since, min_samples=min_samples)
    r = median_power(db, "reference", since=since, min_samples=min_samples)
    return t, r


def paired_samples(db, since):
    rows = db.execute(
        select(
            PanelReading.panel_type, PanelReading.timestamp, PanelReading.power, PanelReading.temp
        ).where(PanelReading.timestamp >= since)
    ).all()
    by = {"test": [], "reference": []}
    for panel, ts, power, temp in rows:
        by[panel].append((aware(ts), power, temp))
    return by["test"], by["reference"]


def current_electrical_loss(db, since=None, min_samples: int = 1):
    """A5: median of per-pair test/reference ratios since `since` (default: last 5 minutes).
    None when there is no data OR no usable light (night); never a fake 0."""
    since = since or now() - timedelta(minutes=5)
    test, ref = paired_samples(db, since)
    return paired_electrical_loss(test, ref, baseline_at(now()), min_pairs=min_samples)


def active_cycle(db):
    return db.scalars(select(CleaningCycle).where(CleaningCycle.status != "complete")).first()


def latest_cycle(db):
    return db.scalars(select(CleaningCycle).order_by(CleaningCycle.id.desc())).first()


def _ok_since(db, ts) -> bool:
    return bool(
        db.scalar(
            select(func.count())
            .select_from(SoilingEvent)
            .where(SoilingEvent.alert_level == "ok", SoilingEvent.timestamp > aware(ts))
        )
    )


def next_attempt(db) -> int:
    """A8: the clean after an 'insufficient' first attempt is the one allowed retry (attempt 2).

    The retry is not a special path: it is the next clean the engine would do anyway, so the
    cooldown, daily cap, device health, both signal gates and rain deferral all still apply.
    A recovery to 'ok' in between, or a long gap, starts a fresh incident (attempt 1).
    """
    last = latest_cycle(db)
    if last is None or last.status != "complete" or last.result != "insufficient":
        return 1
    if last.attempt != 1 or last.completed_at is None:
        return 1
    if now() - aware(last.completed_at) > timedelta(seconds=settings.retry_window_s):
        return 1
    return 1 if _ok_since(db, last.completed_at) else 2


def inspection_hold(db) -> bool:
    """A8: after a 'manual inspection needed' alert the pump stays off until the panel reads ok
    again or someone starts a cleaning by hand (a cycle newer than the alert)."""
    alert = db.scalars(
        select(Alert)
        .where(Alert.kind == "cleaning", Alert.level == "inspection")
        .order_by(Alert.id.desc())
    ).first()
    if alert is None:
        return False
    cyc = latest_cycle(db)
    if cyc is not None and aware(cyc.triggered_at) > aware(alert.timestamp):
        return False
    return not _ok_since(db, alert.timestamp)


def trigger_clean(db, mqtt, pre_loss: float, attempt: int | None = None) -> CleaningCycle:
    attempt = next_attempt(db) if attempt is None else attempt
    cyc = CleaningCycle(pre_loss=pre_loss, attempt=attempt)
    db.add(cyc)
    db.commit()
    payload = {"cycle_id": cyc.id, "duration_s": settings.clean_duration_s}
    mqtt.publish("cleaning/trigger", json.dumps(payload), qos=1)
    record_alert(
        db,
        "cleaning",
        "info",
        f"Cleaning #{cyc.id} started (attempt {attempt}), loss {pre_loss:.1f}%",
        cycle_id=cyc.id,
    )
    return cyc


def expire_stale_cycles(db) -> None:
    """A cycle stuck in triggered/running/verifying would block all future cleaning.
    A8: a timeout is 'failed' (we never learned the outcome), not 'insufficient'."""
    limit = timedelta(seconds=settings.cycle_timeout_s)
    for cyc in db.scalars(select(CleaningCycle).where(CleaningCycle.status != "complete")):
        if now() - aware(cyc.triggered_at) > limit:
            cyc.status, cyc.result, cyc.completed_at = "complete", "failed", now()
            db.commit()
            record_alert(
                db,
                "cleaning",
                "inspection",
                f"Cleaning #{cyc.id} timed out without verification (failed). "
                "Manual inspection needed.",
                cycle_id=cyc.id,
            )


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


def confirm_streak(db) -> int:
    """A4: consecutive prior evaluations that wanted to clean but were still confirming.

    Derived from the event log (no extra state, survives restarts). A gap longer than three
    evaluation intervals breaks the streak, so a stale run cannot carry over an outage.
    """
    max_gap = timedelta(seconds=3 * settings.eval_interval_s)
    streak, newer = 0, now()
    for ev in db.scalars(select(SoilingEvent).order_by(SoilingEvent.timestamp.desc()).limit(50)):
        if ev.reason != CONFIRMING or newer - aware(ev.timestamp) > max_gap:
            break
        streak, newer = streak + 1, aware(ev.timestamp)
    return streak


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


def severity_to_loss_pct(img) -> float:
    """Expected power loss (%) implied by the image. Calibrated value if B4 provided one,
    otherwise the raw 0-100 index scaled by severity_loss_factor (an index, not a measurement)."""
    if img.severity_pct is not None:
        return img.severity_pct
    return img.severity_score * settings.severity_loss_factor


def signal_hold_reason(elec: float, img, fresh: bool, net: float) -> str | None:
    """A6/A7 gates: both signals must agree, and cleaning must pay. None = go ahead."""
    if elec < settings.gate_electrical_pct:
        return "cnn_only"  # image says dirty, the meter does not: notify, do not spray
    if fresh and img.soiling_class == "clean":
        return "electrical_only"  # meter says loss, image says clean: shadow / cloud edge
    if net <= 0:
        return "cost_exceeds_benefit"
    return None


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
    rt, rr = recent_power(db, "test"), recent_power(db, "reference")
    if not fault and rt is not None and rr is not None:
        fault = health.cross_check(rt, rr)
    if fault:
        _record_state(db, prev, "sensor_fault", f"Sensor fault: {fault}. Auto-clean paused.")
        return
    elec = current_electrical_loss(db, since=start, min_samples=3)
    if elec is None:  # reference too dark (or no test/reference pairs) to compare
        _record_state(db, prev, "insufficient_light")
        return

    img = db.scalars(
        select(ImageCapture)
        .where(ImageCapture.used.is_(True))  # B2: dark/low-confidence/stub frames are ignored
        .order_by(ImageCapture.timestamp.desc())
    ).first()
    fresh = img is not None and now() - aware(img.timestamp) < timedelta(hours=24)
    if fresh and cutoff is not None and aware(img.timestamp) <= cutoff:
        fresh = False  # image predates the last clean: a new one is required
    sev = (
        (img.severity_pct if img.severity_pct is not None else img.severity_score)
        if fresh
        else None
    )
    loss = combined_loss(severity_to_loss_pct(img) if fresh else None, elec)
    rain = max_rain_probability_24h()
    prev_level = prev.alert_level if prev else "ok"
    d = decide(loss, rain, active_cycle(db) is not None, prev_level)
    net = economics.net_benefit_inr(loss, rain)

    reason = None
    if d.action == "clean":
        reason = signal_hold_reason(elec, img, fresh, net)
        if reason:
            d = Decision(d.level, "notify")  # a human decides; the pump stays off
        elif inspection_hold(db):  # A8: a human must look before the pump runs again
            d, reason = Decision(d.level, "notify"), INSPECTION
        else:
            reason = clean_block_reason(db)
            if reason:
                d = Decision(d.level, "wait")
            elif confirm_streak(db) + 1 < settings.confirm_n:  # A4: not yet confirmed
                d, reason = Decision(d.level, "wait"), CONFIRMING

    db.add(
        SoilingEvent(
            combined_loss=loss,
            electrical_loss=elec,
            cnn_severity=sev,
            alert_level=d.level,
            action=d.action,
            net_benefit_inr=net,
            reason=reason,
        )
    )
    db.commit()
    if reason not in (None, CONFIRMING, INSPECTION) and (prev is None or prev.reason != reason):
        record_alert(
            db,
            "cleaning",
            d.level,
            f"Auto-clean held ({reason}): loss {loss:.1f}%, electrical {elec:.1f}%, "
            f"net benefit INR {net:,.0f}",
        )
    if d.level != prev_level and not (prev_level == "unknown" and d.level == "ok"):
        record_alert(
            db,
            "level_change",
            d.level,
            f"Solar soiling level: {d.level} (loss {loss:.1f}%, action: {d.action})",
        )
    if d.action == "clean":
        trigger_clean(db, mqtt, loss)


def verify_cycles(db) -> None:
    """Once cleaning is done and the panel has settled, compare loss before vs after.

    Only readings taken AFTER the settle window count; a plain 5-minute median would still
    contain pre-clean (dirty) readings and report every cleaning as insufficient.
    A8: a post-clean image (context 'post_clean', B3) taken after the spray is fused with the
    electrical loss; without one, electrical data alone decides.
    """
    for cyc in db.scalars(select(CleaningCycle).where(CleaningCycle.status == "verifying")):
        ready_at = aware(cyc.completed_at) + timedelta(seconds=settings.settle_s)
        if now() < ready_at:
            continue
        elec = current_electrical_loss(db, since=ready_at, min_samples=3)
        if elec is None:  # not enough post-clean data yet
            continue
        img = db.scalars(
            select(ImageCapture)
            .where(
                ImageCapture.context == "post_clean",
                ImageCapture.used.is_(True),
                ImageCapture.timestamp >= aware(cyc.completed_at),
            )
            .order_by(ImageCapture.timestamp.desc())
        ).first()
        post = combined_loss(severity_to_loss_pct(img) if img else None, elec)
        cyc.post_loss, cyc.status, cyc.post_image_id = post, "complete", img.id if img else None
        cyc.result = "success" if post < 5 else "insufficient"
        db.commit()
        change = f"{cyc.pre_loss:.1f}% -> {post:.1f}%"
        if cyc.result == "success":
            level, msg = "ok", f"Cleaning #{cyc.id} success: {change}"
        elif cyc.attempt < 2:
            level = "warning"
            msg = f"Cleaning #{cyc.id} insufficient: {change}. One retry when the cooldown allows."
        else:
            level = "inspection"
            msg = (
                f"Cleaning #{cyc.id} still insufficient after retry: {change}. "
                "Manual inspection needed."
            )
        record_alert(db, "cleaning", level, msg, cycle_id=cyc.id)
