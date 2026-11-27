"""Tests for battery modules described by unit profiles."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sunflow.analysis.battery_module_history import module_day
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.devices.battery_modules import battery_modules
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary

KEYS = {
    "pack_soc": "52", "pack_total_voltage": "51.4", "pack_current": "-3.1", "pack_temperature_1": "18", "pack_temperature_2": "21",
    "pack_temperature_mos": "35", "cell_max_voltage": "3.312", "cell_min_voltage": "3.297", "soh": "98", "pack_cycles": "211",
    "pack_full_charge_capacity": "104.8", "pack_alarm": "None", "pack_fault": "Cell overvoltage",
}


# solax_modbus before 2026-09: SoC and voltage of a module without the "pack_" prefix
OLDER_KEYS = {("soc" if k == "pack_soc" else "total_voltage" if k == "pack_total_voltage" else k): v for k, v in KEYS.items()}


def _snapshot(nested: bool, keys: dict[str, str] = KEYS) -> Snapshot:
    """Two modules below the inverter, or below a tower controller (extended battery data)."""
    parent = "tower" if nested else "inverter"
    devices = [
        {"id": "inverter", "name": "Sofar", "identifiers": [["solax_modbus", "Sofar"]]},
        {"id": "tower", "name": "Sofar Battery 1", "identifiers": [["solax_modbus", "Sofar", "battery_1"]], "via_device_id": "inverter"},
        {"id": "m10", "name": "Sofar Battery 1/10", "model": "BTS 5K", "identifiers": [["solax_modbus", "Sofar", "battery_1_10"]], "via_device_id": parent},
        {"id": "m2", "name": "Sofar Battery 1/2", "model": "BTS 5K", "identifiers": [["solax_modbus", "Sofar", "battery_1_2"]], "via_device_id": parent},
    ]
    entities, states = [], {}
    for device in ("m10", "m2"):
        for key, value in keys.items():
            entity_id = f"sensor.{device}_{key}"
            entities.append({"entity_id": entity_id, "unique_id": f"Sofar_{device}_{key}", "device_id": device})
            states[entity_id] = {"state": value, "attributes": {}}
    # The tower controller has no SoC of its own: no module
    entities.append({"entity_id": "sensor.tower_dcdc_loss", "unique_id": "Sofar_battery_1_bdu_dcdc_loss", "device_id": "tower"})
    return Snapshot(entries=[], devices=devices, entities=entities, states=states, energy_prefs=None)


def _config(chosen: list[str] | None = None) -> SunflowConfig:
    config = SunflowConfig(
        devices=[DeviceConfig(id="bat", name="Sofar", profile="solax_modbus/sofar", profile_version=1, ha_device_id="inverter")]
    )
    if chosen is not None:
        config.battery_modules["bat"] = chosen
    return config


@pytest.mark.parametrize(
    ("nested", "keys"),
    [
        pytest.param(False, KEYS, id="below_inverter"),
        pytest.param(True, KEYS, id="below_tower"),
        pytest.param(False, OLDER_KEYS, id="older_solax_modbus"),
    ],
)
def test_modules_with_values_and_problems(library: ProfileLibrary, nested: bool, keys: dict[str, str]) -> None:
    """Modules in natural order; temperatures of the cells (not MOS) aggregated; "None" is no alarm."""
    (battery,) = battery_modules(_config(), library, _snapshot(nested, keys))

    assert [m["name"] for m in battery["modules"]] == ["Sofar Battery 1/2", "Sofar Battery 1/10"]
    module = battery["modules"][0]
    assert module["values"] == {
        "soc": 52.0, "voltage": 51.4, "current": -3.1, "temperature_max": 21.0, "temperature_min": 18.0,
        "cell_voltage_max": 3.312, "cell_voltage_min": 3.297, "soh": 98.0, "cycles": 211.0, "capacity_full_ah": 104.8,
    }
    assert module["status"] == {"alarm": None, "fault": "Cell overvoltage"}
    assert module["model"] == "BTS 5K"


def test_chosen_modules_win(library: ProfileLibrary) -> None:
    """Modules chosen in the setup replace the ones found below the inverter."""
    (battery,) = battery_modules(_config(["m10"]), library, _snapshot(nested=False))

    assert [m["id"] for m in battery["modules"]] == ["m10"]


class StatisticsClient:
    """Answers statistics with one row per entity at the first slot."""

    def __init__(self, means: dict[str, float]) -> None:
        """Means per entity id."""
        self.means = means

    async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
        """Answer recorder/statistics_during_period."""
        start = datetime.fromisoformat(data["start_time"]).timestamp() * 1000
        return {e: [{"start": start, "mean": m}] for e, m in self.means.items()}


async def test_module_day() -> None:
    """Highest of the temperatures, cell spread in mV; values a module lacks are left out."""
    start = datetime(2026, 11, 21, 23, tzinfo=UTC)
    modules = [
        {"id": "m1", "entities": {"soc": ["sensor.soc"], "temperature_max": ["sensor.t1", "sensor.t2"],
                                  "cell_voltage_max": ["sensor.hi"], "cell_voltage_min": ["sensor.lo"]}},
    ]
    client = StatisticsClient({"sensor.soc": 40, "sensor.t1": 17, "sensor.t2": 19.5, "sensor.hi": 3.31, "sensor.lo": 3.295})

    (day,) = await module_day(client, modules, start, start + timedelta(minutes=10), timedelta(minutes=5))

    assert day == {"id": "m1", "soc": [40, None], "temperature_max": [19.5, None], "cell_spread_mv": [15.0, None]}
