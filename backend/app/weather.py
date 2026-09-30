"""Rain forecast for the NEXT 24 HOURS (Open-Meteo hourly data, no API key) with a cache.

Merged from both members: hourly window (not "today's" daily max), 30-minute cache, stale-cache
fallback when the network fails, and a startup check that refuses the default 0,0 location.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import httpx

from .config import settings

API_URL = "https://api.open-meteo.com/v1/forecast"
_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "data": None}


class WeatherUnavailable(RuntimeError):
    """Raised when there is no fresh forecast and no cached one to fall back on."""


def assert_weather_configured() -> None:
    """Call at startup. Refuses to run with the default 0,0 location."""
    if settings.weather_lat == 0.0 and settings.weather_lon == 0.0:
        raise RuntimeError("WEATHER_LAT and WEATHER_LON are still 0,0. Set your site location.")
    if not (-90 <= settings.weather_lat <= 90 and -180 <= settings.weather_lon <= 180):
        raise RuntimeError("WEATHER_LAT/WEATHER_LON are out of range.")


def _http_fetch(lat: float, lon: float) -> dict:
    r = httpx.get(
        API_URL,
        timeout=5,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation_probability,precipitation",
            "forecast_hours": 24,
            "timezone": "auto",
        },
    )
    r.raise_for_status()
    return r.json()


def _parse(raw: dict) -> dict:
    hourly = raw.get("hourly") or {}
    probs = [float(v) for v in hourly.get("precipitation_probability") or [] if v is not None]
    mms = [float(v) for v in hourly.get("precipitation") or [] if v is not None]
    return {
        "max_probability": max(probs) if probs else None,
        "total_mm": round(sum(mms), 2) if mms else None,
        "hours": len(probs),
    }


def get_forecast(
    fetch: Callable[[float, float], dict] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> dict:
    """Return {'max_probability', 'total_mm', 'hours', 'fetched_age_s', 'stale'}.

    Hits the network at most once per WEATHER_CACHE_S. On failure it serves the last good
    forecast (marked stale) or raises WeatherUnavailable if there is none.
    """
    key = (settings.weather_lat, settings.weather_lon)
    fetch = fetch or _http_fetch
    with _lock:
        age = now() - _cache["at"]
        if _cache["key"] == key and _cache["data"] is not None and age < settings.weather_cache_s:
            return {**_cache["data"], "fetched_age_s": age, "stale": False}
        try:
            data = _parse(fetch(settings.weather_lat, settings.weather_lon))
        except Exception as exc:  # network, HTTP or parse failure
            if _cache["key"] == key and _cache["data"] is not None:
                return {**_cache["data"], "fetched_age_s": age, "stale": True}
            raise WeatherUnavailable(str(exc)) from exc
        _cache.update(key=key, at=now(), data=data)
        return {**data, "fetched_age_s": 0.0, "stale": False}


def max_rain_probability_24h() -> int | None:
    """Highest hourly rain chance (%) in the next 24 h; None = unknown (never blocks cleaning)."""
    try:
        p = get_forecast()["max_probability"]
    except WeatherUnavailable:
        return None
    return None if p is None else int(p)


def rain_mm_next_24h() -> float | None:
    try:
        return get_forecast()["total_mm"]
    except WeatherUnavailable:
        return None


def reset_cache() -> None:
    with _lock:
        _cache.update(key=None, at=0.0, data=None)
