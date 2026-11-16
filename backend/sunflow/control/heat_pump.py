"""Steering a heat pump: which state (normal, boost, block, forced) and what to write for it.

Sunflow only recommends: SG-Ready contacts, a select, a raised setpoint, the PV surplus or a
boost switch. The heat pump's own control decides what that means.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
import math
from typing import Any

from sunflow.config.models import BuiltinRod, HeatPumpSettings, HeatPumpState, RodState
from sunflow.i18n import N_, t
from sunflow.planning.house_model import HourRow, HouseModel

PREFIX = "hp:"
RELEASE = f"{PREFIX}normal"
# SG-Ready contacts (1, 2) per state
CONTACTS: dict[str, tuple[bool, bool]] = {
    "block": (True, False),
    "normal": (False, False),
    "boost": (False, True),
    "forced": (True, True),
}
# Methods that can block the heat pump
CAN_BLOCK = {"sg_relays", "sg_select", "setpoint"}
CAN_FORCE = {"sg_relays", "sg_select"}
# While boosting on surplus, more import than this means the surplus is not there
IMPORT_TOLERANCE_W = 300.0
# The flash guard allows 10 writes per entity and day: blocking stops at 6 changes, so releasing
# the rod (cold, by hand, handing back) always has writes left
MAX_ROD_CHANGES = 6
SLOTS_PER_HOUR = 4


def _switch(on: bool, inverted: bool = False) -> str:
    return "on" if on != inverted else "off"


def rod_runtime(state: str, rod: BuiltinRod | None) -> dict[str, Any]:
    """Variables of the built-in rod's steps; a state its entity cannot show falls back to free."""
    if rod is None:
        return {}
    options = rod.options
    normal = rod.limit_normal or 0.0
    limit = {"blocked": rod.limit_blocked, "max": rod.limit_max if rod.limit_max is not None else normal}
    return {
        "rod_allow": _switch(state != "blocked"),
        "rod_block": _switch(state == "blocked"),
        "rod_option": options.get(state) or options.get("free", ""),  # type: ignore[call-overload]
        "rod_limit": limit.get(state, normal),
    }


def runtime(state: str, settings: HeatPumpSettings, surplus_w: float = 0.0, rod: str = "free") -> dict[str, Any]:
    """Variables of the profile's steps for a state; only the method's own roles are bound."""
    contact1, contact2 = CONTACTS[state]
    if settings.method == "sg_relay":
        # Only contact 2 is wired to Sunflow: boost or normal
        contact1, contact2 = False, state in ("boost", "forced")
    normal = settings.setpoint_normal or 0.0
    setpoint = {"boost": normal + settings.boost_k, "forced": normal + settings.boost_k, "block": normal - settings.block_k}
    options = settings.options
    return {
        "sg_contact1": _switch(contact1, settings.contact1_inverted),
        "sg_contact2": _switch(contact2, settings.contact2_inverted),
        "sg_option": options.get(state) or options.get("normal", ""),
        "setpoint": setpoint.get(state, normal),
        "surplus_w": round(surplus_w) if state in ("boost", "forced") else 0,
        "boost_switch": _switch(state in ("boost", "forced")),
    } | rod_runtime(rod, settings.rod)


@dataclass(kw_only=True)
class HeatPumpTrack:
    """What Sunflow last chose for a heat pump, for the minimum time per state and the block limits."""

    state: str = "normal"
    since: datetime | None = None
    block_day: date | None = None
    blocked: timedelta = field(default_factory=timedelta)
    last_tick: datetime | None = None
    rod: str = "free"
    rod_since: datetime | None = None
    # Rod changes per day: its settings count against the daily flash write limit
    rod_day: date | None = None
    rod_changes: int = 0

    def advance(self, now: datetime, tz: tzinfo) -> None:
        """Count the time blocked since the last step; a new day starts from zero."""
        today = now.astimezone(tz).date()
        if self.block_day != today:
            self.block_day, self.blocked = today, timedelta()
        if self.state == "block" and self.last_tick is not None:
            self.blocked += now - self.last_tick
        self.last_tick = now


