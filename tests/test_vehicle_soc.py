"""A car's SoC between its reports, from what the wallbox charged, learned from the reports."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sunflow.config.models import Vehicle
from sunflow.planning.vehicle_soc import SocEstimator

START = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
FIAT = Vehicle(id="fiat", name="Fiat 500e", capacity_wh=37_300, charge_efficiency_pct=90, soc_entity="sensor.fiat_soc")


def report(soc: float | str, at: datetime) -> dict[str, dict[str, str]]:
    """The car's SoC sensor as Home Assistant has it."""
    return {"sensor.fiat_soc": {"state": str(soc), "last_updated": at.isoformat()}}


def charge(estimator: SocEstimator, states: dict[str, dict[str, str]], watts: float, minutes: int, vehicle: Vehicle = FIAT) -> None:
    """One sample a minute while the wallbox charges the car with watts."""
    for minute in range(minutes + 1):
        estimator.observe([vehicle], states, {vehicle.id: watts}, START + timedelta(minutes=minute))


def test_estimate_between_reports() -> None:
    """Two hours at 7.4 kW with 90 %: 14.8 kWh, 13.3 kWh in a 37.3 kWh battery are 35.7 points."""
    estimator = SocEstimator()
    states = report(40, START)
    charge(estimator, states, 7400, 120)
    assert estimator.soc(FIAT, states) == (75.7, True)
    # The car reports again: its value counts
    assert estimator.soc(FIAT, report(80, START + timedelta(hours=2))) == (80.0, False)


def test_learns_from_the_next_report(tmp_path: Path) -> None:
    """35 points from 14.8 kWh at the wallbox: 37.3 kWh * 0.35 / 14.8 kWh = 88 % reach the battery."""
    estimator = SocEstimator()
    estimator.load(tmp_path)
    states = report(40, START)
    charge(estimator, states, 7400, 120)
    estimator.observe([FIAT], report(75, START + timedelta(hours=2)), {}, START + timedelta(hours=2))
    learned = estimator.as_dict(FIAT)
    assert learned["learned"] is True
    assert learned["charges"] == 1
    assert learned["efficiency"] == pytest.approx(37_300 * 0.35 / 14_800, abs=0.001)
    assert learned["error_pct"] == pytest.approx(0.7, abs=0.05)
    # Kept across a restart
    again = SocEstimator()
    again.load(tmp_path)
    assert again.efficiency(FIAT) == pytest.approx(learned["efficiency"])


@pytest.mark.parametrize(
    ("new_soc", "learned"),
    [
        pytest.param(100, False, id="full: the car may have stopped earlier"),
        pytest.param(41, False, id="implausibly little"),
        pytest.param(35, False, id="driven in between"),
    ],
)
def test_no_learning_from_odd_reports(new_soc: float, learned: bool) -> None:
    """Reports that say nothing about the charging efficiency are not learned."""
    estimator = SocEstimator()
    states = report(40, START)
    charge(estimator, states, 7400, 120)
    estimator.observe([FIAT], report(new_soc, START + timedelta(hours=3)), {}, START + timedelta(hours=3))
    assert estimator.as_dict(FIAT)["learned"] is learned


@pytest.mark.parametrize("state", [pytest.param("unavailable", id="unavailable"), pytest.param("0", id="zero from the cloud")])
def test_unreadable_sensor_keeps_the_estimate(state: str) -> None:
    """The car's cloud is gone for a while or sends 0 %: the last report plus what was charged since."""
    estimator = SocEstimator()
    charge(estimator, report(40, START), 3700, 60)
    later = report(state, START + timedelta(hours=1))
    estimator.observe([FIAT], later, {FIAT.id: 3700}, START + timedelta(hours=1))
    assert estimator.soc(FIAT, later) == (48.9, True)


def test_switched_off() -> None:
    """Without the estimate only the car's own report counts."""
    vehicle = FIAT.model_copy(update={"estimate_soc": False})
    estimator = SocEstimator()
    states = report(40, START)
    charge(estimator, states, 7400, 120, vehicle)
    assert estimator.soc(vehicle, states) == (40.0, False)
    assert estimator.soc(vehicle, report("unavailable", START)) == (None, False)
