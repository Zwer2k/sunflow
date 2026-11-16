"""Steering a heat pump: state per plan slot, protection times and what each method writes."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sunflow.api import create_app
from sunflow.api.devices import ManualDevice, _manual_setup
from sunflow.config.models import BuiltinRod, HeatPumpSettings
from sunflow.control.heat_pump import (
    HeatPumpTrack,
    HouseView,
    next_rod,
    next_state,
    runtime,
    wanted_rod,
    wanted_state,
)
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import plan_steps
from sunflow.devices.profiles import ProfileLibrary
from sunflow.planning.house_model import HouseModel

START = datetime(2026, 11, 16, tzinfo=UTC)
SLOT = timedelta(minutes=15)
SNAPSHOT = Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None)


@dataclass
class Slot:
    """The fields of a plan slot the heat pump looks at."""

    start: datetime
    end: datetime
    price: float
    export_w: float = 0.0
    curtail_w: float = 0.0


def _day(prices: list[float], export_w: float = 0.0) -> list[Slot]:
    """One slot per hour price (4 slots each); export in every slot."""
    slots = []
    for hour, price in enumerate(prices):
        for quarter in range(4):
            start = START + timedelta(hours=hour) + quarter * SLOT
            slots.append(Slot(start=start, end=start + SLOT, price=price, export_w=export_w))
    return slots


PRICES = [0.30, 0.20, 0.10, 0.40, 0.25, -0.01]


@pytest.mark.parametrize(
    ("settings", "hour", "export_w", "grid_w", "expected"),
    [
        pytest.param(HeatPumpSettings(method="sg_relays"), 0, 2000, None, "boost", id="surplus boosts"),
        pytest.param(HeatPumpSettings(method="sg_relays"), 0, 1000, None, "normal", id="too little surplus"),
        pytest.param(HeatPumpSettings(method="sg_relays", cheap_hours=2), 2, 0, None, "boost", id="among the cheapest hours"),
        pytest.param(HeatPumpSettings(method="sg_relays", cheap_hours=2), 1, 0, None, "normal", id="not among the cheapest"),
        pytest.param(
            HeatPumpSettings(method="sg_relays", use_block=True, expensive_hours=1), 3, 0, None, "block", id="dearest hour blocks"
        ),
        pytest.param(HeatPumpSettings(method="sg_relays", expensive_hours=1), 3, 0, None, "normal", id="block not allowed"),
        pytest.param(
            HeatPumpSettings(method="sg_relay", use_block=True, expensive_hours=1), 3, 0, None, "normal", id="one relay cannot block"
        ),
        pytest.param(HeatPumpSettings(method="sg_relays", use_forced=True), 5, 0, None, "forced", id="negative price"),
        pytest.param(HeatPumpSettings(method="surplus", use_forced=True), 5, 0, None, "normal", id="surplus cannot force"),
    ],
)
def test_wanted_state(settings: HeatPumpSettings, hour: int, export_w: float, grid_w: float | None, expected: str) -> None:
    """Surplus and cheap hours boost, the dearest block, negative prices force, where the method can."""
    slots = _day(PRICES, export_w)
    now = START + timedelta(hours=hour, minutes=5)

    assert wanted_state(settings, slots, now, UTC, HeatPumpTrack(), grid_w).state == expected


def test_boost_ends_when_the_surplus_is_not_there() -> None:
    """Boosting on planned surplus while the house imports: back to normal."""
    slots = _day(PRICES, 2000)
    track = HeatPumpTrack(state="boost", since=START - timedelta(hours=1))

    assert wanted_state(HeatPumpSettings(method="sg_relays"), slots, START, UTC, track, 800).state == "normal"


def test_minimum_time_per_state() -> None:
    """A state stays at least min_state_min; then the wanted one follows."""
    settings = HeatPumpSettings(method="sg_relays", min_state_min=20)
    track = HeatPumpTrack()
    surplus = _day(PRICES, 2000)
    quiet = _day(PRICES)

    assert next_state(settings, surplus, START, UTC, track, None).state == "boost"
    assert next_state(settings, quiet, START + timedelta(minutes=10), UTC, track, None).state == "boost"
    assert next_state(settings, quiet, START + timedelta(minutes=25), UTC, track, None).state == "normal"


def test_block_limits() -> None:
    """A block ends after block_max_min, at once, and counts towards the day's limit."""
    settings = HeatPumpSettings(method="sg_relays", use_block=True, expensive_hours=6, block_max_min=30, block_day_max_min=40)
    slots = _day([0.40] * 6)
    track = HeatPumpTrack()
    states = [next_state(settings, slots, START + timedelta(minutes=m), UTC, track, None).state for m in range(0, 120, 10)]

    assert states[:3] == ["block", "block", "block"]
    assert states[3] == "normal"
    assert "block" not in states[6:]
    assert track.blocked <= timedelta(minutes=40)


