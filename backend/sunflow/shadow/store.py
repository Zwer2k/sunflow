"""Shadow mode: record what Sunflow would do next to what actually happened.

Every minute the live values are sampled; at each 15 minute boundary the
finished slot is stored with its averages and the plan is recomputed. The
first slot of a fresh plan is what Sunflow would command right now.
"""

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from statistics import fmean
import threading
from typing import Any

from sunflow.planning.optimizer import Settings

_LOGGER = logging.getLogger(__name__)

SLOT = timedelta(minutes=15)

# Schema version 1; later changes go into SCHEMA_STEPS only, so new and old databases end up alike
SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    start TEXT PRIMARY KEY,
    planned_action TEXT,
    planned_power_w REAL,
    planned_soc_wh REAL,
    planned_pv_w REAL,
    planned_load_w REAL,
    planned_price REAL,
    actual_soc_wh REAL,
    actual_battery_w REAL,
    actual_grid_w REAL,
    actual_pv_w REAL,
    actual_load_w REAL,
    actual_action TEXT,
    samples INTEGER,
    forecast_pv_w REAL,
    weather_cloud REAL,
    actual_loadpoint_w REAL,
    weather_temp REAL,
    plane_forecasts TEXT,
    actual_strings TEXT
)
"""
# Databases from before the schema version (user_version 0) get these columns
ADDED_COLUMNS = {"forecast_pv_w": "REAL", "weather_cloud": "REAL", "actual_loadpoint_w": "REAL", "weather_temp": "REAL", "plane_forecasts": "TEXT", "actual_strings": "TEXT"}
SCHEMA_VERSION = 11
# SQL from version n to n + 1; steps only ever get added
SCHEMA_STEPS: dict[int, list[str]] = {
    # Power per controllable consumer (device id -> W)
    1: ["ALTER TABLE slots ADD COLUMN actual_consumers TEXT"],
    # Level per controllable consumer (vehicle %, tank °C)
    2: ["ALTER TABLE slots ADD COLUMN actual_levels TEXT"],
    # Per battery (device id -> {"soc_wh", "action"} planned, {"soc_wh", "battery_w"} measured)
    3: ["ALTER TABLE slots ADD COLUMN planned_batteries TEXT", "ALTER TABLE slots ADD COLUMN actual_batteries TEXT"],
    # The SoC a plan made PLAN_AHEAD before a slot expected at its end, in total and per battery
    4: ["CREATE TABLE plan_ahead (start TEXT PRIMARY KEY, soc_wh REAL, batteries TEXT)"],
    # Per plane and local day: the share of its clean yield expected the day before, by season and as now
    5: ["CREATE TABLE soiling_ahead (day TEXT, plane TEXT, expected REAL, current REAL, PRIMARY KEY (day, plane))"],
    # PV and house load that plan expected for the slot: the plan of the slot itself is corrected by the day so far
    6: ["ALTER TABLE plan_ahead ADD COLUMN pv_w REAL", "ALTER TABLE plan_ahead ADD COLUMN load_w REAL"],
    # Per wallbox the vehicle plugged in during the slot (and the scanned card); sessions imported from evcc
    7: [
        "ALTER TABLE slots ADD COLUMN actual_vehicles TEXT",
        "CREATE TABLE imported_sessions (source TEXT, id TEXT, start TEXT, end TEXT, device_id TEXT, loadpoint TEXT,"
        " vehicle_id TEXT, vehicle TEXT, card TEXT, energy_wh REAL, charging_s REAL, solar_share REAL, cost_eur REAL,"
        " co2_g_per_kwh REAL,"
        " PRIMARY KEY (source, id))",
    ],
    # evcc's CSV export also has the vehicle's state of charge before and after
    8: ["ALTER TABLE imported_sessions ADD COLUMN soc_start REAL", "ALTER TABLE imported_sessions ADD COLUMN soc_end REAL"],
    # 5 minute means of a heat pump with its states (design/heat_pump.md, sections 9.1 and 10); kept a year
    9: [
        "CREATE TABLE heat_pump_samples (device_id TEXT, start TEXT, samples INTEGER,"
        " tv_c REAL, tr_c REAL, tvbh_c REAL, tv_max_c REAL, heat_w REAL, heat_source TEXT, outside_c REAL,"
        " bypass_pct REAL, setpoint_c REAL, max_setpoint_c REAL, written_c REAL, compressor REAL,"
        " program TEXT, activity TEXT, strategy TEXT, error TEXT, rooms TEXT, events TEXT, flags TEXT,"
        " PRIMARY KEY (device_id, start))",
        # Clearing out rows older than a year goes by start over all devices
        "CREATE INDEX heat_pump_samples_start ON heat_pump_samples (start)",
    ],
    # Per heat pump row the flow target and the room setpoint in force (mean of the minutes), for the history view
    10: [
        "ALTER TABLE heat_pump_samples ADD COLUMN target_c REAL",
        "ALTER TABLE heat_pump_samples ADD COLUMN room_setpoint_c REAL",
    ],
}
# Per table whether an import takes it over; a table missing here is taken over too.
# Only what can be thrown away may be left out.
IMPORT_TABLES: dict[str, bool] = {
    "slots": True,
    "plan_ahead": True,
    "soiling_ahead": True,
    "imported_sessions": True,
    "heat_pump_samples": True,
}
HEAT_PUMP_COLUMNS = (
    "device_id", "start", "samples", "tv_c", "tr_c", "tvbh_c", "tv_max_c", "heat_w", "heat_source", "outside_c",
    "bypass_pct", "setpoint_c", "max_setpoint_c", "written_c", "compressor", "program", "activity", "strategy",
    "error", "rooms", "events", "flags", "target_c", "room_setpoint_c",
)  # fmt: skip
HEAT_PUMP_JSON = ("rooms", "events", "flags")
# Heat pump samples are kept this long; older ones are deleted in blocks of this many rows
HEAT_PUMP_KEEP = timedelta(days=365)
PRUNE_BLOCK = 5000
# Stored as JSON objects (key -> W)
JSON_COLUMNS = (
    "plane_forecasts", "actual_strings", "actual_consumers", "actual_levels", "planned_batteries", "actual_batteries",
    "ahead_batteries", "actual_vehicles",
)  # fmt: skip
# The plan a slot is compared with: every replan starts from the measured SoC, so the latest one
# matches the measurement by construction
PLAN_AHEAD = timedelta(hours=6)


def slot_start(moment: datetime) -> datetime:
    """Return the start of the 15 minute slot containing moment (UTC)."""
    moment = moment.astimezone(UTC)
    return moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)


@dataclass(kw_only=True)
class Sample:
    """Live values at one moment (W, Wh; battery + = charge, grid + = import)."""

    soc_wh: float | None
    battery_w: float | None
    grid_w: float | None
    pv_w: float | None
    load_w: float | None
    # Power of all controllable consumers (wallbox, heating rod, own consumers)
    loadpoint_w: float | None = None
    # The same per consumer (device id)
    consumers: dict[str, float] = field(default_factory=dict)
    # Their level (vehicle %, tank °C) by device id
    levels: dict[str, float] = field(default_factory=dict)
    # DC power per PV string ("<device id>:<string>")
    strings: dict[str, float] = field(default_factory=dict)
    # SoC and power of every battery (device id); soc_wh and battery_w are their sums
    battery_socs: dict[str, float] = field(default_factory=dict)
    battery_powers: dict[str, float] = field(default_factory=dict)


@dataclass(kw_only=True)
class SlotRecorder:
    """Collects samples of the running slot."""

    start: datetime
    samples: list[Sample] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Average the powers; the SoC is the last reading of the slot."""

        def mean(attribute: str) -> float | None:
            values = [getattr(s, attribute) for s in self.samples if getattr(s, attribute) is not None]
            return fmean(values) if values else None

        socs = [s.soc_wh for s in self.samples if s.soc_wh is not None]
        result = {
            "actual_soc_wh": socs[-1] if socs else None,
            "actual_battery_w": mean("battery_w"),
            "actual_grid_w": mean("grid_w"),
            "actual_pv_w": mean("pv_w"),
            "actual_load_w": mean("load_w"),
            "actual_loadpoint_w": mean("loadpoint_w"),
            "actual_strings": json.dumps(
                {
                    key: fmean(s.strings[key] for s in self.samples if key in s.strings)
                    for key in {k for s in self.samples for k in s.strings}
                }
            ),
            "actual_consumers": json.dumps(
                {
                    key: fmean(s.consumers[key] for s in self.samples if key in s.consumers)
                    for key in {k for s in self.samples for k in s.consumers}
                }
            ),
            "actual_levels": json.dumps(
                {
                    key: fmean(s.levels[key] for s in self.samples if key in s.levels)
                    for key in {k for s in self.samples for k in s.levels}
                }
            ),
            "actual_batteries": json.dumps(
                {
                    key: {
                        # The SoC at the slot end, like the site's; the power as mean
                        "soc_wh": next((s.battery_socs[key] for s in reversed(self.samples) if key in s.battery_socs), None),
                        "battery_w": fmean(values) if (values := [s.battery_powers[key] for s in self.samples if key in s.battery_powers]) else None,
                    }
                    for key in {k for s in self.samples for k in (*s.battery_socs, *s.battery_powers)}
                }
            ),
            "samples": len(self.samples),
        }
        result["actual_action"] = actual_action(result["actual_grid_w"], result["actual_battery_w"])
        return result


