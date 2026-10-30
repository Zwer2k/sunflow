"""Tests for device parameters and the site view."""

from typing import Any

import pytest
from sunflow.config.models import DeviceConfig, MeasuredBy
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary
from sunflow.devices.site import derive_parameter, device_parameters, site_status


def _entity(entity_id: str, unique_id: str, device_id: str) -> dict[str, Any]:
    return {"entity_id": entity_id, "unique_id": unique_id, "device_id": device_id}


def _state(value: str, unit: str | None = None) -> dict[str, Any]:
    return {"state": value, "attributes": {"unit_of_measurement": unit} if unit else {}}


@pytest.fixture
def snapshot() -> Snapshot:
    """Return a Sofar with two battery modules measuring a Huawei as PV ext."""
    sofar = {
        "battery_capacity_total": ("40", "%"),
        "battery_power_total": ("1.0", "kW"),
        "pv_power_total": ("2.0", "kW"),
        "active_power_pv_ext": ("0.40", "kW"),
        "active_power_pcc_total": ("0.1", "kW"),
        "active_power_load_sys": ("1.2", "kW"),
        "active_power_output_total": ("0.9", "kW"),
    }
    entities = [_entity(f"sensor.sofar_{k}", f"sofar_{k}", "d_inv") for k in sofar]
    states = {f"sensor.sofar_{k}": _state(v, u) for k, (v, u) in sofar.items()}
    for i, ah in enumerate(("104.8", "100.0"), start=1):
        entity_id = f"sensor.sofar_battery_{i}_pack_full_charge_capacity"
        entities.append(_entity(entity_id, f"sofar_battery_{i}_pack_full_charge_capacity", f"d_bat{i}"))
        states[entity_id] = _state(ah, "Ah")
    entities += [
        _entity("sensor.inverter_active_power", "hv_active_power", "d_hw"),
        _entity("sensor.inverter_input_power", "hv_input_power", "d_hw"),
    ]
    states["sensor.inverter_active_power"] = _state("430", "W")
    states["sensor.inverter_input_power"] = _state("450", "W")
    devices = [
        {"id": "d_inv"},
        {"id": "d_bat1", "via_device_id": "d_inv"},
        {"id": "d_bat2", "via_device_id": "d_inv"},
        {"id": "d_hw"},
    ]
    return Snapshot(entries=[], devices=devices, entities=entities, states=states, energy_prefs=None)


def _devices(measured: bool) -> list[DeviceConfig]:
    sofar = DeviceConfig(id="sofar", ha_device_id="d_inv", profile="solax_modbus/sofar", profile_version=1, name="Sofar")
    huawei = DeviceConfig(
        id="huawei",
        ha_device_id="d_hw",
        profile="huawei_solar/pv_only",
        profile_version=1,
        name="Huawei",
        measured_by=MeasuredBy(device_id="sofar", role="pv_ext_power") if measured else None,
    )
    return [sofar, huawei]


def test_derive_capacity(snapshot: Snapshot, library: ProfileLibrary) -> None:
    """Battery capacity is the sum of module Ah times the nominal voltage."""
    spec = library.profiles["solax_modbus/sofar"]["parameters"]["capacity_wh"]

    assert derive_parameter(spec, snapshot, "d_inv") == round((104.8 + 100.0) * 51.2)
    assert derive_parameter({"unit": "%", "default": 10}, snapshot, "d_inv") is None


def test_device_parameters_precedence(snapshot: Snapshot, library: ProfileLibrary) -> None:
    """Stored values win over derived values, which win over defaults."""
    device = _devices(measured=False)[0]
    device.parameters = {"max_charge": 8000}

    params = {p["name"]: p for p in device_parameters(library.profiles["solax_modbus/sofar"], device, snapshot)}

    assert params["max_charge"]["effective"] == 8000
    assert params["capacity_wh"]["effective"] == params["capacity_wh"]["derived"]
    assert params["min_soc"]["effective"] == 10
    # Visible since Sunflow controls: the grid target matters for everyone then
    assert params["grid_offset"]["advanced"] is False


def test_site_cross_check_and_total(snapshot: Snapshot, library: ProfileLibrary) -> None:
    """A measured PV plant is cross-checked and counted once in the total."""
    site = site_status(_devices(measured=True), library.profiles, snapshot)

    (check,) = site.cross_checks
    assert check.own_value == 430
    assert check.measured_value == pytest.approx(400)
    assert check.plausible is True
    # Sofar PV (DC) + Huawei AC; Sofar's PV ext reading is not added again
    assert site.pv_total == pytest.approx(2000 + 430)
    assert site.hints == []


def test_site_hint_for_unassigned_pv_ext(snapshot: Snapshot, library: ProfileLibrary) -> None:
    """Producing PV ext without an assigned device yields a hint."""
    site = site_status(_devices(measured=False), library.profiles, snapshot)

    assert site.cross_checks == []
    assert len(site.hints) == 1


def test_battery_capacity_from_assigned_modules() -> None:
    """Automatic: the inverter's sub-devices; chosen in the setup: exactly those modules."""
    from types import SimpleNamespace

    from sunflow.config.models import DeviceConfig
    from sunflow.devices.site import battery_module_ids, derive_parameter

    spec = {"unit": "Wh", "derive": {"sum_key": "pack_full_charge_capacity", "factor": 51.2}}
    devices = [
        {"id": "inv"},
        {"id": "m1", "via_device_id": "inv"},
        {"id": "m2", "via_device_id": "inv"},
        {"id": "m3", "via_device_id": "other"},
    ]
    entities = [
        {"entity_id": f"sensor.{m}", "unique_id": f"{m}_pack_full_charge_capacity", "device_id": m} for m in ("m1", "m2", "m3")
    ]
    snapshot = SimpleNamespace(devices=devices, entities=entities, states={e["entity_id"]: {"state": "100"} for e in entities})
    inverter = DeviceConfig(ha_device_id="inv", profile="p", profile_version=1, name="WR")

    assert battery_module_ids(snapshot, inverter) == ["m1", "m2"]
    assert derive_parameter(spec, snapshot, "inv", inverter) == 2 * 5120

    inverter.battery_modules = ["m1", "m3"]
    assert derive_parameter(spec, snapshot, "inv", inverter) == 2 * 5120
    inverter.battery_modules = ["m1"]
    assert derive_parameter(spec, snapshot, "inv", inverter) == 5120
