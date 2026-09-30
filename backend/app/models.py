"""ORM tables: the four blueprint schemas plus a few extra columns (marked +)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Always hands back timezone-aware UTC datetimes (SQLite drops tzinfo, Postgres keeps it)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def ts():
    return mapped_column(UTCDateTime, default=now, index=True)


class PanelReading(Base):
    __tablename__ = "panel_readings"
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_type: Mapped[str] = mapped_column(String(10), index=True)  # test | reference
    voltage: Mapped[float]
    current: Mapped[float]  # amps
    power: Mapped[float]  # watts
    temp: Mapped[float | None]
    humidity: Mapped[float | None]
    timestamp: Mapped[datetime] = ts()


class ImageCapture(Base):
    __tablename__ = "image_captures"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str | None]
    label: Mapped[str | None]
    confidence: Mapped[float | None]
    severity_score: Mapped[float | None]  # raw 0-100 from the class probabilities
    severity_pct: Mapped[float | None]  # calibrated expected power-loss %, needs severity_map.json
    model: Mapped[str] = mapped_column(String(10), default="stub")  # cnn | stub
    context: Mapped[str] = mapped_column(String(12), default="routine")  # routine|post_clean|manual
    used: Mapped[bool] = mapped_column(Boolean, default=True)  # False -> ignored by the engine
    note: Mapped[str | None]  # why an image was not used
    timestamp: Mapped[datetime] = ts()


class SoilingEvent(Base):
    __tablename__ = "soiling_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    combined_loss: Mapped[float]
    electrical_loss: Mapped[float | None]  # +
    cnn_severity: Mapped[float | None]  # +
    level: Mapped[str]  # ok | watch | clean_recommended | critical
    action: Mapped[str]  # + none | notify | clean | defer | wait | blocked
    reason: Mapped[str | None]  # + human-readable explanation of the decision
    net_benefit_inr: Mapped[float | None]  # + energy value saved minus cleaning cost
    timestamp: Mapped[datetime] = ts()


class CleaningCycle(Base):
    __tablename__ = "cleaning_cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    triggered_at: Mapped[datetime] = ts()
    status: Mapped[str] = mapped_column(
        default="triggered"
    )  # + triggered|running|verifying|complete
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # +
    pre_loss: Mapped[float]
    post_loss: Mapped[float | None]
    result: Mapped[str | None]  # success | insufficient


class Alert(Base):
    """Things worth telling a human about (also sent to Telegram)."""

    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # level_change|cleaning_result|device_offline|...
    level: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str]
    cycle_id: Mapped[int | None]
    timestamp: Mapped[datetime] = ts()


class DeviceState(Base):
    """One row per data source (test / reference panel channel)."""

    __tablename__ = "device_state"
    source: Mapped[str] = mapped_column(String(10), primary_key=True)
    last_seen: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    fault: Mapped[str | None]
