"""Open-Meteo 24-hour rain probability (no API key needed)."""

import httpx

from .config import settings


def max_rain_probability_24h() -> int | None:
    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            timeout=5,
            params={
                "latitude": settings.weather_lat,
                "longitude": settings.weather_lon,
                "hourly": "precipitation_probability",
                "forecast_hours": 24,
            },
        )
        r.raise_for_status()
        vals = [v for v in r.json()["hourly"]["precipitation_probability"] if v is not None]
        return max(vals) if vals else None
    except Exception:
        return None  # unknown forecast: don't block cleaning
