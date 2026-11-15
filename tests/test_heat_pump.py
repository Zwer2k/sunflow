"""Heat pump: readings from the measurement variants and setup by hand."""

from fastapi import HTTPException
import pytest
from sunflow.api.devices import ManualDevice, _manual_setup
from sunflow.devices.discovery import Snapshot
from sunflow.devices.heat_pump import HeatPumpReading, reading

SNAPSHOT = Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        pytest.param(
            {"power": 1000.0},
            HeatPumpReading(electric_w=1000.0, electric_source="measured"),
            id="total power",
        ),
        pytest.param(
            {"power_l1": 400.0, "power_l2": 300.0, "power_l3": 300.0},
            HeatPumpReading(
                electric_w=1000.0,
                electric_source="measured",
                phases_w={"l1": 400.0, "l2": 300.0, "l3": 300.0},
            ),
            id="power per phase",
        ),
        pytest.param(
            {"current_l1": 2.0, "current_l3": 1.0},
            HeatPumpReading(electric_w=690.0, electric_source="measured", phases_w={"l1": 460.0, "l3": 230.0}),
            id="current of two phases",
        ),
        pytest.param(
            {"power": 1000.0, "heat_power": 3500.0},
            HeatPumpReading(
                electric_w=1000.0, heat_w=3500.0, cop=3.5,
                electric_source="measured", heat_source="measured", cop_source="computed",
            ),
            id="cop from heat and power",
        ),
        pytest.param(
            {"power": 1000.0, "cop": 3.0},
            HeatPumpReading(
                electric_w=1000.0, heat_w=3000.0, cop=3.0,
                electric_source="measured", heat_source="computed", cop_source="measured",
            ),
            id="heat from power and cop",
        ),
        pytest.param(
            {"heat_power": 4000.0, "cop": 4.0},
            HeatPumpReading(
                electric_w=1000.0, heat_w=4000.0, cop=4.0,
                electric_source="computed", heat_source="measured", cop_source="measured",
            ),
            id="power from heat and cop",
        ),
        pytest.param(
            {"power": 50.0, "heat_power": 20.0},
            HeatPumpReading(electric_w=50.0, heat_w=20.0, electric_source="measured", heat_source="measured"),
            id="idle: no cop",
        ),
        pytest.param(
            {"power": 3500.0, "heat_power": 1000.0},
            HeatPumpReading(
                electric_w=3500.0, heat_w=1000.0, cop=1000.0 / 3500.0,
                electric_source="measured", heat_source="measured", cop_source="computed",
                problem="cop_implausible",
            ),
            id="heat and power swapped",
        ),
        pytest.param(
            {"power": 1000.0, "heat_power": 3000.0, "cop": 4.5},
            HeatPumpReading(
                electric_w=1000.0, heat_w=3000.0, cop=4.5,
                electric_source="measured", heat_source="measured", cop_source="measured",
                problem="cop_mismatch",
            ),
            id="measured cop disagrees",
        ),
        pytest.param({}, HeatPumpReading(), id="nothing measured"),
    ],
)
def test_reading(values: dict[str, float], expected: HeatPumpReading) -> None:
    """Any two of power, heat and COP give the third; implausible readings are flagged."""
    assert reading(values) == expected


def test_reading_with_own_voltage() -> None:
    """Currents are multiplied by the configured voltage."""
    assert reading({"current_l1": 10.0}, voltage=240.0).electric_w == 2400.0


def test_setup_binds_phases_in_order() -> None:
    """Phase sensors keep their phase, an empty one leaves the phase out."""
    overrides, parameters, steps = _manual_setup(
        ManualDevice(
            name="Wärmepumpe",
            type="heat_pump",
            phase_current_entities=["sensor.wp_l1", None, "sensor.wp_l3"],
            voltage=235,
            heat_power_entity="sensor.wp_heat",
            outside_temperature_entity="sensor.wp_outside",
        ),
        SNAPSHOT,
    )

    assert {role: o.entity_id for role, o in overrides.items()} == {
        "current_l1": "sensor.wp_l1",
        "current_l3": "sensor.wp_l3",
        "heat_power": "sensor.wp_heat",
        "outside_temperature": "sensor.wp_outside",
    }
    assert (parameters, steps) == ({"voltage": 235}, None)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(ManualDevice(name="Wärmepumpe", type="heat_pump"), id="nothing"),
        pytest.param(
            ManualDevice(name="Wärmepumpe", type="heat_pump", heat_power_entity="sensor.wp_heat"), id="heat only"
        ),
        pytest.param(
            ManualDevice(name="Wärmepumpe", type="heat_pump", power_entity="switch.wp"), id="not a sensor"
        ),
    ],
)
def test_setup_needs_the_electrical_power(body: ManualDevice) -> None:
    """Without electrical power (or heat and COP) Sunflow cannot tell what the heat pump draws."""
    with pytest.raises(HTTPException):
        _manual_setup(body, SNAPSHOT)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            ManualDevice(name="Wärmepumpe", type="heat_pump", energy_entity="sensor.wp_energy"), id="energy counter"
        ),
        pytest.param(
            ManualDevice(name="Wärmepumpe", type="heat_pump", heat_power_entity="sensor.h", cop_entity="sensor.c"),
            id="heat and cop",
        ),
    ],
)
def test_setup_accepts(body: ManualDevice) -> None:
    """An energy counter alone or heat with COP are enough."""
    overrides, _, _ = _manual_setup(body, SNAPSHOT)

    assert overrides
