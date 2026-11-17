"""Plugging a car in, unplugging it or scanning a card replans at once."""

import pytest
from sunflow.shadow.runner import plugs_differ

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
