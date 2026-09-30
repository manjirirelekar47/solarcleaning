"""One place that writes an alert row and sends the notification (transitions only)."""

from .models import Alert
from .notify import notify


def record_alert(db, kind: str, level: str, message: str, cycle_id: int | None = None) -> None:
    db.add(Alert(kind=kind, level=level, message=message, cycle_id=cycle_id))
    db.commit()
    notify(message)
