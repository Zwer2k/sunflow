"""Jump protection at a change of the feedforward source (design/heat_pump.md, 8.1, 16 step 3 part 4.5).

Only while Sunflow writes for real: the correction is shifted by old − new start value (within ±3 K), the
rest stays as a jump and goes into the journal. In the shadow the flow target jumps once, by design.
Writing for real is switched on only in part 4.7; here it is simulated with FlowShadow.active.
"""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sunflow.heat_pump.feedforward import BASE, HOUSE_MODEL, HouseContext, source_shift
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from test_heat_pump_curve import MIN, NOW, Shadow, _house_model, _minute, _ready_curve

# Base (effective setpoint) of the synthetic minutes
BASE_C = 31.0
# A house model that needs 2.5 kW at 5 °C outside: (0.02·16 − 0.02)/0.12; the curve gives about 34.18 °C
HOUSE = _house_model(b=0.12)
CURVE_C = _ready_curve().flow_c(21.0, 2500.0)
JUMP = CURVE_C - BASE_C


def _notes(shadow: Shadow) -> list[str]:
    return [m for _, m, _ in shadow.shadow.notes if "Quelle des Vorlauf-Ziels" in m]


def _flow_events(shadow: Shadow) -> list[dict[str, Any]]:
    return [e for _, e in shadow.shadow.source_events if e["kind"] == "flow_source"]


async def _to_house_model(tmp_path: Path, correction: float, active: bool, gap: timedelta = MIN) -> tuple[Shadow, dict[str, Any], dict[str, Any]]:
    """One minute on the base, then (after gap) the house model takes over: flows before and after."""
    shadow = Shadow(tmp_path)
    shadow.shadow.active["hp"] = active
    shadow.shadow.set_curve("hp", _ready_curve())
    shadow.shadow._correction("hp", "fixed").value_k = correction
    before = await shadow.tick(NOW, _minute(NOW, setpoint=BASE_C))
    shadow.shadow.house = HouseContext(model=HOUSE)
    after = await shadow.tick(NOW + gap, _minute(NOW + gap, setpoint=BASE_C, outside=5.0))
    return shadow, before, after


def test_shift_keeps_the_target_within_the_limit() -> None:
    assert source_shift(31.0, 33.0, 0.5) == pytest.approx((-2.0, 0.0))
    assert source_shift(33.0, 31.0, -1.0) == pytest.approx((2.0, 0.0))
    # Only to +3 K: 1 K of the jump stays (new target 1 K below the old one)
    assert source_shift(35.0, 31.0, 0.0) == pytest.approx((3.0, -1.0))
    assert source_shift(31.0, 36.0, -1.0) == pytest.approx((-2.0, 3.0))
    # At the limit already, in the direction needed: no shift, the whole jump stays
    assert source_shift(31.0, 33.0, -3.0) == pytest.approx((0.0, 2.0))


async def test_shadow_jumps_once_and_keeps_the_correction(tmp_path: Path) -> None:
    shadow, before, after = await _to_house_model(tmp_path, 1.0, active=False)
    curve_c = after["curve_c"]

    assert (before["source"], after["source"]) == (BASE, HOUSE_MODEL)
    assert after["correction_k"] == before["correction_k"] == 1.0
    assert after["target_c"] == pytest.approx(curve_c + 1.0, abs=0.01)
    [event] = _flow_events(shadow)
    assert event["shadow"] is True and "shift_k" not in event and "protected" not in event
    assert event["jump_k"] == pytest.approx(curve_c - BASE_C, abs=0.01)
    assert after["source_change"] == event
    assert _notes(shadow) == []