@pytest.mark.parametrize(
    ("settings", "state", "expected"),
    [
        pytest.param(HeatPumpSettings(method="sg_relays"), "block", {"sg_contact1": "on", "sg_contact2": "off"}, id="relays block"),
        pytest.param(HeatPumpSettings(method="sg_relays"), "boost", {"sg_contact1": "off", "sg_contact2": "on"}, id="relays boost"),
        pytest.param(HeatPumpSettings(method="sg_relays"), "forced", {"sg_contact1": "on", "sg_contact2": "on"}, id="relays forced"),
        pytest.param(
            HeatPumpSettings(method="sg_relays", contact2_inverted=True), "normal", {"sg_contact1": "off", "sg_contact2": "on"},
            id="normally closed contact",
        ),
        pytest.param(HeatPumpSettings(method="sg_relay"), "forced", {"sg_contact1": "off", "sg_contact2": "on"}, id="one relay"),
        pytest.param(
            HeatPumpSettings(method="sg_select", options={"normal": "Normal", "boost": "Verstärkt"}), "block",
            {"sg_option": "Normal"}, id="select without block option",
        ),
        pytest.param(HeatPumpSettings(method="setpoint", setpoint_normal=50, boost_k=8), "boost", {"setpoint": 58}, id="setpoint"),
        pytest.param(HeatPumpSettings(method="setpoint", setpoint_normal=50, block_k=5), "block", {"setpoint": 45}, id="setpoint low"),
        pytest.param(HeatPumpSettings(method="boost_switch"), "boost", {"boost_switch": "on"}, id="boost switch"),
    ],
)
def test_runtime(settings: HeatPumpSettings, state: str, expected: dict[str, object]) -> None:
    """What each method writes for a state."""
    values = runtime(state, settings, 2345.6)

    assert {k: values[k] for k in expected} == expected


def test_only_the_methods_roles_are_written(library: ProfileLibrary) -> None:
    """The profile's steps skip the roles of other methods."""
    profile = library.profiles["generic/heat_pump"]
    settings = HeatPumpSettings(method="surplus")

    calls = plan_steps(profile["heat_pump_control"], runtime("boost", settings, 2345.6), [], "ram", {"surplus_power": "number.wp_pv"})

    assert [(c.entity_id, c.service, c.data) for c in calls] == [("number.wp_pv", "set_value", {"value": 2346})]


