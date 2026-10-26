"""Shadow mode: record what Sunflow would do next to what actually happened.

Every minute the live values are sampled; at each 15 minute boundary the
finished slot is stored with its averages and the plan is recomputed. The
first slot of a fresh plan is what Sunflow would command right now.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import threading
from statistics import fmean
from typing import Any

from .optimizer import Settings

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
SCHEMA_VERSION = 1
# SQL from version n to n + 1; steps only ever get added
SCHEMA_STEPS: dict[int, list[str]] = {}
# Stored as JSON objects (key -> W)
JSON_COLUMNS = ("plane_forecasts", "actual_strings")


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
    # DC power per PV string ("<device id>:<string>")
    strings: dict[str, float] = field(default_factory=dict)


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
                db.execute(statement)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


class ShadowStore:
    """SQLite storage of planned and actual slot values."""

    def __init__(self, data_dir: Path) -> None:
        """Open (and create) the database."""
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "shadow.db", check_same_thread=False)
        # The connection is shared by worker threads (runner, export, import); sqlite3
        # leaves serializing writes to the caller
        self._lock = threading.Lock()
        migrate(self._db)

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
                                   weather_temp, plane_forecasts)
                VALUES (:start, :action, :power, :soc, :pv, :load, :price, :forecast_pv, :cloud, :temp, :planes)
                ON CONFLICT(start) DO UPDATE SET
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
                {"start": start.isoformat(), "forecast_pv": None, "cloud": None, "temp": None, "planes": None, **planned},
            )
            self._db.commit()

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

    def merge_from(self, path: Path) -> int:
        """Merge slots of another shadow database; existing slots are replaced. Returns the count."""
        with self._lock:
            other = sqlite3.connect(path)
            try:
                # An export of an older version is migrated first; columns of a newer one are left out
                migrate(other)
                own = {row[1] for row in self._db.execute("PRAGMA table_info(slots)")}
                theirs = [row[1] for row in other.execute("PRAGMA table_info(slots)")]
                names = [name for name in theirs if name in own]
                rows = other.execute(f"SELECT {', '.join(names)} FROM slots").fetchall()  # noqa: S608
            finally:
                other.close()
            placeholders = ", ".join("?" for _ in names)
            self._db.executemany(
                f"INSERT OR REPLACE INTO slots ({', '.join(names)}) VALUES ({placeholders})", rows  # noqa: S608
            )
            self._db.commit()
            return len(rows)

    def rows(self, since: datetime) -> list[dict[str, Any]]:
        """Return all slots starting at or after since, oldest first."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT * FROM slots WHERE start >= ? ORDER BY start", (since.astimezone(UTC).isoformat(),)
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
        pairs = [(r[planned], r[actual]) for r in complete if r[planned] is not None and r[actual] is not None]
        return fmean(abs(p - a) for p, a in pairs) if pairs else None

    differing = [r for r in complete if comparable(r["planned_action"]) != r["actual_action"]]
    return {
        "slots": len(rows),
        "compared": len(complete),
        "pv_forecast_kwh": energy("planned_pv_w"),
        "pv_actual_kwh": energy("actual_pv_w"),
        "pv_mae_w": mae("planned_pv_w", "actual_pv_w"),
        "load_forecast_kwh": energy("planned_load_w"),
        "load_actual_kwh": energy("actual_base_load_w"),
        "load_mae_w": mae("planned_load_w", "actual_base_load_w"),
        "soc_mae_wh": mae("planned_soc_wh", "actual_soc_wh"),
        "agreement": 1 - len(differing) / len(complete) if complete else None,
        "differing_slots": len(differing),
    }
