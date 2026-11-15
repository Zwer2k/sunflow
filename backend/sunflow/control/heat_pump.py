"""Steering a heat pump: which state (normal, boost, block, forced) and what to write for it.

Sunflow only recommends: SG-Ready contacts, a select, a raised setpoint, the PV surplus or a
boost switch. The heat pump's own control decides what that means.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from sunflow.config.models import HeatPumpSettings, HeatPumpState
from sunflow.i18n import N_, t

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
SLOTS_PER_HOUR = 4


def _switch(on: bool, inverted: bool = False) -> str:
    return "on" if on != inverted else "off"


def runtime(state: str, settings: HeatPumpSettings, surplus_w: float = 0.0) -> dict[str, Any]:
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
    }


@dataclass(kw_only=True)
class HeatPumpTrack:
    """What Sunflow last chose for a heat pump, for the minimum time per state and the block limits."""

    state: str = "normal"
    since: datetime | None = None
    block_day: date | None = None
    blocked: timedelta = field(default_factory=timedelta)
    last_tick: datetime | None = None

    def advance(self, now: datetime, tz: tzinfo) -> None:
        """Count the time blocked since the last step; a new day starts from zero."""
        today = now.astimezone(tz).date()
        if self.block_day != today:
            self.block_day, self.blocked = today, timedelta()
        if self.state == "block" and self.last_tick is not None:
            self.blocked += now - self.last_tick
        self.last_tick = now


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


def wanted_state(
    settings: HeatPumpSettings, slots: list[Any], now: datetime, tz: tzinfo, track: HeatPumpTrack, grid_w: float | None
) -> Choice:
    """The state the plan suggests now."""
    index = next((i for i, s in enumerate(slots) if s.start <= now < s.end), None)
    if index is None:
        return Choice(state="normal", reason=t("kein aktueller Plan"))
    slot = slots[index]
    surplus = max(slot.export_w, 0.0) + max(getattr(slot, "curtail_w", 0.0), 0.0)
    method = settings.method
    cheaper, dearer = _rank(slots, index, tz)
    if settings.use_forced and method in CAN_FORCE and slot.price < 0:
        return Choice(state="forced", reason=t("negativer Strompreis"), surplus_w=surplus)
    # Planned surplus that is there: imports while boosting mean the forecast was too high
    surplus_there = grid_w is None or grid_w < IMPORT_TOLERANCE_W or track.state not in ("boost", "forced")
    if surplus >= settings.min_surplus_w and surplus_there:
        return Choice(state="boost", reason=t("PV-Überschuss"), surplus_w=surplus)
    if settings.cheap_hours and cheaper < settings.cheap_hours * SLOTS_PER_HOUR:
        return Choice(state="boost", reason=t("günstige Stunde"), surplus_w=surplus)
    blocking = settings.use_block and method in CAN_BLOCK and settings.expensive_hours
    if blocking and dearer < settings.expensive_hours * SLOTS_PER_HOUR:
        running = now - track.since if track.state == "block" and track.since else timedelta()
        if running >= timedelta(minutes=settings.block_max_min):
            return Choice(state="normal", reason=t("Sperre lange genug"), limit=True)
        if track.blocked >= timedelta(minutes=settings.block_day_max_min):
            return Choice(state="normal", reason=t("Sperrzeit für heute erreicht"), limit=True)
        return Choice(state="block", reason=t("teure Stunde"))
    return Choice(state="normal", reason=t("nach Plan: normal"))


def next_state(
    settings: HeatPumpSettings, slots: list[Any], now: datetime, tz: tzinfo, track: HeatPumpTrack, grid_w: float | None
) -> Choice:
    """The wanted state, kept back until the current one ran its minimum time (compressor protection).

    Ending a block too long or over the daily limit is never held back.
    """
    track.advance(now, tz)
    choice = wanted_state(settings, slots, now, tz, track, grid_w)
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


STATE_LABELS = {"block": N_("gesperrt"), "normal": N_("normal"), "boost": N_("verstärkt"), "forced": N_("Anlaufbefehl")}


def state_label(state: str) -> str:
    """The state for people."""
    return t(STATE_LABELS.get(state, state))
