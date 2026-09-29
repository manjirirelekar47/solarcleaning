"""ORM tables: panel_readings, image_captures, soiling_events, cleaning_cycles."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
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
    severity_score: Mapped[float]  # 0-100
    timestamp: Mapped[datetime] = ts_col()


class SoilingEvent(Base):
    __tablename__ = "soiling_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    combined_loss: Mapped[float]
    electrical_loss: Mapped[float]
    cnn_severity: Mapped[float | None]
    alert_level: Mapped[str]  # ok | watch | clean_recommended | critical
    action: Mapped[str]  # none | notify | clean | defer | wait
    timestamp: Mapped[datetime] = ts_col()


class CleaningCycle(Base):
    __tablename__ = "cleaning_cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    triggered_at: Mapped[datetime] = ts_col()
    status: Mapped[str] = mapped_column(default="triggered")  # triggered|running|verifying|complete
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pre_loss: Mapped[float]
    post_loss: Mapped[float | None]
    result: Mapped[str | None]  # success | insufficient