@pytest.mark.parametrize(
    ("body", "roles"),
    [
        pytest.param(
            ManualDevice(
                name="WP", type="heat_pump", power_entity="sensor.p", sg_contact1_entity="switch.c1", sg_contact2_entity="switch.c2",
                heat_pump=HeatPumpSettings(method="sg_relays", use_block=True),
            ),
            {"power", "sg_contact1", "sg_contact2"},
            id="two relays",
        ),
        pytest.param(
            ManualDevice(
                name="WP", type="heat_pump", power_entity="sensor.p", sg_contact2_entity="switch.c2", setpoint_entity="number.ww",
                heat_pump=HeatPumpSettings(method="sg_relay"),
            ),
            {"power", "sg_contact2"},
            id="other methods' entities are not bound",
        ),
        pytest.param(
            ManualDevice(name="WP", type="heat_pump", power_entity="sensor.p", heat_pump=HeatPumpSettings()),
            {"power"},
            id="only measured",
        ),
    ],
)
def test_control_setup(body: ManualDevice, roles: set[str]) -> None:
    """Only the chosen method's entities are bound."""
    overrides, _, _ = _manual_setup(body, SNAPSHOT)

    assert set(overrides) == roles


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param(HeatPumpSettings(method="sg_relays", use_block=True), id="block without contact 1"),
        pytest.param(HeatPumpSettings(method="sg_select", options={"normal": "N"}), id="select without boost option"),
        pytest.param(HeatPumpSettings(method="setpoint"), id="setpoint without normal value"),
    ],
)
def test_control_setup_refuses(settings: HeatPumpSettings) -> None:
    """Missing entities or values of the method are refused."""
    body = ManualDevice(
        name="WP", type="heat_pump", power_entity="sensor.p", sg_contact2_entity="switch.c2", sg_select_entity="select.sg",
        setpoint_entity="number.ww", heat_pump=settings,
    )

    with pytest.raises(HTTPException):
        _manual_setup(body, SNAPSHOT)


def _client(tmp_path: Path) -> TestClient:
    from sunflow.config.store import ConfigStore
    from sunflow.devices.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    app = create_app()
    app.state.store = ConfigStore(tmp_path)
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))

    async def snapshot(fresh: bool = False) -> Snapshot:
        return SNAPSHOT

    app.state.snapshots = SimpleNamespace(get=snapshot)
    return TestClient(app)


ROD = {"name": "Heizstab", "type": "relays", "kind": "heater", "relay_entities": ["switch.rod"], "relay_power_w": 2000,
       "temperature_entity": "sensor.tank", "tank_l": 300}
HEAT_PUMP = {"name": "WP", "type": "heat_pump", "power_entity": "sensor.p"}


@pytest.mark.parametrize(
    ("rod", "status"),
    [
        pytest.param(ROD, 200, id="heating consumer"),
        pytest.param(ROD | {"kind": "load"}, 422, id="not a heating consumer"),
    ],
)
def test_rod_link(tmp_path: Path, rod: dict[str, object], status: int) -> None:
    """A heat pump links a consumer that heats as its rod, and only one heat pump does."""
    client = _client(tmp_path)
    rod_id = client.post("/api/v1/config/devices/manual", json=rod).json()["id"]
    body = HEAT_PUMP | {"heat_pump": {"rod_id": rod_id}}

    assert client.post("/api/v1/config/devices/manual", json=body).status_code == status


def test_rod_of_one_heat_pump(tmp_path: Path) -> None:
    """A second heat pump cannot take the same rod."""
    client = _client(tmp_path)
    rod_id = client.post("/api/v1/config/devices/manual", json=ROD).json()["id"]
    body = HEAT_PUMP | {"heat_pump": {"rod_id": rod_id}}
    first = client.post("/api/v1/config/devices/manual", json=body).json()["id"]

    assert client.put(f"/api/v1/config/devices/manual/{first}", json=body).status_code == 200
    assert client.post("/api/v1/config/devices/manual", json=body).status_code == 422


@pytest.mark.parametrize(
    ("profile", "kind"),
    [
        pytest.param("generic/heat_pump", "heat_pump", id="heat pump"),
        pytest.param("generic/relays", "loadpoint", id="heating rod"),
        pytest.param("solax_modbus/sofar", "battery", id="battery"),
    ],
)
def test_control_kind(library: ProfileLibrary, profile: str, kind: str) -> None:
    """The control tab names a heat pump as such, not as a battery."""
    from sunflow.api.control import control_kind

    assert control_kind(library.profiles[profile]) == kind


ROD_SETTINGS = BuiltinRod(method="select", options={"free": "free", "blocked": "blocked"})


