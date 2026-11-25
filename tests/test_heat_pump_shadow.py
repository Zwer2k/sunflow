"""Flow control: room temperature, correction, flow target, climate entity "Haus" (design/heat_pump.md, 6, 8, 9)."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sunflow.api.heat_pump import router
from sunflow.api.house import router as house_router
from sunflow.api.integration import router as integration_router
from sunflow.config.models import ClimateSettings, DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.correction import Correction
from sunflow.heat_pump.defaults import HEATING_SURFACES, correction_ti_h
from sunflow.heat_pump.rooms import device_sensors, reported_times, room_temperature
from sunflow.heat_pump.samples import Minute
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from sunflow.heat_pump.states import Evaluation, Reading

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)
ROOM = "sensor.wohnzimmer"
PROFILE = "esphome/rotex_hpsu_can"


# Correction (9)


def test_steady_room_below_setpoint_integrates() -> None:
    """0.5 K below the setpoint for 6 h without any change of the room: +0.5 K (a step every 30 min)."""
    correction = Correction()
    for minute in range(6 * 60 + 1):
        correction.step(20.5, 21.0, NOW + minute * MIN)

    assert correction.value_k == pytest.approx(0.5)


def test_no_integral_within_deadband() -> None:
    correction = Correction()
    for minute in range(6 * 60 + 1):
        correction.step(20.85, 21.0, NOW + minute * MIN)

    assert correction.value_k == 0.0


def test_room_rising_acts_at_once_through_the_proportional_part() -> None:
    correction = Correction()
    correction.step(21.0, 21.2, NOW)

    # Within the deadband (21.3 vs 21.2): no integral part
    assert correction.step(21.3, 21.2, NOW + MIN)
    assert correction.value_k == pytest.approx(-0.3)


def test_frozen_changes_nothing_not_even_afterwards() -> None:
    """While frozen the room falls by 1 K; it is dropped, nothing is made up after releasing."""
    correction = Correction()
    correction.step(21.0, 21.0, NOW)
    for minute in range(1, 120):
        correction.hold(21.0 - minute / 120, NOW + minute * MIN)

    correction.step(20.0 + 1 / 120, 20.0, NOW + 120 * MIN)

    assert correction.value_k == pytest.approx(0.0)


@pytest.mark.parametrize(("room", "limit"), [(15.0, 3.0), (27.0, -3.0)])
def test_clamped_at_the_limit(room: float, limit: float) -> None:
    correction = Correction(2.9)
    correction.step(room, 21.0, NOW)
    for minute in range(1, 48 * 60):
        correction.step(room, 21.0, NOW + minute * MIN)

    assert correction.value_k == limit
    assert Correction(7.0).value_k == 3.0


def test_gap_counts_at_most_30_minutes() -> None:
    """No step for 2 h (e.g. Sunflow not running): the next step counts 30 min."""
    correction = Correction()
    correction.step(20.0, 21.0, NOW)

    correction.step(20.0, 21.0, NOW + timedelta(hours=2))

    assert correction.value_k == pytest.approx(1.0 / 6.0 * 1.0 * 0.5)


# Room temperature (6)


def _state(value: str, updated: datetime | None = None) -> dict[str, Any]:
    return {"state": value, "attributes": {"unit_of_measurement": "°C"}, "last_updated": (updated or NOW - timedelta(hours=5)).isoformat()}


def test_room_temperature_weighted_without_stale_and_missing() -> None:
    rooms = [Room(entity_id="sensor.a", weight=2), Room(entity_id="sensor.b"), Room(entity_id="sensor.c"), Room(entity_id="sensor.d")]
    states = {"sensor.a": _state("21.0"), "sensor.b": _state("24.0"), "sensor.c": _state("30.0"), "sensor.d": _state("unavailable")}
    reported = {"sensor.a": NOW - timedelta(minutes=10), "sensor.b": None, "sensor.c": NOW - timedelta(hours=3)}

    room = room_temperature(rooms, states, reported, NOW)

    # Steady room still reporting counts (last_updated old), without last_reported it counts
    assert room.value_c == pytest.approx(22.0)
    assert (room.used, room.stale, room.missing) == (("sensor.a", "sensor.b"), ("sensor.c",), ("sensor.d",))


def test_all_rooms_stale_gives_no_room_temperature() -> None:
    room = room_temperature([Room(entity_id="sensor.a")], {"sensor.a": _state("21.0")}, {"sensor.a": NOW - timedelta(hours=3)}, NOW)

    assert room.value_c is None


class Reader:
    """get_state of HAClient: last_reported per sensor; a failure for one."""

    def __init__(self, reported: dict[str, datetime | None], failing: set[str] = frozenset()) -> None:
        self.reported, self.failing, self.calls = reported, failing, 0

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        self.calls += 1
        if entity_id in self.failing:
            raise OSError("timeout")
        at = self.reported.get(entity_id)
        return {"entity_id": entity_id, "state": "21.0"} | ({"last_reported": at.isoformat()} if at else {})


async def test_reported_times_failed_request_means_no_check() -> None:
    reader = Reader({"sensor.a": NOW, "sensor.b": NOW}, failing={"sensor.b"})

    assert await reported_times(reader, ["sensor.a", "sensor.b", "sensor.c"]) == {"sensor.a": NOW, "sensor.b": None, "sensor.c": None}


REGISTRY = [
    {"entity_id": "sensor.kian_temperature", "device_id": "kian"},
    {"entity_id": "sensor.kian_humidity", "device_id": "kian"},
    {"entity_id": "sensor.kian_battery", "device_id": "kian"},
    {"entity_id": "sensor.kian_linkquality", "device_id": "kian", "disabled_by": "integration"},
    {"entity_id": "number.kian_temperature_calibration", "device_id": "kian"},
    {"entity_id": "sensor.other_humidity", "device_id": "other"},
    {"entity_id": "sensor.template_room"},
]


def test_device_sensors_are_the_other_enabled_sensors_of_the_device() -> None:
    siblings = device_sensors(REGISTRY, ["sensor.kian_temperature", "sensor.template_room", "sensor.unknown"])

    assert siblings == {
        "sensor.kian_temperature": ["sensor.kian_battery", "sensor.kian_humidity"],
        "sensor.template_room": [],
        "sensor.unknown": [],
    }


async def test_a_steady_temperature_counts_while_its_device_reports() -> None:
    """MQTT (Zigbee2MQTT) does not write an unchanged temperature again: its last_reported stands still while
    the humidity of the same device keeps reporting; the latest of the device counts (6)."""
    reader = Reader({"sensor.kian_temperature": NOW - timedelta(hours=3), "sensor.kian_humidity": NOW - timedelta(minutes=5),
                     "sensor.kian_battery": NOW - timedelta(hours=5)}, failing={"sensor.kian_battery"})
    siblings = device_sensors(REGISTRY, ["sensor.kian_temperature"])

    reported = await reported_times(reader, ["sensor.kian_temperature"], siblings)
    room = room_temperature([Room(entity_id="sensor.kian_temperature")], {"sensor.kian_temperature": _state("22.4")}, reported, NOW)

    assert reported == {"sensor.kian_temperature": NOW - timedelta(minutes=5)}
    assert room.value_c == 22.4 and room.stale == ()
    # The whole device silent: stale as before
    silent = Reader({"sensor.kian_temperature": NOW - timedelta(hours=3), "sensor.kian_humidity": NOW - timedelta(hours=3)})
    reported = await reported_times(silent, ["sensor.kian_temperature"], siblings)
    assert room_temperature([Room(entity_id="sensor.kian_temperature")], {"sensor.kian_temperature": _state("22.4")}, reported, NOW).stale == ("sensor.kian_temperature",)


async def test_flow_shadow_reads_the_device_sensors_from_the_registry(tmp_path: Path) -> None:
    class RegistryReader(Reader):
        async def get_entity_registry(self) -> list[dict[str, Any]]:
            return [{"entity_id": ROOM, "device_id": "room"}, {"entity_id": "sensor.room_humidity", "device_id": "room"}]

    reader = RegistryReader({ROOM: NOW - timedelta(hours=3), "sensor.room_humidity": NOW - timedelta(minutes=1)})
    house = House(tmp_path, reader)
    await house.tick(NOW)

    assert house.shadow.room is not None and house.shadow.room.used == (ROOM,)
    assert house.shadow.house_status(house.config)["rooms_stale"] == []


# Control: curve, correction and flow target (8, 9)


def _minute(at: datetime, setpoint: float | None = 31.0, valid: bool = True, strategy: str = "fixed", frozen: tuple[str, ...] = ()) -> Minute:
    reading = Reading(program="heating", activity="heating" if valid else "hot_water", strategy=strategy, values={"flow_setpoint": setpoint})
    evaluation = Evaluation(valid=valid, frozen=frozen if not valid else ())
    return Minute(at=at, reading=reading, evaluation=evaluation, heat_w=None, heat_source=None, outside_c=5.0, rooms={})


class House:
    """One heat pump, one room and the shadow control over minutes."""

    def __init__(self, tmp_path: Path, reader: Reader | None = None) -> None:
        self.tmp_path = tmp_path
        self.device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=2, name="HPSU CAN")
        self.config = SunflowConfig(devices=[self.device], house=HouseSettings(rooms=[Room(entity_id=ROOM)]))
        self.room = "21.0"
        self.reader = reader
        self.shadow = self.restart()

    def restart(self) -> FlowShadow:
        self.shadow = FlowShadow(HeatPumpStateStore(self.tmp_path), self.reader)
        return self.shadow

    async def tick(self, at: datetime, minute: Minute | None | str = "default") -> None:
        sample = _minute(at) if minute == "default" else minute
        states = {ROOM: _state(self.room)} if self.room is not None else {}
        await self.shadow.tick(self.config, states, {"hp": (self.device, sample)}, at)

    def flow(self) -> dict[str, Any]:
        return self.shadow.flow_status("hp")


async def test_flow_target_from_the_curve_also_during_hot_water(tmp_path: Path) -> None:
    """21 + 0.6·(21 − 5) = 30.6 (underfloor start slope); hot water freezes the correction, the target stays."""
    house = House(tmp_path)
    await house.tick(NOW)
    await house.tick(NOW + MIN, _minute(NOW + MIN, 48.0, valid=False, frozen=("activity_hot_water",)))

    flow = house.flow()

    assert (flow["curve_c"], flow["correction_k"], flow["target_c"], flow["shadow"]) == (30.6, 0.0, 30.6, True)
    assert flow["frozen"] == ["activity_hot_water"]


async def test_correction_survives_a_restart_a_setpoint_change_holds_nothing(tmp_path: Path) -> None:
    house = House(tmp_path)
    house.room = "20.5"
    for minute in range(6 * 60 + 1):
        await house.tick(NOW + minute * MIN)
    house.config.house.climate.setpoint_c = 22.0
    await house.tick(NOW + 362 * MIN)

    house.restart()
    house.config.house.climate.setpoint_c = 22.0
    await house.tick(NOW + 363 * MIN, _minute(NOW + 363 * MIN, valid=False, frozen=("activity_hot_water",)))

    flow = house.flow()
    assert flow["correction_k"] == pytest.approx(0.5)
    # A setpoint change does not hold the correction
    assert flow["frozen"] == ["activity_hot_water"]
    stored = json.loads((tmp_path / "heat_pump_state.json").read_text())
    assert stored["devices"]["hp"]["corrections"]["fixed"] == pytest.approx(0.5)


async def test_rooms_missing_freezes_and_return_counts_at_most_30_min(tmp_path: Path) -> None:
    house = House(tmp_path)
    house.room = "20.0"
    await house.tick(NOW)
    house.room = "unavailable"
    for minute in range(1, 121):
        await house.tick(NOW + minute * MIN)
    assert house.flow()["frozen"] == ["rooms_missing"]

    house.room = "20.0"
    for minute in range(121, 152):
        await house.tick(NOW + minute * MIN)

    # Back after 2 h: the first value is the reference, then one 30 min step of the integral part
    assert house.flow()["correction_k"] == pytest.approx(1.0 / 6.0 * 1.0 * 0.5, abs=1e-3)


async def test_stale_rooms_read_by_last_reported_freeze(tmp_path: Path) -> None:
    reader = Reader({ROOM: NOW - timedelta(hours=3)})
    house = House(tmp_path, reader)

    await house.tick(NOW)

    assert house.flow()["frozen"] == ["rooms_missing"]
    assert house.shadow.house_status()["rooms_stale"] == [ROOM]
    # Read again only after 30 min or when a room changed
    await house.tick(NOW + MIN)
    assert reader.calls == 1
    house.room = "21.2"
    await house.tick(NOW + 2 * MIN)
    assert reader.calls == 2


async def test_strategy_change_starts_without_jump(tmp_path: Path) -> None:
    house = House(tmp_path)
    await house.tick(NOW)
    house.room = "21.5"
    await house.tick(NOW + MIN, _minute(NOW + MIN, strategy="weather"))

    assert house.flow()["correction_k"] == 0.0
    assert house.flow()["strategy"] == "weather"


# Integral time per heating surface type (9)


def test_integral_time_per_heating_surface_type() -> None:
    assert set(HEATING_SURFACES) == {"underfloor", "mixed", "radiator"}
    assert {s: correction_ti_h(s) for s in HEATING_SURFACES} == {"underfloor": 6.0, "mixed": 4.0, "radiator": 2.0}
    # Unknown or missing type: the default's
    assert correction_ti_h("floor_and_ceiling") == correction_ti_h(None) == 6.0


@pytest.mark.parametrize(("ti", "expected"), [(6.0, 0.5), (4.0, 0.75), (2.0, 1.5)])
def test_correction_integrates_with_the_given_integral_time(ti: float, expected: float) -> None:
    """0.5 K below the setpoint for 6 h: Kp/Ti · e · t."""
    correction = Correction()
    for minute in range(6 * 60 + 1):
        correction.step(20.5, 21.0, NOW + minute * MIN, ti_h=ti)

    assert correction.value_k == pytest.approx(expected)


def test_non_positive_integral_time_counts_as_the_default() -> None:
    correction = Correction()
    for minute in range(6 * 60 + 1):
        correction.step(20.5, 21.0, NOW + minute * MIN, ti_h=0.0)

    assert correction.value_k == pytest.approx(0.5)


@pytest.mark.parametrize(("surface", "ti"), [("underfloor", 6.0), ("mixed", 4.0), ("radiator", 2.0)])
async def test_shadow_uses_the_integral_time_of_the_heating_surface(tmp_path: Path, surface: str, ti: float) -> None:
    house = House(tmp_path)
    house.config.house.heating_surface = surface
    house.room = "20.5"
    for minute in range(6 * 60 + 1):
        await house.tick(NOW + minute * MIN)

    flow = house.flow()
    assert flow["parameters"]["ti_h"] == ti
    assert flow["correction_k"] == pytest.approx(0.5 * 6.0 / ti)


async def test_changing_the_heating_surface_does_not_jump_the_correction(tmp_path: Path) -> None:
    """Underfloor for 3 h, then radiators: the value stays at the change, only the increments get faster."""
    house = House(tmp_path)
    house.room = "20.5"
    for minute in range(3 * 60 + 1):
        await house.tick(NOW + minute * MIN)
    before = house.flow()["correction_k"]
    assert before == pytest.approx(0.25)

    house.config.house.heating_surface = "radiator"
    await house.tick(NOW + 181 * MIN)
    assert house.flow()["correction_k"] == pytest.approx(before)
    assert house.flow()["parameters"]["ti_h"] == 2.0

    for minute in range(182, 6 * 60 + 1):
        await house.tick(NOW + minute * MIN)
    # 3 h more with Ti 2 h: + 0.5 · 3 / 2
    assert house.flow()["correction_k"] == pytest.approx(0.25 + 0.75)


# Climate entity "Haus" (6) and the room setpoint in the configuration


def _api(tmp_path: Path, library: Any, config: SunflowConfig | None = None) -> tuple[TestClient, ConfigStore]:
    app = FastAPI()
    for r in (router, house_router, integration_router):
        app.include_router(r, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path, lambda: library.profiles)
    app.state.store.save(config or SunflowConfig(house=HouseSettings(rooms=[Room(entity_id=ROOM)])))
    app.state.runner = SimpleNamespace(
        heat_pump=None, latest=None, last_sample=None, status=SimpleNamespace(last_plan=None, last_error=None),
        relearn_house=lambda: None, request_replan=lambda: None,
    )
    return TestClient(app), app.state.store


def test_setpoint_stored_to_a_tenth_and_survives_a_restart(tmp_path: Path, library: Any) -> None:
    client, _ = _api(tmp_path, library)

    answer = client.put("/api/v1/house/climate", json={"setpoint_c": 21.3}).json()
    client.put("/api/v1/house/climate", json={"mode": "off"})
    client.put("/api/v1/house/climate", json={"setpoint_c": 21.26})

    assert answer == {"setpoint_c": 21.3, "mode": "heat", "preheat_k": 1.0, "lower_k": 0.0, "plan_model": "auto"}
    # A new store reads the file as after a restart
    climate = ConfigStore(tmp_path).load().house.climate
    assert (climate.setpoint_c, climate.mode) == (21.3, "off")


@pytest.mark.parametrize("value", [17.5, 27.5])
def test_setpoint_outside_the_range_refused(tmp_path: Path, library: Any, value: float) -> None:
    client, store = _api(tmp_path, library)

    assert client.put("/api/v1/house/climate", json={"setpoint_c": value}).status_code == 422
    assert store.load().house.climate.setpoint_c == 21.0


def test_saving_the_rooms_keeps_the_setpoint(tmp_path: Path, library: Any) -> None:
    client, store = _api(tmp_path, library)
    client.put("/api/v1/house/climate", json={"setpoint_c": 22.5})

    client.put("/api/v1/config/house", json={"rooms": [{"entity_id": ROOM, "weight": 2}], "climate": {"setpoint_c": 20.0}})

    assert store.load().house.climate.setpoint_c == 22.5
    assert store.load().house.rooms[0].weight == 2


def test_integration_state_has_the_house(tmp_path: Path, library: Any) -> None:
    client, _ = _api(tmp_path, library)

    house = client.get("/api/v1/integration/state").json()["house"]

    assert {k: house[k] for k in ("setpoint_c", "mode", "min_c", "max_c", "step_c", "shadow")} == {
        "setpoint_c": 21.0, "mode": "heat", "min_c": 18.0, "max_c": 27.0, "step_c": 0.5, "shadow": True,
    }


def test_no_house_without_rooms_and_heat_pump(tmp_path: Path, library: Any) -> None:
    client, _ = _api(tmp_path, library, SunflowConfig())

    assert client.get("/api/v1/integration/state").json()["house"] is None


def test_climate_model_rounds_and_refuses() -> None:
    assert ClimateSettings(setpoint_c=21.26).setpoint_c == 21.3
    with pytest.raises(ValueError):
        ClimateSettings(setpoint_c=17.9)


# Margin of the planning (6, 14)


def test_margin_stored_to_a_tenth_with_the_setpoint(tmp_path: Path, library: Any) -> None:
    client, _ = _api(tmp_path, library)

    answer = client.put("/api/v1/house/climate", json={"preheat_k": 1.54, "lower_k": 0.5}).json()
    client.put("/api/v1/house/climate", json={"setpoint_c": 20.0})

    assert answer == {"setpoint_c": 21.0, "mode": "heat", "preheat_k": 1.5, "lower_k": 0.5, "plan_model": "auto"}
    climate = ConfigStore(tmp_path).load().house.climate
    assert (climate.setpoint_c, climate.preheat_k, climate.lower_k) == (20.0, 1.5, 0.5)


@pytest.mark.parametrize("body", [{"preheat_k": -0.1}, {"preheat_k": 3.1}, {"lower_k": 3.5}, {"lower_k": "x"}])
def test_margin_outside_the_range_refused(tmp_path: Path, library: Any, body: dict[str, Any]) -> None:
    client, store = _api(tmp_path, library)

    assert client.put("/api/v1/house/climate", json=body).status_code == 422
    climate = store.load().house.climate
    assert (climate.preheat_k, climate.lower_k) == (1.0, 0.0)


@pytest.mark.parametrize(("stored", "expected"), [
    ({}, (1.0, 0.0)),
    ({"preheat_k": 2.0, "lower_k": 1.0}, (2.0, 1.0)),
    ({"preheat_k": 9, "lower_k": -1}, (1.0, 0.0)),
    ({"preheat_k": "warm", "lower_k": None}, (1.0, 0.0)),
    ({"preheat_k": True, "lower_k": 0.26}, (1.0, 0.3)),
])
def test_margin_without_migration(tmp_path: Path, stored: dict[str, Any], expected: tuple[float, float]) -> None:
    # Missing or broken values (older or newer version, edited by hand) load as the start values, no error
    (tmp_path / "config.json").write_text(json.dumps({
        "version": 6, "house": {"rooms": [{"entity_id": ROOM}], "climate": {"setpoint_c": 21.5, **stored}},
    }))

    climate = ConfigStore(tmp_path).load().house.climate

    assert (climate.preheat_k, climate.lower_k) == expected
    assert climate.setpoint_c == 21.5


def test_status_has_the_margin(tmp_path: Path, library: Any) -> None:
    client, _ = _api(tmp_path, library)
    client.put("/api/v1/house/climate", json={"preheat_k": 0.5})

    house = client.get("/api/v1/integration/state").json()["house"]

    assert {k: house[k] for k in ("preheat_k", "lower_k", "margin_max_k", "margin_step_k")} == {
        "preheat_k": 0.5, "lower_k": 0.0, "margin_max_k": 3.0, "margin_step_k": 0.1,
    }


# Migration 5 -> 6


@pytest.mark.parametrize(("comfort", "expected"), [(20.5, 20.5), (16, 21.0), ("warm", 21.0), (None, 21.0), (True, 21.0)])
def test_setpoint_from_comfort_minimum(tmp_path: Path, comfort: Any, expected: float) -> None:
    house = {"rooms": [{"entity_id": ROOM, "weight": 1, "reference": True}], "comfort_max": 23.0, "night_min": 18.0,
             "night_start": "22:00", "night_end": "06:00", "reference": ROOM}
    if comfort is not None:
        house["comfort_min"] = comfort
    stored = {"version": 5, "house": house}
    (tmp_path / "config.json").write_text(json.dumps(stored))

    config = ConfigStore(tmp_path).load()

    assert config.house.climate.setpoint_c == expected
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["version"] == 6
    # The former fields stay for older versions
    assert {k: saved["house"][k] for k in house if k != "rooms"} == {k: v for k, v in house.items() if k != "rooms"}
    assert saved["house"]["rooms"][0]["reference"] is True
    assert json.loads((tmp_path / "config.v5.json").read_text()) == stored
    # A second load changes nothing
    before = (tmp_path / "config.json").read_text()
    ConfigStore(tmp_path).load()
    assert (tmp_path / "config.json").read_text() == before


def test_rest_url_next_to_the_websocket() -> None:
    from sunflow.ha.client import HAClient  # noqa: PLC0415

    assert HAClient("ws://supervisor/core/websocket", "t").rest_url == "http://supervisor/core/api"
    assert HAClient("wss://ha.local:8123/api/websocket", "t").rest_url == "https://ha.local:8123/api"


async def test_recorder_feeds_the_shadow_control(tmp_path: Path, library: Any) -> None:
    """The recorder's minutes drive the control; its status shows curve, correction and target."""
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        await pump.minutes(NOW, 2)
        status = pump.recorder.status()["hp"]
    finally:
        pump.store.close()

    # Outside 5 °C, room setpoint 21 °C (default), the test node's own slope "1": 21 + 1·16
    assert (status["flow"]["outside_c"], status["flow"]["curve_c"]) == (5.0, 37.0)
    assert (status["flow"]["correction_k"], status["flow"]["target_c"], status["flow"]["frozen"]) == (0.0, 37.0, [])
    assert pump.recorder.flow.house_status()["room_c"] == 21.0
