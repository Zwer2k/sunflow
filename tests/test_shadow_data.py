"""Keeping the shadow database: the copy before a migration and importing all tables of an export."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest
from sunflow.shadow import store as shadow
from sunflow.shadow.store import IMPORT_TABLES, SCHEMA_VERSION, ShadowBackupError, ShadowStore

START = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
PLANNED = {"action": "auto", "power": None, "soc": 1, "pv": 0, "load": 0, "price": 0.3}
SESSION = {"source": "evcc", "id": "1", "start": "2026-10-25T08:00:00+00:00", "energy_wh": 5000.0}


def _sql(path: Path, *statements: str) -> None:
    db = sqlite3.connect(path)
    try:
        for statement in statements:
            db.execute(statement)
        db.commit()
    finally:
        db.close()


def _query(path: Path, sql: str) -> list[tuple]:
    db = sqlite3.connect(path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def _version(path: Path) -> int:
    return _query(path, "PRAGMA user_version")[0][0]


def _filled(directory: Path, action: str = "auto") -> Path:
    """A database of the current version with a row in every table."""
    store = ShadowStore(directory)
    try:
        store.store_planned(START, PLANNED | {"action": action})
        store.store_ahead(START, 900.0, {"house": 900.0}, pv_w=1500.0, load_w=400.0)
        store.store_soiling_ahead(date(2026, 10, 25), "south", 0.9, 0.95)
        store.store_imported_sessions([SESSION])
    finally:
        store.close()
    return directory / "shadow.db"


# Backup before the migration


def test_backup_before_migration(tmp_path: Path) -> None:
    """Migrating keeps a copy of the old version next to the database; nothing else is left behind."""
    path = _filled(tmp_path)
    _sql(path, f"PRAGMA user_version = {SCHEMA_VERSION - 1}")

    ShadowStore(tmp_path).close()

    kept = tmp_path / f"shadow.v{SCHEMA_VERSION - 1}.db"
    assert _version(kept) == SCHEMA_VERSION - 1
    assert _query(kept, "SELECT planned_action FROM slots") == [("auto",)]
    assert _version(path) == SCHEMA_VERSION
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shadow.db", kept.name]


def test_existing_backup_is_not_overwritten(tmp_path: Path) -> None:
    """A copy of the same version stays as it was made first."""
    path = _filled(tmp_path)
    _sql(path, f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    ShadowStore(tmp_path).close()
    _sql(path, f"PRAGMA user_version = {SCHEMA_VERSION - 1}", "UPDATE slots SET planned_action = 'hold'")

    ShadowStore(tmp_path).close()

    assert _query(tmp_path / f"shadow.v{SCHEMA_VERSION - 1}.db", "SELECT planned_action FROM slots") == [("auto",)]


def test_no_backup_without_migration(tmp_path: Path) -> None:
    """A fresh database and one already current are not copied, however often Sunflow starts."""
    ShadowStore(tmp_path).close()
    _filled(tmp_path)
    ShadowStore(tmp_path).close()

    assert [p.name for p in tmp_path.iterdir()] == ["shadow.db"]


def test_newer_database_is_not_copied(tmp_path: Path) -> None:
    """A database of a newer Sunflow is neither migrated nor copied."""
    path = _filled(tmp_path)
    _sql(path, "PRAGMA user_version = 99")

    ShadowStore(tmp_path).close()

    assert [p.name for p in tmp_path.iterdir()] == ["shadow.db"]
    assert _version(path) == 99


def test_failed_backup_stops_the_migration(tmp_path: Path) -> None:
    """Without a copy (here: its path is taken by a directory) the database stays at its version."""
    path = _filled(tmp_path)
    _sql(path, f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    (tmp_path / f"shadow.v{SCHEMA_VERSION - 1}.db.tmp").mkdir()

    with pytest.raises(ShadowBackupError):
        ShadowStore(tmp_path)

    assert _version(path) == SCHEMA_VERSION - 1
    assert not (tmp_path / f"shadow.v{SCHEMA_VERSION - 1}.db").exists()


def test_old_database_without_version_is_copied(tmp_path: Path) -> None:
    """A database from before the schema version (user_version 0) is copied as v0."""
    _sql(tmp_path / "shadow.db", "CREATE TABLE slots (start TEXT PRIMARY KEY, planned_action TEXT)")

    ShadowStore(tmp_path).close()

    assert _query(tmp_path / "shadow.v0.db", "SELECT name FROM pragma_table_info('slots')") == [("start",), ("planned_action",)]


# Import of all tables


def test_import_list_covers_all_tables(tmp_path: Path) -> None:
    """Every table decides whether an import takes it over: a new one must be added to IMPORT_TABLES."""
    ShadowStore(tmp_path).close()

    tables = {row[0] for row in _query(tmp_path / "shadow.db", "SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert tables == set(IMPORT_TABLES)


@pytest.fixture
def own(tmp_path: Path) -> Iterator[ShadowStore]:
    """The database an export is imported into: other plan at START, rows the export lacks."""
    store = ShadowStore(tmp_path / "own")
    store.store_planned(START, PLANNED | {"action": "force_charge"})
    store.store_ahead(START - timedelta(hours=1), 500.0, {})
    store.store_soiling_ahead(date(2026, 10, 1), "west", 0.8, 0.8)
    store.store_imported_sessions([SESSION | {"id": "own"}])
    yield store
    store.close()


def _rows(store: ShadowStore) -> dict[str, list[tuple]]:
    queries = {
        "slots": "SELECT start, planned_action FROM slots ORDER BY start",
        "plan_ahead": "SELECT start, soc_wh FROM plan_ahead ORDER BY start",
        "soiling_ahead": "SELECT day, plane FROM soiling_ahead ORDER BY day",
        "imported_sessions": "SELECT id FROM imported_sessions ORDER BY id",
    }
    return {table: store._db.execute(sql).fetchall() for table, sql in queries.items()}


def test_import_of_an_older_export(tmp_path: Path, own: ShadowStore) -> None:
    """An export of version 6 is migrated and merged table by table; equal keys are replaced."""
    path = _filled(tmp_path / "old")
    # Back to version 6: before the expectations of PV and load, vehicles and evcc sessions
    _sql(
        path,
        "DROP TABLE imported_sessions",
        "ALTER TABLE plan_ahead DROP COLUMN pv_w",
        "ALTER TABLE plan_ahead DROP COLUMN load_w",
        "ALTER TABLE slots DROP COLUMN actual_vehicles",
        "PRAGMA user_version = 6",
    )

    counts = own.merge_from(path)

    assert counts == {"imported_sessions": 0, "plan_ahead": 1, "slots": 1, "soiling_ahead": 1}
    start, before = START.isoformat(), (START - timedelta(hours=1)).isoformat()
    assert _rows(own) == {
        "slots": [(start, "auto")],
        "plan_ahead": [(before, 500.0), (start, 900.0)],
        "soiling_ahead": [("2026-10-01", "west"), ("2026-10-25", "south")],
        "imported_sessions": [("own",)],
    }


def test_import_of_a_newer_export(tmp_path: Path, own: ShadowStore) -> None:
    """Tables and columns of a newer version are left out; a table the export lacks stays as it is."""
    path = _filled(tmp_path / "newer")
    _sql(
        path,
        "CREATE TABLE heat_pump_future (device_id TEXT, start TEXT, tv_c REAL, PRIMARY KEY (device_id, start))",
        "INSERT INTO heat_pump_future VALUES ('hp', '2026-10-25T10:00:00+00:00', 30.5)",
        "ALTER TABLE plan_ahead ADD COLUMN future_c REAL",
        "UPDATE plan_ahead SET future_c = 1",
        "DROP TABLE soiling_ahead",
        "PRAGMA user_version = 99",
    )

    counts = own.merge_from(path)

    assert counts == {"imported_sessions": 1, "plan_ahead": 1, "slots": 1}
    rows = _rows(own)
    assert rows["soiling_ahead"] == [("2026-10-01", "west")]
    assert rows["imported_sessions"] == [("1",), ("own",)]
    assert rows["slots"] == [(START.isoformat(), "auto")]
    tables = {row[0] for row in own._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "heat_pump_future" not in tables
    assert _version(path) == 99


def test_failed_import_changes_nothing(tmp_path: Path, own: ShadowStore) -> None:
    """An error in the last table leaves the tables merged before it as they were."""
    path = _filled(tmp_path / "export")
    before = _rows(own)
    # soiling_ahead comes last: slots, plan_ahead and imported_sessions are written already
    own._db.execute("CREATE TRIGGER fail BEFORE INSERT ON soiling_ahead BEGIN SELECT RAISE(ABORT, 'disk gone'); END")
    own._db.commit()

    with pytest.raises(sqlite3.Error):
        own.merge_from(path)

    assert _rows(own) == before
    # The export is detached again: a second try works
    own._db.execute("DROP TRIGGER fail")
    own._db.commit()
    assert own.merge_from(path)["soiling_ahead"] == 1


def test_excluded_table_is_not_imported(tmp_path: Path, own: ShadowStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """A table marked as not imported keeps its own rows."""
    monkeypatch.setitem(shadow.IMPORT_TABLES, "soiling_ahead", False)

    counts = own.merge_from(_filled(tmp_path / "export"))

    assert "soiling_ahead" not in counts
    assert _rows(own)["soiling_ahead"] == [("2026-10-01", "west")]