def actual_action(grid: float | None, battery: float | None) -> str | None:
    """Classify what the battery actually did from grid (+ import) and battery (+ charge).

    The grid meter is the reliable reference: PV readings of hybrid inverters are
    often coarse DC values that do not balance with the AC load.
    """
    if grid is None or battery is None:
        return None
    threshold = Settings().action_threshold_w
    if battery < -threshold and grid < -threshold:
        return "force_discharge"
    if battery > threshold and grid > threshold:
        return "force_charge"
    if abs(battery) <= threshold and grid > threshold:
        return "hold"
    if battery < -threshold and grid > threshold:
        # The battery covers only part of the deficit
        return "limit_discharge"
    return "auto"


class ShadowBackupError(Exception):
    """The copy before a migration could not be written; the database was not migrated."""


def backup_before_migration(db: sqlite3.Connection, path: Path) -> Path | None:
    """Keep a copy <name>.v<n>.db of a database about to be migrated, for going back to that release.

    Nothing is copied for a database at (or above) SCHEMA_VERSION or a fresh, empty one; an
    existing copy of the same name stays as it is. Raises ShadowBackupError if the copy fails.
    """
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION or db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone() is None:
        return None
    kept = path.with_name(f"{path.stem}.v{version}.db")
    if kept.exists():
        return kept
    # Written under a temporary name: a copy cut short (disk full) must not pass for a complete one
    partial = kept.with_name(f"{kept.name}.tmp")
    try:
        target = sqlite3.connect(partial)
        try:
            db.backup(target)
        finally:
            target.close()
        partial.replace(kept)
    except (sqlite3.Error, OSError) as err:
        with suppress(OSError):
            partial.unlink(missing_ok=True)
        raise ShadowBackupError(f"Could not back up {path.name} to {kept.name} before migrating it: {err}") from err
    _LOGGER.info("Shadow database backed up to %s before migrating it from version %s", kept.name, version)
    return kept


