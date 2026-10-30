"""Tests for integration and device discovery."""

from typing import Any

import pytest
from sunflow.devices.discovery import Snapshot, discover, energy_entity_ids
from sunflow.devices.profiles import ProfileLibrary

SOFAR_KEYS = ["charger_use_mode", "passive_mode_battery_charge_discharge", "battery_capacity_total"]


def _entity(entity_id: str, unique_id: str, device_id: str, entry_id: str) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "unique_id": unique_id,
        "device_id": device_id,
        "config_entry_id": entry_id,
        "disabled_by": None,
    }


@pytest.fixture
def snapshot() -> Snapshot:
    """Return a snapshot with Sofar (+ battery module), Huawei and an unrelated integration."""
    entries = [
        {"entry_id": "e_sofar", "domain": "solax_modbus", "title": "Sofar", "state": "loaded"},
        {"entry_id": "e_huawei", "domain": "huawei_solar", "title": "Huawei", "state": "loaded"},
        {"entry_id": "e_met", "domain": "met", "title": "Home", "state": "loaded"},
        {"entry_id": "e_meter", "domain": "shelly", "title": "Meter", "state": "loaded"},
    ]
    devices = [
        {"id": "d_inv", "config_entries": ["e_sofar"], "name": "Sofar Inverter", "manufacturer": "Sofar Solar", "model": "HYD10KTL-3P 2nd"},
        {"id": "d_bat", "config_entries": ["e_sofar"], "name": "Sofar Battery 1/1", "model": "BTS 5K", "via_device_id": "d_inv"},
        {"id": "d_hw", "config_entries": ["e_huawei"], "name": "Inverter", "model": "SUN2000-3KTL-L1"},
        {"id": "d_met", "config_entries": ["e_met"], "name": "Forecast"},
        {"id": "d_meter", "config_entries": ["e_meter"], "name": "Meter"},
    ]
    entities = [
        *(_entity(f"sensor.sofar_{k}", f"sofar_{k}", "d_inv", "e_sofar") for k in SOFAR_KEYS),
        _entity("sensor.sofar_battery_1_soc", "sofar_battery_1_soc", "d_bat", "e_sofar"),
        _entity("sensor.inverter_active_power", "hw_active_power", "d_hw", "e_huawei"),
        _entity("weather.home", "met_home", "d_met", "e_met"),
        _entity("sensor.meter_energy", "meter_energy", "d_meter", "e_meter"),
    ]
    states = {
        "sensor.sofar_battery_capacity_total": {"attributes": {"device_class": "battery"}},
    }
    energy_prefs = {
        "energy_sources": [{"type": "grid", "flow_from": [{"stat_energy_from": "sensor.meter_energy"}]}],
        "device_consumption": [],
    }
    names = {"solax_modbus": "SolaX Inverter Modbus", "huawei_solar": "Huawei Solar"}
    return Snapshot(entries=entries, devices=devices, entities=entities, states=states, energy_prefs=energy_prefs, integration_names=names)


def test_discover(snapshot: Snapshot, library: ProfileLibrary) -> None:
    """Supported and energy-dashboard integrations are listed with matched profiles."""
    result = {i.domain: i for i in discover(snapshot, library)}

    assert list(result) == ["huawei_solar", "solax_modbus", "shelly"]

    sofar = result["solax_modbus"]
    assert sofar.name == "SolaX Inverter Modbus"
    assert result["shelly"].name == "shelly"
    assert [d.name for d in sofar.devices] == ["Sofar Inverter"]
    inverter = sofar.devices[0]
    assert inverter.profile == "solax_modbus/sofar"
    assert "battery_window" in inverter.capabilities
    assert [c.name for c in inverter.components] == ["Sofar Battery 1/1"]
    assert inverter.components[0].profile is None

    assert result["huawei_solar"].devices[0].profile == "huawei_solar/pv_only"
    assert result["huawei_solar"].devices[0].capabilities == ["pv", "curtail"]
    assert inverter.components[0].capabilities == []

    shelly = result["shelly"]
    assert not shelly.supported
    assert shelly.in_energy_dashboard


def test_missing_required_key_prevents_match(snapshot: Snapshot, library: ProfileLibrary) -> None:
    """A device lacking a required control entity gets no profile."""
    snapshot.entities[:] = [e for e in snapshot.entities if "charger_use_mode" not in e["unique_id"]]

    sofar = next(i for i in discover(snapshot, library) if i.domain == "solax_modbus")

    assert sofar.devices[0].profile is None


def test_energy_entity_ids() -> None:
    """Entity ids are collected from nested energy preferences."""
    prefs = {"energy_sources": [{"type": "solar", "stat_energy_from": "sensor.pv", "config_entry_solar_forecast": ["abc"]}]}

    assert energy_entity_ids(prefs) == {"sensor.pv"}


