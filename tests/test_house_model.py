"""Learning how the house cools and warms from its room temperatures."""

from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from heat_pump_node import Node, snapshot as node_snapshot, with_manifest
from pydantic import ValidationError
import pytest
from sunflow.api.house import router
from sunflow.config.models import CONFIG_VERSION, DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary
from sunflow.planning.house_model import (
    HourRow,
    busy_hours,
    carnot_cop,
    chart,
    fit,
    heat_pump_device,
    house_data,
    restless_rooms,
    simulate,
    weighted_inside,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)
HOUR = timedelta(hours=1)
# A house with τ = 50 h, 25 kWh/K, sun +0.3 K/h, internal gains +0.02 K/h
A, B, C, D = 0.02, 0.04, 0.3, 0.02


def _house(days: int, heat: bool = True, sun: bool = True) -> list[HourRow]:
    """Hourly rows of a simulated house: outside swings daily, the heat pump runs in the morning and evening."""
    rows = []
    inside = 21.0
    start = NOW - timedelta(days=days)
    for i in range(days * 24):
        moment = start + i * HOUR
        hour = moment.hour
        outside = 3 + 5 * math.sin((hour - 9) / 24 * 2 * math.pi) + (i % 7) * 0.3
        heat_kwh = 6.0 if hour in (5, 6, 7, 17, 18, 19) else 1.0 if hour % 3 == 0 else 0.0
        sunshine = max(math.sin((hour - 6) / 12 * math.pi), 0.0) * (0.3 + (i // 24 % 3) * 0.35)
        rows.append(HourRow(start=moment, inside=inside, outside=outside, heat=heat_kwh if heat else None, sun=sunshine if sun else None))
        inside += A * (outside - inside) + B * heat_kwh + C * sunshine + D
    return rows


def test_learns_a_known_house() -> None:
    """The coefficients of a simulated house come back, and it beats "it stays as it is"."""
    model = fit(_house(21), NOW)

    assert model is not None
    assert model.tau_h == pytest.approx(50, rel=0.05)
    assert model.capacity_kwh_per_k == pytest.approx(25, rel=0.05)
    assert model.loss_w_per_k == pytest.approx(500, rel=0.05)
    assert model.c == pytest.approx(C, rel=0.1)
    assert model.useful


def test_without_heat_or_sun() -> None:
    """Without a heat measurement or sun the model still fits, without those terms."""
    model = fit(_house(21, heat=False, sun=False), NOW)

    assert model is not None
    assert (model.b, model.c, model.capacity_kwh_per_k) == (None, None, None)


def test_too_few_days() -> None:
    """Fewer than seven days are not enough."""
    assert fit(_house(5), NOW) is None


def test_simulation_starts_again_after_a_gap() -> None:
    """Every 24 hours and after a gap the simulation starts from the measurement."""
    rows = _house(8)
    rows = rows[:30] + rows[31:]
    model = fit(_house(21), NOW)
    assert model is not None

    predicted = simulate(model, rows)

    assert predicted[0] is None
    assert predicted[24] is None
    assert predicted[30] is None
    assert predicted[1] == pytest.approx(rows[1].inside, abs=0.05)


def test_chart_has_the_last_week() -> None:
    """The chart shows measured and expected temperatures of the last seven days."""
    rows = _house(21)
    points = chart(fit(rows, NOW), rows)

    assert len(points) == 7 * 24
    assert points[0]["model"] is None
    assert points[1]["model"] == pytest.approx(points[1]["measured"], abs=0.1)


def test_weighted_rooms_and_restless_ones() -> None:
    """Rooms count by weight; a room that jumps far more than the others is pointed out."""
    hours = [NOW - i * HOUR for i in range(72)]
    calm = {f"sensor.r{i}": {h: 20.0 + 0.01 * k for k, h in enumerate(hours)} for i in range(3)}
    calm["sensor.sun"] = {h: 20.0 + (3.0 if k % 2 else 0.0) for k, h in enumerate(hours)}
    weights = dict.fromkeys(calm, 1.0) | {"sensor.r0": 2.0}

    inside = weighted_inside(calm, weights)

    assert inside[hours[0]] == pytest.approx(20.0)
    assert restless_rooms(calm, inside) == ["sensor.sun"]


@pytest.mark.parametrize(("outside", "cop"), [(-10.0, 2.74), (7.0, 4.40), (40.0, 6.0)])
def test_carnot_cop(outside: float, cop: float) -> None:
    """A rough COP from the Carnot limit when nothing measures it."""
    assert carnot_cop(outside) == pytest.approx(cop, abs=0.01)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"rooms": [{"entity_id": "sensor.a", "weight": 0}]}, id="weight zero"),
        pytest.param({"rooms": [{"weight": 1}]}, id="no sensor"),
    ],
)
def test_house_settings_refused(body: dict[str, object]) -> None:
    """A room without a sensor or weight is refused."""
    with pytest.raises(ValidationError):
        HouseSettings.model_validate(body)


