"""Tests for finding optimizers, assigning them to strings and placing them on the roof."""

from pathlib import Path
from typing import Any

import pytest
from sunflow.config.models import (
    DeviceConfig,
    OptimizerPlacement,
    OptimizerSettings,
    SunflowConfig,
)
from sunflow.devices import optimizers as module
from sunflow.devices.discovery import Snapshot

HUAWEI_KEYS = ("output_power", "input_voltage", "input_current", "output_voltage", "output_current", "temperature")
TIGO_KEYS = ("power", "voltage_in", "current", "voltage_out", "temperature", "pwm")


def _snapshot() -> Snapshot:
    devices = [
        {"id": "inv", "name": "Inverter", "identifiers": [["huawei_solar", "HV1"]]},
        {"id": "o2", "name": "JV22B2", "identifiers": [["huawei_solar", "JV22B2"]], "via_device_id": "inv"},
        {"id": "o1", "name": "JV22B1", "identifiers": [["huawei_solar", "JV22B1"]], "via_device_id": "inv"},
        {"id": "a2", "name": "Tigo Optimizer A2", "identifiers": [["tigo_rs485", "e_A2"]]},
        {"id": "b1", "name": "Tigo Optimizer B1", "identifiers": [["tigo_rs485", "e_B1"]]},
        {"id": "x", "name": "Tigo Optimizer Garage", "identifiers": [["tigo_rs485", "e_Garage"]]},
    ]
    entities, states = [], {}
    # The inverter has an input power but is no optimizer
    entities.append({"entity_id": "sensor.inv_input_power", "unique_id": "HV1_input_power", "device_id": "inv"})
    for device, keys in (("o1", HUAWEI_KEYS), ("o2", HUAWEI_KEYS), ("a2", TIGO_KEYS), ("b1", TIGO_KEYS), ("x", TIGO_KEYS)):
        for key in keys:
            entity_id = f"sensor.{device}_{key}"
            entities.append({"entity_id": entity_id, "unique_id": f"{device.upper()}_{key}", "device_id": device})
            states[entity_id] = {"state": "300" if "power" in key else "40", "attributes": {}}
    states["sensor.o1_output_power"] = {"state": "0.31", "attributes": {"unit_of_measurement": "kW"}}
    return Snapshot(entries=[], devices=devices, entities=entities, states=states, energy_prefs=None)


def _strings(huawei_in_use: tuple[bool, bool] = (True, False)) -> list[dict[str, Any]]:
    return [
        {"key": f"hw:{i}", "device_id": "hw", "ha_device_id": "inv", "in_use": huawei_in_use[i - 1]} for i in (1, 2)
    ] + [{"key": f"sofar:{i}", "device_id": "sofar", "ha_device_id": "sof", "in_use": True} for i in (1, 2)]


def _found(config: SunflowConfig, monkeypatch: pytest.MonkeyPatch, strings: list[dict[str, Any]]) -> dict[str, module.Optimizer]:
    monkeypatch.setattr(module, "plant_strings", lambda *_: strings)
    found, _ = module.optimizers(config, {}, _snapshot())
    return {o.label: o for o in found}


def test_discovers_huawei_and_tigo_optimizers() -> None:
    """Sub-devices with optimizer values; "_power" does not take "_output_power"."""
    found = module.discover(_snapshot())

    assert [(o.source, o.label) for o in found] == [
        ("huawei", "JV22B1"), ("huawei", "JV22B2"), ("tigo", "A2"), ("tigo", "B1"), ("tigo", "Garage"),
    ]
    assert found[0].entities["power"] == "sensor.o1_output_power"
    assert found[2].entities["current_in"] == "sensor.a2_current"


@pytest.mark.parametrize(
    ("in_use", "expected"),
    [
        pytest.param((True, False), "hw:1", id="one-string-in-use"),
        # Both in use: the integration does not tell which string
        pytest.param((True, True), None, id="unknown-string"),
    ],
)
def test_proposes_strings_and_positions(monkeypatch: pytest.MonkeyPatch, in_use: tuple[bool, bool], expected: str | None) -> None:
    """Huawei by the string in use; Tigo by its label "A2": string A of the plant without own optimizers, module 2."""
    found = _found(SunflowConfig(), monkeypatch, _strings(in_use))

    assert (found["JV22B1"].string, found["JV22B1"].col, found["JV22B2"].col) == (expected, 0, 1)
    assert (found["A2"].string, found["A2"].row, found["A2"].col) == ("sofar:1", 0, 1)
    assert found["B1"].string == "sofar:2"
    assert found["Garage"].string is None
    assert found["JV22B1"].values["power"] == pytest.approx(310)
    assert found["JV22B1"].string_auto


def test_placements_win_over_the_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chosen string, "" for none, a position and a name are kept."""
    config = SunflowConfig(
        devices=[DeviceConfig(id="sofar", ha_device_id="sof", profile="p", profile_version=1, name="Sofar")],
        optimizer_settings=OptimizerSettings(placements=[
            OptimizerPlacement(ha_device_id="a2", string="sofar:2", row=1, col=3, name="Süd 5", rotated=True),
            OptimizerPlacement(ha_device_id="o1", string=""),
        ]),
    )
    found = _found(config, monkeypatch, _strings())

    assert (found["A2"].string, found["A2"].row, found["A2"].col, found["A2"].name, found["A2"].string_auto) == ("sofar:2", 1, 3, "Süd 5", False)
    assert found["A2"].rotated
    assert not found["B1"].rotated
    assert found["JV22B1"].string is None


def test_proposed_places_do_not_cover_placed_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """A module put on the place another one is proposed for pushes that one aside."""
    config = SunflowConfig(optimizer_settings=OptimizerSettings(placements=[OptimizerPlacement(ha_device_id="o2", row=0, col=0)]))
    found = _found(config, monkeypatch, _strings())

    assert {(o.row, o.col) for o in (found["JV22B1"], found["JV22B2"])} == {(0, 0), (0, 1)}


async def test_layout_separators_are_stored(tmp_path: Path) -> None:
    """Separators between rows and places are kept per string."""
    from types import SimpleNamespace

    from sunflow.api.optimizers import set_optimizers
    from sunflow.config.models import StringLayout
    from sunflow.config.store import ConfigStore

    store = ConfigStore(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=store)))

    await set_optimizers(request, OptimizerSettings(layouts={"sofar:1": StringLayout(row_breaks=[2], col_breaks=[3, 5])}))

    assert ConfigStore(tmp_path).load().optimizer_settings.layouts["sofar:1"] == StringLayout(row_breaks=[2], col_breaks=[3, 5])
