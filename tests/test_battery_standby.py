"""Tests for learning the battery's own draw."""

from dataclasses import replace

import pytest
from sunflow.analysis.efficiency import EfficiencyEstimate
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary
from sunflow.devices.site import device_parameters
from sunflow.planning.battery_standby import learns_standby, standby_of, store_learned

ESTIMATE = EfficiencyEstimate(
    efficiency=0.84, round_trip=0.71, hours=2000, charged_kwh=990, discharged_kwh=720, stored_change_kwh=0,
    cycles=49.7, reliable=True, plausible=True, source="counters", conversion_round_trip=0.87, standby_w=70.6,
)
SNAPSHOT = Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None)


@pytest.mark.parametrize(
    ("estimate", "standby"),
    [
        pytest.param(ESTIMATE, 71, id="plausible"),
        pytest.param(replace(ESTIMATE, standby_w=900.0), 300, id="capped"),
        pytest.param(replace(ESTIMATE, plausible=False), None, id="implausible"),
        pytest.param(replace(ESTIMATE, standby_w=None), None, id="no-split"),
        pytest.param(None, None, id="no-estimate"),
    ],
)
def test_standby_of(estimate: EfficiencyEstimate | None, standby: float | None) -> None:
    """Only a plausible split of the counters gives a standby draw."""
    assert standby_of(estimate) == standby


@pytest.mark.parametrize(
    ("learned", "value", "changed"),
    [
        pytest.param({}, 71.0, True, id="first"),
        pytest.param({"standby_w": 70.5}, 71.0, False, id="moved-a-little"),
        pytest.param({"standby_w": 60.0}, 71.0, True, id="moved-clearly"),
    ],
)
def test_store_learned(learned: dict[str, float], value: float, changed: bool) -> None:
    """Small moves of the daily estimate do not rewrite the configuration."""
    device = DeviceConfig(id="bat", ha_device_id="b", profile="solax_modbus/sofar", profile_version=1, name="Sofar", learned=learned)
    config = SunflowConfig(devices=[device])

    assert store_learned(config, {"bat": value}) is changed


@pytest.mark.parametrize(
    ("parameters", "effective"),
    [
        pytest.param({}, 71.0, id="learned"),
        pytest.param({"standby_w": 40.0}, 40.0, id="own-value-wins"),
    ],
)
def test_learned_value_is_the_suggestion(library: ProfileLibrary, parameters: dict[str, float], effective: float) -> None:
    """The learned value takes the place of a value from sensors; an own value wins."""
    profile = library.profiles["solax_modbus/sofar"]
    device = DeviceConfig(
        id="bat", ha_device_id="b", profile="solax_modbus/sofar", profile_version=1, name="Sofar",
        parameters=parameters, learned={"standby_w": 71.0},
    )

    (param,) = [p for p in device_parameters(profile, device, SNAPSHOT) if p["name"] == "standby_w"]

    assert learns_standby(profile)
    assert (param["derived"], param["learned"], param["effective"]) == (71.0, True, effective)


async def test_runner_learns_once_a_day(monkeypatch: pytest.MonkeyPatch, library: ProfileLibrary) -> None:
    """The estimate of the counters lands in the device and is saved; the next call within the day does nothing."""
    from types import SimpleNamespace

    from sunflow.analysis.energy import BatteryCounters
    from sunflow.shadow import runner as module
    from sunflow.shadow.runner import ShadowRunner

    device = DeviceConfig(id="bat", ha_device_id="b", profile="solax_modbus/sofar", profile_version=1, name="Sofar")
    config = SunflowConfig(devices=[device])
    saved: list[SunflowConfig] = []
    estimates: list[str] = []

    async def estimate(client: object, charge: str, *args: object) -> EfficiencyEstimate:
        estimates.append(charge)
        return ESTIMATE

    async def snapshot() -> Snapshot:
        return SNAPSHOT

    counters = BatteryCounters(device_id="bat", name="Sofar", charge="in", discharge="out", soc="soc", capacity_wh=20000)
    monkeypatch.setattr(module, "battery_counters", lambda *args: [counters])
    monkeypatch.setattr(module, "estimate_from_counters", estimate)
    runner = SimpleNamespace(
        _standby_at=None, _store=SimpleNamespace(load=lambda: config, save=saved.append), _library=library,
        snapshots=SimpleNamespace(get=snapshot), _client=object(),
    )

    await ShadowRunner._update_learned_standby(runner)  # type: ignore[arg-type]
    await ShadowRunner._update_learned_standby(runner)  # type: ignore[arg-type]

    assert estimates == ["in"]
    assert [c.devices[0].learned for c in saved] == [{"standby_w": 71}]
