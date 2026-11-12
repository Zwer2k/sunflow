"""One-time loads from the integration: what cannot be placed is refused, not kept."""

from pydantic import ValidationError
import pytest
from sunflow.api.integration import NewLoad


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"earliest": "2026-11-12T10:00:00"}, id="time without an offset"),
        pytest.param({"power_w": 1e-9}, id="a run of weeks"),
    ],
)
def test_load_that_cannot_be_placed_is_refused(changes: dict[str, object]) -> None:
    """Such a load raised on every later state request and took the integration's entities down."""
    with pytest.raises(ValidationError):
        NewLoad(**({"name": "Washer", "energy_wh": 1000, "power_w": 2000} | changes))


def test_load_with_an_offset_is_accepted() -> None:
    """The usual case from Home Assistant."""
    load = NewLoad(name="Washer", energy_wh=1000, power_w=2000, earliest="2026-11-12T10:00:00+01:00")
    assert load.earliest is not None and load.earliest.utcoffset() is not None
