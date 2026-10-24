"""Shadow mode: record what Sunflow would do next to what actually happened.

Every minute the live values are sampled; at each 15 minute boundary the
finished slot is stored with its averages and the plan is recomputed. The
first slot of a fresh plan is what Sunflow would command right now.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from statistics import fmean
from typing import Any

from .optimizer import Settings, choose_action

SLOT = timedelta(minutes=15)

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
    samples INTEGER
)
"""


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
            "samples": len(self.samples),
        }
        result["actual_action"] = actual_action(
            result["actual_pv_w"], result["actual_load_w"], result["actual_battery_w"]
        )
        return result


def actual_action(pv: float | None, load: float | None, battery: float | None) -> str | None:
    """Classify what the battery actually did with the same rules as the plan."""
    if pv is None or load is None or battery is None:
        return None
    action, _ = choose_action(
        pv, load, max(battery, 0), max(-battery, 0), Settings().action_threshold_w
    )
    return action


class ShadowStore:
    """SQLite storage of planned and actual slot values."""

    def __init__(self, data_dir: Path) -> None:
        """Open (and create) the database."""
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "shadow.db", check_same_thread=False)
        self._db.execute(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        """Close the database."""
        self._db.close()

    def store_planned(self, start: datetime, planned: dict[str, Any]) -> None:
        """Store what Sunflow would do in the slot starting at start."""
        self._db.execute(
            """
            INSERT INTO slots (start, planned_action, planned_power_w, planned_soc_wh,
                               planned_pv_w, planned_load_w, planned_price)
            VALUES (:start, :action, :power, :soc, :pv, :load, :price)
            ON CONFLICT(start) DO UPDATE SET
                planned_action = excluded.planned_action,
                planned_power_w = excluded.planned_power_w,
                planned_soc_wh = excluded.planned_soc_wh,
                planned_pv_w = excluded.planned_pv_w,
                planned_load_w = excluded.planned_load_w,
                planned_price = excluded.planned_price
            """,
            {"start": start.isoformat(), **planned},
        )
        self._db.commit()

    def store_actual(self, start: datetime, actual: dict[str, Any]) -> None:
        """Store the measured averages of a finished slot."""
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
        target = sqlite3.connect(path)
        try:
            self._db.backup(target)
        finally:
            target.close()

    def merge_from(self, path: Path) -> int:
        """Merge slots of another shadow database; existing slots are replaced. Returns the count."""
        other = sqlite3.connect(path)
        try:
            cursor = other.execute("SELECT * FROM slots")
            names = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
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
        cursor = self._db.execute(
            "SELECT * FROM slots WHERE start >= ? ORDER BY start", (since.astimezone(UTC).isoformat(),)
        )
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare planned and actual values of slots that have both."""
    complete = [r for r in rows if r["planned_action"] is not None and r["actual_action"] is not None]

    def energy(key: str) -> float:
        return sum(r[key] or 0 for r in complete) * 0.25 / 1000

    def mae(planned: str, actual: str) -> float | None:
        pairs = [(r[planned], r[actual]) for r in complete if r[planned] is not None and r[actual] is not None]
        return fmean(abs(p - a) for p, a in pairs) if pairs else None

    differing = [r for r in complete if r["planned_action"] != r["actual_action"]]
    return {
        "slots": len(rows),
        "compared": len(complete),
        "pv_forecast_kwh": energy("planned_pv_w"),
        "pv_actual_kwh": energy("actual_pv_w"),
        "pv_mae_w": mae("planned_pv_w", "actual_pv_w"),
        "load_forecast_kwh": energy("planned_load_w"),
        "load_actual_kwh": energy("actual_load_w"),
        "load_mae_w": mae("planned_load_w", "actual_load_w"),
        "soc_mae_wh": mae("planned_soc_wh", "actual_soc_wh"),
        "agreement": 1 - len(differing) / len(complete) if complete else None,
        "differing_slots": len(differing),
    }
