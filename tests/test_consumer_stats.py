"""Sessions of the consumers from the recording, their energy split and the evcc import."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sunflow.analysis import evcc_import
from sunflow.analysis.consumer_stats import (
    Session,
    imported,
    merge,
    sessions_from_rows,
    split_slot,
    statistics,
)

BERLIN = ZoneInfo("Europe/Berlin")
START = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)
NAMES = {"wb": "Wallbox", "ww": "Warmwasser"}


def _row(i: int, wb: float = 0.0, *, pv: float = 0.0, load: float | None = None, battery: float = 0.0, car: dict | None = None) -> dict:
    return {
        "start": (START + i * SLOT).isoformat(),
        "actual_consumers": {"wb": wb},
        "actual_load_w": load if load is not None else 400.0 + wb,
        "actual_pv_w": pv,
        "actual_battery_w": battery,
        "planned_price": 0.30,
        "actual_vehicles": {"wb": car} if car else {},
        "actual_levels": {},
    }


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(_row(0, 4000, pv=0), (0.0, 0.0, 1000.0), id="all from the grid"),
        # The house takes 400 W of the PV first, the rest goes to the car
        pytest.param(_row(0, 4000, pv=2400), (500.0, 0.0, 500.0), id="pv surplus first"),
        # The battery covers the house (400 W) and 1 kW of the car
        pytest.param(_row(0, 4000, battery=-1400), (0.0, 250.0, 750.0), id="battery after the house"),
    ],
)
def test_split_slot(row: dict, expected: tuple[float, float, float]) -> None:
    """Energy (Wh) of a slot from PV, battery and grid."""
    assert split_slot(row)["wb"] == pytest.approx(expected)


def test_sessions_by_pauses_and_by_plug() -> None:
    """Without plug data a pause over an hour ends a session; a known plug-in lasts until unplugging, pauses included."""
    rows = [
        _row(0, 4000), _row(1, 4000),  # session 1
        *[_row(i) for i in range(2, 8)],  # 1.5 h pause
        _row(8, 2000, pv=2400),  # session 2: half of it from PV
        *[_row(i, 0, car={"vehicle": "fiat", "card": 3}) for i in range(9, 12)],
        _row(12, 4000, car={"vehicle": "fiat", "card": 3}),
        *[_row(i) for i in range(13, 15)],  # unplugged
        _row(15, 4000, car={"vehicle": "tesla", "card": None}),
    ]
    found = sessions_from_rows(rows, NAMES, {"fiat": "Fiat", "tesla": "Tesla"}, {("wb", 3): "Urlaub"}, feed_in=0.08)

    assert [(s.start - START, s.vehicle, s.card, round(s.energy_wh)) for s in found] == [
        (timedelta(0), None, None, 2000),
        (8 * SLOT, None, None, 500),
        (9 * SLOT, "Fiat", "Urlaub", 1000),
        (15 * SLOT, "Tesla", None, 1000),
    ]
    # Grid part at the import price, PV at the feed-in not earned
    assert (found[1].paid_eur, found[1].cost_eur) == pytest.approx((0.0, 0.04))
    assert found[2].end - found[2].start == 4 * SLOT


def test_imported_only_before_own_recording() -> None:
    """evcc's sessions fill the time before Sunflow recorded; the same consumer is not counted twice."""
    own = sessions_from_rows([_row(0, 4000), _row(1, 4000)], NAMES, {}, {}, 0.08)
    rows = [
        {"source": "evcc", "id": str(i), "start": (START + timedelta(days=d)).isoformat(), "end": (START + timedelta(days=d, hours=2)).isoformat(),
         "device_id": "wb", "loadpoint": "Carport", "vehicle_id": None, "vehicle": "Fiat", "card": None, "energy_wh": 10000.0,
         "solar_share": 0.6, "cost_eur": 1.2, "co2_g_per_kwh": None}
        for i, d in enumerate((-30, 5))
    ]
    merged = merge(own, imported(rows, NAMES))

    assert [(s.source, s.start.date().isoformat()) for s in merged] == [("evcc", "2026-10-04"), ("sunflow", "2026-11-03")]
    totals = statistics(merged, None, BERLIN)["totals"]
    # 6 of 10 kWh own (imported share) and the 2 kWh from the grid
    assert totals["solar_share"] == pytest.approx(6000 / 12000)
    assert totals["sessions"] == 2


def test_statistics_per_month_and_filters() -> None:
    """Totals per month in local time; filtered by consumer and vehicle."""
    sessions = [
        Session(source="sunflow", device_id="wb", consumer="Wallbox", start=datetime(2026, 10, 31, 23, 30, tzinfo=UTC),
                end=datetime(2026, 11, 1, 1, 0, tzinfo=UTC), energy_wh=5000, grid_wh=5000, paid_eur=1.5, cost_eur=1.5,
                grid_only_eur=1.5, vehicle_id="fiat", vehicle="Fiat"),
        Session(source="sunflow", device_id="ww", consumer="Warmwasser", start=datetime(2026, 11, 2, 12, tzinfo=UTC),
                end=datetime(2026, 11, 2, 14, tzinfo=UTC), energy_wh=3000, pv_wh=3000, cost_eur=0.24, grid_only_eur=0.9),
    ]
    result = statistics(sessions, None, BERLIN)

    # 23:30 UTC on Oct 31 is already November in Berlin
    assert [(m["month"], m["sessions"]) for m in result["months"]] == [("2026-11", 2)]
    assert result["totals"]["saved_eur"] == pytest.approx(0.9)
    assert [s["consumer"] for s in statistics(sessions, None, BERLIN, vehicle_id="fiat")["sessions"]] == ["Wallbox"]


