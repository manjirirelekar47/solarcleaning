"""ORM tables: readings, images, soiling events, cleaning cycles, alerts, device state."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC so comparisons never crash."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def ts_col():
    return mapped_column(DateTime(timezone=True), default=now, index=True)


class PanelReading(Base):
    __tablename__ = "panel_readings"
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_type: Mapped[str] = mapped_column(String(10), index=True)  # test | reference
    voltage: Mapped[float]
    current: Mapped[float]  # amps
    power: Mapped[float]  # watts
    temp: Mapped[float | None]
    humidity: Mapped[float | None]
    timestamp: Mapped[datetime] = ts_col()


class ImageCapture(Base):
    __tablename__ = "image_captures"
    id: Mapped[int] = mapped_column(primary_key=True)
    image_path: Mapped[str]
    soiling_class: Mapped[str]
    confidence: Mapped[float]
    severity_score: Mapped[float]  # raw 0-100 value
    model: Mapped[str] = mapped_column(String(10), default="stub")  # stub | cnn
    context: Mapped[str] = mapped_column(String(12), default="routine")  # routine|post_clean|manual
    severity_pct: Mapped[float | None]  # calibrated severity (B4); None until calibrated
    used: Mapped[bool] = mapped_column(Boolean, default=True)  # False = ignored for decisions
    note: Mapped[str | None]  # why the image was not used (dark frame, low confidence, stub...)
    timestamp: Mapped[datetime] = ts_col()


class SoilingEvent(Base):
    __tablename__ = "soiling_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    combined_loss: Mapped[float | None]  # None when no loss figure exists (see reason)
    electrical_loss: Mapped[float | None]
    cnn_severity: Mapped[float | None]
    alert_level: Mapped[str]  # ok | watch | clean_recommended | critical | unknown
    action: Mapped[str]  # none | notify | clean | defer | wait
    net_benefit_inr: Mapped[float | None]  # A7
    reason: Mapped[str | None]  # why this action / state (insufficient_light, cooldown, ...)
    timestamp: Mapped[datetime] = ts_col()


class CleaningCycle(Base):
    __tablename__ = "cleaning_cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    triggered_at: Mapped[datetime] = ts_col()
    status: Mapped[str] = mapped_column(default="triggered")  # triggered|running|verifying|complete
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pre_loss: Mapped[float]
    post_loss: Mapped[float | None]
    result: Mapped[str | None]  # success | insufficient | failed
    attempt: Mapped[int] = mapped_column(default=1)  # 1 or 2 (one retry)
    post_image_id: Mapped[int | None]


class Alert(Base):
    """Written only on transitions (level change, cleaning, device, sensor fault)."""

    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = ts_col()
    kind: Mapped[str] = mapped_column(String(20))  # level_change|cleaning|device|sensor_fault
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str]
    cycle_id: Mapped[int | None]


class DeviceState(Base):
    __tablename__ = "device_state"
    source: Mapped[str] = mapped_column(String(10), primary_key=True)  # test | reference
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    fault: Mapped[str | None]
