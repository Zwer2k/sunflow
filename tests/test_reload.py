"""Reloading the integration of a silent device, only where it is allowed."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.control.reload import MAX_RELOADS_PER_DAY, ReloadWatchdog
from sunflow.devices.discovery import Snapshot

NOW = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
PROFILE = {"profile": "test/meter", "roles": {"grid_power": {"key": "grid", "unit": "W"}, "battery_soc": {"key": "soc", "unit": "%"}}}


class FakeHA:
    """Records the actions called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self.calls.append((domain, service, data))


def _snapshot(state: str) -> Snapshot:
    return Snapshot(
        entries=[],
        devices=[{"id": "h", "config_entries": ["entry1"], "primary_config_entry": "entry1"}],
        entities=[
            {"entity_id": "sensor.grid", "unique_id": "x_grid", "device_id": "h"},
            {"entity_id": "sensor.soc", "unique_id": "x_soc", "device_id": "h"},
        ],
        states={
            "sensor.grid": {"entity_id": "sensor.grid", "state": state, "attributes": {"unit_of_measurement": "W"}},
            "sensor.soc": {"entity_id": "sensor.soc", "state": state, "attributes": {"unit_of_measurement": "%"}},
        },
        energy_prefs=None,
    )


def _config(minutes: int | None = 10) -> SunflowConfig:
    device = DeviceConfig(id="d", ha_device_id="h", profile="test/meter", profile_version=1, name="Sofar", reload_after_min=minutes)
    return SunflowConfig(devices=[device])


async def _run(watchdog: ReloadWatchdog, config: SunflowConfig, state: str, minutes: int, busy: set[str] | None = None) -> None:
    """One check a minute."""
    for minute in range(minutes + 1):
        await watchdog.check(config, _snapshot(state), NOW + timedelta(minutes=minute), busy or set())


@pytest.mark.parametrize(
    ("config", "state", "busy", "reloads"),
    [
        pytest.param(_config(), "unavailable", set(), 1, id="silent-allowed"),
        pytest.param(_config(None), "unavailable", set(), 0, id="not-allowed"),
        pytest.param(_config(), "0", set(), 0, id="zero-is-a-value"),
        pytest.param(_config(), "unavailable", {"d"}, 0, id="device-test-running"),
        pytest.param(_config(30), "unavailable", set(), 0, id="not-long-enough"),
    ],
)
async def test_reloads_only_where_allowed(config: SunflowConfig, state: str, busy: set[str], reloads: int) -> None:
    """After its minutes without values, once; never without permission, with values or during a test."""
    ha = FakeHA()
    watchdog = ReloadWatchdog(ha, lambda: {"test/meter": PROFILE})

    await _run(watchdog, config, state, 20, busy)

    assert ha.calls == [("homeassistant", "reload_config_entry", {"entry_id": "entry1"})] * reloads


async def test_pause_and_daily_limit() -> None:
    """Still silent: again after the pause, at most a few times a day."""
    ha = FakeHA()
    watchdog = ReloadWatchdog(ha, lambda: {"test/meter": PROFILE})

    await _run(watchdog, _config(), "unknown", 45)
    assert len(ha.calls) == 2

    await _run(watchdog, _config(), "unknown", 24 * 60)
    assert len(ha.calls) == MAX_RELOADS_PER_DAY


async def test_gap_starts_the_silence_anew() -> None:
    """After Home Assistant was away, it gets its minutes again before a reload."""
    ha = FakeHA()
    watchdog = ReloadWatchdog(ha, lambda: {"test/meter": PROFILE})
    await watchdog.check(_config(), _snapshot("unavailable"), NOW, set())

    later = NOW + timedelta(minutes=30)
    for minute in range(5):
        await watchdog.check(_config(), _snapshot("unavailable"), later + timedelta(minutes=minute), set())

    assert ha.calls == []
