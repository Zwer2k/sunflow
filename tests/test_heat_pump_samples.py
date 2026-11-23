"""Recording the HPSU: 5 minute rows in heat_pump_samples, events, journal (design/heat_pump.md, 4, 9.1, 10)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from heat_pump_node import Node, snapshot, with_manifest
import pytest
from sunflow.config.models import DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.control.journal import ControlJournal
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary
from sunflow.heat_pump.samples import HeatPumpRecorder
from sunflow.shadow.store import ShadowStore

PROFILE = "esphome/rotex_hpsu_can"
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)
# Shown states of a German firmware, heating validly
STATES: dict[str, tuple[str, str | None]] = {
    "tv": ("30.0", "°C"),
    "tv_max": ("30.4", "°C"),
    "tvbh": ("30.2", "°C"),
    "tr": ("26.0", "°C"),
    "target_supply_temperature": ("31.0", "°C"),
    "max_target_flow_temp": ("31.0", "°C"),
    "thermal_power": ("1.5", "kW"),
    "temperature_outside": ("5.0", "°C"),
    "ta2": ("3.0", "°C"),
    "status_kompressor": ("on", None),
    "operating_mode": ("Heizen", None),
    "mode_of_operating": ("Heizen", None),
    "hk_function": ("Fest", None),
    "bypass_valve": ("0.0", "%"),
    "room_therm": ("Aus", None),
    "error_code": ("Kein Fehler", None),
    # ESP regulation off (0): Sunflow may write (8.5)
    "supply_setpoint_regulated": ("0.0", "°C"),
}


class Pump:
    """A configured HPSU, its states and the recorder."""

    def __init__(self, tmp_path: Path, library: ProfileLibrary) -> None:
        self.node = Node()
        self.states = dict(STATES)
        self.extra: dict[str, dict[str, Any]] = {"sensor.wohnzimmer": self._state("21.0", "°C")}
        self.config = SunflowConfig(
            devices=[DeviceConfig(id="hp", ha_device_id=self.node.device_id, profile=PROFILE, profile_version=2, name="HPSU CAN")],
            house=HouseSettings(rooms=[Room(entity_id="sensor.wohnzimmer")]),
        )
        self.store = ShadowStore(tmp_path / "data")
        self.journal = ControlJournal(tmp_path)
        self.usable = True
        # State of every other entity of the node
        self.base = "1"
        self.recorder = HeatPumpRecorder(self.store, lambda: library.profiles, lambda _: self.usable, self.journal)

    @staticmethod
    def _state(value: str, unit: str | None = None, changed: datetime | None = None) -> dict[str, Any]:
        return {
            "state": value,
            "attributes": {"unit_of_measurement": unit} if unit else {},
            "last_changed": (changed or NOW - timedelta(hours=1)).isoformat(),
        }

    def snapshot(self) -> Snapshot:
        snap = snapshot(self.node, state=self.base)
        states = dict(snap.states)
        for key, (value, unit) in self.states.items():
            states[self.node.entity_id(key)] = {"entity_id": self.node.entity_id(key)} | self._state(value, unit)
        return with_manifest(replace(snap, states=states | self.extra), self.node)

    async def tick(self, at: datetime) -> None:
        await self.recorder.tick(self.config, self.snapshot(), at)

    async def minutes(self, start: datetime, count: int) -> None:
        for minute in range(count):
            await self.tick(start + minute * MIN)

    def event(self, key: str, old: str, new: str, at: datetime) -> None:
        self.recorder.on_state_changed(
            {"data": {
                "entity_id": self.node.entity_id(key),
                "old_state": {"state": old},
                "new_state": {"state": new, "last_updated": at.isoformat(), "last_changed": at.isoformat()},
            }}
        )

    def rows(self) -> list[dict[str, Any]]:
        return self.store.heat_pump_samples("hp", NOW - timedelta(days=1))

    def notes(self) -> list[str]:
        return [e["reason"] for e in reversed(self.journal.entries(NOW - timedelta(days=1), "hp"))]


@pytest.fixture
def pump(tmp_path: Path, library: ProfileLibrary) -> Pump:
    p = Pump(tmp_path, library)
    yield p
    p.store.close()


async def test_five_minute_row(pump: Pump) -> None:
    """Five samples become one row; tv_max and a compressor stop come from the events between them."""
    await pump.tick(NOW)
    pump.event("tv_max", "30.4", "31.7", NOW + 2 * MIN + timedelta(seconds=30))
    pump.event("status_kompressor", "on", "off", NOW + 3 * MIN + timedelta(seconds=10))
    await pump.minutes(NOW + MIN, 5)

    [row] = pump.rows()

    assert row["start"] == NOW.isoformat()
    assert row["samples"] == 5
    assert (row["tv_c"], row["tr_c"], row["tvbh_c"], row["tv_max_c"]) == (30.0, 26.0, 30.2, 31.7)
    assert (row["setpoint_c"], row["max_setpoint_c"], row["written_c"], row["bypass_pct"]) == (31.0, 31.0, None, 0.0)
    # kW of the heat pump as W
    assert (row["heat_w"], row["heat_source"]) == (1500.0, "measured")
    assert row["outside_c"] == 5.0
    assert (row["program"], row["activity"], row["strategy"], row["error"]) == ("heating", "heating", "fixed", "err_0")
    assert row["compressor"] == 1.0
    assert row["rooms"] == {"sensor.wohnzimmer": 21.0}
    [event] = row["events"]
    assert (event["kind"], event["tv_c"], event["tv_max_c"], event["setpoint_c"]) == ("stop", 30.0, 31.7, 31.0)
    assert row["flags"] == {"valid": True, "frozen": [], "after_run": [], "blocked": False, "fault": False, "self_limited": False, "ventilation": []}
    assert pump.recorder.status()["hp"]["valid"]


async def test_row_is_valid_only_when_every_minute_was(pump: Pump) -> None:
    """Hot water starting in the interval: the row is not valid and names why; the most frequent activity counts."""
    await pump.minutes(NOW, 3)
    pump.states["mode_of_operating"] = ("Warmwasserbereitung", None)
    pump.states["bypass_valve"] = ("100.0", "%")
    await pump.minutes(NOW + 3 * MIN, 3)

    [row] = pump.rows()

    assert row["activity"] == "heating"
    assert row["bypass_pct"] == pytest.approx(40.0)
    assert not row["flags"]["valid"]
    assert row["flags"]["frozen"] == ["activity_hot_water", "bypass"]
    assert not pump.recorder.status()["hp"]["valid"]


async def test_after_run_flag(pump: Pump) -> None:
    """The after-run following hot water is stored as a flag of its own."""
    pump.states["mode_of_operating"] = ("Warmwasserbereitung", None)
    await pump.tick(NOW)
    pump.states["mode_of_operating"] = ("Heizen", None)
    await pump.minutes(NOW + MIN, 5)

    [row] = pump.rows()

    assert row["flags"]["after_run"] == ["hot_water"]
    assert "after_hot_water" in row["flags"]["frozen"]


@pytest.mark.parametrize(
    ("source", "own", "expected"),
    [
        pytest.param("auto", None, 5.0, id="auto: heat pump"),
        pytest.param("auto", "sensor.garden", -1.0, id="auto: own sensor first"),
        pytest.param("heat_pump", "sensor.garden", 5.0, id="heat pump"),
        pytest.param("heat_pump_unit", "sensor.garden", 3.0, id="outdoor unit TA2"),
        pytest.param("learning_source", "sensor.garden", -1.0, id="own sensor"),
        pytest.param("learning_source", None, None, id="own sensor missing"),
    ],
)
async def test_outside_source(pump: Pump, source: str, own: str | None, expected: float | None) -> None:
    """The outside temperature follows the setting house.outside_source."""
    pump.config.house.outside_source = source  # type: ignore[assignment]
    pump.config.learning_sources.outdoor_temperature = own
    pump.extra["sensor.garden"] = pump._state("-1.0", "°C")
    await pump.minutes(NOW, 6)

    assert pump.rows()[0]["outside_c"] == expected


async def test_own_heat_meter_first(pump: Pump) -> None:
    """A heat meter of the house wins over the heat pump's heat; its unit is converted to W."""
    pump.config.house.heat_entity = "sensor.heat_meter"
    pump.extra["sensor.heat_meter"] = pump._state("2.0", "kW")
    await pump.minutes(NOW, 6)

    row = pump.rows()[0]

    assert (row["heat_w"], row["heat_source"]) == (2000.0, "meter")