EVCC_JSON = """{"result": [
 {"id": 7, "created": "2025-06-01T10:00:00+02:00", "finished": "2025-06-01T13:30:00+02:00", "loadpoint": "Carport",
  "identifier": "a1b2", "vehicle": "Fiat 500e", "chargedEnergy": 12.5, "chargeDuration": 10800000000000,
  "solarPercentage": 80.5, "price": 0.98, "pricePerKWh": 0.078, "co2PerKWh": 120}
]}"""

EVCC_CSV_DE = """Erstellt;Beendet;Ladepunkt;Kennung;Fahrzeug;Kilometerstand (km);Zählerstand Start (kWh);Zählerstand Ende (kWh);Energie (kWh);Ladedauer;Sonne (%);Preis;Preis/kWh;CO₂/kWh (gCO₂eq)
01.06.2025 10:00:00;01.06.2025 13:30:00;Carport;;Fiat 500e;12345;100,0;112,5;12,5;3h0m0s;80,5;0,98;0,078;120
"""


# The export of a newer evcc: start and end time, the vehicle's SoC, a byte order mark
EVCC_CSV_NEW = """\ufeffStartzeit;Endzeit;Ladepunkt;Kennung;Fahrzeug;Kilometerstand (km);Anfangszählerstand (kWh);Endzählerstand (kWh);Energie (kWh);Ladedauer;Ladestand Start (%);Ladestand Ende (%);Sonne (%);Kosten;Preis/kWh;CO₂/kWh (gCO₂eq);Reference Price/kWh;Reference CO2/kWh (gCO2eq)
2025-06-01 10:00:00;2025-06-01 13:30:00;Carport;Natascha;Fiat 500e;;100,0;112,5;12,5;3h0m0s;35;80;80,5;0,98;0,078;;0,3056;
"""


@pytest.mark.parametrize(
    ("content", "duration"),
    [
        pytest.param(EVCC_JSON, 10800.0, id="api json, duration in nanoseconds"),
        pytest.param(EVCC_CSV_DE, 10800.0, id="german csv export"),
        pytest.param(EVCC_CSV_NEW, 10800.0, id="newer german csv export"),
    ],
)
def test_parse_evcc(content: str, duration: float) -> None:
    """The same session from evcc's API and from its German CSV export."""
    (session,) = evcc_import.parse(content, BERLIN)

    assert session["start"] == datetime(2025, 6, 1, 8, 0, tzinfo=UTC)
    assert (session["loadpoint"], session["vehicle"], session["energy_wh"]) == ("Carport", "Fiat 500e", 12500.0)
    assert (session["solar_share"], session["cost_eur"], session["duration_s"]) == (pytest.approx(0.805), 0.98, duration)


def test_newer_export_brings_the_state_of_charge() -> None:
    """The newer export has the vehicle's SoC before and after; it is kept with the session."""
    (session,) = evcc_import.parse(EVCC_CSV_NEW, BERLIN)
    assert (session["soc_start"], session["soc_end"], session["identifier"]) == (35.0, 80.0, "Natascha")


def test_parse_evcc_refuses_other_data() -> None:
    """Something that has no sessions is an error, not an empty import."""
    with pytest.raises(evcc_import.EvccImportError):
        evcc_import.parse('{"result": {"loadpoints": []}}', BERLIN)


def test_preview_matches_by_name() -> None:
    """evcc's names are matched to Sunflow's ignoring case and an "(evcc)" suffix; unclear ones stay open."""
    sessions = evcc_import.parse(EVCC_JSON, BERLIN)
    result = evcc_import.preview(sessions, {"wb": "Wallbox Carport (evcc)", "ww": "Warmwasser"}, {"fiat": "fiat 500E"})

    assert result["loadpoints"] == [{"name": "Carport", "device_id": "wb"}]
    assert result["vehicles"] == [{"name": "Fiat 500e", "vehicle_id": "fiat"}]
    assert (result["count"], result["energy_wh"]) == (1, 12500)


def test_imported_sessions_are_stored_once(tmp_path: Path) -> None:
    """Taking over the same evcc data twice keeps each session once; removing forgets them."""
    from sunflow.shadow.store import ShadowStore

    store = ShadowStore(tmp_path)
    sessions = evcc_import.parse(EVCC_JSON, BERLIN)
    rows = evcc_import.to_store(sessions, {"Carport": "wb"}, {"Fiat 500e": "fiat"}, {"fiat": "Fiat"})

    assert store.store_imported_sessions(rows) == 1
    assert store.store_imported_sessions(rows) == 0
    (stored,) = imported(store.imported_sessions(), NAMES)
    assert (stored.consumer, stored.vehicle, stored.charging_h, stored.paid_known) == ("Wallbox", "Fiat", 3.0, False)
    assert store.remove_imported_sessions("evcc") == 1
    store.close()