def test_house_settings_api(tmp_path: Path) -> None:
    """Rooms are stored; without rooms there is nothing to learn."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.store = ConfigStore(tmp_path / "config.json")
    client = TestClient(app)

    assert client.get("/api/v1/house/model").json() == {"state": "no_rooms"}
    body = HouseSettings(rooms=[Room(entity_id="sensor.wohnzimmer", weight=20)], heat_entity="sensor.heat").model_dump()
    assert client.put("/api/v1/config/house", json=body).json() == body
    assert client.get("/api/v1/config/house").json() == body


def test_old_house_fields_survive(tmp_path: Path) -> None:
    """Comfort band, night setback and reference room of the former control stay in the file; rooms are kept."""
    old_house = {
        "rooms": [{"entity_id": "sensor.a", "weight": 2.0, "reference": True}, {"entity_id": "sensor.b", "weight": 1.0}],
        "heat_entity": None,
        "comfort_min": 20.5, "comfort_max": 22.0, "night_min": 18.0, "night_start": "22:00", "night_end": "06:00",
    }
    store = ConfigStore(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"version": CONFIG_VERSION, "house": old_house}))

    config = store.load()
    store.save(config)

    assert [(r.entity_id, r.weight) for r in config.house.rooms] == [("sensor.a", 2.0), ("sensor.b", 1.0)]
    # New fields are written with their default
    assert json.loads((tmp_path / "config.json").read_text())["house"] == old_house | {"outside_source": "auto"}


@pytest.mark.parametrize(
    ("profile", "found"),
    [
        pytest.param("x/hp", True, id="capability heat_pump"),
        pytest.param("x/meter", False, id="other device"),
        pytest.param("x/gone", False, id="unknown profile"),
    ],
)
def test_heat_pump_device(profile: str, found: bool) -> None:
    """The heat source of the house is the first device whose profile has the capability heat_pump."""
    profiles = {"x/hp": {"capabilities": ["heat_pump"]}, "x/meter": {"capabilities": ["appliance"]}}
    device = DeviceConfig(id="d", ha_device_id="d", profile=profile, profile_version=1, name="D")

    assert (heat_pump_device(SunflowConfig(devices=[device]), profiles) is device) is found


def test_weeks_without_heat() -> None:
    """A heat sensor that read zero for weeks (summer) still fits; the house then is not plausible."""
    rows = [HourRow(start=r.start, inside=r.inside, outside=r.outside, heat=0.0, sun=r.sun) for r in _house(21, heat=False)]

    model = fit(rows, NOW)

    assert model is not None
    assert not model.plausible


# Heat source and hours with hot water or defrost (design/heat_pump.md, 9.3)

HPSU = "esphome/rotex_hpsu_can"
T0 = NOW - 4 * HOUR


def _history(*changes: tuple[datetime, str], unit: str | None = None) -> list[dict[str, Any]]:
    """Minimal history rows as Home Assistant returns them; the first one carries the unit."""
    rows: list[dict[str, Any]] = [{"s": state, "lu": moment.timestamp()} for moment, state in changes]
    if unit:
        rows[0]["a"] = {"unit_of_measurement": unit}
    return rows


class Recorder:
    """Home Assistant without statistics: hourly means come from the recorded states."""

    def __init__(self, history: dict[str, list[dict[str, Any]]]) -> None:
        self.history = history
        self.asked: list[str] = []

    async def command(self, msg_type: str, **data: Any) -> Any:
        if msg_type == "recorder/statistics_during_period":
            return {}
        assert msg_type == "history/history_during_period"
        self.asked.extend(data["entity_ids"])
        return {e: self.history[e] for e in data["entity_ids"] if e in self.history}


def _hpsu(library: ProfileLibrary, **house: Any) -> tuple[SunflowConfig, Snapshot, Node]:
    node = Node()
    device = DeviceConfig(id="hp", ha_device_id=node.device_id, profile=HPSU, profile_version=2, name="HPSU CAN")
    config = SunflowConfig(devices=[device], house=HouseSettings(rooms=[Room(entity_id="sensor.room")], **house))
    return config, with_manifest(node_snapshot(node), node), node


def _recorder(node: Node, activity: list[tuple[datetime, str]]) -> Recorder:
    return Recorder({
        "sensor.room": _history((T0, "21.0"), unit="°C"),
        node.entity_id("temperature_outside"): _history((T0, "5.0"), unit="°C"),
        node.entity_id("ta2"): _history((T0, "2.0"), unit="°C"),
        # The heat pump reports kW
        node.entity_id("thermal_power"): _history((T0, "1.5"), unit="kW"),
        "sensor.meter": _history((T0, "800"), unit="W"),
        node.entity_id("mode_of_operating"): _history(*activity),
    })


def test_busy_hours() -> None:
    """Any share of hot water or defrost leaves the hour out; hours before the history stay in."""
    rows = _history(
        (T0 + HOUR, "heating"), (T0 + HOUR + timedelta(minutes=50), "hot_water"), (T0 + 2 * HOUR + timedelta(minutes=5), "heating"),
        (T0 + 3 * HOUR + timedelta(minutes=59), "defrost"),
    )

    hours = busy_hours(rows, lambda s: s, T0, NOW)

    assert hours == {T0 + HOUR, T0 + 2 * HOUR, T0 + 3 * HOUR}


async def test_hot_water_hour_is_left_out(library: ProfileLibrary) -> None:
    """Heat of the heat pump through its manifest, kW as W; the hour with hot water is left out, the others count."""
    config, snap, node = _hpsu(library)
    client = _recorder(node, [(T0, "Heizen"), (T0 + HOUR + timedelta(minutes=10), "Warmwasserbereitung"), (T0 + HOUR + timedelta(minutes=20), "Heizen")])

    data = await house_data(client, config, library.profiles, NOW, snap)  # type: ignore[arg-type]

    assert [r.start for r in data.rows] == [T0, T0 + 2 * HOUR, T0 + 3 * HOUR]
    assert data.excluded_hours == 1
    assert data.heat_source == "measured"
    assert {r.heat for r in data.rows} == {1.5}
    assert data.outside_entity == node.entity_id("temperature_outside")


async def test_own_meter_first_and_also_without_hot_water_hours(library: ProfileLibrary) -> None:
    """The house's heat meter wins over the heat pump; hot water hours are left out for it too."""
    config, snap, node = _hpsu(library, heat_entity="sensor.meter")
    client = _recorder(node, [(T0, "Heizen"), (T0 + 2 * HOUR, "Abtauen"), (T0 + 2 * HOUR + timedelta(minutes=5), "Heizen")])

    data = await house_data(client, config, library.profiles, NOW, snap)  # type: ignore[arg-type]

    assert data.heat_source == "meter"
    assert {r.heat for r in data.rows} == {0.8}
    assert [r.start for r in data.rows] == [T0, T0 + HOUR, T0 + 3 * HOUR]


async def test_outdoor_unit_chosen(library: ProfileLibrary) -> None:
    """The outside temperature of the outdoor unit (TA2) when chosen."""
    config, snap, node = _hpsu(library, outside_source="heat_pump_unit")

    data = await house_data(_recorder(node, [(T0, "Heizen")]), config, library.profiles, NOW, snap)  # type: ignore[arg-type]

    assert data.outside_entity == node.entity_id("ta2")
    assert {r.outside for r in data.rows} == {2.0}


async def test_nothing_left_out_without_a_heat_pump(library: ProfileLibrary) -> None:
    """Without a heat pump no activity is known: every hour counts, with the own meter's heat."""
    config, _, node = _hpsu(library, heat_entity="sensor.meter")
    config.devices.clear()
    config.learning_sources.outdoor_temperature = node.entity_id("temperature_outside")
    client = _recorder(node, [(T0, "Heizen"), (T0 + HOUR, "Warmwasserbereitung")])

    data = await house_data(client, config, library.profiles, NOW, None)  # type: ignore[arg-type]

    assert len(data.rows) == 4
    assert data.excluded_hours == 0
    assert node.entity_id("mode_of_operating") not in client.asked
