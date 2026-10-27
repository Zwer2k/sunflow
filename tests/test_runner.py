"""Tests for the background runner."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sunflow import runner as runner_module
from sunflow.ha import HACommandError
from sunflow.runner import ShadowRunner
from sunflow.shadow import Sample, ShadowStore


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
