"""Tests for logging lasting conditions once and reading the App's state files."""

import logging
from pathlib import Path

import pytest
from sunflow.devices.executor import EepromGuard
from sunflow.log_state import Conditions


def test_a_condition_is_logged_when_it_comes_and_goes(caplog: pytest.LogCaptureFixture) -> None:
    """Every plan retries; the log shows the failure once and once when it works again."""
    conditions = Conditions(logging.getLogger("test.conditions"))

    with caplog.at_level("INFO", logger="test.conditions"):
        conditions.failed("weather", "Weather of %s unavailable", "weather.home")
        conditions.failed("weather", "Weather of %s unavailable", "weather.home")
        conditions.ok("weather")
        conditions.ok("weather")

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "Weather of weather.home unavailable"),
        ("INFO", "Works again: Weather of weather.home unavailable"),
    ]


@pytest.mark.parametrize(
    ("files", "warnings"),
    [
        # First start: no file, nothing to tell
        pytest.param({}, 0, id="missing"),
        # Cut off by a crash: today's EEPROM writes start from zero, which the log must say
        pytest.param({"eeprom_writes.json": '{"day": "2026-11'}, 1, id="cut-off"),
    ],
)
def test_state_file_unreadable_is_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, files: dict[str, str], warnings: int
) -> None:
    """A missing state file is normal, an unreadable one is logged once however often it is read."""
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    guard = EepromGuard(tmp_path, 30)

    with caplog.at_level("WARNING", logger="sunflow.state"):
        allowed = [guard.allowed("number.x") for _ in range(3)]

    assert allowed == [True] * 3
    assert len(caplog.records) == warnings