async def test_active_shifts_the_correction_so_the_target_does_not_jump(tmp_path: Path) -> None:
    shadow, before, after = await _to_house_model(tmp_path, 1.0, active=True)

    assert JUMP == pytest.approx(3.18, abs=0.01)
    assert after["source"] == HOUSE_MODEL and after["curve_c"] == pytest.approx(CURVE_C, abs=0.01)
    assert after["target_c"] == pytest.approx(before["target_c"], abs=0.01) == BASE_C + 1.0
    assert after["correction_k"] == pytest.approx(1.0 - JUMP, abs=0.01)
    [event] = _flow_events(shadow)
    assert (event["shadow"], event["protected"]) == (False, True)
    assert event["jump_k"] == pytest.approx(JUMP, abs=0.01)
    assert event["shift_k"] == pytest.approx(-JUMP, abs=0.01)
    assert event["rest_k"] == pytest.approx(0.0, abs=0.01)
    assert _notes(shadow) == []
    # Kept for the strategy (state file) like any correction
    restarted = FlowShadow(HeatPumpStateStore(tmp_path))
    assert restarted._correction("hp", "fixed").value_k == pytest.approx(after["correction_k"], abs=1e-3)


async def test_active_rest_beyond_the_limit_jumps_and_is_noted(tmp_path: Path) -> None:
    """Correction 0: the shift stops at −3 K, about 0.18 K of the jump stays."""
    shadow, before, after = await _to_house_model(tmp_path, 0.0, active=True)

    assert after["correction_k"] == pytest.approx(-3.0)
    assert after["target_c"] - before["target_c"] == pytest.approx(JUMP - 3.0, abs=0.01)
    [event] = _flow_events(shadow)
    assert event["shift_k"] == pytest.approx(-3.0) and event["rest_k"] == pytest.approx(JUMP - 3.0, abs=0.01)
    [note] = _notes(shadow)
    assert "springt um +0,2 K" in note and "Basis → Hausmodell" in note


async def test_active_at_the_limit_nothing_to_shift(tmp_path: Path) -> None:
    shadow, before, after = await _to_house_model(tmp_path, -3.0, active=True)

    assert after["correction_k"] == pytest.approx(-3.0)
    assert after["target_c"] - before["target_c"] == pytest.approx(JUMP, abs=0.01)
    [event] = _flow_events(shadow)
    assert event["shift_k"] == pytest.approx(0.0) and event["rest_k"] == pytest.approx(JUMP, abs=0.01)
    assert len(_notes(shadow)) == 1


async def test_active_without_a_recent_old_value_no_protection(tmp_path: Path) -> None:
    """The old start value is older than 30 min (e.g. node away): no shift, journal."""
    shadow, _, after = await _to_house_model(tmp_path, 1.0, active=True, gap=timedelta(minutes=31))

    assert after["correction_k"] == pytest.approx(1.0)
    [event] = _flow_events(shadow)
    assert event["protected"] is False and "shift_k" not in event and "jump_k" not in event
    [note] = _notes(shadow)
    assert "ohne Sprungschutz" in note


async def test_active_way_back_and_after_a_restart(tmp_path: Path) -> None:
    """House model → base after a restart of Sunflow: the stored start value protects as well."""
    _, _, on_curve = await _to_house_model(tmp_path, 1.0, active=True)
    restarted = Shadow(tmp_path)
    restarted.shadow.active["hp"] = True
    restarted.shadow.set_curve("hp", _ready_curve())
    # No house model any more, no recent heat after the restart: back to the base
    back = await restarted.tick(NOW + 2 * MIN, _minute(NOW + 2 * MIN, setpoint=BASE_C))

    assert back["source"] == BASE
    assert back["target_c"] == pytest.approx(on_curve["target_c"], abs=0.01) == BASE_C + 1.0
    assert back["correction_k"] == pytest.approx(1.0, abs=0.01)
    [event] = _flow_events(restarted)
    assert (event["from"], event["to"], event["protected"]) == (HOUSE_MODEL, BASE, True)
    assert _notes(restarted) == []


async def test_no_change_no_event(tmp_path: Path) -> None:
    shadow = Shadow(tmp_path)
    shadow.shadow.active["hp"] = True
    for i in range(3):
        flow = await shadow.tick(NOW + i * MIN, _minute(NOW + i * MIN, setpoint=BASE_C))

    assert _flow_events(shadow) == [] and flow["source_change"] is None
