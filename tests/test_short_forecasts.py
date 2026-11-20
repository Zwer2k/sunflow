"""PV forecasts that end before the prices: one warning per end time."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sunflow.planning.plan import short_forecasts

END = datetime(2026, 11, 20, 23, tzinfo=UTC)
PRICES_END = END + timedelta(days=1)


def test_planes_ending_alike_share_one_warning() -> None:
    """Seven free forecast planes ending together give one warning naming them; a long one none."""
    planes = [SimpleNamespace(display_name=f"Dach {i}") for i in range(7)] + [SimpleNamespace(display_name="Carport")]
    ends = [END] * 7 + [PRICES_END]

    warnings = short_forecasts(planes, ends, PRICES_END, UTC)

    assert warnings == [
        "PV-Prognosen von 7 Flächen (Dach 0, Dach 1, Dach 2, Dach 3, Dach 4, Dach 5, Dach 6) reichen nur bis Fr 23:00 – "
        "danach fehlt ihr Ertrag im Plan (die PV-Prognose ist dort eher zu niedrig)."
    ]


def test_one_short_plane() -> None:
    """A single short plane keeps its own wording."""
    warnings = short_forecasts([SimpleNamespace(display_name="Süd")], [END], PRICES_END, UTC)

    assert warnings == ["PV-Prognose „Süd“ reicht nur bis Fr 23:00 – danach fehlt ihr Ertrag im Plan (die PV-Prognose ist dort eher zu niedrig)."]