@dataclass(frozen=True, kw_only=True)
class HouseView:
    """The learned house now: room temperature (the reference room's), outside, and the comfort band."""

    model: HouseModel
    # Weighted mean of the rooms (what the model knows) and the reference room's offset to it
    inside: float
    offset: float = 0.0
    outside: float
    upper: float
    # The lower bound at a moment (night setback)
    lower: Callable[[datetime], float]

    @property
    def reference(self) -> float:
        """The temperature the comfort band applies to."""
        return self.inside + self.offset

    def coldest_margin(self, now: datetime, minutes: float) -> tuple[float, float]:
        """Without heat or sun for this long: the smallest distance to the lower bound and the bound there."""
        inside = self.inside
        row = HourRow(start=now, inside=inside, outside=self.outside, heat=0.0, sun=0.0)
        model = self.model
        if model.b is None and model.d > 0:
            # Without a heat measurement the gains hold the usual heating too: none while blocked
            model = replace(model, d=0.0)
        worst = (inside + self.offset - self.lower(now), self.lower(now))
        for hour in range(1, max(math.ceil(minutes / 60), 1) + 1):
            inside = model.step(inside, row)
            moment = now + timedelta(hours=hour)
            margin = inside + self.offset - self.lower(moment)
            if margin < worst[0]:
                worst = (margin, self.lower(moment))
        return worst


def _rank(slots: list[Any], index: int, tz: tzinfo) -> tuple[int, int]:
    """Position of the slot's price among the slots of its local day: (cheaper ones, dearer ones)."""
    day = slots[index].start.astimezone(tz).date()
    prices = [s.price for s in slots if s.start.astimezone(tz).date() == day]
    price = slots[index].price
    return sum(p < price for p in prices), sum(p > price for p in prices)


@dataclass(frozen=True, kw_only=True)
class Choice:
    """A state with why, and the surplus (W) to report while boosting."""

    state: HeatPumpState
    reason: str
    surplus_w: float = 0.0
    # A block ends because of its limits: not held back by the minimum time
    limit: bool = False


def _dear_minutes(slots: list[Any], index: int, tz: tzinfo, hours: int) -> float:
    """How long the run of dear slots from now lasts (minutes)."""
    minutes = 0.0
    for i in range(index, len(slots)):
        if _rank(slots, i, tz)[1] >= hours * SLOTS_PER_HOUR:
            break
        minutes += (slots[i].end - max(slots[i].start, slots[index].start)).total_seconds() / 60
    return minutes


# The plan's heat pump power counts as preheating or pause beyond this much from what it would draw anyway
PLAN_MARGIN_W = 300.0
PLAN_MARGIN_SHARE = 0.25


def _heat_plan(slot: Any, name: str | None) -> str | None:
    """What the plan with the house wants in a slot: boost (preheat), block (pause), normal; None without it."""
    planned = getattr(slot, "heat_w", {}).get(name) if name else None
    if planned is None:
        return None
    base = slot.heat_base_w.get(name, 0.0)
    margin = max(PLAN_MARGIN_W, PLAN_MARGIN_SHARE * base)
    if planned > base + margin:
        return "boost"
    if planned < base - margin:
        return "block"
    return "normal"


def _planned_pause_minutes(slots: list[Any], index: int, name: str) -> float:
    """How long the planned pause from now lasts (minutes)."""
    minutes = 0.0
    for slot in slots[index:]:
        if _heat_plan(slot, name) != "block":
            break
        minutes += (slot.end - max(slot.start, slots[index].start)).total_seconds() / 60
    return minutes


