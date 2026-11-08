"""Tests for shadow mode recording and evaluation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sunflow.shadow.store import (
    Sample,
    ShadowStore,
    SlotRecorder,
    actual_action,
    evaluate,
    slot_start,
)

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
                start, {"action": planned, "power": None, "soc": 10400, "pv": 2000, "load": 1000, "price": 0.3}
            )
            # Six hours before the slot the plan expected 10000 Wh; a later plan does not replace it
            store.store_ahead(start, 10000, {"battery": 10000})
            store.store_ahead(start, 10400, {"battery": 10400})
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
        store.store_ahead(late, 9000, {})

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
    assert rows[0]["ahead_batteries"] == {"battery": 10000}
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


def test_old_database_gets_columns_and_schema_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A database from before the schema version is brought up to date step by step."""
    import sqlite3

    from sunflow.shadow import store as shadow

    db = sqlite3.connect(tmp_path / "shadow.db")
    db.execute("CREATE TABLE slots (start TEXT PRIMARY KEY, planned_action TEXT, actual_pv_w REAL)")
    db.execute("INSERT INTO slots (start, actual_pv_w) VALUES ('2026-10-01T10:00:00+00:00', 1200)")
    db.commit()
    db.close()
    monkeypatch.setattr(shadow, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(shadow, "SCHEMA_STEPS", {1: ["ALTER TABLE slots ADD COLUMN extra REAL"]})

    shadow.ShadowStore(tmp_path).close()

    db = sqlite3.connect(tmp_path / "shadow.db")
    columns = {row[1] for row in db.execute("PRAGMA table_info(slots)")}
    assert {"plane_forecasts", "actual_strings", "extra"} <= columns
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert db.execute("SELECT actual_pv_w FROM slots").fetchone()[0] == 1200


def test_new_database_has_current_schema_version(tmp_path: Path) -> None:
    """A fresh database starts at the current version, so no step runs on it twice."""
    import sqlite3

    from sunflow.shadow import store as shadow

    shadow.ShadowStore(tmp_path).close()
    shadow.ShadowStore(tmp_path).close()

    db = sqlite3.connect(tmp_path / "shadow.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] == shadow.SCHEMA_VERSION


def test_merge_skips_columns_of_a_newer_version(tmp_path: Path) -> None:
    """An export of a newer Sunflow can be imported; its unknown columns are left out."""
    import sqlite3

    newer = ShadowStore(tmp_path / "newer")
    newer.close()
    db = sqlite3.connect(tmp_path / "newer" / "shadow.db")
    db.execute("ALTER TABLE slots ADD COLUMN future REAL")
    db.execute("INSERT INTO slots (start, actual_pv_w, future) VALUES ('2026-10-01T10:00:00+00:00', 900, 1)")
    db.execute("PRAGMA user_version = 99")
    db.commit()
    db.close()
    store = ShadowStore(tmp_path / "own")

    assert store.merge_from(tmp_path / "newer" / "shadow.db") == 1
    assert store.rows(datetime(2026, 10, 1, tzinfo=UTC))[0]["actual_pv_w"] == 900


def test_load_forecast_is_compared_without_consumers() -> None:
    """The forecast is the base load; wallbox and heater are subtracted from the meter."""
    empty = dict.fromkeys(("planned_pv_w", "actual_pv_w", "planned_soc_wh", "actual_soc_wh"))
    row = empty | {
        "planned_action": "auto",
        "actual_action": "auto",
        "planned_load_w": 500.0,
        "actual_load_w": 11500.0,
        "actual_loadpoint_w": 11000.0,
    }
    old = row | {"start": "older", "actual_loadpoint_w": None, "actual_load_w": 600.0}

    evaluation = evaluate([row, old])

    assert evaluation["load_actual_kwh"] == pytest.approx((500 + 600) * 0.25 / 1000)
    assert evaluation["load_mae_w"] == pytest.approx(50)


def test_consumers_are_kept_per_device(tmp_path: Path) -> None:
    """The slot keeps the mean power of each controllable consumer."""
    from sunflow.shadow.store import Sample, SlotRecorder

    store = ShadowStore(tmp_path)
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    recorder = SlotRecorder(start=start)
    for wallbox in (4000.0, 4200.0):
        recorder.samples.append(Sample(soc_wh=None, battery_w=None, grid_w=None, pv_w=None, load_w=None, loadpoint_w=wallbox, consumers={"wb": wallbox}))

    store.store_actual(start, recorder.summary())

    assert store.rows(start)[0]["actual_consumers"] == {"wb": 4100.0}


@pytest.mark.parametrize(
    ("planned", "power", "actual", "battery", "same"),
    [
        pytest.param("limit_discharge", 982.0, "auto", -838.0, True, id="limit-above-demand"),
        pytest.param("limit_discharge", 982.0, "auto", -2500.0, False, id="discharging-beyond-the-limit"),
        pytest.param("limit_discharge", 982.0, "limit_discharge", -982.0, True, id="limit-bites"),
        pytest.param("hold", None, "auto", -838.0, False, id="hold-but-discharging"),
        pytest.param("limit_charge", 500.0, "auto", 500.0, True, id="limited-charge"),
    ],
)
def test_same_decision(planned: str, power: float | None, actual: str, battery: float, same: bool) -> None:
    """A discharge limit the demand never reaches looks like self-consumption and still keeps to the plan."""
    from sunflow.shadow.store import same_decision

    assert same_decision(planned, power, actual, battery) is same


def test_batteries_one_by_one(tmp_path: Path) -> None:
    """Several batteries: the plan and the measurement of each one are kept next to the sums."""
    import json

    recorder = SlotRecorder(start=START)
    recorder.samples = [
        Sample(soc_wh=11000, battery_w=-300, grid_w=0, pv_w=0, load_w=300,
               battery_socs={"house": 10000, "plug": 1000}, battery_powers={"house": 0, "plug": -300}),
        Sample(soc_wh=10900, battery_w=-500, grid_w=0, pv_w=0, load_w=500,
               battery_socs={"house": 10000, "plug": 900}, battery_powers={"house": -200, "plug": -300}),
    ]
    store = ShadowStore(tmp_path)
    try:
        store.store_planned(START, {
            "action": "hold", "power": None, "soc": 10900, "pv": 0, "load": 400, "price": 0.3,
            "batteries": json.dumps({"house": {"soc_wh": 10000, "action": "hold"}, "plug": {"soc_wh": 900, "action": "auto"}}),
        })
        store.store_actual(START, recorder.summary())
        row = store.rows(START)[0]
    finally:
        store.close()

    assert row["planned_batteries"]["plug"] == {"soc_wh": 900, "action": "auto"}
    assert row["actual_batteries"] == {
        "house": {"soc_wh": 10000, "battery_w": -100},
        "plug": {"soc_wh": 900, "battery_w": -300},
    }
