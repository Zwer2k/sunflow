"""A car's state of charge between its reports.

Some cars (e.g. a Fiat 500e through its cloud) report their SoC only now and then. Between two
reports Sunflow adds what the wallbox charged into the car since the last one. At each new report
it compares the estimate with it and learns how much of the wallbox's energy really ends up in the
battery, so the estimate gets better with every charge.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from sunflow.config.models import Vehicle

_LOGGER = logging.getLogger(__name__)

FILE = "vehicle_soc.json"
# Energy (Wh) between two reports to learn from; less is mostly rounding of the car's SoC
LEARN_MIN_WH = 1500.0
# Learned share of the wallbox's energy that is plausible
EFFICIENCY_RANGE = (0.5, 1.0)
# Energy (Wh) the learned value is averaged over: older charges fade (cold weather charges worse)
LEARN_MEMORY_WH = 60_000.0
# A charging pause longer than this is not integrated over (missed samples)
MAX_GAP_S = 600.0


@dataclass(kw_only=True)
class Learned:
    """What a car's reports taught about charging it."""

    efficiency: float
    # Energy (Wh) the efficiency is averaged over, at most LEARN_MEMORY_WH
    weight_wh: float = 0.0
    charges: int = 0
    # Mean deviation (percentage points) of the estimate from the next report
    error_pct: float | None = None


@dataclass(kw_only=True)
class Track:
    """A car's last report and what the wallbox charged since."""

    soc: float
    updated: str | None
    energy_wh: float = 0.0
    power_w: float = 0.0
    power_at: datetime | None = None


@dataclass(kw_only=True)
class SocEstimator:
    """Estimated SoC of every car between its reports, learned from the reports themselves."""

    path: Path | None = None
    tracks: dict[str, Track] = field(default_factory=dict)
    learned: dict[str, Learned] = field(default_factory=dict)

    def load(self, data_dir: Path) -> None:
        """Read what was learned before."""
        self.path = data_dir / FILE
        try:
            raw = json.loads(self.path.read_text())
            self.learned = {key: Learned(**value) for key, value in raw.items()}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as err:
            _LOGGER.warning("Learned vehicle charging not readable, starting anew: %s", err)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.write_text(json.dumps({key: asdict(value) for key, value in self.learned.items()}))
        except OSError as err:
            _LOGGER.warning("Learned vehicle charging not saved: %s", err)

    def efficiency(self, vehicle: Vehicle) -> float:
        """Share of the wallbox's energy that ends up in the battery: learned, else as set."""
        learned = self.learned.get(vehicle.id)
        return learned.efficiency if learned and learned.charges else vehicle.charge_efficiency_pct / 100

    def observe(self, vehicles: list[Vehicle], states: dict[str, dict[str, Any]], charging: dict[str, float], now: datetime) -> None:
        """Take the cars' reports and integrate what each one is charged (W by vehicle id) since the last sample."""
        for vehicle in vehicles:
            if not vehicle.estimate_soc or not vehicle.soc_entity:
                self.tracks.pop(vehicle.id, None)
                continue
            track = self.tracks.get(vehicle.id)
            if track is not None and track.power_at is not None:
                seconds = (now - track.power_at).total_seconds()
                if 0 < seconds <= MAX_GAP_S:
                    track.energy_wh += track.power_w * seconds / 3600
            reported = reading(vehicle, states)
            if reported is not None and (track is None or reported != (track.soc, track.updated)):
                if track is not None:
                    self._learn(vehicle, track, reported[0])
                track = self.tracks[vehicle.id] = Track(soc=reported[0], updated=reported[1])
            if track is not None:
                track.power_w = max(charging.get(vehicle.id, 0.0), 0.0)
                track.power_at = now

    def _learn(self, vehicle: Vehicle, track: Track, soc: float) -> None:
        if track.energy_wh < LEARN_MIN_WH or soc >= 100 or soc <= track.soc:
            return
        efficiency = (soc - track.soc) / 100 * vehicle.capacity_wh / track.energy_wh
        if not EFFICIENCY_RANGE[0] <= efficiency <= EFFICIENCY_RANGE[1]:
            return
        learned = self.learned.setdefault(vehicle.id, Learned(efficiency=vehicle.charge_efficiency_pct / 100))
        error = abs(self._estimate(vehicle, track) - soc)
        learned.error_pct = error if learned.error_pct is None else 0.7 * learned.error_pct + 0.3 * error
        # The first charge replaces the set value; later ones average with the recent ones
        prior = max(min(learned.weight_wh, LEARN_MEMORY_WH - track.energy_wh), 0.0) if learned.charges else 0.0
        learned.efficiency = (learned.efficiency * prior + efficiency * track.energy_wh) / (prior + track.energy_wh)
        learned.weight_wh = min(prior + track.energy_wh, LEARN_MEMORY_WH)
        learned.charges += 1
        self._save()

    def _estimate(self, vehicle: Vehicle, track: Track) -> float:
        return min(track.soc + track.energy_wh * self.efficiency(vehicle) / vehicle.capacity_wh * 100, 100.0)

    def soc(self, vehicle: Vehicle, states: dict[str, dict[str, Any]]) -> tuple[float | None, bool]:
        """The SoC in % and whether it is estimated; the car's report as long as nothing was charged since."""
        reported = reading(vehicle, states)
        track = self.tracks.get(vehicle.id) if vehicle.estimate_soc else None
        # A report not yet taken by observe() is newer than the track
        if track is None or (reported is not None and reported != (track.soc, track.updated)):
            return (reported[0] if reported else None), False
        if track.energy_wh <= 0:
            return track.soc, reported is None
        return round(self._estimate(vehicle, track), 1), True

    def as_dict(self, vehicle: Vehicle) -> dict[str, Any]:
        """For the API: what was learned and since when the estimate runs."""
        learned = self.learned.get(vehicle.id)
        track = self.tracks.get(vehicle.id)
        return {
            "efficiency": self.efficiency(vehicle),
            "learned": bool(learned and learned.charges),
            "charges": learned.charges if learned else 0,
            "error_pct": learned.error_pct if learned else None,
            "reported_soc": track.soc if track else None,
            "charged_since_wh": round(track.energy_wh) if track else None,
        }


def reading(vehicle: Vehicle, states: dict[str, dict[str, Any]]) -> tuple[float, str | None] | None:
    """The car's own report: SoC and when it changed."""
    state = states.get(vehicle.soc_entity or "") or {}
    try:
        return float(state.get("state")), state.get("last_updated")
    except TypeError, ValueError:
        return None


# One per app: the runner feeds it, everything reading a car's SoC asks it
estimator = SocEstimator()