def migrate(db: sqlite3.Connection) -> None:
    """Bring a shadow database to SCHEMA_VERSION; one of a newer Sunflow is left as it is."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        return
    with db:
        db.execute(SCHEMA)
        if version == 0:
            existing = {row[1] for row in db.execute("PRAGMA table_info(slots)")}
            for column, kind in ADDED_COLUMNS.items():
                if column not in existing:
                    db.execute(f"ALTER TABLE slots ADD COLUMN {column} {kind}")
            version = 1
        for step in range(version, SCHEMA_VERSION):
            for statement in SCHEMA_STEPS[step]:
                try:
                    db.execute(statement)
                except sqlite3.OperationalError as err:
                    # Each statement commits on its own: one interrupted between them left its part behind
                    if "duplicate column name" not in str(err) and "already exists" not in str(err):
                        raise
            db.execute(f"PRAGMA user_version = {step + 1}")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _tables(db: sqlite3.Connection, schema: str) -> list[str]:
    """Names of the tables in an attached database (main, export), SQLite's own left out."""
    return [
        row[0]
        for row in db.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"  # noqa: S608
        )
    ]


def _columns(db: sqlite3.Connection, schema: str, table: str) -> list[str]:
    """Column names of a table in an attached database."""
    return [row[0] for row in db.execute("SELECT name FROM pragma_table_info(?, ?)", (table, schema))]


