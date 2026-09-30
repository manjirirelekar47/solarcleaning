"""Telegram alerts; falls back to stdout when no bot token is configured."""

import httpx

from .config import settings


def notify(text: str) -> None:
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