@pytest.mark.parametrize(
    ("rod", "hour", "season", "outside", "expected"),
    [
        pytest.param(ROD_SETTINGS, 0, True, 5.0, "blocked", id="heat pump copes"),
        pytest.param(ROD_SETTINGS, 0, True, -8.0, "free", id="colder than the bivalence point"),
        pytest.param(ROD_SETTINGS, 0, True, None, "free", id="no outside temperature"),
        pytest.param(ROD_SETTINGS, 5, True, 5.0, "max", id="negative price"),
        pytest.param(ROD_SETTINGS, 0, False, 20.0, "max", id="summer surplus"),
        pytest.param(ROD_SETTINGS.model_copy(update={"summer_mode": "need"}), 0, False, 20.0, "blocked", id="summer only on need"),
        pytest.param(ROD_SETTINGS.model_copy(update={"heating_mode": "auto"}), 0, True, 5.0, "free", id="heat pump decides"),
        pytest.param(
            ROD_SETTINGS.model_copy(update={"released_until": START + timedelta(hours=2)}), 0, True, 5.0, "max", id="released by hand"
        ),
    ],
)
def test_wanted_rod(rod: BuiltinRod, hour: int, season: bool, outside: float | None, expected: str) -> None:
    """The built-in rod is blocked while the heat pump copes and released when cold, cheap, sunny or by hand."""
    slots = _day(PRICES, 2000)
    now = START + timedelta(hours=hour, minutes=5)

    assert wanted_rod(rod, slots, now, season, outside, 1500).state == expected


def test_rod_changes_rarely() -> None:
    """The rod's state is held for min_state_min; releasing it for the cold is never held back."""
    rod = ROD_SETTINGS.model_copy(update={"min_state_min": 60})
    track = HeatPumpTrack()
    slots = _day(PRICES)

    assert next_rod(rod, slots, START, True, 5.0, 1500, track).state == "blocked"
    assert next_rod(rod, _day(PRICES, 2000), START + timedelta(minutes=30), False, 20.0, 1500, track).state == "blocked"
    assert next_rod(rod, slots, START + timedelta(minutes=40), True, -10.0, 1500, track).state == "free"


@pytest.mark.parametrize(
    ("rod", "state", "expected"),
    [
        pytest.param(BuiltinRod(method="allow_switch"), "blocked", {"rod_allow": "off"}, id="allow switch blocked"),
        pytest.param(BuiltinRod(method="block_switch"), "blocked", {"rod_block": "on"}, id="block switch blocked"),
        pytest.param(BuiltinRod(method="block_switch"), "max", {"rod_block": "off"}, id="block switch released"),
        pytest.param(
            BuiltinRod(method="select", options={"free": "3 kW", "blocked": "0 kW", "max": "9 kW"}), "max", {"rod_option": "9 kW"},
            id="select max",
        ),
        pytest.param(BuiltinRod(method="select", options={"free": "free", "blocked": "blocked"}), "max", {"rod_option": "free"}, id="select without max"),
        pytest.param(BuiltinRod(method="limit", limit_normal=3, limit_max=9), "max", {"rod_limit": 9}, id="limit max"),
        pytest.param(BuiltinRod(method="limit", limit_normal=3), "blocked", {"rod_limit": 0}, id="limit blocked"),
    ],
)
def test_rod_runtime(rod: BuiltinRod, state: str, expected: dict[str, object]) -> None:
    """What each method of the built-in rod writes for a state."""
    values = runtime("normal", HeatPumpSettings(rod=rod), 0.0, state)

    assert {k: values[k] for k in expected} == expected


def test_rod_steps_go_to_flash(library: ProfileLibrary) -> None:
    """The rod's settings are written only when they differ, counted as flash writes."""
    profile = library.profiles["generic/heat_pump"]
    settings = HeatPumpSettings(rod=BuiltinRod(method="block_switch"))

    calls = plan_steps(profile["heat_pump_control"], runtime("normal", settings, 0.0, "blocked"), [], "ram", {"rod_block": "switch.aux_off"})

    assert [(c.entity_id, c.service, c.storage, c.write) for c in calls] == [("switch.aux_off", "turn_on", "eeprom", "if_differs")]


