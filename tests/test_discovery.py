"""Tests for integration and device discovery."""

from typing import Any

import pytest

from sunflow.discovery import Snapshot, discover, energy_entity_ids
from sunflow.profiles import ProfileLibrary

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
    assert result["huawei_solar"].devices[0].capabilities == ["pv"]
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
