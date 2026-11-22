"""Foreign writers of the set value (design/heat_pump.md, 8.5, step 3 part 4.4).

Sunflow follows the real Max T-Vorlauf of the heat pump every minute. A change it did not write itself
(an automation, a script, the ESP regulation, by hand) is a foreign write:
- the value Sunflow takes as written (in the shadow the virtual one) follows the real value, and the
  minimum spacing runs from the change, so Sunflow does not answer it at once;
- the first foreign change of an episode goes into the journal (warning); further ones are counted in
  the status, not noted one by one; the episode ends FOREIGN_EPISODE_S after the last change (journal);
- while an episode runs, the setup summary names it.
Its own writes Sunflow recognizes only once it writes for real (step 3 part 4.7): a change to the value
it wrote within OWN_WRITE_CONFIRM_S of that write. In the shadow every real change is foreign.
A change smaller than half a step of the entity is no change; an unknown value is skipped (the last
known one stays). The last value seen is kept in heat_pump_state.json, so a change while Sunflow was not
running counts as well.

ESP regulation (optional role regulated_setpoint, key supply_setpoint_regulated of the CAN component):
above 0 the component regulates Max T-Vorlauf itself; Sunflow then writes nothing (guard.py, inactive
esp_regulation), says so once in the journal, in the status and the summary. 0 or unknown: not active,
as the component itself treats it.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sunflow.i18n import number, t

from .defaults import FOREIGN_EPISODE_S, GUARD_RECENT_MAX, OWN_WRITE_CONFIRM_S

FOREIGN_EVENT = "foreign_write"
REGULATED_ROLE = "regulated_setpoint"
EPISODE = timedelta(seconds=FOREIGN_EPISODE_S)
CONFIRM = timedelta(seconds=OWN_WRITE_CONFIRM_S)
_EPS = 1e-6


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _time(text: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def regulation_active(value: float | None) -> bool:
    """The ESP regulates itself: its regulated setpoint is above 0 (0, unknown: it does not)."""
    return value is not None and value > 0


@dataclass(kw_only=True)
class Episode:
    """Foreign changes following each other (at most FOREIGN_EPISODE_S apart)."""

    since: datetime
    last_at: datetime
    count: int
    from_c: float | None
    value_c: float

    def as_dict(self) -> dict[str, Any]:
        """For the state file and the status."""
        return {"since": _iso(self.since), "last_at": _iso(self.last_at), "count": self.count, "from_c": self.from_c, "value_c": self.value_c}

    @classmethod
    def from_dict(cls, data: Any) -> "Episode | None":
        """From the state file; None if unreadable."""
        if not isinstance(data, dict):
            return None
        since, last_at, value = _time(data.get("since")), _time(data.get("last_at")), _float(data.get("value_c"))
        count = data.get("count")
        if since is None or last_at is None or value is None or not isinstance(count, int) or isinstance(count, bool):
            return None
        return cls(since=since, last_at=last_at, count=max(count, 1), from_c=_float(data.get("from_c")), value_c=value)


@dataclass(kw_only=True)
class ForeignStep:
    """What the watch saw this minute."""

    # The foreign change (event for heat_pump_samples); None when there was none
    change: dict[str, Any] | None = None
    # A change to the value Sunflow wrote (confirmed, from part 4.7)
    own: bool = False
    notes: list[tuple[str, bool]] = field(default_factory=list)


class ForeignWatch:
    """Foreign changes of the set value and the ESP regulation of one heat pump; kept in the guard's state."""

    def __init__(self, state: Any = None) -> None:
        """From the stored state (unreadable parts are dropped)."""
        state = state if isinstance(state, dict) else {}
        self.seen_c = _float(state.get("seen_c"))
        self.expect_c = _float(state.get("expect_c"))
        self.expect_at = _time(state.get("expect_at"))
        self.episode = Episode.from_dict(state.get("episode"))
        regulation = state.get("regulation") if isinstance(state.get("regulation"), dict) else {}
        self.regulation_since = _time(regulation.get("since"))
        self.regulation_c = _float(regulation.get("value_c"))
        self.recent: list[dict[str, Any]] = [c for c in state.get("recent") or [] if isinstance(c, dict)][-GUARD_RECENT_MAX:]

    def as_dict(self) -> dict[str, Any]:
        """For heat_pump_state.json."""
        return {
            "seen_c": self.seen_c,
            "expect_c": self.expect_c,
            "expect_at": _iso(self.expect_at) if self.expect_at else None,
            "episode": self.episode.as_dict() if self.episode else None,
            "regulation": {"since": _iso(self.regulation_since), "value_c": self.regulation_c} if self.regulation_since else None,
            "recent": self.recent[-GUARD_RECENT_MAX:],
        }

    @property
    def regulating(self) -> bool:
        """The ESP regulation was active at the last minute."""
        return self.regulation_since is not None

    def expect(self, value: float, now: datetime) -> None:
        """Sunflow wrote value to the heat pump for real: the change to it is its own."""
        self.expect_c, self.expect_at = value, now

    def step(self, now: datetime, actual: float | None, regulated: float | None, step: float, written_c: float | None) -> ForeignStep:
        """Follow the real set value and the ESP regulation for one minute."""
        result = ForeignStep()
        self._regulation(now, regulated, result)
        if self.episode is not None and now - self.episode.last_at > EPISODE:
            hours = round(EPISODE.total_seconds() / 3600)
            result.notes.append((t(
                "Seit {hours} h keine Änderung von Max T-Vorlauf von außen mehr (zuvor {n}× geändert).", hours=hours, n=self.episode.count,
            ), False))
            self.episode = None
        if self.expect_at is not None and now - self.expect_at > CONFIRM:
            self.expect_c = self.expect_at = None
        if actual is None:
            return result
        if self.seen_c is None:
            # First value: what the heat pump has, without a message
            self.seen_c = actual
            return result
        tolerance = max(step, 0.0) / 2
        if abs(actual - self.seen_c) <= tolerance + _EPS:
            return result
        before, self.seen_c = self.seen_c, actual
        if self.expect_c is not None and abs(actual - self.expect_c) <= tolerance + _EPS:
            self.expect_c = self.expect_at = None
            result.own = True
            return result
        self.expect_c = self.expect_at = None
        regulating = self.regulating
        change = {
            "at": _iso(now), "kind": FOREIGN_EVENT, "from_c": before, "value_c": actual, "written_c": written_c, "regulation": regulating,
        }
        result.change = change
        self.recent.append(change)
        del self.recent[:-GUARD_RECENT_MAX]
        if self.episode is None:
            self.episode = Episode(since=now, last_at=now, count=1, from_c=before, value_c=actual)
            message = t(
                "Max T-Vorlauf von außen geändert: {before} → {value} °C, nicht von Sunflow (Automation, Skript, ESP-Regelung oder von Hand). Sunflow geht ab jetzt von diesem Wert aus; weitere Änderungen zählt es, meldet sie aber nicht einzeln.",
                before=number(before), value=number(actual),
            )
            if regulating:
                message = t("{message} Die ESP-Regelung ist aktiv.", message=message)
            result.notes.append((message, True))
        else:
            self.episode.last_at, self.episode.count, self.episode.value_c = now, self.episode.count + 1, actual
        return result

    def _regulation(self, now: datetime, regulated: float | None, result: ForeignStep) -> None:
        active = regulation_active(regulated)
        if active:
            self.regulation_c = regulated
            if self.regulation_since is None:
                self.regulation_since = now
                result.notes.append((t(
                    "ESP-Regelung des Vorlaufs aktiv ({value} °C): die ESP-Komponente regelt Max T-Vorlauf selbst; Sunflow schreibt nicht, solange sie aktiv ist. Zum Abschalten den Wert in ESPHome auf 0 setzen.",
                    value=number(regulated or 0.0),
                ), True))
        elif self.regulation_since is not None:
            self.regulation_since, self.regulation_c = None, None
            result.notes.append((t("ESP-Regelung des Vorlaufs beendet."), False))

    def status(self, now: datetime) -> dict[str, Any]:
        """For the status: the running episode, the latest foreign changes, the ESP regulation."""
        return {
            "seen_c": self.seen_c,
            "episode": self.episode.as_dict() if self.episode and now - self.episode.last_at <= EPISODE else None,
            "recent": list(reversed(self.recent)),
            "regulation": {
                "active": self.regulating,
                "value_c": self.regulation_c,
                "since": _iso(self.regulation_since) if self.regulation_since else None,
            },
            "own_pending": {"value_c": self.expect_c, "at": _iso(self.expect_at)} if self.expect_at else None,
        }