class ShadowStore:
    """SQLite storage of planned and actual slot values."""

    def __init__(self, data_dir: Path) -> None:
        """Open (and create) the database."""
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / "shadow.db"
        self._db = sqlite3.connect(path, check_same_thread=False)
        # The connection is shared by worker threads (runner, export, import); sqlite3
        # leaves serializing writes to the caller
        self._lock = threading.Lock()
        try:
            # Without a copy the database stays unmigrated: better not to start than to lose the way back
            backup_before_migration(self._db, path)
            migrate(self._db)
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        """Close the database."""
        with self._lock:
            self._db.close()

    def store_planned(self, start: datetime, planned: dict[str, Any]) -> None:
        """Store what Sunflow would do in the slot starting at start."""
        with self._lock:
            self._db.execute(
                """
                INSERT INTO slots (start, planned_action, planned_power_w, planned_soc_wh,
                                   planned_pv_w, planned_load_w, planned_price, forecast_pv_w, weather_cloud,
                                   weather_temp, plane_forecasts, planned_batteries)
                VALUES (:start, :action, :power, :soc, :pv, :load, :price, :forecast_pv, :cloud, :temp, :planes, :batteries)
                ON CONFLICT(start) DO UPDATE SET
                    planned_batteries = excluded.planned_batteries,
                    forecast_pv_w = excluded.forecast_pv_w,
                    weather_cloud = excluded.weather_cloud,
                    weather_temp = excluded.weather_temp,
                    plane_forecasts = excluded.plane_forecasts,
                    planned_action = excluded.planned_action,
                    planned_power_w = excluded.planned_power_w,
                    planned_soc_wh = excluded.planned_soc_wh,
                    planned_pv_w = excluded.planned_pv_w,
                    planned_load_w = excluded.planned_load_w,
                    planned_price = excluded.planned_price
                """,
                {"start": start.isoformat(), "forecast_pv": None, "cloud": None, "temp": None, "planes": None, "batteries": None, **planned},
            )
            self._db.commit()

    def store_ahead(
        self, start: datetime, soc_wh: float, batteries: dict[str, float], pv_w: float | None = None, load_w: float | None = None
    ) -> None:
        """Keep what the plan expects for the slot at start (SoC at its end, PV, load); the first plan to say so stays."""
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO plan_ahead (start, soc_wh, batteries, pv_w, load_w) VALUES (?, ?, ?, ?, ?)",
                (start.astimezone(UTC).isoformat(), soc_wh, json.dumps(batteries), pv_w, load_w),
            )
            self._db.commit()

    def store_soiling_ahead(self, day: date, plane: str, expected: float, current: float) -> None:
        """Keep the expectation for a day; the first one made stays."""
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO soiling_ahead (day, plane, expected, current) VALUES (?, ?, ?, ?)",
                (day.isoformat(), plane, expected, current),
            )
            self._db.commit()

    def soiling_ahead(self, since: date) -> dict[str, dict[date, tuple[float, float]]]:
        """Stored expectations per plane: day -> (by season, as now)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT day, plane, expected, current FROM soiling_ahead WHERE day >= ?", (since.isoformat(),)
            ).fetchall()
        result: dict[str, dict[date, tuple[float, float]]] = {}
        for day, plane, expected, current in rows:
            result.setdefault(plane, {})[date.fromisoformat(day)] = (expected, current)
        return result

    def store_vehicles(self, start: datetime, vehicles: dict[str, dict[str, Any]]) -> None:
        """Per wallbox the vehicle plugged in during the slot (and its card), for the charging statistics."""
        with self._lock:
            self._db.execute(
                "INSERT INTO slots (start, actual_vehicles) VALUES (?, ?)"
                " ON CONFLICT(start) DO UPDATE SET actual_vehicles = excluded.actual_vehicles",
                (start.astimezone(UTC).isoformat(), json.dumps(vehicles)),
            )
            self._db.commit()

    def store_imported_sessions(self, sessions: list[dict[str, Any]]) -> int:
        """Keep sessions imported from another system; one already there (same source and id) stays as it is."""
        columns = (
            "source", "id", "start", "end", "device_id", "loadpoint", "vehicle_id", "vehicle", "card", "energy_wh",
            "charging_s", "solar_share", "cost_eur", "co2_g_per_kwh", "soc_start", "soc_end",
        )  # fmt: skip
        with self._lock:
            before = self._db.total_changes
            self._db.executemany(
                f"INSERT OR IGNORE INTO imported_sessions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",  # noqa: S608
                [tuple(s.get(c) for c in columns) for s in sessions],
            )
            self._db.commit()
            return self._db.total_changes - before

    def imported_sessions(self) -> list[dict[str, Any]]:
        """All imported sessions, oldest first."""
        with self._lock:
            cursor = self._db.execute("SELECT * FROM imported_sessions ORDER BY start")
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def remove_imported_sessions(self, source: str) -> int:
        """Forget the sessions of one import source (to import again)."""
        with self._lock:
            removed = self._db.execute("DELETE FROM imported_sessions WHERE source = ?", (source,)).rowcount
            self._db.commit()
            return removed

    def store_heat_pump_samples(self, rows: list[dict[str, Any]]) -> None:
        """Keep 5 minute rows of heat pumps; a row of the same device and start is replaced."""
        if not rows:
            return
        values = []
        for row in rows:
            record = {c: row.get(c) for c in HEAT_PUMP_COLUMNS}
            record["start"] = row["start"].astimezone(UTC).isoformat() if isinstance(row["start"], datetime) else row["start"]
            for column in HEAT_PUMP_JSON:
                if record[column] is not None:
                    record[column] = json.dumps(record[column], ensure_ascii=False)
            values.append(tuple(record[c] for c in HEAT_PUMP_COLUMNS))
        with self._lock:
            self._db.executemany(
                f"INSERT OR REPLACE INTO heat_pump_samples ({', '.join(HEAT_PUMP_COLUMNS)})"  # noqa: S608
                f" VALUES ({', '.join('?' for _ in HEAT_PUMP_COLUMNS)})",
                values,
            )
            self._db.commit()

    def heat_pump_samples(self, device_id: str, since: datetime) -> list[dict[str, Any]]:
        """The 5 minute rows of a heat pump from since on, oldest first; JSON columns decoded."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT * FROM heat_pump_samples WHERE device_id = ? AND start >= ? ORDER BY start",
                (device_id, since.astimezone(UTC).isoformat()),
            )
            names = [c[0] for c in cursor.description]
            rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        for row in rows:
            for column in HEAT_PUMP_JSON:
                # A row written by another version may hold something else: then empty
                try:
                    row[column] = json.loads(row[column]) if row.get(column) else None
                except ValueError:
                    row[column] = None
        return rows

    def heat_pump_valid_days(self, device_id: str, min_seconds: float) -> int:
        """Days (UTC) of the whole table with at least min_seconds of valid heating without ventilation.

        Valid heating is flags.valid of a row (every minute valid, design/heat_pump.md section 4); a row
        counts with its minutes. Rows whose flags are not readable JSON do not count.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT substr(start, 1, 10), SUM(samples) FROM heat_pump_samples"
                " WHERE device_id = ? AND CASE WHEN json_valid(flags) THEN"
                " json_extract(flags, '$.valid') = 1"
                " AND COALESCE(json_array_length(json_extract(flags, '$.ventilation')), 0) = 0"
                " ELSE 0 END"
                " GROUP BY substr(start, 1, 10)",
                (device_id,),
            ).fetchall()
        return sum(1 for _, minutes in rows if (minutes or 0) * 60 >= min_seconds)

    def prune_heat_pump_samples(self, before: datetime, block: int = PRUNE_BLOCK) -> int:
        """Delete heat pump rows starting before before, a block at a time; returns how many."""
        removed = 0
        cutoff = before.astimezone(UTC).isoformat()
        while True:
            # The lock is released between the blocks: recording and reading go on meanwhile
            with self._lock:
                count = self._db.execute(
                    "DELETE FROM heat_pump_samples WHERE rowid IN"
                    " (SELECT rowid FROM heat_pump_samples WHERE start < ? LIMIT ?)",
                    (cutoff, block),
                ).rowcount
                self._db.commit()
            removed += count
            if count < block:
                return removed

    def store_actual(self, start: datetime, actual: dict[str, Any]) -> None:
        """Store the measured averages of a finished slot."""
        with self._lock:
            columns = ", ".join(actual)
            updates = ", ".join(f"{k} = excluded.{k}" for k in actual)
            self._db.execute(
                f"INSERT INTO slots (start, {columns}) VALUES (:start, {', '.join(':' + k for k in actual)}) "  # noqa: S608
                f"ON CONFLICT(start) DO UPDATE SET {updates}",
                {"start": start.isoformat(), **actual},
            )
            self._db.commit()

    def backup_to(self, path: Path) -> None:
        """Write a consistent copy of the database to path."""
        with self._lock:
            target = sqlite3.connect(path)
            try:
                self._db.backup(target)
            finally:
                target.close()

    def merge_from(self, path: Path) -> dict[str, int]:
        """Merge the tables of another shadow database (an unpacked export); rows with the same key are replaced.

        An export of an older version is migrated first (path must be a copy); tables and columns
        only a newer version knows are left out, tables only this database has stay as they are.
        All tables are merged in one transaction. Returns the number of rows taken per table.
        """
        other = sqlite3.connect(path)
        try:
            migrate(other)
        finally:
            other.close()
        with self._lock:
            self._db.execute("ATTACH DATABASE ? AS export", (str(path),))
            try:
                theirs = set(_tables(self._db, "export"))
                counts: dict[str, int] = {}
                # Commits all tables or, on an error, none
                with self._db:
                    for table in _tables(self._db, "main"):
                        if table not in theirs or not IMPORT_TABLES.get(table, True):
                            continue
                        known = set(_columns(self._db, "export", table))
                        # Only names of the own schema reach the SQL
                        names = ", ".join(f'"{c}"' for c in _columns(self._db, "main", table) if c in known)
                        counts[table] = self._db.execute(
                            f'INSERT OR REPLACE INTO main."{table}" ({names}) SELECT {names} FROM export."{table}"'  # noqa: S608
                        ).rowcount
            finally:
                self._db.execute("DETACH DATABASE export")
            return counts

    def rows(self, since: datetime) -> list[dict[str, Any]]:
        """Return all slots starting at or after since, oldest first."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT slots.*, plan_ahead.soc_wh AS ahead_soc_wh, plan_ahead.batteries AS ahead_batteries,"
                " plan_ahead.pv_w AS ahead_pv_w, plan_ahead.load_w AS ahead_load_w"
                " FROM slots LEFT JOIN plan_ahead USING (start) WHERE start >= ? ORDER BY start",
                (since.astimezone(UTC).isoformat(),),
            )
            names = [c[0] for c in cursor.description]
            rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            for row in rows:
                for column in JSON_COLUMNS:
                    row[column] = json.loads(row[column]) if row.get(column) else {}
            # Classify from the stored values so older rows follow the current rules
            for row in rows:
                if row["actual_grid_w"] is not None and row["actual_battery_w"] is not None:
                    row["actual_action"] = actual_action(row["actual_grid_w"], row["actual_battery_w"])
            return rows


