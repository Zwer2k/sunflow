"""Energy counters per PV string, summed up from the minutely measured power."""

from datetime import datetime, timedelta
import json
from pathlib import Path

# Longer gaps (app stopped, Home Assistant away) are not bridged: the energy in them is unknown
MAX_GAP = timedelta(minutes=5)


class EnergyCounters:
    """Wh per key since the counter started, kept in the data directory.

    Strings measured only as power get an energy for the energy dashboard this way.
    """

    def __init__(self, data_dir: Path) -> None:
        """Load the counters."""
        self._path = data_dir / "string_energy.json"
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError, ValueError:
            data = {}
        self.wh: dict[str, float] = data.get("wh", {})
        self._last: dict[str, float] = data.get("last", {})
        self._at = datetime.fromisoformat(data["at"]) if data.get("at") else None

    def add(self, powers: dict[str, float], at: datetime) -> None:
        """Add the energy since the previous measurement (mean of both powers)."""
        if self._at is not None and timedelta(0) < at - self._at <= MAX_GAP:
            hours = (at - self._at).total_seconds() / 3600
            for key, power in powers.items():
                previous = self._last.get(key, power)
                self.wh[key] = self.wh.get(key, 0.0) + max((previous + power) / 2, 0.0) * hours
        for key in powers:
            self.wh.setdefault(key, 0.0)
        self._last, self._at = dict(powers), at
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": self._at.isoformat() if self._at else None, "last": self._last, "wh": self.wh}))
        tmp.replace(self._path)