async def test_unusable_device_is_recorded_not_valid(pump: Pump) -> None:
    """Incompatible or unavailable: the values are kept, the row is not valid."""
    pump.usable = False
    await pump.minutes(NOW, 6)

    assert pump.rows()[0]["flags"]["frozen"] == ["unusable"]


async def test_no_row_without_values(pump: Pump) -> None:
    """Everything unavailable (node away): no sample, no row."""
    pump.base = "unavailable"
    pump.states = {key: ("unavailable", None) for key in STATES}
    await pump.minutes(NOW, 6)

    assert pump.rows() == []
    assert pump.recorder.status() == {}


async def test_journal_on_changes_only(pump: Pump) -> None:
    """Unknown values, faults, the block and the heat pump limiting itself are noted once per change."""
    pump.states["operating_mode"] = ("Party", None)
    await pump.minutes(NOW, 2)
    pump.states["operating_mode"] = ("Heizen", None)
    pump.states["error_code"] = ("E9001 Rücklauffühler", None)
    pump.states["room_therm"] = ("An", None)
    await pump.minutes(NOW + 2 * MIN, 2)
    pump.states["error_code"] = ("Kein Fehler", None)
    pump.states["room_therm"] = ("Aus", None)
    pump.states["target_supply_temperature"] = ("29.0", "°C")
    await pump.minutes(NOW + 4 * MIN, 12)
    # Hot water starts and ends: frozen and valid again, but no journal entry for that
    pump.states["mode_of_operating"] = ("Warmwasserbereitung", None)
    await pump.minutes(NOW + 16 * MIN, 2)

    assert pump.notes() == [
        "Unbekannter Zustand „Party“ (program) – Sunflow greift dann nicht ein.",
        "Störung der Wärmepumpe: E9001 Rücklauffühler",
        "Raumthermostat-Sperre aktiv: die Wärmepumpe heizt nicht.",
        "Störung der Wärmepumpe behoben",
        "Raumthermostat-Sperre aufgehoben",
        "Die Wärmepumpe begrenzt den Vorlauf selbst (Absenk-Zeit, T-Vorlauf Nacht).",
    ]