@pytest.mark.parametrize(
    ("settings", "status"),
    [
        pytest.param({"rod": {"method": "block_switch"}}, 200, id="block switch"),
        pytest.param({"rod": {"method": "limit"}}, 422, id="limit without normal value"),
        pytest.param({"rod": {"method": "block_switch"}, "rod_id": "x"}, 422, id="not both rods"),
    ],
)
def test_builtin_rod_setup(tmp_path: Path, settings: dict[str, object], status: int) -> None:
    """The built-in rod needs its entity and values, and excludes a rod consumer."""
    body = HEAT_PUMP | {"rod_entity": "switch.aux_off", "heat_pump": settings}

    assert _client(tmp_path).post("/api/v1/config/devices/manual", json=body).status_code == status


MODEL = HouseModel(a=0.02, b=0.04, c=None, d=0.0, days=14, samples=300, error_k=0.2, baseline_k=0.8)


def _view(inside: float, outside: float, lower: float = 20.0, upper: float = 22.0) -> HouseView:
    return HouseView(model=MODEL, inside=inside, outside=outside, upper=upper, lower=lambda _moment: lower)


@pytest.mark.parametrize(
    ("house", "season", "expected"),
    [
        pytest.param(_view(21.5, 0.0), True, "block", id="warm house may pause"),
        pytest.param(_view(20.3, -5.0), True, "normal", id="block would cool below the band"),
        pytest.param(_view(20.3, -5.0), False, "block", id="summer: rooms do not count"),
    ],
)
def test_block_within_the_comfort_band(house: HouseView, season: bool, expected: str) -> None:
    """A block is set only while the house stays above the lower bound until it ends."""
    settings = HeatPumpSettings(method="sg_relays", use_block=True, expensive_hours=1, block_max_min=120)
    now = START + timedelta(hours=3, minutes=5)

    assert wanted_state(settings, _day(PRICES), now, UTC, HeatPumpTrack(), None, house, season).state == expected


@pytest.mark.parametrize(
    ("house", "season", "expected"),
    [
        pytest.param(_view(21.0, 5.0), True, "boost", id="room for more heat"),
        pytest.param(_view(22.1, 5.0), True, "normal", id="upper bound reached"),
        pytest.param(_view(22.1, 5.0), False, "boost", id="summer: hot water"),
    ],
)
def test_boost_up_to_the_upper_bound(house: HouseView, season: bool, expected: str) -> None:
    """Boosting heats the rooms only up to the upper bound in the heating season."""
    slots = _day(PRICES, 2000)

    assert wanted_state(HeatPumpSettings(method="sg_relays"), slots, START, UTC, HeatPumpTrack(), None, house, season).state == expected


def test_block_ends_early_when_the_house_cools() -> None:
    """A running block ends as soon as the rest of it would cool the house too far, despite the minimum time."""
    settings = HeatPumpSettings(method="sg_relays", use_block=True, expensive_hours=6, min_state_min=60)
    track = HeatPumpTrack()
    slots = _day([0.40] * 6)

    assert next_state(settings, slots, START, UTC, track, None, _view(21.0, 0.0), True).state == "block"
    assert next_state(settings, slots, START + timedelta(minutes=10), UTC, track, None, _view(20.1, -10.0), True).state == "normal"


def test_reference_room_and_night_setback() -> None:
    """The band applies to the reference room; at night the lower bound may be lower."""
    night = HouseView(
        model=MODEL, inside=20.0, offset=-0.5, outside=0.0, upper=22.0,
        lower=lambda moment: 18.0 if moment.hour < 6 else 20.0,
    )

    margin, bound = night.coldest_margin(START, 120)

    assert night.reference == 19.5
    assert bound == 18.0
    assert 0 < margin < 1.5
