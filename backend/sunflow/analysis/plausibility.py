"""Plausibility of a learning source: which of its recorded values the learning may use.

Home Assistant records a state when it changes; each holds until the next one. A value
is left out when it is outside the physical range of its kind, when it is a short spike
away from its neighbours, or when it did not change for longer than the quantity
plausibly stays the same (a hanging sensor). Short unavailability is bridged; longer
gaps stay missing instead of being filled. Nothing is changed in Home Assistant.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import itertools
from statistics import median
from typing import Any

from sunflow.ha import HAClient

# Physical range per kind (in the canonical unit)
RANGES = {
    "temperature": (-40.0, 60.0),
    # Dark modules in the sun get far warmer than the air
    "module_temperature": (-40.0, 90.0),
    "humidity": (0.0, 100.0),
    "illuminance": (0.0, 200_000.0),
    "irradiance": (0.0, 1_500.0),
    "precipitation": (0.0, 500.0),
}
# Smallest deviation from the neighbours that counts as spike; brightness and rain
# change quickly by nature and are not checked for spikes
SPIKE_MIN = {"temperature": 4.0, "module_temperature": 15.0, "humidity": 20.0}
SPIKE_WINDOW = timedelta(minutes=15)
SPIKE_MIN_NEIGHBOURS = 2
# Robust spread to deviation: 1.4826 * MAD estimates the standard deviation
SPIKE_SIGMAS = 5.0
# Unchanged for longer is a hanging sensor; even a calm cloudy night moves by 0.1 K within hours
STUCK_AFTER = {
    "temperature": timedelta(hours=8),
    "module_temperature": timedelta(hours=8),
    "humidity": timedelta(hours=12),
    # Dark nights are constant; a constant brightness above zero is not
    "illuminance": timedelta(hours=3),
    "irradiance": timedelta(hours=3),
}
# Unavailable for up to this long keeps the last value
BRIDGE = timedelta(minutes=30)
# No source changed for this long: Home Assistant may not have recorded
OFFLINE_AFTER = timedelta(hours=1)


@dataclass(kw_only=True)
class Segment:
    """A value holding from start to end."""

    start: datetime
    end: datetime
    value: float | None
    reason: str | None = None


@dataclass(kw_only=True)
class Quality:
    """How much of a period the learning can use, and why the rest not."""

    hours: float = 0.0
    # Home Assistant did not record: neither good nor bad, not part of the hours
    offline_hours: float = 0.0
    valid_hours: float = 0.0
    out_of_range: int = 0
    spikes: int = 0
    stuck_hours: float = 0.0
    gaps: int = 0
    gap_hours: float = 0.0
    segments: list[Segment] = field(default_factory=list)

    @property
    def share(self) -> float | None:
        """Share of the period with plausible values."""
        return self.valid_hours / self.hours if self.hours else None

    def as_dict(self) -> dict[str, Any]:
        """Summary without the segments."""
        return {
            "hours": round(self.hours, 1),
            "offline_hours": round(self.offline_hours, 1),
            "valid_share": round(self.share, 3) if self.share is not None else None,
            "out_of_range": self.out_of_range,
            "spikes": self.spikes,
            "stuck_hours": round(self.stuck_hours, 1),
            "gaps": self.gaps,
            "gap_hours": round(self.gap_hours, 1),
        }


def to_canonical(value: float, unit: str | None) -> float:
    """Temperatures in °C; other units stay."""
    if unit == "°F":
        return (value - 32) * 5 / 9
    if unit == "K":
        return value - 273.15
    return value


def kind_of(source: str, unit: str | None) -> str:
    """Kind of check for a learning source key and the sensor's unit."""
    if source == "irradiance":
        return "irradiance" if unit == "W/m²" else "illuminance"
    return {"outdoor_temperature": "temperature", "outdoor_humidity": "humidity"}.get(source, source)


def _spikes(values: list[tuple[datetime, float]], kind: str) -> set[int]:
    """Indices of values far from the median of their neighbours within the window (sorted by time)."""
    if kind not in SPIKE_MIN:
        return set()
    found = set()
    first = last = 0
    for i, (moment, value) in enumerate(values):
        while values[first][0] < moment - SPIKE_WINDOW:
            first += 1
        while last + 1 < len(values) and values[last + 1][0] <= moment + SPIKE_WINDOW:
            last += 1
        neighbours = [v for _, v in values[first:i]] + [v for _, v in values[i + 1 : last + 1]]
        if len(neighbours) < SPIKE_MIN_NEIGHBOURS:
            continue
        middle = median(neighbours)
        spread = 1.4826 * median(abs(v - middle) for v in neighbours)
        if abs(value - middle) > max(SPIKE_MIN[kind], SPIKE_SIGMAS * spread):
            found.add(i)
    return found


