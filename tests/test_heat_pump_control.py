"""Steering a heat pump: state per plan slot, protection times and what each method writes."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
import pytest
from sunflow.api.devices import ManualDevice, _manual_setup
from sunflow.config.models import HeatPumpSettings
from sunflow.control.heat_pump import HeatPumpTrack, next_state, runtime, wanted_state
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import plan_steps
from sunflow.devices.profiles import ProfileLibrary

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
