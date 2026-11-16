"""Heating rod: relays per phase, modes by season, switched on by hand."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sunflow.api.devices import ManualDevice, _manual_setup
from sunflow.config.models import (
    DeviceConfig,
    DeviceOverride,
    FlexSettings,
    HeatingSeason,
    HeatPumpSettings,
    SunflowConfig,
)
from sunflow.control.controller import relay_states
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import ExecutorError, plan_steps
from sunflow.planning.heating_season import (
    by_dates,
    by_temperature,
    daily_means,
    uses_seasons,
    with_season,
)

SNAPSHOT = Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None)
NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)
ROD = DeviceConfig(id="rod", ha_device_id="manual:rod", profile="generic/relays", profile_version=1, name="Heizstab", kind="heater")
HEAT_PUMP = DeviceConfig(
    id="hp", ha_device_id="manual:hp", profile="generic/heat_pump", profile_version=1, name="WP",
    heat_pump=HeatPumpSettings(rod_id="rod"),
)


@pytest.mark.parametrize(
    ("count", "bound", "day", "expected"),
    [
        pytest.param(0, ["l1", "l2", "l3"], 0, {"relay_l1": "off", "relay_l2": "off", "relay_l3": "off"}, id="off"),
        pytest.param(1, ["l1", "l2", "l3"], 0, {"relay_l1": "on", "relay_l2": "off", "relay_l3": "off"}, id="one, day 0"),
        pytest.param(1, ["l1", "l2", "l3"], 1, {"relay_l1": "off", "relay_l2": "on", "relay_l3": "off"}, id="one, day 1"),
        pytest.param(2, ["l1", "l2", "l3"], 2, {"relay_l1": "on", "relay_l2": "off", "relay_l3": "on"}, id="two, day 2"),
        pytest.param(3, ["l1", "l2", "l3"], 5, {"relay_l1": "on", "relay_l2": "on", "relay_l3": "on"}, id="all"),
        pytest.param(1, ["l1", "l3"], 1, {"relay_l1": "off", "relay_l2": "off", "relay_l3": "on"}, id="two relays"),
    ],
)
def test_relay_states(count: int, bound: list[str], day: int, expected: dict[str, str]) -> None:
    """The planned number of phases is on; which ones moves on every day."""
    assert relay_states(count, bound, day) == expected


def test_relays_setup() -> None:
    """Each relay keeps its phase; the steps are 0 … n times the power of one."""
    overrides, parameters, steps = _manual_setup(
        ManualDevice(
            name="Heizstab",
            type="relays",
            kind="heater",
            relay_entities=["switch.rod_l1", None, "switch.rod_l3"],
            relay_power_w=2000,
            temperature_entity="sensor.tank",
            tank_l=300,
        ),
        SNAPSHOT,
    )

    assert {role: o.entity_id for role, o in overrides.items()} == {
        "switch_l1": "switch.rod_l1",
        "switch_l3": "switch.rod_l3",
        "temperature": "sensor.tank",
    }
    assert steps == {"0": 0, "1": 2000, "2": 4000}
    assert parameters == {"max_power": 4000, "tank_l": 300}


def test_relay_switch_state_from_runtime() -> None:
    """A switch step takes its state from the call (the relay chosen); anything but on/off is refused."""
    step = [{"switch": {"role": "switch_l2", "state": "{{ relay_l2 }}"}}]
    roles = {"switch_l2": "switch.rod_l2"}

    (call,) = plan_steps(step, {"relay_l2": "on"}, [], "ram", roles)

    assert (call.service, call.entity_id) == ("turn_on", "switch.rod_l2")
    with pytest.raises(ExecutorError):
        plan_steps(step, {"relay_l2": "maybe"}, [], "ram", roles)


@pytest.mark.parametrize(
    ("means", "active", "expected"),
    [
        pytest.param([16, 14, 13, 12], False, True, id="three cold days start it"),
        pytest.param([12, 16, 14, 13], False, False, id="two cold days are not enough"),
        pytest.param([14, 16, 17, 18], True, False, id="three warm days end it"),
        pytest.param([14, 16, 14, 18], True, True, id="mixed days keep it"),
        pytest.param([10, 10], False, False, id="too little history"),
    ],
)
def test_season_by_temperature(means: list[float], active: bool, expected: bool) -> None:
    """Three days in a row on one side of 15 °C switch the season; otherwise it stays."""
    assert by_temperature(means, HeatingSeason(active=active)) is expected


@pytest.mark.parametrize(
    ("day", "start", "end", "expected"),
    [
        pytest.param(date(2026, 12, 24), "10-01", "04-30", True, id="winter over new year"),
        pytest.param(date(2026, 7, 1), "10-01", "04-30", False, id="summer"),
        pytest.param(date(2026, 4, 30), "10-01", "04-30", True, id="last day"),
        pytest.param(date(2026, 6, 1), "05-01", "09-30", True, id="within one year"),
    ],
)
def test_season_by_dates(day: date, start: str, end: str, expected: bool) -> None:
    """From start to end date, also across the turn of the year."""
    assert by_dates(day, HeatingSeason(start=start, end=end)) is expected


def test_daily_means_skip_today_and_short_days() -> None:
    """Only full days before today count."""
    hourly = [(datetime(2026, 11, 13, h, tzinfo=UTC), 10.0) for h in range(24)]
    hourly += [(datetime(2026, 11, 14, h, tzinfo=UTC), 4.0) for h in range(10)]
    hourly += [(datetime(2026, 11, 15, h, tzinfo=UTC), 0.0) for h in range(24)]

    assert daily_means(hourly, date(2026, 11, 15), UTC) == [10.0]


def _config(active: bool, override: DeviceOverride | None = None, linked: bool = True) -> SunflowConfig:
    return SunflowConfig(
        devices=[ROD, HEAT_PUMP] if linked else [ROD],
        planning={"rod": FlexSettings(strategy="cost", heating_mode="off", summer_mode="pv_only")},
        heating_season=HeatingSeason(active=active),
        overrides={"rod": override} if override else {},
    )


@pytest.mark.parametrize(
    ("active", "override", "strategy", "paused"),
    [
        pytest.param(True, None, "cost", True, id="heating season: off"),
        pytest.param(False, None, "pv_only", False, id="summer: only PV"),
        pytest.param(True, DeviceOverride(strategy="fast"), "fast", False, id="on by hand in winter"),
        pytest.param(True, DeviceOverride(target=70), "cost", True, id="a target alone does not switch it on"),
        pytest.param(False, DeviceOverride(paused=True), "pv_only", True, id="paused by hand in summer"),
    ],
)
def test_season_modes(active: bool, override: DeviceOverride | None, strategy: str, paused: bool) -> None:
    """The season's mode replaces the strategy, "off" pauses; a strategy set by hand wins."""
    settings, is_paused = _config(active, override).effective_flex(ROD, NOW)

    assert (settings.strategy, is_paused) == (strategy, paused)


