"""Cost-benefit rule (A7): clean only when the energy value recovered beats the cost of cleaning.

    clean if  loss% x daily_kWh x tariff x days_until_rain  >  water + pump energy + labor

All parameters live in config/.env. For a 10-20 W demo panel the rupee values are tiny, so the
demo runs with the same parameters as a real plant (default: 1 MW) and the numbers scale.
"""

from .config import settings


def daily_energy_kwh() -> float:
    return settings.plant_kwp * settings.peak_sun_hours


def cleaning_cost_inr() -> float:
    pump_kwh = settings.pump_w * settings.clean_duration_s / 3600 / 1000
    return (
        settings.water_cost_per_clean
        + pump_kwh * settings.tariff_inr_per_kwh
        + settings.labor_cost_per_clean
    )


def days_until_rain(rain_prob: int | None) -> float:
    """Rain expected within 24 h washes the panel anyway: the clean only pays for ~1 day."""
    if rain_prob is not None and rain_prob >= settings.rain_threshold_pct:
        return 1.0
    return settings.dry_days_horizon


def benefit_inr(loss_pct: float, days: float) -> float:
    return loss_pct / 100 * daily_energy_kwh() * settings.tariff_inr_per_kwh * days


def net_benefit_inr(loss_pct: float, rain_prob: int | None) -> float:
    return benefit_inr(loss_pct, days_until_rain(rain_prob)) - cleaning_cost_inr()