def comparable(planned: str) -> str:
    """Planned action as the measurement can tell it.

    Charging while exporting looks the same for a limited charge and for a battery
    charging at its maximum power, so both count as self-consumption.
    """
    return "auto" if planned == "limit_charge" else planned


def same_decision(planned: str, planned_power_w: float | None, actual: str, battery_w: float | None) -> bool:
    """The measurement matches the plan.

    A discharge limit above the demand never bites: the battery covers everything, which looks
    like self-consumption. Discharging at most the limit (with some tolerance) keeps to the plan.
    """
    if comparable(planned) == actual:
        return True
    if planned != "limit_discharge" or actual != "auto" or battery_w is None:
        return False
    limit = abs(planned_power_w) if planned_power_w is not None else None
    tolerance = max(Settings().action_threshold_w, 0.1 * (limit or 0.0))
    return limit is None or -battery_w <= limit + tolerance


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare planned and actual values of slots that have both."""
    complete = [r for r in rows if r["planned_action"] is not None and r["actual_action"] is not None]
    # The load forecast leaves out the controllable consumers (wallbox, heater), the meter includes them
    for r in complete:
        load = r["actual_load_w"]
        r["actual_base_load_w"] = None if load is None else max(load - (r.get("actual_loadpoint_w") or 0.0), 0.0)

    def energy(key: str) -> float:
        return sum(r[key] or 0 for r in complete) * 0.25 / 1000

    def mae(planned: str, actual: str) -> float | None:
        pairs = [(r.get(planned), r[actual]) for r in complete if r.get(planned) is not None and r[actual] is not None]
        return fmean(abs(p - a) for p, a in pairs) if pairs else None

    def bias(planned: str, actual: str) -> float | None:
        pairs = [(r.get(planned), r[actual]) for r in complete if r.get(planned) is not None and r[actual] is not None]
        return fmean(p - a for p, a in pairs) if pairs else None

    differing = [
        r
        for r in complete
        if not same_decision(r["planned_action"], r.get("planned_power_w"), r["actual_action"], r.get("actual_battery_w"))
    ]
    return {
        "slots": len(rows),
        "compared": len(complete),
        "pv_forecast_kwh": energy("planned_pv_w"),
        "pv_actual_kwh": energy("actual_pv_w"),
        "pv_mae_w": mae("planned_pv_w", "actual_pv_w"),
        "load_forecast_kwh": energy("planned_load_w"),
        "load_actual_kwh": energy("actual_base_load_w"),
        "load_mae_w": mae("planned_load_w", "actual_base_load_w"),
        "soc_mae_wh": mae("ahead_soc_wh", "actual_soc_wh"),
        "pv_ahead_mae_w": mae("ahead_pv_w", "actual_pv_w"),
        "pv_ahead_bias_w": bias("ahead_pv_w", "actual_pv_w"),
        "load_ahead_mae_w": mae("ahead_load_w", "actual_base_load_w"),
        "load_ahead_bias_w": bias("ahead_load_w", "actual_base_load_w"),
        "soc_ahead_hours": PLAN_AHEAD.total_seconds() / 3600,
        "agreement": 1 - len(differing) / len(complete) if complete else None,
        "differing_slots": len(differing),
    }
