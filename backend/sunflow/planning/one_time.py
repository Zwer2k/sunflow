"""One-time loads (e.g. a washing machine run): the cheapest contiguous start within the plan.

The appliance is not controlled by Sunflow; Home Assistant gets the start time (sensor,
event) and switches it. The run is fitted into the plan as it is: its energy comes from
the planned export first (losing the feed-in), the rest from the grid at the slot's
price. Once the start has passed it is fixed; the next plans see the run in the load.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from typing import Any
import uuid

SLOT = timedelta(minutes=15)


@dataclass(kw_only=True)
class OneTimeLoad:
    """A run to place: energy, power and the window it has to fit in."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    energy_wh: float
    power_w: float
    earliest: datetime | None = None
    finish_by: datetime | None = None
    start: datetime | None = None
    started: bool = False
    cost_eur: float | None = None

    @property
    def duration(self) -> timedelta:
        """Run time at its power, in whole slots."""
        return SLOT * max(math.ceil(self.energy_wh / self.power_w / 0.25), 1)

    @property
    def end(self) -> datetime | None:
        """End of the run."""
        return self.start + self.duration if self.start else None


def run_cost(slots: list[Any], first: int, count: int, power_w: float) -> float:
    """€ of a run in slots first..first+count: planned export first (feed-in lost), then grid."""
    cost = 0.0
    for slot in slots[first : first + count]:
        from_export = min(power_w, max(slot.export_w, 0.0))
        cost += (from_export * slot.feed_in + (power_w - from_export) * slot.price) * 0.25 / 1000
    return cost


def best_start(load: OneTimeLoad, slots: list[Any], now: datetime) -> tuple[datetime, float] | None:
    """Cheapest start (slot start) and its cost; the earliest of equally cheap ones. None if it does not fit."""
    count = round(load.duration / SLOT)
    earliest = max(load.earliest or now, now)
    best: tuple[float, datetime] | None = None
    for first, slot in enumerate(slots):
        start = datetime.fromisoformat(str(slot.start))
        # The running slot counts as "now": it may start right away
        if start + SLOT <= earliest or first + count > len(slots):
            continue
        begin = max(start, now)
        if load.finish_by is not None and begin + load.duration > load.finish_by:
            continue
        cost = run_cost(slots, first, count, load.power_w)
        if best is None or cost < best[0] - 1e-6:
            best = (cost, begin)
    return (best[1], round(best[0], 3)) if best else None


class OneTimeLoads:
    """The loads in the data directory."""

    def __init__(self, data_dir: Path) -> None:
        """Load them."""
        self._path = data_dir / "one_time_loads.json"
        try:
            raw = json.loads(self._path.read_text())
        except FileNotFoundError, ValueError:
            raw = []
        self.loads = [
            OneTimeLoad(
                **{
                    **item,
                    **{k: datetime.fromisoformat(item[k]) for k in ("earliest", "finish_by", "start") if item.get(k)},
                }
            )
            for item in raw
        ]

    def save(self) -> None:
        """Keep them over restarts."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {k: v.isoformat() if isinstance(v, datetime) else v for k, v in asdict(load).items()} for load in self.loads
        ]
        self._path.write_text(json.dumps(data))

    def update(self, slots: list[Any], now: datetime) -> None:
        """Place every load not started yet; one whose start came is fixed, finished ones go."""
        changed = False
        for load in self.loads:
            if load.started:
                continue
            if (found := best_start(load, slots, now)) is not None:
                if (load.start, load.cost_eur) != found:
                    load.start, load.cost_eur = found
                    changed = True
            if load.start is not None and load.start <= now:
                load.started, changed = True, True
        kept = [load for load in self.loads if not (load.end is not None and load.started and load.end <= now)]
        if len(kept) != len(self.loads) or changed:
            self.loads = kept
            self.save()

    def as_list(self) -> list[dict[str, Any]]:
        """For the API."""
        return [
            {
                "id": load.id,
                "name": load.name,
                "energy_wh": load.energy_wh,
                "power_w": load.power_w,
                "finish_by": load.finish_by.isoformat() if load.finish_by else None,
                "start": load.start.isoformat() if load.start else None,
                "end": load.end.isoformat() if load.end else None,
                "started": load.started,
                "cost_eur": load.cost_eur,
            }
            for load in self.loads
        ]
