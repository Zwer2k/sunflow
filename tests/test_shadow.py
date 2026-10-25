"""Tests for shadow mode recording and evaluation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sunflow.shadow import Sample, ShadowStore, SlotRecorder, actual_action, evaluate, slot_start

START = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)


def test_slot_start() -> None:
    """Moments map to the start of their 15 minute slot."""
    assert slot_start(datetime(2026, 10, 25, 10, 29, 59, tzinfo=UTC)) == datetime(2026, 10, 25, 10, 15, tzinfo=UTC)


def test_recorder_summary() -> None:
    """Powers are averaged, the SoC is the last reading, the action is classified."""
    recorder = SlotRecorder(start=START)
    recorder.samples += [
        Sample(soc_wh=10000, battery_w=1000, grid_w=0, pv_w=3000, load_w=2000),
        Sample(soc_wh=10250, battery_w=1200, grid_w=None, pv_w=3400, load_w=2200),
    ]

    summary = recorder.summary()

    assert summary["actual_soc_wh"] == 10250
    assert summary["actual_battery_w"] == pytest.approx(1100)
    assert summary["actual_grid_w"] == 0
    assert summary["samples"] == 2
    assert summary["actual_action"] == "auto"


@pytest.mark.parametrize(
    ("grid", "battery", "expected"),
    [
        pytest.param(0, -500, "auto", id="covers_deficit"),
        pytest.param(10, -364, "auto", id="coarse_pv_reading_is_ignored"),
        pytest.param(500, 0, "hold", id="idle_while_importing"),
        pytest.param(2500, 2000, "force_charge", id="grid_charging"),
        pytest.param(-800, -1000, "force_discharge", id="battery_exporting"),
        pytest.param(-2000, 1500, "auto", id="pv_surplus_charges_and_exports"),
        pytest.param(800, -1200, "limit_discharge", id="battery_covers_part_of_deficit"),
        pytest.param(None, 0, None, id="missing"),
    ],
)
def test_actual_action(grid: float | None, battery: float, expected: str | None) -> None:
    """Measured behaviour is classified from the grid meter and the battery."""
    assert actual_action(grid, battery) == expected


def test_store_and_evaluate(tmp_path: Path) -> None:
    """Planned and actual values of a slot are merged and compared."""
    store = ShadowStore(tmp_path)
    try:
        for i, (planned, actual) in enumerate((("auto", "auto"), ("hold", "auto"), ("auto", None))):
            start = START + i * timedelta(minutes=15)
            store.store_planned(
                start, {"action": planned, "power": None, "soc": 10000, "pv": 2000, "load": 1000, "price": 0.3}
            )
            if actual:
                store.store_actual(
                    start,
                    {"actual_soc_wh": 10400, "actual_battery_w": 0, "actual_grid_w": 0, "actual_pv_w": 1800,
                     "actual_load_w": 1100, "actual_action": actual, "samples": 15},
                )
        # Actual first, plan later must merge as well
        late = START + timedelta(hours=1)
        store.store_actual(late, {"actual_soc_wh": 9000, "actual_action": "auto", "samples": 3})
        store.store_planned(late, {"action": "auto", "power": None, "soc": 9000, "pv": 0, "load": 500, "price": 0.3})

        rows = store.rows(START)
        evaluation = evaluate(rows)
    finally:
        store.close()

    assert len(rows) == 4
    assert rows[-1]["planned_action"] == "auto" and rows[-1]["actual_soc_wh"] == 9000
    assert evaluation["compared"] == 3
    assert evaluation["differing_slots"] == 1
    assert evaluation["agreement"] == pytest.approx(2 / 3)
    assert evaluation["soc_mae_wh"] == pytest.approx(800 / 3)
    assert evaluation["pv_forecast_kwh"] == pytest.approx(2 * 2000 * 0.25 / 1000)


def test_backup_and_merge(tmp_path: Path) -> None:
    """A backup of one store merges into another; equal slots are replaced."""
    prod = ShadowStore(tmp_path / "prod")
    dev = ShadowStore(tmp_path / "dev")
    try:
        planned = {"action": "auto", "power": None, "soc": 1, "pv": 0, "load": 0, "price": 0.3}
        prod.store_planned(START, planned)
        prod.store_planned(START + timedelta(minutes=15), planned | {"action": "hold"})
        dev.store_planned(START, planned | {"action": "force_charge"})
        prod.backup_to(tmp_path / "copy.db")

        assert dev.merge_from(tmp_path / "copy.db") == 2
        rows = dev.rows(START)
    finally:
        prod.close()
        dev.close()

    assert [r["planned_action"] for r in rows] == ["auto", "hold"]
