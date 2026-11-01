"""Tests for learning what a car uses and planning it before it is back."""

from datetime import UTC, datetime, time, timedelta

import pytest
from sunflow.config.models import (
    ChargingProfile,
    DeviceConfig,
    SunflowConfig,
    Vehicle,
    VehicleDetection,
)
from sunflow.planning.habits import departures_from
from sunflow.planning.vehicle_usage import usage_from

# Monday
START = datetime(2026, 10, 5, tzinfo=UTC)


def _socs(days: int, glitch: bool = False) -> list[tuple[datetime, float]]:
    """Driving at 08:00 and 17:00 (10 % each), charged back at 22:00."""
    result, soc = [], 80.0
    for hour in range(days * 24):
        moment = START + timedelta(hours=hour)
        result.append((moment, 0.0 if glitch and hour == 30 else soc))
        soc += {8: -10.0, 17: -10.0, 22: 20.0}.get(moment.hour, 0.0)
    return result


def test_learns_the_daily_use() -> None:
    """Drops are use, rises are charging; a sensor dropping to 0 for an hour is no trip."""
    usage = usage_from(_socs(15, glitch=True), UTC)

    assert usage is not None
    assert usage.days == 15
    assert usage.hourly_pct["workday"][8] == pytest.approx(10)
    assert usage.hourly_pct["workday"][22] == 0
    assert usage.daily_pct == pytest.approx(20, rel=0.02)
    assert usage.used_pct(START + timedelta(hours=7, minutes=30), START + timedelta(hours=9), UTC) == pytest.approx(10)


@pytest.mark.parametrize(
    "socs",
    [
        pytest.param(_socs(13), id="too-few-days"),
        # The sensor reports only now and then: no day is covered well
        pytest.param([p for i, p in enumerate(_socs(20)) if i % 2 == 0], id="sparse-sensor"),
    ],
)
def test_nothing_learned_without_enough_data(socs: list[tuple[datetime, float]]) -> None:
    """A few days or a sparse sensor would distort the plan: nothing is learned."""
    assert usage_from(socs, UTC) is None


