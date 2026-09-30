import pytest
from app.decision_engine import decide
from app.loss import combined_loss, electrical_loss_pct


@pytest.mark.parametrize(
    "loss,rain,active,level,action",
    [
        (4.99, None, False, "ok", "none"),
        (5.0, None, False, "watch", "notify"),
        (9.99, 90, False, "watch", "notify"),
        (10.0, None, False, "clean_recommended", "clean"),
        (15.0, 80, False, "clean_recommended", "defer"),  # rain deferral
        (15.0, 59, False, "clean_recommended", "clean"),  # just under rain threshold
        (15.0, None, True, "clean_recommended", "wait"),  # cleaning already running
        (20.0, 100, False, "critical", "clean"),  # critical ignores rain
        (25.0, None, True, "critical", "wait"),
    ],
)
def test_decide(loss, rain, active, level, action):
    d = decide(loss, rain, active)
    assert (d.level, d.action) == (level, action)


def test_electrical_loss():
    assert electrical_loss_pct(8.0, 10.0) == pytest.approx(20.0)
    assert electrical_loss_pct(10.0, 10.0) == pytest.approx(0.0)
    assert electrical_loss_pct(0.0, 0.01) == 0.0  # night guard
    assert electrical_loss_pct(11.0, 10.0) == 0.0  # never negative
    assert electrical_loss_pct(9.0, 10.0, baseline=0.9) == pytest.approx(0.0)


def test_combined_weights():
    assert combined_loss(50, 10) == pytest.approx(34.0)  # .6*50 + .4*10
    assert combined_loss(None, 10) == 10  # no image
