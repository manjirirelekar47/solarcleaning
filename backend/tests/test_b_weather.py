from pathlib import Path  # noqa: F401

import pytest
from app import weather
from app.config import settings

RAW = {
    "hourly": {
        "time": ["2026-09-30T10:00", "2026-09-30T11:00", "2026-09-30T12:00"],
        "precipitation_probability": [10, 70, None],
        "precipitation": [0.0, 4.5, None],
    }
}


@pytest.fixture(autouse=True)
def _site(monkeypatch):
    monkeypatch.setattr(settings, "weather_lat", float("18.62"))
    monkeypatch.setattr(settings, "weather_lon", float("73.80"))
    monkeypatch.setattr(settings, "weather_cache_s", int("1800"))
    weather.reset_cache()
    yield
    weather.reset_cache()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_forecast_is_parsed_with_probability_and_mm():
    data = weather.get_forecast(fetch=lambda la, lo: RAW, now=Clock())
    assert data["max_probability"] == 70.0 and data["total_mm"] == 4.5 and data["hours"] == 2
    assert data["stale"] is False


def test_api_called_at_most_twice_per_hour():
    calls, clock = [], Clock()

    def fetch(la, lo):
        calls.append(1)
        return RAW

    for _ in range(10):  # every 5 minutes for ~50 minutes
        weather.get_forecast(fetch=fetch, now=clock)
        clock.t += 300
    assert len(calls) == 2  # one at t=0, one after the 30 minute cache expires


def test_network_failure_serves_stale_cache_then_raises_when_empty():
    clock = Clock()
    weather.get_forecast(fetch=lambda la, lo: RAW, now=clock)
    clock.t += 4000

    def boom(la, lo):
        raise OSError("offline")

    stale = weather.get_forecast(fetch=boom, now=clock)
    assert stale["stale"] is True and stale["total_mm"] == 4.5
    weather.reset_cache()
    with pytest.raises(weather.WeatherUnavailable):
        weather.get_forecast(fetch=boom, now=clock)


def test_changed_location_invalidates_cache(monkeypatch):
    clock, calls = Clock(), []
    fetch = lambda la, lo: calls.append((la, lo)) or RAW  # noqa: E731
    weather.get_forecast(fetch=fetch, now=clock)
    monkeypatch.setattr(settings, "weather_lat", float("19.07"))
    weather.get_forecast(fetch=fetch, now=clock)
    assert len(calls) == 2


def test_startup_check_rejects_default_location(monkeypatch):
    monkeypatch.setattr(settings, "weather_lat", float("0"))
    monkeypatch.setattr(settings, "weather_lon", float("0"))
    with pytest.raises(RuntimeError, match="0,0"):
        weather.assert_weather_configured()


def test_startup_check_rejects_out_of_range_and_accepts_real_site(monkeypatch):
    monkeypatch.setattr(settings, "weather_lat", float("95"))
    with pytest.raises(RuntimeError, match="range"):
        weather.assert_weather_configured()
    monkeypatch.setattr(settings, "weather_lat", float("18.62"))
    weather.assert_weather_configured()


def test_helpers_return_none_when_forecast_unknown(monkeypatch):
    def boom(la, lo):
        raise OSError("offline")

    monkeypatch.setattr(weather, "_http_fetch", boom)
    assert weather.max_rain_probability_24h() is None  # unknown forecast must not block cleaning
    assert weather.rain_mm_next_24h() is None


def test_max_rain_probability_uses_hourly_maximum(monkeypatch):
    monkeypatch.setattr(weather, "_http_fetch", lambda la, lo: RAW)
    assert weather.max_rain_probability_24h() == 70