def test_arrival_needs_more_stays_than_departure() -> None:
    """Plug-in times of real stays; an expected arrival needs five of them per day type."""
    rows = []
    for day in range(6):
        monday = START + timedelta(days=7 * (day // 5) + day % 5)
        rows += [{"s": "on", "lu": (monday + timedelta(hours=17, minutes=30)).timestamp()},
                 {"s": "off", "lu": (monday + timedelta(days=1, hours=7)).timestamp()}]

    learned = departures_from(rows, UTC)

    assert learned.arrival_workday == time(17, 30)
    assert learned.arrival_weekend is None
    assert departures_from(rows[:8], UTC).arrival_workday is None


def test_car_on_the_way_is_planned_from_its_arrival(library) -> None:
    """The only car of the wallbox is away: planned from 17:30 with the SoC it usually comes back with."""
    from zoneinfo import ZoneInfo

    from sunflow.devices.discovery import Snapshot
    from sunflow.planning.habits import Departure
    from sunflow.planning.plan import expected_levels, flex_loads

    entities = [{"entity_id": "binary_sensor.goe_car", "unique_id": "216505-binary_sensor-car-1", "device_id": "goe"}]
    states = {"binary_sensor.goe_car": {"state": "off", "attributes": {}}, "sensor.fiat_soc": {"state": "60", "attributes": {}}}
    snapshot = Snapshot(entries=[], devices=[{"id": "goe"}], entities=entities, states=states, energy_prefs=None)
    wallbox = DeviceConfig(id="wb", ha_device_id="goe", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")
    config = SunflowConfig(
        devices=[wallbox],
        vehicles=[Vehicle(id="fiat", name="Fiat", capacity_wh=37500, charge_efficiency_pct=90, soc_entity="sensor.fiat_soc")],
        charging=[ChargingProfile(vehicle_id="fiat", wallbox_id="wb")],
        # Recognized by card only: unplugged, the car is not "the only vehicle"
        vehicle_detection={"wb": VehicleDetection(single=False)},
    )
    tz = ZoneInfo("UTC")
    usage = usage_from(_socs(15), tz)
    habit = Departure(workday=time(7), weekend=None, sessions=10, arrival_workday=time(17, 30))
    # Tuesday noon: the car drives again at 17:00 before it is back
    start = START + timedelta(days=15, hours=12)
    slots = [start + timedelta(minutes=15 * i) for i in range(96)]

    loads, (info,), _, _ = flex_loads(config, library.profiles, snapshot, slots, tz, departures={"wb": habit}, vehicle_usages={"fiat": usage})

    assert (info.vehicle, info.vehicle_method, info.expected_arrival) == ("Fiat", "expected", start.replace(hour=17, minute=30))
    # Half of the 17:00 trip is done by 17:30: back with 55 %, charged to 100 % (no target set)
    assert info.need_wh == pytest.approx((100 - 55) / 100 * 37500 / 0.9)
    assert not any(loads[0].available[:22]) and loads[0].available[22]
    # The chart drains it from 60 % now to 55 % on its return
    plan_slots = [type("S", (), {"start": s, "end": s + timedelta(minutes=15), "flex_w": {}})() for s in slots[:24]]
    assert expected_levels(info, plan_slots)[21] == pytest.approx(55)


def test_several_cars_are_not_guessed(library) -> None:
    """Two cars at the wallbox: which one comes back is unknown, nothing is planned before."""
    from zoneinfo import ZoneInfo

    from sunflow.devices.discovery import Snapshot
    from sunflow.planning.habits import Departure
    from sunflow.planning.plan import flex_loads

    entities = [{"entity_id": "binary_sensor.goe_car", "unique_id": "216505-binary_sensor-car-1", "device_id": "goe"}]
    snapshot = Snapshot(entries=[], devices=[{"id": "goe"}], entities=entities,
                        states={"binary_sensor.goe_car": {"state": "off", "attributes": {}}}, energy_prefs=None)
    wallbox = DeviceConfig(id="wb", ha_device_id="goe", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")
    cars = [Vehicle(id=i, name=i, capacity_wh=40000, soc_entity=f"sensor.{i}") for i in ("a", "b")]
    config = SunflowConfig(devices=[wallbox], vehicles=cars, charging=[ChargingProfile(vehicle_id=c.id, wallbox_id="wb") for c in cars])
    usage = usage_from(_socs(15), ZoneInfo("UTC"))
    habit = Departure(workday=time(7), weekend=None, sessions=10, arrival_workday=time(17, 30))
    start = START + timedelta(days=15, hours=12)

    loads, (info,), _, _ = flex_loads(config, library.profiles, snapshot, [start + timedelta(minutes=15 * i) for i in range(96)],
                                      ZoneInfo("UTC"), departures={"wb": habit}, vehicle_usages={"a": usage, "b": usage})

    assert loads == []
    assert info.reason == "Fahrzeug nicht angesteckt"


@pytest.mark.parametrize(
    ("expected", "charged_wh"),
    [
        # Only expected back: charged where cheaper than the median price, nothing forced
        pytest.param(True, 2 * 11000 * 0.25, id="expected-car"),
        pytest.param(False, 10000, id="plugged-in-car"),
    ],
)
def test_expected_car_is_no_must(expected: bool, charged_wh: float) -> None:
    """A car not there yet gets cheap energy only; a plugged-in one its whole need."""
    from dataclasses import replace

    from sunflow.planning.optimizer import BatterySpec, FlexLoad, SlotInput, optimize

    prices = [0.20 if t in (2, 3) else 0.35 for t in range(8)]
    inputs = [SlotInput(start=START + timedelta(minutes=15 * t), pv_w=0, load_w=0, price=p, feed_in=0.0) for t, p in enumerate(prices)]
    battery = BatterySpec(capacity_wh=1000, soc_wh=200, min_soc_wh=200, max_charge_w=100, max_discharge_w=100)
    car = FlexLoad(name="Fiat", energy_wh=10000, max_power_w=11000, available=[True] * 8, expected=expected)
    if expected:
        car = replace(car, unmet_cost=0.35)

    plan = optimize(inputs, battery, flex=[car])

    assert sum(s.flex_w["Fiat"] for s in plan.slots) * 0.25 == pytest.approx(charged_wh, rel=0.01)


def _away_setup(presence_state: str) -> tuple[SunflowConfig, object, list[datetime]]:
    from sunflow.devices.discovery import Snapshot

    entities = [{"entity_id": "binary_sensor.goe_car", "unique_id": "216505-binary_sensor-car-1", "device_id": "goe"}]
    states = {
        "binary_sensor.goe_car": {"state": "off", "attributes": {}},
        "sensor.fiat_soc": {"state": "60", "attributes": {}},
        "device_tracker.fiat": {"state": presence_state, "attributes": {}},
    }
    snapshot = Snapshot(entries=[], devices=[{"id": "goe"}], entities=entities, states=states, energy_prefs=None)
    wallbox = DeviceConfig(id="wb", ha_device_id="goe", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")
    config = SunflowConfig(
        devices=[wallbox],
        vehicles=[Vehicle(id="fiat", name="Fiat", capacity_wh=37500, soc_entity="sensor.fiat_soc", plugged_entity="device_tracker.fiat")],
        charging=[ChargingProfile(vehicle_id="fiat", wallbox_id="wb")],
        vehicle_detection={"wb": VehicleDetection(single=False, sensor=False)},
    )
    start = START + timedelta(days=15, hours=12)
    return config, snapshot, [start + timedelta(minutes=15 * i) for i in range(96)]


def test_car_at_home_can_be_plugged_in_now(library) -> None:
    """Its presence sensor says home, the wallbox sees no car: planned from now, optional, and hinted."""
    from types import SimpleNamespace

    from sunflow.planning.habits import Departure
    from sunflow.planning.plan import flex_loads, plug_in_hints

    config, snapshot, slots = _away_setup("home")
    presence = Departure(workday=time(7), weekend=None, sessions=10, arrival_workday=time(17, 30), entity_id="device_tracker.fiat")

    loads, (info,), _, _ = flex_loads(config, library.profiles, snapshot, slots, UTC, presences={"fiat": presence})

    assert (info.at_home, info.expected_arrival) == (True, slots[0])
    assert loads[0].expected and loads[0].available[0]
    planned = [SimpleNamespace(start=s, flex_w={"Wallbox": 4000.0 if i < 8 else 0.0}, pv_w=3000.0, load_w=500.0) for i, s in enumerate(slots)]
    (hint,) = plug_in_hints([info], planned, slots[0])
    assert (hint["vehicle"], hint["energy_wh"], hint["pv_wh"]) == ("Fiat", 8000, 5000)


def test_presence_habit_wins_over_the_wallbox(library) -> None:
    """Away: the car's own arrival home counts, not the plug-in time at the wallbox."""
    from sunflow.planning.habits import Departure
    from sunflow.planning.plan import flex_loads

    config, snapshot, slots = _away_setup("not_home")
    presence = Departure(workday=time(7), weekend=None, sessions=10, arrival_workday=time(16), entity_id="device_tracker.fiat")
    wallbox = Departure(workday=time(7), weekend=None, sessions=10, arrival_workday=time(21))

    _, (info,), _, _ = flex_loads(config, library.profiles, snapshot, slots, UTC, departures={"wb": wallbox},
                                  vehicle_usages={"fiat": usage_from(_socs(15), UTC)}, presences={"fiat": presence})

    assert (info.at_home, info.expected_arrival.hour) == (False, 16)