def wanted_state(
    settings: HeatPumpSettings, slots: list[Any], now: datetime, tz: tzinfo, track: HeatPumpTrack, grid_w: float | None,
    house: HouseView | None = None, season: bool = True, name: str | None = None,
) -> Choice:
    """The state the plan suggests now; with a learned house, within its comfort band.

    A plan made with the house (name: the heat pump's) decides preheating and pauses itself;
    otherwise surplus, cheap and dear hours do.
    """
    index = next((i for i, s in enumerate(slots) if s.start <= now < s.end), None)
    if index is None:
        return Choice(state="normal", reason=t("kein aktueller Plan"))
    slot = slots[index]
    surplus = max(slot.export_w, 0.0) + max(getattr(slot, "curtail_w", 0.0), 0.0)
    method = settings.method
    cheaper, dearer = _rank(slots, index, tz)
    if settings.use_forced and method in CAN_FORCE and slot.price < 0:
        return Choice(state="forced", reason=t("negativer Strompreis"), surplus_w=surplus)
    planned = _heat_plan(slot, name)
    # Planned surplus that is there: imports while boosting mean the forecast was too high
    surplus_there = grid_w is None or grid_w < IMPORT_TOLERANCE_W or track.state not in ("boost", "forced")
    if planned is not None:
        boost = (planned == "boost", t("Vorheizen nach Plan"))
        pause = planned == "block"
    else:
        on_surplus = surplus >= settings.min_surplus_w and surplus_there
        cheap = bool(settings.cheap_hours) and cheaper < settings.cheap_hours * SLOTS_PER_HOUR
        boost = (on_surplus or cheap, t("PV-Überschuss") if on_surplus else t("günstige Stunde"))
        pause = bool(settings.expensive_hours) and dearer < settings.expensive_hours * SLOTS_PER_HOUR
    if boost[0]:
        # In the heating season boosting heats the rooms: not beyond the band (in summer it is hot water)
        if house is not None and season and house.reference >= house.upper:
            return Choice(
                state="normal", reason=t("Haus warm genug ({temp} °C)", temp=f"{house.reference:.1f}"), surplus_w=surplus
            )
        return Choice(state="boost", reason=boost[1], surplus_w=surplus)
    if pause and planned is not None and house is None:
        # Planned with the house, but no room values now: no pause without the comfort check
        return Choice(state="normal", reason=t("Pause geplant, aber keine Raumtemperatur"))
    if pause and settings.use_block and method in CAN_BLOCK:
        running = now - track.since if track.state == "block" and track.since else timedelta()
        if running >= timedelta(minutes=settings.block_max_min):
            return Choice(state="normal", reason=t("Sperre lange genug"), limit=True)
        if track.blocked >= timedelta(minutes=settings.block_day_max_min):
            return Choice(state="normal", reason=t("Sperrzeit für heute erreicht"), limit=True)
        if house is not None and season:
            ahead = _planned_pause_minutes(slots, index, name) if planned is not None else _dear_minutes(
                slots, index, tz, settings.expensive_hours
            )
            left = min(
                settings.block_max_min - running.total_seconds() / 60,
                settings.block_day_max_min - track.blocked.total_seconds() / 60,
                ahead,
            )
            margin, bound = house.coldest_margin(now, left)
            if margin < 0:
                return Choice(
                    state="normal", reason=t("Sperre würde das Haus unter {temp} °C kühlen", temp=f"{bound:g}"), limit=True
                )
        return Choice(state="block", reason=t("Pause nach Plan") if planned is not None else t("teure Stunde"))
    return Choice(state="normal", reason=t("nach Plan: normal"))


def next_state(
    settings: HeatPumpSettings, slots: list[Any], now: datetime, tz: tzinfo, track: HeatPumpTrack, grid_w: float | None,
    house: HouseView | None = None, season: bool = True, name: str | None = None,
) -> Choice:
    """The wanted state, kept back until the current one ran its minimum time (compressor protection).

    Ending a block too long or over the daily limit is never held back.
    """
    track.advance(now, tz)
    choice = wanted_state(settings, slots, now, tz, track, grid_w, house, season, name)
    young = track.since is not None and now - track.since < timedelta(minutes=settings.min_state_min)
    if choice.state != track.state and young and not choice.limit:
        held: HeatPumpState = track.state  # type: ignore[assignment]
        return Choice(
            state=held, reason=t("{state} bleibt noch (Mindestdauer)", state=state_label(held)), surplus_w=choice.surplus_w
        )
    if choice.state != track.state:
        track.state, track.since = choice.state, now
    elif track.since is None:
        track.since = now
    return choice


