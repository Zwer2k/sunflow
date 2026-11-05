"""Planning several batteries together (e.g. a hybrid inverter and a plug-in storage)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sunflow.planning.optimizer import BatterySpec, Settings, SlotInput, optimize

START = datetime(2026, 11, 5, 18, 0, tzinfo=ZoneInfo("Europe/Berlin"))
SLOT = timedelta(minutes=15)

HOUSE = BatterySpec(
    key="house", capacity_wh=20000, soc_wh=10000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000, wear_ct_per_kwh=30
)
PLUG = BatterySpec(
    key="plug", capacity_wh=2000, soc_wh=1500, min_soc_wh=200, max_charge_w=1200, max_discharge_w=800, wear_ct_per_kwh=1
)


def _slots(hours: int, pv: float = 0.0, load: float = 600) -> list[SlotInput]:
    """An expensive first hour, cheap afterwards."""
    return [
        SlotInput(start=START + i * SLOT, pv_w=pv, load_w=load, price=0.40 if i < 4 else 0.15, feed_in=0.08)
        for i in range(hours * 4)
    ]


def test_sums_and_shares_per_battery() -> None:
    """Every slot holds each battery; charge, discharge and SoC of the slot are their sums."""
    plan = optimize(_slots(4), [HOUSE, PLUG], Settings())

    for slot in plan.slots:
        assert set(slot.batteries) == {"house", "plug"}
        assert slot.discharge_w == pytest.approx(sum(b.discharge_w for b in slot.batteries.values()))
        assert slot.soc_wh == pytest.approx(sum(b.soc_wh for b in slot.batteries.values()))
    # The battery with the cheaper wear covers the expensive hour
    assert plan.slots[0].batteries["plug"].discharge_w == pytest.approx(600, abs=1)
    assert plan.slots[0].batteries["house"].discharge_w == pytest.approx(0, abs=1)
    assert plan.slots[0].import_w == pytest.approx(0, abs=1)


def test_idle_battery_holds_while_the_other_covers_the_house() -> None:
    """Two batteries in self-consumption would share the load; the one left out is told to hold."""
    plan = optimize(_slots(2), [HOUSE, PLUG], Settings())

    assert plan.slots[0].batteries["plug"].action == "auto"
    assert plan.slots[0].batteries["house"].action == "hold"
    # The slot's action stays the first battery's
    assert plan.slots[0].action == "hold"


def test_no_energy_moved_between_batteries() -> None:
    """Charging one battery from the other only loses energy, even when its stored energy is worth more."""
    cheap_wear = BatterySpec(
        key="a", capacity_wh=10000, soc_wh=1000, min_soc_wh=500, max_charge_w=5000, max_discharge_w=5000, wear_ct_per_kwh=0
    )
    full = BatterySpec(
        key="b", capacity_wh=10000, soc_wh=9000, min_soc_wh=500, max_charge_w=5000, max_discharge_w=5000, wear_ct_per_kwh=0,
        efficiency_charge=0.99, efficiency_discharge=0.99,
    )
    plan = optimize(_slots(6, load=300), [cheap_wear, full], Settings(allow_grid_charge=False))

    for slot in plan.slots:
        charging = sum(b.charge_w for b in slot.batteries.values())
        assert charging <= max(slot.pv_w - slot.load_w, 0) + slot.import_w + 1


@pytest.mark.parametrize(("include", "first"), [(True, "plug"), (False, "house")], ids=["wear-counted", "house-free"])
def test_wear_per_battery_decides_which_discharges(include: bool, first: str) -> None:
    """Free cycles (wear left out of planning) make a battery the first choice."""
    house = HOUSE if include else BatterySpec(**{**HOUSE.__dict__, "wear_ct_per_kwh": 0})
    plug = PLUG if include else BatterySpec(**{**PLUG.__dict__, "wear_ct_per_kwh": 20})

    plan = optimize(_slots(6), [house, plug], Settings())

    assert plan.slots[0].batteries[first].discharge_w == pytest.approx(600, abs=1)


def test_baseline_uses_all_batteries() -> None:
    """Without Sunflow both batteries cover the load one after the other."""
    plan = optimize(_slots(8), [HOUSE, PLUG], Settings())

    # 8 h x 600 W fits into the two batteries: no import without Sunflow
    assert plan.baseline_cost == pytest.approx(0, abs=1e-6)


def test_plan_response_lists_the_batteries() -> None:
    """The UI gets every battery with its capacity and each slot's share under the same id."""
    from sunflow.api.plan import plan_response
    from sunflow.config.models import SunflowConfig
    from sunflow.planning.plan import PlanResult

    plan = optimize(_slots(2), [HOUSE, PLUG], Settings())
    result = PlanResult(plan=plan, battery=HOUSE, batteries=[("house", "Sofar", HOUSE), ("plug", "Indevolt", PLUG)])

    response = plan_response(result, SunflowConfig())

    assert response["batteries"] == [
        {"id": "house", "name": "Sofar", "capacity_wh": 20000, "min_soc_wh": 2000},
        {"id": "plug", "name": "Indevolt", "capacity_wh": 2000, "min_soc_wh": 200},
    ]
    assert set(response["slots"][0]["batteries"]) == {"house", "plug"}
    assert response["slots"][0]["batteries"]["plug"]["action"] == "auto"


@pytest.mark.parametrize(
    ("mode", "discharge", "forced"),
    [
        pytest.param(None, 5000, True, id="plan"),
        pytest.param("hold", 0, True, id="hold"),
        pytest.param("self", 5000, False, id="self"),
    ],
)
def test_battery_override(mode: str | None, discharge: float, forced: bool) -> None:
    """Held from the control tab a battery gives nothing; running on its own it is never forced."""
    from sunflow.planning.plan import with_override

    spec = with_override(HOUSE, mode)

    assert spec.max_discharge_w == discharge
    assert (spec.force_charge, spec.force_discharge) == (forced, forced)


def test_battery_mode_until() -> None:
    """A temporary mode ends at its time and goes with the device."""
    from datetime import UTC

    from sunflow.config.models import BatteryOverride, SunflowConfig

    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    config = SunflowConfig(battery_overrides={
        "house": BatteryOverride(mode="hold", until=now + SLOT),
        "plug": BatteryOverride(mode="self", until=now - SLOT),
    })

    assert config.battery_mode("house", now) == "hold"
    assert config.battery_mode("plug", now) is None
    config.forget_device("house")
    assert config.battery_mode("house", now) is None


def test_held_battery_does_not_discharge() -> None:
    """The held battery keeps its energy; the other one covers what it can."""
    from sunflow.planning.plan import with_override

    plan = optimize(_slots(6), [HOUSE, with_override(PLUG, "hold")], Settings())

    assert all(s.batteries["plug"].discharge_w < 1 for s in plan.slots)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Sofar Inverter", "Sofar"), ("INDEVOLT CMS-SF2000", "INDEVO"), ("Balkon", "Balkon")],
)
def test_default_short_name(name: str, expected: str) -> None:
    """Without a short name of its own a battery shows its name's first word, cut to 6 characters."""
    from sunflow.api.live import short_name

    assert short_name(name) == expected