@pytest.mark.parametrize(
    ("name", "strip", "expected"),
    [
        pytest.param("Inverter", [], "Huawei SUN2000-3KTL-L1", id="generic_name"),
        pytest.param("Sofar Inverter", [], "Sofar Inverter", id="descriptive_name"),
        pytest.param("evcc - Loadpoint Wallbox Carport [evcc]", ["evcc - ", "Loadpoint ", " [evcc]"], "Wallbox Carport", id="stripped"),
    ],
)
def test_display_name(name: str, strip: list[str], expected: str) -> None:
    """New devices get a readable name."""
    from sunflow.api.common import display_name
    from sunflow.devices.discovery import DiscoveredDevice

    device = DiscoveredDevice(
        device_id="d", name=name, manufacturer="Huawei", model="SUN2000-3KTL-L1", entity_count=1, profile="x/y"
    )

    assert display_name(device, {"name_strip": strip}) == expected


@pytest.mark.parametrize("model", ["Loadpoint Wallbox Carport [evcc]", "Ladepunkt Wallbox Carport [evcc]"])
def test_evcc_loadpoint_in_any_language(library: ProfileLibrary, model: str) -> None:
    """evcc names devices in the HA language; English and German loadpoints match."""
    from sunflow.devices.discovery import match_profile

    device = {"id": "lp", "model": model}
    entities = [
        {"entity_id": "sensor.evcc_wallbox_carport_charge_power", "unique_id": "evcc_intg.evcc_wallbox_carport_charge_power"},
        {"entity_id": "select.evcc_wallbox_carport_mode", "unique_id": "evcc_intg.evcc_wallbox_carport_mode"},
    ]

    assert match_profile(library, "evcc_intg", device, entities, False) == "evcc_intg/loadpoint"
    assert match_profile(library, "evcc_intg", {"id": "v", "model": "Fahrzeug Fiat 500e [evcc]"}, entities, False) is None


async def test_snapshot_cache_shares_fetches() -> None:
    """Concurrent and repeated requests reuse one fetch; registries live longer than states."""
    import asyncio
    from datetime import timedelta

    from sunflow.devices.discovery import SnapshotCache

    calls: dict[str, int] = {}

    class Client:
        async def command(self, msg_type: str, **data: object) -> list:
            calls[msg_type] = calls.get(msg_type, 0) + 1
            return []

        async def get_config_entries(self) -> list:
            return await self.command("config_entries/get")

        async def get_device_registry(self) -> list:
            return await self.command("device_registry")

        async def get_entity_registry(self) -> list:
            return await self.command("entity_registry")

        async def get_energy_prefs(self) -> None:
            return None

        async def get_states(self) -> list:
            await asyncio.sleep(0)
            return await self.command("get_states")

    cache = SnapshotCache(Client(), registry_ttl=timedelta(minutes=2), states_ttl=timedelta(0))

    await asyncio.gather(*(cache.get() for _ in range(5)))
    assert calls["device_registry"] == 1

    await cache.get()
    # States expired (ttl 0), registries are reused
    assert calls["device_registry"] == 1
    assert calls["get_states"] > 1

    await cache.get(fresh=True)
    assert calls["device_registry"] == 2


async def test_snapshot_cache_bridges_short_gaps() -> None:
    """A briefly unknown value keeps its last valid state; a long outage shows up."""
    from datetime import UTC, datetime, timedelta

    import sunflow.devices.discovery as discovery
    from sunflow.devices.discovery import SnapshotCache

    answers = [
        [{"entity_id": "sensor.soc", "state": "55"}],
        [{"entity_id": "sensor.soc", "state": "unknown"}],
        [{"entity_id": "sensor.soc", "state": "unknown"}],
    ]

    class Client:
        generation = 1

        async def command(self, msg_type: str, **data: object) -> list:
            return []

        async def get_config_entries(self) -> list:
            return []

        async def get_device_registry(self) -> list:
            return []

        async def get_entity_registry(self) -> list:
            return []

        async def get_energy_prefs(self) -> None:
            return None

        async def get_states(self) -> list:
            return answers.pop(0)

    cache = SnapshotCache(Client(), states_ttl=timedelta(0))
    assert (await cache.get()).states["sensor.soc"]["state"] == "55"
    assert (await cache.get()).states["sensor.soc"]["state"] == "55"

    cache._last_good["sensor.soc"] = (cache._last_good["sensor.soc"][0], datetime.now(UTC) - discovery.GAP_BRIDGE * 2)
    assert (await cache.get()).states["sensor.soc"]["state"] == "unknown"