@dataclass(frozen=True, kw_only=True)
class RodChoice:
    """What the built-in rod may do now, and why."""

    state: RodState
    reason: str
    # Released for safety or by hand: not held back by the minimum time
    urgent: bool = False


def wanted_rod(
    rod: BuiltinRod, slots: list[Any], now: datetime, season: bool, outside_c: float | None, min_surplus_w: float,
    cold: bool = False,
) -> RodChoice:
    """Block the rod while the heat pump copes; release it when cold, cheap, sunny or by hand.

    cold: a room below the comfort band, the heat pump does not keep up (or is broken).
    Without a current plan (no slots) the heat pump decides.
    """
    if rod.released_until is not None and now < rod.released_until:
        return RodChoice(state="max", reason=t("von Hand freigegeben"), urgent=True)
    mode = rod.heating_mode if season else rod.summer_mode
    if mode == "auto":
        return RodChoice(state="free", reason=t("Wärmepumpe entscheidet"), urgent=True)
    if cold:
        return RodChoice(state="free", reason=t("Haus unter dem Komfortband – Wärmepumpe reicht nicht"), urgent=True)
    if not slots:
        return RodChoice(state="free", reason=t("kein aktueller Plan – Wärmepumpe entscheidet"), urgent=True)
    if outside_c is None:
        # Blocking blind could leave the house cold below the bivalence point
        return RodChoice(state="free", reason=t("keine Außentemperatur – Wärmepumpe entscheidet"), urgent=True)
    if outside_c < rod.bivalence_c:
        return RodChoice(state="free", reason=t("kälter als {limit} °C", limit=f"{rod.bivalence_c:g}"), urgent=True)
    slot = next((s for s in slots if s.start <= now < s.end), None)
    if slot is not None and slot.price < 0:
        return RodChoice(state="max", reason=t("negativer Strompreis"))
    surplus = max(slot.export_w, 0.0) + max(getattr(slot, "curtail_w", 0.0), 0.0) if slot is not None else 0.0
    if mode == "pv" and surplus >= min_surplus_w:
        return RodChoice(state="max", reason=t("PV-Überschuss"))
    return RodChoice(state="blocked", reason=t("Wärmepumpe reicht"))


def next_rod(
    rod: BuiltinRod, slots: list[Any], now: datetime, season: bool, outside_c: float | None, min_surplus_w: float,
    track: HeatPumpTrack, cold: bool = False,
) -> RodChoice:
    """The wanted rod state, changed at most every min_state_min (its settings sit in flash memory)."""
    choice = wanted_rod(rod, slots, now, season, outside_c, min_surplus_w, cold)
    today = now.date()
    if track.rod_day != today:
        track.rod_day, track.rod_changes = today, 0
    young = track.rod_since is not None and now - track.rod_since < timedelta(minutes=rod.min_state_min)
    if choice.state != track.rod and young and not choice.urgent:
        held: RodState = track.rod  # type: ignore[assignment]
        return RodChoice(state=held, reason=t("{state} bleibt noch (Mindestdauer)", state=rod_label(held)))
    if choice.state == "blocked" != track.rod and track.rod_changes >= MAX_ROD_CHANGES:
        choice = RodChoice(state="free", reason=t("genug Wechsel für heute – Wärmepumpe entscheidet"))
    if choice.state != track.rod or track.rod_since is None:
        if track.rod_since is not None:
            track.rod_changes += 1
        track.rod, track.rod_since = choice.state, now
    return choice


ROD_LABELS = {"free": N_("Heizstab frei"), "blocked": N_("Heizstab gesperrt"), "max": N_("Heizstab freigegeben")}


def rod_label(state: str) -> str:
    """The rod's state for people."""
    return t(ROD_LABELS.get(state, state))


STATE_LABELS = {"block": N_("gesperrt"), "normal": N_("normal"), "boost": N_("verstärkt"), "forced": N_("Anlaufbefehl")}


def state_label(state: str) -> str:
    """The state for people."""
    return t(STATE_LABELS.get(state, state))