async def test_old_rows_are_cleared_out(pump: Pump) -> None:
    """Once a day rows older than a year are deleted."""
    old = NOW - timedelta(days=400)
    pump.store.store_heat_pump_samples([{"device_id": "hp", "start": old, "samples": 5}])

    await pump.tick(NOW)

    assert pump.store.heat_pump_samples("hp", old - timedelta(days=1)) == []


async def test_status_api_shows_the_states(pump: Pump, library: ProfileLibrary, tmp_path: Path) -> None:
    """GET /heat_pump/status carries program, activity, block, fault and valid heating with the reasons."""
    from types import SimpleNamespace  # noqa: PLC0415

    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from sunflow.api.heat_pump import router  # noqa: PLC0415
    from sunflow.config.store import ConfigStore  # noqa: PLC0415

    pump.states["room_therm"] = ("An", None)
    await pump.tick(NOW)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
    config = app.state.store.load()
    config.devices = pump.config.devices
    app.state.store.save(config)
    app.state.runner = SimpleNamespace(heat_pump=pump.recorder)

    [device] = TestClient(app).get("/api/v1/heat_pump/status").json()["devices"]

    state = device["state"]
    assert (state["program"], state["activity"], state["strategy"], state["blocked"], state["fault"]) == ("heating", "heating", "fixed", True, False)
    assert (state["valid"], state["frozen"]) == (False, ["blocked"])


# Mode off ("Aus", 16 step 3): nothing recorded, nothing learned


async def test_mode_off_records_and_learns_nothing(tmp_path: Path, library: ProfileLibrary) -> None:
    from sunflow.heat_pump.shadow import FlowShadow  # noqa: PLC0415
    from sunflow.heat_pump.state_store import HeatPumpStateStore  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        await pump.minutes(NOW, 6)
        assert len(pump.rows()) == 1
        pump.config.house.climate.mode = "off"
        # The room well below the setpoint: the correction would move
        pump.extra["sensor.wohnzimmer"] = pump._state("19.0", "°C")
        await pump.minutes(NOW + 6 * MIN, 20)
        rows = pump.rows()
        flow = pump.recorder.status()["hp"]["flow"]
        task = pump.recorder._curve_task
        # Back to heat: recorded again
        pump.config.house.climate.mode = "heat"
        await pump.minutes(NOW + 26 * MIN, 10)
        again = pump.rows()
    finally:
        pump.store.close()

    # The interval that ended while off is dropped with its events; none recorded meanwhile
    assert len(rows) == 1
    assert "mode_off" in flow["frozen"] and flow["correction_k"] == 0.0
    assert task is None or task.done()
    assert len(again) > 1


async def test_stop_event_carries_the_uart_tv(pump: Pump) -> None:
    """The companion node (UART manifest) is bound by its manifest; its Tv goes with each compressor stop (8.4)."""
    from dataclasses import replace as replaced  # noqa: PLC0415

    from sunflow.heat_pump.manifest import DeviceManifest  # noqa: PLC0415

    uart = {"id": "rotex_hpsu_uart", "entities": [{"key": "tv", "domain": "sensor", "name": "TV"}]}
    found = DeviceManifest(service="hpsu_uart_rotex_uart_manifest", node="hpsu_uart", component="uart", manifest=uart, hash="x", ha_device_id="dev_uart")
    entity = {"entity_id": "sensor.hpsu_uart_tv", "unique_id": "AA:BB/0/sensor/TV", "original_name": "TV", "platform": "esphome",
              "device_id": "dev_uart", "disabled_by": None}
    plain = pump.snapshot

    def snapshot() -> Snapshot:
        snap = plain()
        return replaced(
            snap, entities=[*snap.entities, entity], manifests=snap.manifests | {"dev_uart": {"rotex_hpsu_uart": found}},
            states=snap.states | {"sensor.hpsu_uart_tv": pump._state("30.6", "°C")},
        )

    pump.snapshot = snapshot  # type: ignore[method-assign]
    await pump.tick(NOW)
    pump.recorder.on_state_changed({"data": {
        "entity_id": "sensor.hpsu_uart_tv", "old_state": {"state": "30.6"},
        "new_state": {"state": "33.1", "attributes": {"unit_of_measurement": "°C"}, "last_updated": (NOW + MIN).isoformat()},
    }})
    pump.event("status_kompressor", "on", "off", NOW + 2 * MIN)
    await pump.minutes(NOW + MIN, 5)

    [event] = [e for e in pump.rows()[0]["events"] if e["kind"] == "stop"]
    assert (event["tv_uart_c"], event["tvbh_c"], event["activity"], event["blocked"]) == (33.1, 30.2, "heating", False)