def _union(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Overlapping intervals merged, clipped to start and end."""
    merged: list[tuple[datetime, datetime]] = []
    for begin, finish in sorted((max(b, start), min(f, end)) for b, f in intervals if f > start and b < end):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((begin, finish))
    return merged


def _cut(segments: list[Segment], offline: list[tuple[datetime, datetime]]) -> list[Segment]:
    """The parts of the segments outside the offline intervals."""
    for begin, finish in offline:
        parts = []
        for segment in segments:
            if segment.end <= begin or segment.start >= finish:
                parts.append(segment)
                continue
            if segment.start < begin:
                parts.append(Segment(start=segment.start, end=begin, value=segment.value, reason=segment.reason))
            if segment.end > finish:
                parts.append(Segment(start=finish, end=segment.end, value=segment.value, reason=segment.reason))
        segments = parts
    return segments


def check(
    changes: list[tuple[datetime, float | None]],
    kind: str,
    start: datetime,
    end: datetime,
    offline: list[tuple[datetime, datetime]] | None = None,
    ignore: list[tuple[datetime, datetime]] | None = None,
) -> Quality:
    """Plausible segments of recorded changes (value None: unavailable) between start and end.

    offline: intervals Home Assistant did not record; ignore: intervals the sensor is not
    expected to measure (e.g. optimizers at night). Both are left out of the judgement.
    """
    offline = _union(offline or [], start, end)
    left_out = _union([*offline, *(ignore or [])], start, end)
    quality = Quality(
        hours=(end - start) / timedelta(hours=1),
        offline_hours=sum((f - b) / timedelta(hours=1) for b, f in offline),
    )
    quality.hours -= sum((f - b) / timedelta(hours=1) for b, f in left_out)
    offline = left_out
    changes = sorted((max(m, start), v) for m, v in changes if m < end)
    known = [(m, v) for m, v in changes if v is not None]
    low, high = RANGES[kind]
    in_range = [(m, v) for m, v in known if low <= v <= high]
    quality.out_of_range = len(known) - len(in_range)
    spikes = _spikes(in_range, kind)
    quality.spikes = len(spikes)
    rejected = {(m, v) for m, v in known if not low <= v <= high} | {in_range[i] for i in spikes}
    # A rejected value does not hold: the last good one goes on, like a bridged gap
    segments: list[Segment] = []
    bounds = [*changes[1:], (end, None)]
    if changes and changes[0][0] > start:
        segments.append(Segment(start=start, end=changes[0][0], value=None, reason="gap"))
    last: float | None = None
    for (moment, value), (until, _) in zip(changes, bounds, strict=True):
        if until <= moment:
            continue
        if value is not None and (moment, value) not in rejected:
            last = value
            segments.append(Segment(start=moment, end=until, value=value))
        else:
            segments.append(Segment(start=moment, end=until, value=last, reason="bridge"))
    segments = _cut(segments, offline)
    # Several unavailable readings in a row are one gap
    merged: list[Segment] = []
    for segment in segments:
        previous = merged[-1] if merged else None
        if previous is not None and previous.reason is not None and segment.reason is not None and previous.end == segment.start:
            previous.end = segment.end
            previous.reason = "gap" if "gap" in (previous.reason, segment.reason) else "bridge"
        else:
            merged.append(segment)
    segments = merged
    for segment in segments:
        length = segment.end - segment.start
        if segment.reason in ("gap", "bridge") and length > BRIDGE:
            segment.value, segment.reason = None, "gap"
            quality.gaps += 1
            quality.gap_hours += length / timedelta(hours=1)
        elif segment.reason == "gap":
            # Too short to count as outage, but there is no value to bridge with
            segment.value = None
        elif segment.reason is None and kind in STUCK_AFTER and length > STUCK_AFTER[kind]:
            # A dark night is no hanging brightness sensor
            if not (kind in ("illuminance", "irradiance") and segment.value == 0):
                segment.value, segment.reason = None, "stuck"
                quality.stuck_hours += length / timedelta(hours=1)
    quality.segments = [s for s in segments if s.value is not None]
    quality.valid_hours = sum((s.end - s.start) / timedelta(hours=1) for s in quality.segments)
    return quality


async def source_changes(
    client: HAClient, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float | None]]:
    """Recorded changes of a sensor in its canonical unit; None while unavailable."""
    result = await client.command(
        "history/history_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        entity_ids=[entity_id],
        minimal_response=True,
        # The first state brings the unit
        no_attributes=False,
        significant_changes_only=False,
    )
    rows = result.get(entity_id, [])
    unit = (rows[0].get("a") or {}).get("unit_of_measurement") if rows else None
    changes: list[tuple[datetime, float | None]] = []
    for row in rows:
        moment = datetime.fromtimestamp(row.get("lu") or row.get("lc") or 0, start.tzinfo)
        try:
            value: float | None = to_canonical(float(row.get("s")), unit)
        except TypeError, ValueError:
            value = None
        changes.append((moment, value))
    return changes


def quiet_intervals(series: list[list[tuple[datetime, float | None]]], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Intervals of at least OFFLINE_AFTER in which none of the series changed."""
    moments = sorted({m for changes in series for m, _ in changes if start <= m <= end} | {start, end})
    return [(a, b) for a, b in itertools.pairwise(moments) if b - a >= OFFLINE_AFTER]


async def recorder_offline(
    client: HAClient, reference: str, quiet: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """The quiet intervals in which a frequently changing reference (e.g. grid power) did not change either."""
    offline = []
    for begin, finish in quiet:
        result = await client.command(
            "history/history_during_period",
            start_time=begin.isoformat(),
            end_time=finish.isoformat(),
            entity_ids=[reference],
            minimal_response=True,
            no_attributes=True,
            significant_changes_only=False,
            include_start_time_state=False,
        )
        if not result.get(reference):
            offline.append((begin, finish))
    return offline


def combined_quality(judged: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One summary for several sensors of the same quantity (e.g. the optimizers of a plane)."""
    if not judged:
        return None
    if len(judged) == 1:
        return judged[0]
    shares = [j["valid_share"] for j in judged if j["valid_share"] is not None]
    return {
        "hours": max(j["hours"] for j in judged),
        "offline_hours": max(j["offline_hours"] for j in judged),
        "valid_share": round(median(shares), 3) if shares else None,
        "out_of_range": sum(j["out_of_range"] for j in judged),
        "spikes": sum(j["spikes"] for j in judged),
        "stuck_hours": round(median(j["stuck_hours"] for j in judged), 1),
        "gaps": round(median(j["gaps"] for j in judged)),
        "gap_hours": round(median(j["gap_hours"] for j in judged), 1),
        "sensors": len(judged),
    }
