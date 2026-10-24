"""Tests for role binding, normalization and balance check."""

from typing import Any

import pytest

from sunflow.discovery import Snapshot
from sunflow.profiles import ProfileLibrary
from sunflow.resolver import RoleValue, check_balance, normalize, preview

SOFAR_STATES = {
    "battery_capacity_total": ("29", "%"),
    "battery_power_total": ("-1.2", "kW"),
    "pv_power_total": ("3.0", "kW"),
    "active_power_pv_ext": ("1.4", "kW"),
    "active_power_pcc_total": ("-0.5", "kW"),
    "active_power_load_sys": ("6.1", "kW"),
}


def _snapshot(states: dict[str, tuple[str, str]]) -> Snapshot:
    entities: list[dict[str, Any]] = []
    state_map: dict[str, dict[str, Any]] = {}
    for key, (state, unit) in states.items():
        entity_id = f"sensor.sofar_{key}"
        entities.append({"entity_id": entity_id, "unique_id": f"sofar_{key}", "device_id": "d_inv"})
        state_map[entity_id] = {"state": state, "attributes": {"unit_of_measurement": unit}}
    # Battery module with a colliding key suffix must not win over the inverter entity
    entities.append({"entity_id": "sensor.bat1_power", "unique_id": "sofar_battery_1_battery_power_total", "device_id": "d_bat"})
    state_map["sensor.bat1_power"] = {"state": "99", "attributes": {"unit_of_measurement": "kW"}}
    devices = [{"id": "d_inv"}, {"id": "d_bat", "via_device_id": "d_inv"}]
    return Snapshot(entries=[], devices=devices, entities=entities, states=state_map, energy_prefs=None)


def test_preview_normalizes(library: ProfileLibrary) -> None:
    """Values are bound, converted to W and the grid sign is flipped to import positive."""
    values = {v.role: v for v in preview(library.profiles["solax_modbus/sofar"], _snapshot(SOFAR_STATES), "d_inv")}

    assert values["battery_soc"].value == 29
    assert values["battery_power"].entity_id == "sensor.sofar_battery_power_total"
    assert values["battery_power"].value == pytest.approx(-1200)
    assert values["pv_power"].value == pytest.approx(3000)
    assert values["grid_power"].value == pytest.approx(500)
    assert values["grid_power"].unit == "W"


def test_preview_overrides(library: ProfileLibrary) -> None:
    """Overrides replace the entity and the sign of a role."""
    overrides = {
        "pv_power": {"entity_id": "sensor.sofar_active_power_pv_ext", "unit": None, "sign": None},
        "grid_power": {"sign": "import_positive"},
    }

    values = {v.role: v for v in preview(library.profiles["solax_modbus/sofar"], _snapshot(SOFAR_STATES), "d_inv", overrides)}

    assert values["pv_power"].entity_id == "sensor.sofar_active_power_pv_ext"
    assert values["pv_power"].profile_entity_id == "sensor.sofar_pv_power_total"
    assert values["pv_power"].value == pytest.approx(1400)
    assert values["grid_power"].sign == "import_positive"
    assert values["grid_power"].profile_sign == "export_positive"
    assert values["grid_power"].value == pytest.approx(-500)


def test_preview_missing_and_unavailable(library: ProfileLibrary) -> None:
    """Missing entities and unavailable states yield no value."""
    states = dict(SOFAR_STATES)
    del states["active_power_pv_ext"]
    states["pv_power_total"] = ("unavailable", "kW")

    values = {v.role: v for v in preview(library.profiles["solax_modbus/sofar"], _snapshot(states), "d_inv")}

    assert values["pv_ext_power"].missing
    assert values["pv_power"].value is None
    assert check_balance(list(values.values())) is None


@pytest.mark.parametrize(
    ("raw", "unit", "sign", "expected"),
    [
        pytest.param("1.5", "kW", None, (1500, "W"), id="kw_to_w"),
        pytest.param("2", "kWh", None, (2000, "Wh"), id="kwh_to_wh"),
        pytest.param("-0.3", "kW", "export_positive", (300, "W"), id="flip_grid"),
        pytest.param("0.3", "kW", "discharge_positive", (-300, "W"), id="flip_battery"),
        pytest.param("12", "°C", None, (12, "°C"), id="other_unit"),
        pytest.param("unknown", "kW", None, (None, None), id="unknown"),
        pytest.param("Standby", None, None, (None, None), id="text"),
    ],
)
def test_normalize(raw: str, unit: str | None, sign: str | None, expected: tuple[float | None, str | None]) -> None:
    """Conversion and sign handling."""
    assert normalize(raw, unit, sign) == expected


def _values(**watts: float) -> list[RoleValue]:
    return [
        RoleValue(role=role, key=role, entity_id=f"sensor.{role}", raw_state=None, raw_unit=None, value=value, unit="W", optional=False)
        for role, value in watts.items()
    ]


@pytest.mark.parametrize(
    ("values", "method", "plausible"),
    [
        pytest.param(_values(grid_power=10660, pv_power=0, pv_ext_power=0, battery_power=0, load_power=10650), "dc", True, id="dc_night_import"),
        pytest.param(_values(grid_power=-500, pv_power=3000, pv_ext_power=1400, battery_power=-1200, load_power=5100), "dc", True, id="dc_sunny"),
        pytest.param(_values(grid_power=-500, pv_power=3000, pv_ext_power=1400, battery_power=-1200, load_power=3700), "dc", False, id="dc_pv_double_counted"),
        # Measured on the Sofar: DC values include conversion losses the AC side does not see
        pytest.param(_values(grid_power=-20, pv_power=2700, pv_ext_power=460, battery_power=2100, inverter_power=30, load_power=480), "ac", True, id="ac_charging"),
        pytest.param(_values(grid_power=-20, inverter_power=30, pv_ext_power=460, load_power=1480), "ac", False, id="ac_mismatch"),
        pytest.param(_values(grid_power=40, pv_power=0, load_power=0), "dc", True, id="idle_floor"),
    ],
)
def test_check_balance(values: list[RoleValue], method: str, plausible: bool) -> None:
    """The computed load is compared to the reported load."""
    balance = check_balance(values)
    assert balance is not None
    assert balance.method == method
    assert balance.plausible is plausible


def test_check_balance_needs_grid_and_load() -> None:
    """Without grid or load values no balance is computed."""
    assert check_balance(_values(pv_power=100, load_power=100)) is None
    assert check_balance(_values(grid_power=100, load_power=100)) is None
