"""Alerts: every alert is stored in the alerts table (dashboard log) and pushed to Telegram."""

import httpx

from .config import settings
from .models import Alert


def notify(text: str):
    """Push a message to Telegram; falls back to printing when no bot token is configured."""
    if not settings.telegram_bot_token:
        print("ALERT:", text)
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=5,
        )
    except Exception as e:
        print("notify failed:", e)


def raise_alert(db, kind: str, level: str | None, message: str, cycle_id: int | None = None):
    db.add(Alert(kind=kind, level=level, message=message, cycle_id=cycle_id))
    db.commit()
    notify(message)
