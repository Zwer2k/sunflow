"""Write layer behind the protection layer (design/heat_pump.md, 8.5, 16 step 3 part 4.2): leading edge,
minimum spacing (normal / protection step), only the latest value kept, deadband except for protection
steps, rounding the safe way, pending with its time, "write now" (next tick, lapses), journal only for
real writes. Nothing is written to a heat pump: every heat pump runs in the shadow."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from heat_pump_node import joined
import pytest
from sunflow.api.heat_pump import router
from sunflow.config.models import DeviceConfig
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.limits import ControlSettings
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from sunflow.heat_pump.writer import Request, Writer
from test_heat_pump_guard import Sim

NOW = datetime(2026, 11, 21, 10, tzinfo=UTC)
MIN = timedelta(minutes=1)
PROFILE = "esphome/rotex_hpsu_can"
SETTINGS = ControlSettings(step=0.1)
LIMITS = (25.0, 40.0)


class Run:
    """The write layer alone, minute by minute, with the written value it keeps track of."""

    def __init__(self, written: float = 30.0, active: bool = False, settings: ControlSettings = SETTINGS) -> None:
        self.writer = Writer()
        self.written_c: float | None = written
        self.written_at: datetime | None = None
        self.now = NOW
        self.active = active
        self.settings = settings
        self.budget: Any = None
        self.log: list[tuple[datetime, float, str, bool]] = []
        self.notes: list[str] = []
        self.outcomes: list[Any] = []

    def minute(self, value: float | None, reason: str = "normal", inactive: bool = False) -> Any:
        request = Request(value_c=value, reason=reason, protect=reason in ("start", "track")) if value is not None else None
        outcome = self.writer.step(
            now=self.now, request=request, written_c=self.written_c, written_at=self.written_at, settings=self.settings,
            limits=LIMITS, active=self.active, inactive=inactive, budget=self.budget,
        )
        if outcome.write is not None:
            self.written_c, self.written_at = outcome.write.value_c, self.now
            self.log.append((self.now, outcome.write.value_c, outcome.write.reason, outcome.manual))
        self.notes += [m for m, _ in outcome.notes]
        self.outcomes.append(outcome)
        self.now += MIN
        return outcome


# Leading edge, spacing, only the latest value


def test_example_of_the_specification_two_writes_instead_of_three() -> None:
    """10:00 new value at once; 10:01 and 10:02 only kept; 10:05 the latest written."""
    run = Run(written=30.0)
    first = run.minute(33.0)
    second = run.minute(31.0)
    run.minute(32.0)
    run.minute(32.0)
    waiting = run.minute(32.0)
    run.minute(32.0)

    assert [(at.strftime("%H:%M"), value) for at, value, _, _ in run.log] == [("10:00", 33.0), ("10:05", 32.0)]
    assert first.pending is None and first.write is not None
    assert (second.pending.value_c, second.pending.due_at, second.pending.held) == (31.0, NOW + 5 * MIN, "spacing")
    assert waiting.pending.value_c == 32.0


def test_a_value_back_at_the_written_one_is_dropped() -> None:
    run = Run(written=30.0)
    run.minute(33.0)
    run.minute(31.0)
    gone = run.minute(33.2)
    for _ in range(5):
        run.minute(33.2)

    # 33.2 lies within the deadband of 33.0: nothing pending, nothing written
    assert gone.pending is None
    assert [value for _, value, _, _ in run.log] == [33.0]


def test_pending_lapses_when_nothing_is_wanted_any_more() -> None:
    run = Run(written=30.0)
    run.minute(33.0)
    run.minute(31.0)
    quiet = run.minute(None)
    run.minute(None)
    run.minute(None)
    run.minute(None)

    assert quiet.pending is None and len(run.log) == 1


def test_protection_step_waits_two_minutes_after_any_write() -> None:
    run = Run(written=30.0)
    run.minute(33.0)
    early = run.minute(34.0, reason="start")
    run.minute(34.0, reason="start")
    run.minute(34.0, reason="start")

    assert (early.pending.due_at, early.pending.protect) == (NOW + 2 * MIN, True)
    assert [(at - NOW, value, reason) for at, value, reason, _ in run.log] == [(timedelta(0), 33.0, "normal"), (2 * MIN, 34.0, "start")]


def test_protection_step_has_no_deadband_and_only_raises() -> None:
    run = Run(written=30.0)
    small = run.minute(30.1, reason="track")
    run.now += 5 * MIN
    lower = run.minute(29.0, reason="track")

    assert small.write is not None and small.write.value_c == 30.1
    assert lower.write is None and lower.pending is None


def test_normal_value_within_the_deadband_is_not_written() -> None:
    run = Run(written=30.0)
    outcome = run.minute(30.5)

    assert outcome.write is None and outcome.pending is None


def test_first_value_after_a_restart_goes_out_at_once() -> None:
    """Without a known time of the last write the spacing does not hold it back."""
    run = Run(written=30.0)
    assert run.minute(31.0).write is not None


# Rounding the safe way


@pytest.mark.parametrize(
    ("value", "reason", "step", "written"),
    [
        pytest.param(31.01, "start", 0.1, 31.1, id="protection up"),
        pytest.param(31.04, "normal", 0.1, 31.0, id="normal to nearest"),
        pytest.param(31.1, "start", 0.5, 31.5, id="protection up step 0.5"),
        pytest.param(31.2, "normal", 0.5, 31.0, id="normal nearest step 0.5"),
        pytest.param(42.0, "start", 0.1, 40.0, id="never above the upper limit"),
        pytest.param(20.0, "normal", 0.1, 25.0, id="never below the lower limit"),
    ],
)
def test_rounding_the_safe_way(value: float, reason: str, step: float, written: float) -> None:
    run = Run(written=30.0 if written != 25.0 else 28.0, settings=ControlSettings(step=step))
    outcome = run.minute(value, reason=reason)

    assert outcome.write is not None and outcome.write.value_c == written


# Spacing from the profile


def test_spacing_from_the_profile(library: Any) -> None:
    profile = library.profiles[PROFILE]
    settings = ControlSettings.from_profile(profile, joined())
    shorter = ControlSettings.from_profile({**profile, "flow_control": {**profile["flow_control"], "writes": {"min_interval": "1min", "protect_interval": "10s"}}}, joined())
    without = ControlSettings.from_profile({**profile, "flow_control": {}}, None)

    assert (settings.min_interval_s, settings.protect_interval_s) == (300, 120)
    # 1 min for testing; never shorter than the heat pump needs to take a value over
    assert (shorter.min_interval_s, shorter.protect_interval_s) == (60, 60)
    assert (without.min_interval_s, without.protect_interval_s) == (300, 120)


# Through the protection layer (shadow)


def test_tracking_after_a_normal_write_waits_for_the_protection_spacing() -> None:
    sim = Sim(written=30.0)
    sim.target = 31.0
    normal_at = next(sim.results[-1][0] for _ in range(10) if sim.minute(30.0).writes)
    # Tv jumps right after: tracking wants 32.0 (34 − 3 + 0.5, a swing above), the protection spacing holds it back
    tracking = sim.run([34.0] * 3)

    assert sim.writes() == [("normal", 31.0), ("track", 32.0)]
    track_at = next(at for at, _, r in sim.results if r.writes and r.writes[0]["reason"] == "track")
    assert track_at - normal_at == 2 * MIN
    assert tracking[0].pending is not None and tracking[0].pending.reason == "track"
    # Shadow: events as before, no journal for the virtual writes
    assert all(w["shadow"] for _, _, r in sim.results for w in r.writes)
    assert not any("geschrieben" in n for n in sim.notes())


def test_target_changes_within_the_spacing_cause_two_writes() -> None:
    sim = Sim(written=30.0)
    sim.run([30.0] * 5)
    for target in (33.0, 31.0, 32.0, 32.0, 32.0, 32.0, 32.0):
        sim.target = target
        sim.minute(30.0)

    assert sim.writes() == [("normal", 33.0), ("normal", 32.0)]
    assert (sim.results[-3][2].setpoint_c, sim.results[-2][2].setpoint_c) == (33.0, 32.0)


def test_pending_in_the_guard_status() -> None:
    sim = Sim(written=30.0)
    sim.run([30.0] * 5)
    sim.target = 33.0
    sim.minute(30.0)
    sim.target = 31.5
    result = sim.minute(30.0)

    assert result.pending.as_dict() == {
        "value_c": 31.5, "reason": "normal", "protect": False, "due_at": (sim.now - 2 * MIN + 5 * MIN).isoformat(), "held": "spacing",
        "budget": None,
    }
    assert result.setpoint_c == 33.0


# "Write now"


def test_write_now_skips_the_spacing_in_the_next_tick() -> None:
    run = Run(written=30.0, active=True)
    run.minute(33.0)
    run.minute(31.0)
    run.writer.request_now(run.now)
    forced = run.minute(31.0)
    after = run.minute(32.0)

    assert [(value, reason, manual) for _, value, reason, manual in run.log] == [(33.0, "normal", False), (31.0, "normal", True)]
    assert forced.manual_result["done"] and forced.manual_result["reason"] is None
    # The wish lapses after one tick: the next value waits for the spacing again
    assert after.write is None and after.manual_result is None and after.pending is not None
    # Real writes get a journal entry with their reason
    assert run.notes[0].startswith("Max T-Vorlauf geschrieben: 33,0 °C (vorher 30,0 °C), Grund: Vorlauf-Ziel")
    assert run.notes[1] == "Max T-Vorlauf geschrieben: 31,0 °C (vorher 33,0 °C), Grund: manuell (jetzt schreiben)."


@pytest.mark.parametrize(
    ("value", "inactive", "reason"),
    [pytest.param(None, False, "nothing_pending", id="nothing pending"), pytest.param(None, True, "inactive", id="no valid state")],
)
def test_write_now_lapses_without_a_value(value: float | None, inactive: bool, reason: str) -> None:
    run = Run(written=30.0, active=True)
    run.writer.request_now(run.now)
    outcome = run.minute(value, inactive=inactive)

    assert outcome.manual_result["done"] is False and outcome.manual_result["reason"] == reason
    assert run.notes[-1].startswith("„Jetzt schreiben“ verfallen:")
    assert run.writer.manual_at is None


def test_write_now_lapses_when_writing_is_not_active() -> None:
    run = Run(written=30.0, active=False)
    run.minute(33.0)
    run.writer.request_now(run.now)
    outcome = run.minute(31.0)

    assert outcome.write is None and outcome.manual_result["reason"] == "not_active"
    assert run.notes == ["„Jetzt schreiben“ verfallen: Regeln nicht eingeschaltet (nur Lernen)."]


def test_write_now_does_not_skip_the_budget() -> None:
    run = Run(written=30.0, active=True)
    run.minute(33.0)
    run.budget = lambda request, now: "budget"
    run.writer.request_now(run.now)
    outcome = run.minute(31.0)

    assert outcome.write is None and len(run.log) == 1
    assert (outcome.manual_result["done"], outcome.manual_result["reason"]) == (False, "budget")
    assert (outcome.pending.held, outcome.pending.due_at) == ("budget", None)
    assert run.notes[-1] == "„Jetzt schreiben“ verfallen: Budget erschöpft."


# API


def _api(tmp_path: Path, library: Any, flow: Any) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
    config = app.state.store.load()
    config.devices = [DeviceConfig(id="hp", ha_device_id="x", profile=PROFILE, profile_version=2, name="HPSU")]
    app.state.store.save(config)
    app.state.runner = SimpleNamespace(heat_pump=SimpleNamespace(flow=flow), shadow=None)
    return TestClient(app)


def _pending(flow: FlowShadow) -> None:
    """A value waiting for the spacing in the write layer of hp."""
    writer = flow.writer("hp")
    for at, value in ((NOW, 33.0), (NOW + MIN, 31.0)):
        writer.step(now=at, request=Request(value_c=value, reason="normal", protect=False), written_c=30.0 if value == 33.0 else 33.0,
                    written_at=None if value == 33.0 else NOW, settings=SETTINGS, limits=LIMITS, active=flow.writing_active("hp"))


def test_write_now_in_the_shadow_is_refused(tmp_path: Path, library: Any) -> None:
    flow = FlowShadow(HeatPumpStateStore(tmp_path))
    _pending(flow)
    client = _api(tmp_path, library, flow)

    answer = client.post("/api/v1/heat_pump/hp/write_now")
    missing = client.post("/api/v1/heat_pump/nope/write_now")

    assert answer.status_code == 409 and answer.json()["detail"] == {"reason": "not_active"}
    assert missing.status_code == 404
    assert flow.writer("hp").manual_at is None


def test_write_now_accepted_with_a_pending_value(tmp_path: Path, library: Any) -> None:
    flow = FlowShadow(HeatPumpStateStore(tmp_path))
    flow.active["hp"] = True
    client = _api(tmp_path, library, flow)

    nothing = client.post("/api/v1/heat_pump/hp/write_now")
    _pending(flow)
    accepted = client.post("/api/v1/heat_pump/hp/write_now")

    assert nothing.status_code == 409 and nothing.json()["detail"] == {"reason": "nothing_pending"}
    assert accepted.status_code == 202
    assert accepted.json()["pending"]["value_c"] == 31.0
    assert flow.writer("hp").manual_at is not None


def test_no_heat_pump_is_active(tmp_path: Path) -> None:
    """Writing to a heat pump follows in step 3 part 4.7: nothing switches it on before."""
    assert FlowShadow(HeatPumpStateStore(tmp_path)).writing_active("hp") is False
