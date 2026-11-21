"""Plugging a car in, unplugging it or scanning a card replans at once."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sunflow.config.models import DeviceOverride, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.shadow.runner import ShadowRunner, plugs_differ, unplugged

BEFORE = {"wb": ("off", "none")}


@pytest.mark.parametrize(
    ("current", "changed"),
    [
        pytest.param({"wb": ("on", "none")}, True, id="plugged in"),
        pytest.param({"wb": ("off", "card_0")}, True, id="card scanned"),
        pytest.param({"wb": ("off", "none")}, False, id="nothing"),
        pytest.param({"wb": ("unavailable", "none")}, False, id="charger offline"),
        pytest.param({"wb": ("off", "none"), "new": ("on", None)}, False, id="new wallbox"),
    ],
)
def test_plugs_differ(current: dict[str, tuple[str | None, str | None]], changed: bool) -> None:
    """Only a real change of a known wallbox counts; an offline charger or a new one does not."""
    assert plugs_differ(BEFORE, current) is changed


@pytest.mark.parametrize(
    ("previous", "current", "ended"),
    [
        pytest.param({"wb": ("on", None)}, {"wb": ("off", None)}, {"wb"}, id="unplugged"),
        pytest.param({"wb": ("off", None)}, {"wb": ("off", None)}, set(), id="never plugged"),
        pytest.param({"wb": ("on", None)}, {"wb": ("unavailable", None)}, set(), id="charger offline"),
        pytest.param({}, {"wb": ("off", None)}, set(), id="new wallbox"),
    ],
)
def test_unplugged(
    previous: dict[str, tuple[str | None, str | None]], current: dict[str, tuple[str | None, str | None]], ended: set[str]
) -> None:
    """Only a car that was plugged in and is unplugged now ends its charging session."""
    assert unplugged(previous, current) == ended


def test_unplugging_ends_session_changes(tmp_path: Path) -> None:
    """A change until the end of charging goes with the car; a timed one at another wallbox stays."""
    store = ConfigStore(tmp_path)
    config = SunflowConfig()
    config.overrides = {
        "wb": DeviceOverride(strategy="fast", until_done=True),
        "wb2": DeviceOverride(strategy="fast"),
    }
    store.save(config)

    ShadowRunner._end_sessions(SimpleNamespace(_store=store), {"wb", "wb2"})

    assert set(store.load().overrides) == {"wb2"}