def test_without_season_modes() -> None:
    """A consumer without season modes keeps its strategy all year."""
    config = SunflowConfig(devices=[ROD, HEAT_PUMP], heating_season=HeatingSeason(active=True))

    assert config.effective_flex(ROD, NOW) == (FlexSettings(), False)
    assert not uses_seasons(config)
    assert uses_seasons(_config(True))


def test_season_modes_need_a_heat_pump() -> None:
    """Season modes of a rod no heat pump links to are not used."""
    config = _config(True, linked=False)

    assert config.effective_flex(ROD, NOW) == (config.flex_of("rod"), False)
    assert not uses_seasons(config)


def test_season_change_is_stored() -> None:
    """Only a change is stored, with its time."""
    config = _config(True)

    assert not with_season(config, True, NOW)
    assert with_season(config, False, NOW)
    assert (config.heating_season.active, config.heating_season.changed_at) == (False, NOW)


def test_manual_on_ends_against_the_season(tmp_path: Path) -> None:
    """Switched on by hand while its season mode is off: ends after manual_max_h."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sunflow.api.control import router
    from sunflow.config.store import ConfigStore

    store = ConfigStore(tmp_path / "config.json")
    store.save(_config(True))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.store = store
    client = TestClient(app)

    response = client.put("/api/v1/control/devices/rod", json={"strategy": "fast"})

    assert response.status_code == 200
    until = store.load().overrides["rod"].until
    assert until is not None
    assert timedelta(hours=23) < until - datetime.now(UTC) <= timedelta(hours=24)
