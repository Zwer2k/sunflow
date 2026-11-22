"""Writing the set value (design/heat_pump.md, 8.5, step 3 part 4.2), a layer behind the protection layer.

The protection layer (guard.py) computes every minute the set value it wants and whether that is a
protection step (start, tracking); this layer decides when it is written:
- the first change after a calm spell at once (leading edge);
- within the minimum spacing only the latest value is kept (pending), written once the spacing passed,
  if it still differs from the written one - 5 min for normal writes, 2 min for protection steps
  (profile flow_control.writes: min_interval, protect_interval), counted from the last write of either;
- a normal value only when it differs by more than the deadband, a protection step whenever it raises;
- rounded to the entity's step the safe way (protection steps up, normal values to the nearest step),
  within the limits of the set value;
- "write now" skips the spacing (not the budget) for one tick; the wish lapses when nothing can be
  written in the next tick (nothing pending, budget, no valid state, writing not active), with the reason
  in the status and the journal.
- the budget (budget.py, step 3 part 4.3): a value it holds back waits without a time (held budget);
  a protection step held back sets the block instead (guard.py), each exhaustion once a day in the journal.
In the shadow it works on the virtual writes (counted virtually against the budget) and adds nothing to
the journal for a write (events as before); a real write gets a journal entry with its reason. The call to
the heat pump itself is the shadow control's (shadow.py, step 3 part 4.7, only when the user switched it on).
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sunflow.i18n import number, t

from .budget import DAY, EMERGENCY, HOUR, WriteBudget
from .limits import ControlSettings, clamp, round_near, round_up

if TYPE_CHECKING:
    from .guard import Guard, GuardInput, GuardResult

_EPS = 1e-6

# Why a manual write ("write now") lapsed
NOT_ACTIVE = "not_active"
NOTHING_PENDING = "nothing_pending"
INACTIVE = "inactive"
BUDGET = "budget"
# Why a wanted value waits
SPACING = "spacing"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


@dataclass(frozen=True, kw_only=True)
class Request:
    """The set value the protection layer wants written this minute."""

    value_c: float
    reason: str
    # Protection step (start, tracking): shorter spacing, no deadband, emergency budget (part 4.3)
    protect: bool


@dataclass(frozen=True, kw_only=True)
class Pending:
    """A value that would be written but waits: for the spacing (due_at) or the budget (held)."""

    value_c: float
    reason: str
    protect: bool
    due_at: datetime | None
    held: str
    # With held budget: which limit (day, emergency, hour)
    budget: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """For the status."""
        return {
            "value_c": self.value_c, "reason": self.reason, "protect": self.protect,
            "due_at": _iso(self.due_at) if self.due_at else None, "held": self.held, "budget": self.budget,
        }


@dataclass(kw_only=True)
class WriteOutcome:
    """What the write layer decided this minute."""

    # The value written (rounded) with its reason; None when nothing is written
    write: Request | None = None
    manual: bool = False
    pending: Pending | None = None
    # A "write now" decided this tick: {"requested_at", "at", "done", "reason"}
    manual_result: dict[str, Any] | None = None
    notes: list[tuple[str, bool]] = field(default_factory=list)


# Budget (part 4.3, budget.WriteBudget): None when the write may go ahead, else which limit holds it back
Budget = Callable[[Request, datetime], str | None]


def reason_text(reason: str, manual: bool = False) -> str:
    """The reason of a write for the journal."""
    if manual:
        return t("manuell (jetzt schreiben)")
    return {
        "normal": t("Vorlauf-Ziel"),
        "start": t("Schutzschritt Start"),
        "track": t("Schutzschritt Nachführen"),
    }.get(reason, reason)


def lapse_text(reason: str) -> str:
    """Why a "write now" lapsed, for the journal."""
    return {
        NOT_ACTIVE: t("Regeln nicht eingeschaltet (nur Lernen)"),
        NOTHING_PENDING: t("kein ausstehender Wert"),
        INACTIVE: t("kein gültiger Zustand zum Schreiben"),
        BUDGET: t("Budget erschöpft"),
    }.get(reason, reason)


class Writer:
    """The write layer of one heat pump; only a pending "write now" is kept, in memory."""

    def __init__(self) -> None:
        """Nothing requested."""
        self.manual_at: datetime | None = None
        self.manual_result: dict[str, Any] | None = None
        self.pending: Pending | None = None

    def request_now(self, now: datetime) -> None:
        """"Write now": the next tick writes the pending value without waiting for the spacing."""
        self.manual_at = now

    def step(
        self,
        *,
        now: datetime,
        request: Request | None,
        written_c: float | None,
        written_at: datetime | None,
        settings: ControlSettings,
        limits: tuple[float, float] | None,
        active: bool,
        inactive: bool = False,
        budget: Budget | None = None,
    ) -> WriteOutcome:
        """Decide whether the wanted value is written now, waits, or is not needed."""
        outcome = WriteOutcome()
        manual_at, self.manual_at = self.manual_at, None
        lapse: str | None = None
        if manual_at is not None and not active:
            lapse = NOT_ACTIVE
        wanted = self._wanted(request, written_c, settings, limits)
        if wanted is None:
            self.pending = None
            if manual_at is not None and lapse is None:
                lapse = INACTIVE if inactive else NOTHING_PENDING
        else:
            spacing = timedelta(seconds=settings.protect_interval_s if wanted.protect else settings.min_interval_s)
            due = written_at + spacing if written_at is not None else None
            manual = manual_at is not None and lapse is None
            if due is not None and now < due and not manual:
                self.pending = Pending(value_c=wanted.value_c, reason=wanted.reason, protect=wanted.protect, due_at=due, held=SPACING)
            elif budget is not None and (limit := budget(wanted, now)) is not None:
                self.pending = Pending(
                    value_c=wanted.value_c, reason=wanted.reason, protect=wanted.protect, due_at=None, held=BUDGET, budget=limit,
                )
                if manual:
                    lapse = BUDGET
            else:
                self.pending = None
                outcome.write, outcome.manual = wanted, manual
                if active:
                    outcome.notes.append((t(
                        "Max T-Vorlauf geschrieben: {value} °C (vorher {before} °C), Grund: {reason}.",
                        value=number(wanted.value_c), before=number(written_c or 0.0), reason=reason_text(wanted.reason, manual),
                    ), False))
        outcome.pending = self.pending
        if manual_at is not None:
            done = outcome.manual
            outcome.manual_result = {"requested_at": _iso(manual_at), "at": _iso(now), "done": done, "reason": None if done else lapse}
            self.manual_result = outcome.manual_result
            if not done and lapse is not None:
                outcome.notes.append((t("„Jetzt schreiben“ verfallen: {why}.", why=lapse_text(lapse)), False))
        return outcome

    @staticmethod
    def _wanted(
        request: Request | None, written_c: float | None, settings: ControlSettings, limits: tuple[float, float] | None,
    ) -> Request | None:
        """The request rounded the safe way within the limits; None when it need not be written."""
        if request is None or written_c is None:
            return None
        step = settings.step
        value = round_up(request.value_c, step) if request.protect else round_near(request.value_c, step)
        if limits is not None:
            value = clamp(value, limits[0], limits[1], step)
        if request.protect:
            # A protection step only raises; any raise counts (no deadband)
            if value <= written_c + _EPS:
                return None
        elif abs(value - written_c) <= settings.deadband + _EPS:
            return None
        return Request(value_c=value, reason=request.reason, protect=request.protect)


def budget_text(limit: str, limits: Any, shadow: bool) -> str:
    """The journal entry when the budget first holds a value back on a day."""
    if limit == DAY:
        message = t(
            "Schreibbudget für heute erschöpft ({n} Schreibvorgänge): normale Werte erst morgen wieder, Schutzschritte über das Notkontingent.",
            n=limits.per_day,
        )
    elif limit == EMERGENCY:
        message = t(
            "Notkontingent für heute erschöpft ({n} Schreibvorgänge zusätzlich): Raumthermostat-Sperre statt Schutzschritt.",
            n=limits.emergency_per_day,
        )
    elif limit == HOUR:
        message = t("Höchstens {n} Schreibvorgänge je Stunde erreicht: Werte warten, Raumthermostat-Sperre statt Schutzschritt.", n=limits.per_hour)
    else:
        message = t("Budget erschöpft")
    return t("Nur Lernen: {message}", message=message) if shadow else message


def write_step(guard: "Guard", writer: Writer, data: "GuardInput", *, active: bool = False, budget: Budget | None = None) -> "GuardResult":
    """One minute: the protection layer decides what it wants, the write layer when it is written.

    A protection step the budget holds back sets the block instead (guard.budget_block); the block the
    budget set is released once a protection step may be written again."""
    data = replace(data, writing=active)
    if budget is not None:
        free = budget(Request(value_c=0.0, reason="start", protect=True), data.at) is None
        data = replace(data, protect_budget=free)
    result = guard.step(data)
    outcome = writer.step(
        now=data.at, request=result.request, written_c=guard.written_c, written_at=guard.written_at,
        settings=data.settings, limits=result.limits, active=active, inactive=bool(result.inactive), budget=budget,
    )
    if outcome.write is not None:
        guard.record_write(result, data.at, outcome.write.value_c, outcome.write.reason, shadow=not active, manual=outcome.manual)
        if active and outcome.notes:
            # The journal entry of the write (the first note): taken back with it if writing for real fails
            result.write_notes.append((result.writes[-1], outcome.notes[0]))
        if isinstance(budget, WriteBudget):
            budget.wrote(data.at)
    pending = outcome.pending
    if pending is not None and pending.held == BUDGET:
        if pending.protect:
            blocked = guard.budget_block(result, data)
            if blocked:
                # The block protects instead: nothing waits for the budget any more
                writer.pending = pending = None
            elif guard.budget_unprotected(data):
                message = t("Schutzschritt wegen des Budgets nicht möglich, die Raumthermostat-Sperre ebenfalls nicht (keine Sperre oder ihre Mindestdauer): die Wärmepumpe kann abschalten.")
                result.notes.append((t("Nur Lernen: {message}", message=message) if not active else message, True))
        limit = outcome.pending.budget if outcome.pending is not None else None
        if isinstance(budget, WriteBudget) and limit is not None and budget.notice(limit, data.at):
            result.notes.append((budget_text(limit, budget.limits, not active), True))
    result.pending = pending
    result.manual = outcome.manual_result
    result.notes.extend(outcome.notes)
    return result
