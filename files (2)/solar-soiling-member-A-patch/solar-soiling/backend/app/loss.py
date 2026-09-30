"""Electrical loss vs. the clean reference panel, and the combined CNN + electrical score."""

from .config import settings


def electrical_loss_pct(p_test: float, p_ref: float, baseline: float = 1.0) -> float | None:
    """Loss vs. a clean panel. baseline = P_test/P_ref measured when both are clean.

    Returns None (unknown) when the reference gives no usable light signal (night, heavy
    overcast). 0.0 would read as "no loss", which is a different statement.
    """
    if p_ref < settings.min_ref_power_w:
        return None
    ratio = (p_test / p_ref) / baseline
    return max(0.0, min(100.0, (1 - ratio) * 100))


def combined_loss(severity: float | None, electrical: float) -> float:
    if severity is None:  # no recent image: rely on electrical only
        return electrical
    return 0.6 * severity + 0.4 * electrical
