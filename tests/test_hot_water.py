"""Tests for learning when and how much hot water is drawn."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.planning.hot_water import usage_from

# A 300 l tank: Wh per kelvin
TANK = 300 * 1.163
# Monday
START = datetime(2026, 10, 5, tzinfo=UTC)


def _history(days: int) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
    """Heated at 03:00 by 2.8 kW; showers at 07:00 (5 K), dishes at 20:00 (2 K)."""
    temperatures, heating = [], []
    temperature = 50.0
    for hour in range(days * 24):
        moment = START + timedelta(hours=hour)
        temperatures.append((moment, temperature))
        power = 2800.0 if moment.hour == 3 else 0.0
        heating.append((moment, power))
        temperature += power / TANK - {7: 5.0, 20: 2.0}.get(moment.hour, 0.0)
    return temperatures, heating


def test_learns_the_daily_profile() -> None:
    """The draw per hour of the day from heating and temperature; the main usage is the morning."""
    usage = usage_from(*_history(8), TANK, UTC)

    assert usage is not None
    assert usage.days == 8
    assert usage.hourly_wh["workday"][7] == pytest.approx(5 * TANK)
    assert usage.hourly_wh["workday"][20] == pytest.approx(2 * TANK)
    # Heating that warms the tank is no draw
    assert usage.hourly_wh["workday"][3] == pytest.approx(0, abs=1)
    assert usage.daily_wh == pytest.approx(7 * TANK, rel=0.01)
    # Monday 06:00: the showers start at 07:00
    assert usage.next_peak(START + timedelta(hours=6), UTC) == START + timedelta(hours=7)
    assert usage.drawn_wh(START + timedelta(hours=7), START + timedelta(hours=7, minutes=15), UTC) == pytest.approx(5 * TANK / 4)


def test_needs_a_week() -> None:
    """Fewer days say nothing about habits."""
    assert usage_from(*_history(5), TANK, UTC) is None


def test_heater_is_hot_before_the_usual_usage(library) -> None:
    """Without a ready time the tank is planned hot for the showers, with the learned daily need."""
    from sunflow.config.models import DeviceConfig, RoleOverride, SunflowConfig
    from sunflow.devices.discovery import Snapshot
    from sunflow.planning.plan import flex_loads

    states = {
        "select.heizstab": {"state": "off", "attributes": {"options": ["off", "2,8kW"]}},
        "sensor.speicher_oben": {"state": "45", "attributes": {"unit_of_measurement": "°C"}},
    }
    snapshot = Snapshot(entries=[], devices=[], entities=[], states=states, energy_prefs=None)
    rod = DeviceConfig(
        id="ww", ha_device_id="manual:ww", profile="generic/stepped", profile_version=1, name="Warmwasser",
        kind="heater", power_steps={"off": 0, "2,8kW": 2800}, parameters={"max_power": 2800, "tank_l": 300},
        role_overrides={"level": RoleOverride(entity_id="select.heizstab"), "temperature": RoleOverride(entity_id="sensor.speicher_oben")},
    )
    usage = usage_from(*_history(8), TANK, UTC)
    # Tuesday noon; the next showers are Wednesday 07:00 (learned and planned in the same time zone)
    start = START + timedelta(days=8, hours=12)

    _, (info,), _, _ = flex_loads(SunflowConfig(devices=[rod]), {"generic/stepped": library.profiles["generic/stepped"]},
                                  snapshot, [start + timedelta(minutes=15 * i) for i in range(96)], UTC,
                                  hot_water={"ww": usage})

    assert info.deadline == START + timedelta(days=9, hours=7)
    assert info.daily_wh == pytest.approx(usage.daily_wh)
    assert info.usage_days == 8
    assert len(info.drawn_wh) == 96


def _district_heating(days: int) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
    """No own heating: drawn 1 K per hour, district heating warms it from below 44 °C back to 47 °C."""
    temperatures, heating = [], []
    temperature = 47.0
    for hour in range(days * 24):
        moment = START + timedelta(hours=hour)
        temperatures.append((moment, temperature))
        heating.append((moment, 0.0))
        temperature = 47.0 if temperature < 44.5 else temperature - 1.0
    return temperatures, heating


@pytest.mark.parametrize(
    ("history", "floor"),
    [
        pytest.param(_district_heating(8), 44.0, id="other-heat-source"),
        pytest.param(_history(8), None, id="own-heating-only"),
    ],
)
def test_learns_what_another_heat_source_keeps(history: tuple, floor: float | None) -> None:
    """Warming up without the own heating is another heat source; it starts at about the same temperature."""
    usage = usage_from(*history, TANK, UTC)

    assert usage is not None
    assert usage.floor_c == floor


def test_forecast_does_not_fall_below_the_other_heat_source() -> None:
    """Without own heating planned the tank forecast stays at what district heating keeps."""
    from types import SimpleNamespace

    from sunflow.planning.plan import FlexInfo, expected_levels

    info = FlexInfo(
        device_id="ww", name="Warmwasser", kind="heater", current=46.0, target=60.0, unit="°C", need_wh=0.0,
        deadline=START + timedelta(hours=5), max_power_w=2800.0, wh_per_unit=TANK, loss_wh_per_day=24 * TANK,
        level_range=(15.0, 60.0), level_floor=44.0,
    )
    slots = [SimpleNamespace(start=START + timedelta(hours=h), end=START + timedelta(hours=h + 1), flex_w={}) for h in range(5)]

    assert expected_levels(info, slots) == [45.0, 44.0, 44.0, 44.0, 44.0]
