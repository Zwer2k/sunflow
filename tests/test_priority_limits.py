"""Priority limits checked against the live values before a load gets PV or battery energy."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sunflow.config.models import (
    DeviceConfig,
    OptimizerConfig,
    PriorityCondition,
    SunflowConfig,
)
from sunflow.control.controller import unmet_limit

ARRIVAL = datetime(2026, 11, 9, 17, 0, tzinfo=UTC)


def car(expected_arrival: datetime | None) -> SimpleNamespace:
    """The wallbox's plan info: a car at 59 % with target 80 %."""
    return SimpleNamespace(
        device_id="wb", name="Wallbox", unknown_vehicle=False, reason=None, current=59.0, vehicle_id=None,
        target=80.0, unit="%", expected_arrival=expected_arrival,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("condition", "soc_pct", "arrival", "blocked"),
    [
        pytest.param(("battery", 100.0), 97.5, None, False, id="battery at its 98 % charge limit"),
        pytest.param(("battery", 100.0), 90.0, None, True, id="battery below it"),
        pytest.param(("wb", 80.0), 97.5, ARRIVAL, False, id="car only expected back"),
        pytest.param(("wb", 80.0), 97.5, None, True, id="car plugged in below the limit"),
    ],
)
def test_unmet_limit(condition: tuple[str, float], soc_pct: float, arrival: datetime | None, blocked: bool) -> None:
    """A limit above the charge limit counts there; a car away holds nothing back."""
    config = SunflowConfig(
        devices=[
            DeviceConfig(id=key, ha_device_id=key, profile="generic/switch", profile_version=1, name=key) for key in ("wb", "ww")
        ],
        optimizer=OptimizerConfig(max_soc_pct=98.0),
        priorities=["battery", "wb", "ww"],
        priority_conditions={"ww": [PriorityCondition(device_id=condition[0], min_pct=condition[1])]},
    )
    live = SimpleNamespace(soc_wh=soc_pct * 200, levels={})

    assert (unmet_limit(config, "ww", live, 20000.0, [car(arrival)]) is not None) is blocked
