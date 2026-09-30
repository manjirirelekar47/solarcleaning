"""Device health and sensor sanity (A3): last-seen tracking, plausibility, dead-sensor checks."""

import math
from datetime import timedelta

from .alerts import record_alert
from .config import settings
from .models import DeviceState, aware, now

SOURCES = ("test", "reference")


def check_reading(volts: float, amps: float) -> str | None:
    """Return a fault description if the reading is physically implausible, else None."""
    if not (math.isfinite(volts) and math.isfinite(amps)):
        return "non-finite reading"
    if volts < 0 or volts > settings.max_panel_v:
        return f"voltage out of range ({volts:.2f} V)"
    if amps < settings.min_panel_current_a:
        return f"negative current ({amps * 1000:.0f} mA)"
    return None


def cross_check(p_test: float, p_ref: float) -> str | None:
    """Compare the two panels' median power. A dead sensor looks like 100% loss otherwise."""
    if p_ref >= settings.min_ref_power_w and p_test < settings.dead_test_ratio * p_ref:
        return "test panel reads ~0 W while the reference is producing (disconnected sensor?)"
    if p_ref < settings.min_ref_power_w and p_test >= 2 * settings.min_ref_power_w:
        return "reference panel reads ~0 W while the test panel is producing"
    return None


def touch(db, source: str, fault: str | None) -> None:
    """Called for every sensor message (caller commits). Records last_seen and current fault."""
    st = db.get(DeviceState, source)
    if st is None:
        st = DeviceState(source=source, healthy=True)
        db.add(st)
    st.last_seen, st.fault = now(), fault


def evaluate_health(db) -> list[str]:
    """Return the current problems. Writes ONE alert per healthy<->unhealthy transition;
    both panels hang off the same ESP32, so transitions in the same tick share one alert."""
    problems, went_bad, recovered, kinds = [], [], [], set()
    for src in SOURCES:
        st = db.get(DeviceState, src)
        if st is None:  # never seen yet: no readings exist either, nothing to alert about
            continue
        age = (now() - aware(st.last_seen)).total_seconds()
        if age > timedelta(seconds=settings.offline_after_s).total_seconds():
            problem, kind = f"{src} offline (no data for {int(age)} s)", "device"
        elif st.fault:
            problem, kind = f"{src} sensor fault: {st.fault}", "sensor_fault"
        else:
            problem, kind = None, "device"
        healthy = problem is None
        if healthy != st.healthy:
            st.healthy = healthy
            if healthy:
                recovered.append(src)
            else:
                went_bad.append(problem)
                kinds.add(kind)
        if problem:
            problems.append(problem)
    db.commit()
    if went_bad:
        kind = "device" if "device" in kinds else "sensor_fault"
        record_alert(
            db, kind, "critical", f"Device problem: {'; '.join(went_bad)}. Auto-clean paused."
        )
    if recovered:
        record_alert(db, "device", "ok", f"Device recovered: {', '.join(recovered)}")
    return problems
