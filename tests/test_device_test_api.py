"""The device test's writes go through the same variables as control."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sunflow.api import device_test as api
from sunflow.config.models import DeviceConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import PlannedCall
from sunflow.devices.profiles import ProfileLibrary

KEYS = ["vpp_status", "vpp_remote_control", "vpp_allow_ac_charging", "vpp_power", "vpp_time", "battery_soc", "battery_combined_power"]
DOMAINS = {"vpp_status": "select", "vpp_remote_control": "select", "vpp_allow_ac_charging": "select", "vpp_power": "number", "vpp_time": "number"}


async def test_percent_power_in_the_device_test(library: ProfileLibrary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Growatt's VPP power is a percentage: 500 W at 8000 W maximum charge is 6 %."""
    entities = [
        {"entity_id": f"{DOMAINS.get(k, 'sensor')}.sph_{k}", "unique_id": f"sph_{k}", "device_id": "dev"} for k in KEYS
    ]
    snapshot = Snapshot(entries=[], devices=[{"id": "dev"}], entities=entities, states={}, energy_prefs=None)
    written: list[PlannedCall] = []

    async def fetch(*_args: Any) -> Snapshot:
        return snapshot

    async def execute(_client: Any, planned: list[PlannedCall], *_args: Any, **_kwargs: Any) -> None:
        written.extend(planned)

    monkeypatch.setattr(api, "fetch_snapshot", fetch)
    monkeypatch.setattr(api, "execute", execute)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ha=None, settings=SimpleNamespace(data_dir=tmp_path), direct=SimpleNamespace(by_ha_device=lambda _id: None)))
    )
    device = DeviceConfig(
        id="sph", ha_device_id="dev", profile="solax_modbus/growatt_hybrid", profile_version=1, name="SPH",
        parameters={"capacity_wh": 20700, "max_charge": 8000, "max_discharge": 8000},
    )

    _, apply_action, mode = api._device_control(request, device, library.profiles[device.profile], [])
    await apply_action("force_charge", {"P": 500})

    assert next(c.data["value"] for c in written if c.entity_id == "number.sph_vpp_power") == 6
    assert mode is not None
