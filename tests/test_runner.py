"""Tests for the background runner."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sunflow.ha import HACommandError
from sunflow.shadow import runner as runner_module
from sunflow.shadow.runner import ShadowRunner
from sunflow.shadow.store import Sample, ShadowStore


async def test_sampling_goes_on_while_planning_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing Home Assistant query stops the plan, not the measurement; it is retried later."""
    attempts = []

    async def failing_replan(self: ShadowRunner, start: Any) -> None:
        attempts.append(start)
        raise HACommandError("unknown_error: Unknown error")

    async def sample(*args: Any) -> Sample:
        return Sample(soc_wh=5000, battery_w=0, grid_w=300, pv_w=0, load_w=300)

    monkeypatch.setattr(ShadowRunner, "_replan", failing_replan)
    monkeypatch.setattr(runner_module, "take_sample", sample)
    shadow = ShadowStore(tmp_path)
    runner = ShadowRunner(SimpleNamespace(connected=True), None, None, shadow)
    try:
        await runner._tick()
        await runner._tick()
    finally:
        shadow.close()

    assert len(runner._recorder.samples) == 2
    assert runner.last_sample is not None
    # The second tick within the retry pause does not query everything again
    assert len(attempts) == 1
    assert runner.status.last_error == "Plan: unknown_error: Unknown error"
    assert runner._replan_retry_at - runner_module.datetime.now(runner_module.UTC) <= runner_module.REPLAN_RETRY


async def test_replan_pause_grows_up_to_the_maximum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each further failure doubles the pause: 5, 10, 20, then 30 minutes."""
    from datetime import UTC, datetime, timedelta

    async def failing_replan(self: ShadowRunner, start: Any) -> None:
        raise HACommandError("recorder_unavailable: paused")

    async def no_sample(*args: Any) -> None:
        return None

    monkeypatch.setattr(ShadowRunner, "_replan", failing_replan)
    monkeypatch.setattr(runner_module, "take_sample", no_sample)
    shadow = ShadowStore(tmp_path)
    runner = ShadowRunner(SimpleNamespace(connected=True), None, None, shadow)
    pauses = []
    try:
        for _ in range(5):
            runner._replan_retry_at = datetime.min.replace(tzinfo=UTC)
            await runner._tick()
            pauses.append(round((runner._replan_retry_at - datetime.now(UTC)) / timedelta(minutes=1)))
    finally:
        shadow.close()

    assert pauses == [5, 10, 20, 30, 30]


def test_nowcast_carries_the_live_deviation_ahead() -> None:
    """Twice the forecast PV now: the next slots get more, fading out over the hours."""
    from datetime import UTC, datetime, timedelta

    from sunflow.planning.optimizer import SlotInput
    from sunflow.planning.plan import nowcast

    start = datetime(2026, 10, 30, 11, tzinfo=UTC)
    inputs = [SlotInput(start=start + timedelta(minutes=15 * t), pv_w=2000, load_w=500, price=0.3, feed_in=0.08) for t in range(16)]

    result = nowcast(inputs, 4000)

    assert result[0].pv_w == pytest.approx(2000 * 4050 / 2050)
    # One hour ahead half of the deviation is left
    assert result[4].pv_w == pytest.approx(2000 * (1 + (4050 / 2050 - 1) / 2))
    assert result[0].pv_w > result[4].pv_w > result[15].pv_w > 2000
    # At night nothing changes
    assert nowcast([SlotInput(start=start, pv_w=0, load_w=500, price=0.3, feed_in=0.08)], 20)[0].pv_w == 0


def _runner_with_plan(tmp_path: Path, pv: float, load: float = 500, soc: float = 10000) -> ShadowRunner:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    slot = SimpleNamespace(start=now - timedelta(minutes=5), end=now + timedelta(minutes=10), pv_w=pv, load_w=load, soc_wh=soc, flex_w={})
    runner = ShadowRunner(SimpleNamespace(connected=True), None, None, ShadowStore(tmp_path))
    runner.latest = SimpleNamespace(plan=SimpleNamespace(slots=[slot]), battery=SimpleNamespace(capacity_wh=20000))
    runner.status.last_plan = now - timedelta(minutes=10)
    return runner


@pytest.mark.parametrize(
    ("pv", "expected"),
    [
        pytest.param([3500] * 3, "PV +2500 W gegenüber Plan", id="more-pv-lasting"),
        pytest.param([3500, 3500, 1000, 3500, 3500], None, id="passing-cloud"),
        # Only the last three minutes count: the sun came out after a cloud
        pytest.param([1000, 1000, 3500, 3500, 3500], "PV +2500 W gegenüber Plan", id="sun-after-cloud"),
        pytest.param([3500] * 2, None, id="too-short"),
        pytest.param([1500] * 5, None, id="small-deviation"),
    ],
)
def test_early_replan_on_lasting_deviation(tmp_path: Path, pv: list[float], expected: str | None) -> None:
    """Only a deviation lasting all of the last three minutes replans before the next slot."""
    runner = _runner_with_plan(tmp_path, pv=1000)
    for value in pv:
        runner._recent.append(Sample(soc_wh=10000, battery_w=0, grid_w=0, pv_w=value, load_w=500))

    assert runner._deviation() == expected
    runner.shadow.close()


def test_risk_fades_where_measured_and_under_clear_sky() -> None:
    """The pessimistic share applies to cloudy hours ahead, not to now or a clear sky."""
    from sunflow.planning.plan import risk_shares

    shares = risk_shares(0.5, [80, 80, 5, None], [1.0, 0.0, 0.0, 0.5])

    assert shares == pytest.approx([0.0, 0.5, 0.125, 0.25])


def test_recent_action_goes_by_the_mean_of_the_last_minutes() -> None:
    """One reading with grid import while charging is no grid charging when the minutes around it are fine."""
    from sunflow.shadow.runner import ShadowRunner
    from sunflow.shadow.store import Sample

    runner = ShadowRunner.__new__(ShadowRunner)
    from collections import deque

    runner._recent = deque(
        [Sample(soc_wh=None, battery_w=560, grid_w=g, pv_w=None, load_w=None) for g in (-150, -120, -200, -100, 560)]
    )
    assert runner.recent_action() == "auto"
    runner._recent = deque([Sample(soc_wh=None, battery_w=560, grid_w=560, pv_w=None, load_w=None)] * 5)
    assert runner.recent_action() == "force_charge"
    # Just after a start one reading is no judgement yet
    runner._recent = deque([Sample(soc_wh=None, battery_w=560, grid_w=560, pv_w=None, load_w=None)] * 2)
    assert runner.recent_action() is None
