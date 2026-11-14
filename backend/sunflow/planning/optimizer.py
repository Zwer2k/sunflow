"""Battery schedule optimization as a linear program (HiGHS via SciPy).

Per 15 minute slot t (powers in W, energy in Wh):
    pv + import + discharge = load + charge + export
    soc[t] = soc[t-1] + (charge * eta_c - grid_charge * (eta_c - eta_g) - discharge / eta_d) * dt
minimize   sum(price * import - feed_in * export + wear * discharge
               + calendar aging * max(soc - threshold, 0)) * dt / 1000
           - value of the energy left at the end
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

DT_H = 0.25
# €/kWh per hour below the backup reserve: above any price difference, so a battery below
# it is refilled right away (from the grid if allowed) and never planned below it again
RESERVE_PENALTY = 1.0

type Action = Literal["auto", "hold", "force_charge", "force_discharge", "limit_charge", "limit_discharge"]


@dataclass(frozen=True, kw_only=True)
class BatterySpec:
    """Battery characteristics in W, Wh and fractions.

    Several batteries are planned together; the values left at None take the Settings.
    """

    capacity_wh: float
    soc_wh: float
    min_soc_wh: float
    max_charge_w: float
    max_discharge_w: float
    # Charging from PV (DC side of a hybrid inverter), from the grid (through the
    # inverter, AC to DC) and discharging to the house (DC to AC); None: as from PV
    efficiency_charge: float = 0.95
    efficiency_discharge: float = 0.95
    efficiency_charge_grid: float | None = None
    # Id in PlanSlot.batteries (the Sunflow device id)
    key: str = "battery"
    wear_ct_per_kwh: float | None = None
    reserve_wh: float | None = None
    soc_limit_wh: float | None = None
    high_soc_wh: float | None = None
    # The device can be forced to charge (from the grid) and to discharge (into the grid)
    force_charge: bool = True
    force_discharge: bool = True


@dataclass(frozen=True, kw_only=True)
class Settings:
    """Optimization settings."""

    wear_ct_per_kwh: float = 3.0
    allow_grid_charge: bool = True
    # Extra cost per exported kWh so the battery keeps room for PV ("pv_absorption")
    export_penalty_ct_per_kwh: float = 0.0
    # Extra cost per imported kWh: makes grid energy count far above any price difference
    # ("autarky": as little grid as possible rather than the cheapest grid)
    import_penalty_ct_per_kwh: float = 0.0
    # Grid friendly: export and import above these powers cost extra (soft limits)
    export_soft_limit_w: float | None = None
    import_soft_limit_w: float | None = None
    peak_penalty_ct_per_kwh: float = 20.0
    # Energy that must stay in the battery for backup power
    reserve_wh: float = 0.0
    # Per slot how far the battery may be below the reserve at the slot's end, because a safe
    # forecast fills it back in time (empty: never below it)
    reserve_dip_wh: tuple[float, ...] = ()
    # A fixed feed-in tariff only pays for PV; exporting stored grid energy is not
    # remunerated, so export is limited to the current PV unless allowed explicitly
    allow_battery_export: bool = False
    # Below this the battery is considered idle when choosing actions
    action_threshold_w: float = 100.0
    # The battery only charges from PV once these loads got this much energy (Wh);
    # until then it may only charge from the grid
    battery_gate_after: tuple[tuple[str, float], ...] = ()
    # Battery care: SoC kept below this (Wh) in everyday use
    soc_limit_wh: float | None = None
    # A full charge is due: the battery should be full at the end of this slot (soft)
    full_charge_slot: int | None = None
    # €/kWh missing to full at that slot; high enough to prefer PV, low enough not to
    # force expensive grid charging
    full_charge_penalty: float = 0.25
    # Calendar aging is faster at a high SoC: every kWh above this (Wh) costs per hour
    high_soc_wh: float | None = None
    high_soc_ct_per_kwh_h: float = 0.0
    # Share of the first slot still ahead (planned at 17:28, the 17:15 slot has 2 of 15 minutes left)
    first_slot_share: float = 1.0
    # Buffer for a day above the forecast (soft): (slot, Wh above the floor of all batteries
    # at its end, €/kWh missing)
    buffer: tuple[tuple[int, float, float], ...] = ()
    # Room for more PV than planned (soft): (slot, Wh the batteries hold at most at its end,
    # €/kWh above it)
    headroom: tuple[tuple[int, float, float], ...] = ()


@dataclass(frozen=True, kw_only=True)
class SlotInput:
    """Forecasts and prices for one slot."""

    start: datetime
    pv_w: float
    load_w: float
    price: float  # import €/kWh
    feed_in: float  # export €/kWh
    # Pessimistic PV (p10), when the forecast has a range
    pv_low_w: float | None = None
    # €/kWh added to the import price (e.g. "storage": the battery covers expensive periods)
    import_penalty: float = 0.0
    # Part of pv_w from plants whose output Sunflow can limit
    curtailable_w: float = 0.0
    # The price is not published yet but estimated
    price_estimated: bool = False


@dataclass(frozen=True, kw_only=True)
class FlexLoad:
    """A shiftable consumer (EV, heater): energy needed until the deadline.

    available[t] is False when the load cannot run in slot t (not plugged in,
    after its deadline).
    """

    name: str
    energy_wh: float
    max_power_w: float
    available: list[bool]
    # €/kWh for energy that is not delivered: high for a must-have need, the price of an
    # alternative (e.g. district heating) when the need can be covered otherwise
    unmet_cost: float = 2.0
    # Only PV surplus, never grid or battery
    pv_only: bool = False
    # €/kWh added from the first to the last slot: makes the load run as early as possible
    urgency: float = 0.0
    # Whether the home battery may discharge into this load
    uses_battery: bool = True
    # €/kWh preference over lower-priority loads when they compete for the same energy
    priority_bonus: float = 0.0
    # Ranked below the home battery: optional energy (PV only, alternative heat) goes
    # to the battery first and only to this load when the battery cannot take it
    below_battery: bool = False
    # Recurring needs (e.g. reheating hot water every day) as (first slot, end slot, Wh);
    # energy_wh then only has to be delivered before the first of them
    later_needs: list[tuple[int, int, float]] = field(default_factory=list)
    # Released only once the home battery holds this much (Wh at the slot start) and the
    # named loads got this much energy; until then it may only run on grid power
    gate_battery_wh: float | None = None
    gate_after: list[tuple[str, float]] = field(default_factory=list)
    # Waits for a storage whose level is unknown: grid power only for now
    gate_closed: bool = False
    # A car not there yet (expected back): its need is no must, unmet_cost is what it is worth
    expected: bool = False
    # Only off or (nearly) full power, e.g. a charger on a switched socket: a slot plans
    # nothing or at least ON_OFF_MIN_SHARE of its power, never a fraction it cannot run at
    on_off: bool = False
    # On/off: switched on right now, so going on costs no new start (no restart at equal prices)
    running: bool = False
    # On/off: € per switching on, instead of ON_OFF_START_COST (a battery charger: higher)
    start_cost: float | None = None

    @property
    def gated(self) -> bool:
        """Whether the load waits for other storages."""
        return self.gate_battery_wh is not None or bool(self.gate_after) or self.gate_closed

    def needs(self, n: int) -> list[tuple[int, int, float]]:
        """All needs as (first slot, end slot, Wh), in time order."""
        first_end = self.later_needs[0][0] if self.later_needs else n
        return [(0, first_end, self.energy_wh), *self.later_needs]


@dataclass(kw_only=True)
class FlexResult:
    """How much of a flexible load's need the plan covers."""

    name: str
    energy_wh: float
    planned_wh: float
    unmet_wh: float


SOLVER_TIME_LIMIT = 15
# An on/off load is switched on when at least this share of its power is planned
ON_OFF_MIN_SHARE = 0.5
# € per switching on of an on/off load: equally cheap slots become one block instead of
# several pieces (a charger restarts each time), without passing up a clearly cheaper time
ON_OFF_START_COST = 0.01
# €/kWh for PV limited away: above the export tie-breaker, so PV is only limited when
# exporting it really costs (negative feed-in, peak penalty)
CURTAIL_COST = 0.01
# Above this share of the capacity the battery counts as full when choosing actions
FULL_FRACTION = 0.98
# Cost per W of change between consecutive slots (import, export, flexible loads): far below
# any price difference, it only picks the calm solution among equally cheap ones
SMOOTHING_COST = 1e-8


@dataclass(kw_only=True)
class BatterySlot:
    """One battery's share of a slot."""

    charge_w: float
    discharge_w: float
    soc_wh: float
    action: Action
    action_power_w: float | None


@dataclass(kw_only=True)
class PlanSlot:
    """Optimized values and the resulting control action for one slot.

    Charge, discharge and SoC are the sums over all batteries, the action is the first
    battery's; batteries holds each one by its key.
    """

    start: datetime
    end: datetime
    pv_w: float
    load_w: float
    price: float
    feed_in: float
    charge_w: float
    discharge_w: float
    import_w: float
    export_w: float
    soc_wh: float
    action: Action
    action_power_w: float | None
    flex_w: dict[str, float] = field(default_factory=dict)
    # PV the plan limits away (negative feed-in, above the grid friendly export limit)
    curtail_w: float = 0.0
    price_estimated: bool = False
    batteries: dict[str, BatterySlot] = field(default_factory=dict)


@dataclass(kw_only=True)
class Plan:
    """Result of an optimization run."""

    slots: list[PlanSlot] = field(default_factory=list)
    flex: list[FlexResult] = field(default_factory=list)
    cost: float = 0.0
    baseline_cost: float = 0.0
    # Part of cost and baseline_cost for needs covered by an alternative (e.g. district heating)
    alternative_cost: float = 0.0
    baseline_alternative_cost: float = 0.0
    # Electricity cost per slot without Sunflow (for the cost chart)
    baseline_slot_costs: list[float] = field(default_factory=list)
    # Value of the energy left at the end; savings compare cost minus this value
    terminal_value: float = 0.0
    baseline_terminal_value: float = 0.0
    status: str = ""

    @property
    def savings(self) -> float:
        """Savings in € against self-consumption, including the energy left at the end."""
        return (self.baseline_cost - self.baseline_terminal_value) - (self.cost - self.terminal_value)


def choose_action(
    pv_w: float, load_w: float, charge_w: float, discharge_w: float, threshold: float, full: bool = False
) -> tuple[Action, float | None]:
    """Translate the optimized battery flow into a control action.

    Self-consumption (auto) already charges from surplus and covers deficits,
    so only deviations from it need an explicit command.
    """
    surplus = pv_w - load_w
    battery = charge_w - discharge_w
    if battery > max(surplus, 0) + threshold:
        return "force_charge", round(battery)
    if -battery > max(-surplus, 0) + threshold:
        return "force_discharge", round(-battery)
    if surplus < -threshold and -battery < -surplus - threshold:
        # Deficit but the battery should not fully cover it; a planned partial discharge
        # must be executed as a limit, "hold" would leave it unused
        if -battery > threshold:
            return "limit_discharge", round(-battery)
        return "hold", None
    # A (nearly) full battery takes nothing more in self-consumption anyway
    if surplus > threshold and battery < surplus - threshold and not full:
        # Surplus but the battery should not take all of it: the rest is exported
        return "limit_charge", round(max(battery, 0))
    return "auto", None


def smooth_actions(slots: list[PlanSlot] | list[BatterySlot], min_slots: int = 3) -> None:
    """Bridge short auto/hold blips between equal neighbours, in place.

    Partial discharges at low load sit on the auto/hold boundary; switching
    every 15 minutes gains fractions of a cent but makes the plan restless.
    Forced charge/discharge commands are never changed.
    """
    soft = {"auto", "hold"}
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(slots):
            j = i
            while j + 1 < len(slots) and slots[j + 1].action == slots[i].action:
                j += 1
            length = j - i + 1
            if (
                slots[i].action in soft
                and length < min_slots
                and 0 < i
                and j + 1 < len(slots)
                and slots[i - 1].action == slots[j + 1].action
                and slots[i - 1].action in soft
                and slots[i - 1].action != slots[i].action
            ):
                for k in range(i, j + 1):
                    slots[k].action = slots[i - 1].action
                changed = True
            i = j + 1


def simulate_self_consumption(
    inputs: list[SlotInput],
    battery: BatterySpec | list[BatterySpec],
    per_slot: list[float] | None = None,
    dts: list[float] | None = None,
) -> tuple[float, list[float]]:
    """Cost in € and final SoC per battery in Wh without Sunflow: batteries only follow surplus or deficit.

    Several batteries take the surplus and cover the deficit one after the other.
    per_slot, if given, receives the cost of every slot. dts: hours per slot (the running slot counts
    for what is left of it, as in the plan it is compared with).
    """
    batteries = [battery] if isinstance(battery, BatterySpec) else battery
    socs = [b.soc_wh for b in batteries]
    cost = 0.0
    for t, slot in enumerate(inputs):
        dt = dts[t] if dts is not None else DT_H
        rest = slot.pv_w - slot.load_w
        for k, b in enumerate(batteries):
            if dt <= 0:
                break
            if rest >= 0:
                room = (b.capacity_wh - socs[k]) / (b.efficiency_charge * dt)
                charge = min(rest, b.max_charge_w, max(room, 0))
                socs[k] += charge * b.efficiency_charge * dt
                rest -= charge
            else:
                available = (socs[k] - b.min_soc_wh) * b.efficiency_discharge / dt
                discharge = min(-rest, b.max_discharge_w, max(available, 0))
                socs[k] -= discharge / b.efficiency_discharge * dt
                rest += discharge
        export, imported = max(rest, 0.0), max(-rest, 0.0)
        slot_cost = (slot.price * imported - slot.feed_in * export) * dt / 1000
        cost += slot_cost
        if per_slot is not None:
            per_slot.append(slot_cost)
    return cost, socs


def has_alternative(load: FlexLoad) -> bool:
    """The need can be covered otherwise at a real price (e.g. district heating)."""
    return load.unmet_cost < 1.0 and not load.expected


def immediate_flex(
    inputs: list[SlotInput], flex: list[FlexLoad], dts: list[float] | None = None
) -> tuple[list[SlotInput], float]:
    """Flexible loads without Sunflow, and the € the alternative covers.

    Must-have loads start right away at full power. Optional ones (PV only, or with an
    alternative heat source) only use the PV surplus, like a wallbox in PV mode; the
    alternative covers the rest.
    """
    extra = [0.0] * len(inputs)
    alternative = 0.0
    for load in flex:
        optional = load.pv_only or has_alternative(load)
        for first, end, energy in load.needs(len(inputs)):
            remaining = energy
            for t in range(first, end):
                if remaining <= 0:
                    break
                if not load.available[t]:
                    continue
                limit = load.max_power_w
                if optional:
                    limit = min(limit, max(inputs[t].pv_w - inputs[t].load_w - extra[t], 0.0))
                dt = dts[t] if dts is not None else DT_H
                if dt <= 0:
                    continue
                power = min(limit, remaining / dt)
                extra[t] += power
                remaining -= power * dt
            if has_alternative(load):
                alternative += max(remaining, 0.0) / 1000 * load.unmet_cost
    return [replace(slot, load_w=slot.load_w + extra[t]) for t, slot in enumerate(inputs)], alternative


def optimize(
    inputs: list[SlotInput],
    battery: BatterySpec | list[BatterySpec],
    settings: Settings = Settings(),
    flex: list[FlexLoad] | tuple[FlexLoad, ...] = (),
) -> Plan:
    """Compute the cost-optimal schedule of one or more batteries and the flexible loads.

    The full charge of the settings applies to the first battery only.
    """
    n = len(inputs)
    if n == 0:
        return Plan(status="no input")
    batteries = [battery] if isinstance(battery, BatterySpec) else list(battery)
    if not batteries:
        return Plan(status="no battery")
    batteries = [replace(b, soc_wh=min(max(b.soc_wh, 0), b.capacity_wh)) for b in batteries]
    m = len(batteries)
    flex = list(flex)
    # Variable layout: import, export (each n); per battery charge, discharge, soc (each n);
    # then per flexible load its power per slot, then the unmet energy of every need
    gi, ge = np.arange(n), n + np.arange(n)
    c = [2 * n + 3 * n * k + np.arange(n) for k in range(m)]
    d = [index + n for index in c]
    s = [index + 2 * n for index in c]
    base = 2 * n + 3 * n * m
    f = [base + k * n + np.arange(n) for k in range(len(flex))]
    needs = [load.needs(n) for load in flex]
    u: list[list[int]] = []
    for load_needs in needs:
        first = base + len(flex) * n + sum(len(x) for x in u)
        u.append(list(range(first, first + len(load_needs))))
    count = base + len(flex) * n + sum(len(x) for x in u)
    eta_c = [b.efficiency_charge for b in batteries]
    eta_d = [b.efficiency_discharge for b in batteries]
    eta_g = [b.efficiency_charge_grid or b.efficiency_charge for b in batteries]
    # Grid charging loses more than PV charging: the share of the charge coming from the
    # grid gets its own variable (only when the efficiencies differ)
    cg: list[Any] = []
    for k in range(m):
        if eta_g[k] < eta_c[k] - 1e-9:
            cg.append(count + np.arange(n))
            count += n
        else:
            cg.append(None)
    split_grid = any(x is not None for x in cg)
    # Power above the grid friendly limits per slot (export, import)
    over_export = count + np.arange(n) if settings.export_soft_limit_w is not None else None
    if over_export is not None:
        count += n
    over_import = count + np.arange(n) if settings.import_soft_limit_w is not None else None
    if over_import is not None:
        count += n
    # Shortfall (Wh) to a due full charge of the first battery
    full_short = count if settings.full_charge_slot is not None and 0 <= settings.full_charge_slot < n else None
    if full_short is not None:
        count += 1
    buffers = [(t, wh, penalty) for t, wh, penalty in settings.buffer if 0 <= t < n and wh > 0]
    buffer_short = count + np.arange(len(buffers))
    count += len(buffers)
    headrooms = [(t, wh, penalty) for t, wh, penalty in settings.headroom if 0 <= t < n]
    headroom_over = count + np.arange(len(headrooms))
    count += len(headrooms)
    # Discharge limit or backup reserve, whichever is higher, per slot, also while a battery is below it
    dips = [settings.reserve_dip_wh[t] if t < len(settings.reserve_dip_wh) else 0.0 for t in range(n)]
    # A battery with its own reserve (or none) keeps it; the dip applies to the settings' reserve
    floors = [
        [max(b.min_soc_wh, b.reserve_wh if b.reserve_wh is not None else settings.reserve_wh - dips[t]) for t in range(n)]
        for b in batteries
    ]
    floor_sum = [sum(f[t] for f in floors) for t in range(n)]
    # Below it (at the start, after an outage) every slot short of it costs: refilled first. A floor
    # that rises after a dip below the reserve is kept the same way, so a forecast missed never
    # makes the plan infeasible
    dipping = any(dip > 0 for dip in dips)
    reserve_short = (
        count + np.arange(n)
        if dipping or any(b.soc_wh < max(f) - 1 for b, f in zip(batteries, floors, strict=True))
        else None
    )
    if reserve_short is not None:
        count += n
    # Energy above the high SoC threshold per battery and slot
    high = [b.high_soc_wh if b.high_soc_wh is not None else settings.high_soc_wh for b in batteries]
    hs: list[Any] = []
    for k in range(m):
        if high[k] is not None and settings.high_soc_ct_per_kwh_h > 0:
            hs.append(count + np.arange(n))
            count += n
        else:
            hs.append(None)
    # PV limited away per slot, only where plants can be limited
    cu = count + np.arange(n) if any(i.curtailable_w > 0 for i in inputs) else None
    if cu is not None:
        count += n
    wear = [(b.wear_ct_per_kwh if b.wear_ct_per_kwh is not None else settings.wear_ct_per_kwh) / 100 for b in batteries]
    mean_price = float(np.mean([i.price for i in inputs]))
    # Energy left at the end is worth what it saves later, after losses and wear
    terminal = [max(mean_price * eta_d[k] - wear[k], 0.0) for k in range(m)]

    prices = [i.price for i in inputs]
    spread = max(prices) - min(prices)
    # Hours of each slot; of the running one only what is left, so its powers move less energy
    dts = [DT_H * min(max(settings.first_slot_share, 0.0), 1.0), *[DT_H] * (n - 1)]
    cost = np.zeros(count)
    for t, slot in enumerate(inputs):
        cost[gi[t]] = (slot.price + slot.import_penalty + settings.import_penalty_ct_per_kwh / 100) * dts[t] / 1000
        # Risk premium (max 0.5 ct/kWh): export late, after the battery is full. Equally
        # cheap if the forecast holds, but robust when PV falls short and like
        # self-consumption mode; must outweigh the smoothing of a flat export
        export_tie = 0.005 * (1 - t / n)
        cost[ge[t]] = (settings.export_penalty_ct_per_kwh / 100 - slot.feed_in + export_tie) * dts[t] / 1000
        # Tie-breakers (max ~0.1 ct/kWh): when the timing does not matter, discharge in the
        # more expensive slots and, at equal prices, earlier, so the plan does not flip
        # between auto and hold every few minutes
        tie = 0.001 * (max(prices) - slot.price) / spread if spread > 0 else 0.0
        tie += 0.0001 * t / n
        for k in range(m):
            cost[d[k][t]] = (wear[k] + tie) * dts[t] / 1000
    for k in range(m):
        cost[s[k][-1]] -= terminal[k] / 1000
    if full_short is not None:
        cost[full_short] = settings.full_charge_penalty / 1000
    for index, (_, _, penalty) in zip(buffer_short, buffers, strict=True):
        cost[index] = penalty / 1000
    for index, (_, _, penalty) in zip(headroom_over, headrooms, strict=True):
        cost[index] = penalty / 1000
    if reserve_short is not None:
        cost[reserve_short] = RESERVE_PENALTY * DT_H / 1000
    if cu is not None:
        cost[cu] = CURTAIL_COST * DT_H / 1000
    for k in range(m):
        if hs[k] is not None:
            cost[hs[k]] = settings.high_soc_ct_per_kwh_h / 100 * DT_H / 1000
    for over in (over_export, over_import):
        if over is not None:
            cost[over] = settings.peak_penalty_ct_per_kwh / 100 * DT_H / 1000
    # PV stored now is worth this much later; feed-in is what surplus earns otherwise
    stored_value = max(terminal[k] * eta_c[k] for k in range(m))
    mean_feed_in = float(np.mean([i.feed_in for i in inputs]))
    for k, load in enumerate(flex):
        unmet = load.unmet_cost
        optional = load.pv_only or unmet < 1.0
        if load.below_battery and optional:
            # Between feed-in and storage value: beats export, loses against charging
            unmet = min(unmet, max((stored_value + mean_feed_in) / 2, mean_feed_in + 0.001))
        for index in u[k]:
            cost[index] = unmet / 1000
        for t in range(n):
            cost[f[k][t]] += (load.urgency * t / n - load.priority_bonus) * DT_H / 1000

    rows = n + m * n + sum(len(x) for x in u)
    a_eq = lil_matrix((rows, count))
    b_eq = np.zeros(rows)
    for t, slot in enumerate(inputs):
        # Power balance
        a_eq[t, gi[t]] = 1
        a_eq[t, ge[t]] = -1
        for k in range(m):
            a_eq[t, d[k][t]] = 1
            a_eq[t, c[k][t]] = -1
        for k in range(len(flex)):
            a_eq[t, f[k][t]] = -1
        b_eq[t] = slot.load_w - slot.pv_w
        if cu is not None:
            a_eq[t, cu[t]] = -1
        # State of charge of every battery
        for k, b in enumerate(batteries):
            row = n + k * n + t
            a_eq[row, s[k][t]] = 1
            a_eq[row, c[k][t]] = -eta_c[k] * dts[t]
            a_eq[row, d[k][t]] = dts[t] / eta_d[k]
            if cg[k] is not None:
                a_eq[row, cg[k][t]] = (eta_c[k] - eta_g[k]) * dts[t]
            if t == 0:
                b_eq[row] = b.soc_wh
            else:
                a_eq[row, s[k][t - 1]] = -1
    row = n + m * n
    for k in range(len(flex)):
        for (first, end, energy), unmet_index in zip(needs[k], u[k], strict=True):
            # Energy delivered in the need's window plus unmet rest equals the need
            for t in range(first, end):
                a_eq[row, f[k][t]] = dts[t]
            a_eq[row, unmet_index] = 1
            b_eq[row] = energy
            row += 1

    soc_upper: list[float] = []
    soc_min: list[float] = []
    grid_charge = [settings.allow_grid_charge and b.force_charge for b in batteries]
    for k, b in enumerate(batteries):
        # A battery below its floor may stay there but must not be drained further
        soc_min.append(min(*floors[k], b.soc_wh))
        # Charge limit for battery care; lifted while a full charge is due. Above it already,
        # the battery may stay there but not charge further
        limit = b.soc_limit_wh if b.soc_limit_wh is not None else settings.soc_limit_wh
        upper_k = b.capacity_wh
        if limit is not None and not (k == 0 and full_short is not None):
            upper_k = min(b.capacity_wh, max(limit, b.soc_wh, soc_min[k]))
        soc_upper.append(upper_k)
    lower = np.zeros(count)
    upper = np.full(count, np.inf)
    for t, slot in enumerate(inputs):
        surplus = max(slot.pv_w - slot.load_w, 0)
        for k, b in enumerate(batteries):
            # Without grid charging only the surplus over the house load may go into the battery
            upper[c[k][t]] = b.max_charge_w if grid_charge[k] else min(b.max_charge_w, surplus)
            upper[d[k][t]] = b.max_discharge_w
            if cg[k] is not None:
                upper[cg[k][t]] = b.max_charge_w if grid_charge[k] else 0.0
            lower[s[k][t]] = soc_min[k]
            upper[s[k][t]] = soc_upper[k]
        # The grid only carries what the house, loads and batteries can take or give
        upper[gi[t]] = max(slot.load_w, 0) + sum(b.max_charge_w for b in batteries) + sum(
            load.max_power_w for load in flex if load.available[t]
        )
        # Export beyond the PV surplus would be stored energy sold at the feed-in price
        upper[ge[t]] = surplus + sum(
            b.max_discharge_w for b in batteries if settings.allow_battery_export and b.force_discharge
        )
        if cu is not None:
            upper[cu[t]] = min(max(slot.curtailable_w, 0.0), max(slot.pv_w, 0.0))
        for k, load in enumerate(flex):
            limit = load.max_power_w if load.available[t] else 0.0
            if load.pv_only:
                limit = min(limit, max(slot.pv_w - slot.load_w, 0.0))
            upper[f[k][t]] = limit

    # Import and export (charge and discharge) at the same time is physically impossible.
    # A linear model only does it when it pays: import cheaper than export, or negative
    # prices that reward burning energy in losses. Only those slots get binaries.
    export_value = [i.feed_in - settings.export_penalty_ct_per_kwh / 100 for i in inputs]
    grid_binary = [t for t, i in enumerate(inputs) if i.price < export_value[t] and upper[ge[t]] > 0]
    battery_binary = [(k, t) for t, i in enumerate(inputs) if i.price < 0 or export_value[t] < 0 for k in range(m)]
    z = count + np.arange(len(grid_binary))
    y = count + len(grid_binary) + np.arange(len(battery_binary))
    total = count + len(grid_binary) + len(battery_binary)
    # Gates: one binary per gated slot (1 = released). Closed, the load (or the battery)
    # only runs on power imported at the same time, i.e. gets no PV or battery energy.
    names = {load.name: k for k, load in enumerate(flex)}

    def waits_for(after: list[tuple[str, float]] | tuple[tuple[str, float], ...]) -> list[tuple[int, float, int]]:
        """(load index, required Wh, last slot it can receive energy) per named load."""
        result = []
        for name, required in after:
            if name not in names or required <= 0:
                continue
            k = names[name]
            available = [t for t in range(n) if upper[f[k][t]] > 0]
            if not available:
                continue
            # What the load cannot get in the horizon must not hold others back forever
            deliverable = sum(upper[f[k][t]] for t in available) * DT_H
            result.append((k, min(required, deliverable * 0.999), available[-1]))
        return result

    # Rows as ({variable: coefficient}, upper bound), added to every solve
    gate_rows: list[tuple[dict[int, float], float]] = []

    def add_gate(slots: list[tuple[int, int]], flows: list[Any], flow_max: float, soc_wh: float | None,
                 after: list[tuple[int, float, int]]) -> None:
        previous = None
        for t, b in slots:
            gate_rows.append(({**{flow[t]: 1 for flow in flows}, gi[t]: -1, b: -flow_max}, 0.0))
            if soc_wh is not None:
                # Energy in the batteries at the slot end: the load may take what is left in
                # the slot in which they reach the level (often the charge limit); the gate
                # closes again when they drop below
                gate_rows.append(({b: soc_wh, **{s[k][t]: -1 for k in range(m)}}, 0.0))
            for k, required, last in after:
                if t > last:
                    continue  # the named load cannot receive more: no longer waiting for it
                if t == 0:
                    upper[b] = 0.0
                    continue
                gate_rows.append(({b: required, **{f[k][tau]: -dts[tau] for tau in range(t)}}, 0.0))
            if soc_wh is None and previous is not None:
                # Received energy never decreases: once released, the gate stays open,
                # which also prunes the binary search a lot
                gate_rows.append(({previous: 1, b: -1}, 0.0))
            previous = b

    gate_slots: list[tuple[int, list[tuple[int, int]]]] = []
    for k, load in enumerate(flex):
        if load.gated:
            slots = [(t, total + i) for i, t in enumerate(t for t in range(n) if upper[f[k][t]] > 0)]
            total += len(slots)
            gate_slots.append((k, slots))
    # On/off loads: one binary per slot it may run in (1 = on) and one per switching on
    on_off_slots: list[tuple[int, list[tuple[int, int]]]] = []
    on_off_starts: list[int] = []
    for k, load in enumerate(flex):
        if load.on_off:
            slots = [(t, total + i) for i, t in enumerate(t for t in range(n) if upper[f[k][t]] > 0)]
            total += len(slots)
            on_off_slots.append((k, slots))
            on_off_starts.extend(range(total, total + len(slots)))
            total += len(slots)
    battery_after = waits_for(settings.battery_gate_after)
    battery_slots = [(t, total + t) for t in range(n)] if battery_after else []
    total += len(battery_slots)
    cost = np.concatenate([cost, np.zeros(total - count)])
    lower = np.concatenate([lower, np.zeros(total - count)])
    upper = np.concatenate([upper, np.ones(total - count)])
    for k, slots in gate_slots:
        load = flex[k]
        add_gate(slots, [f[k]], load.max_power_w, load.gate_battery_wh, waits_for(load.gate_after))
        if load.gate_closed:
            for _, b in slots:
                upper[b] = 0.0
    if battery_slots:
        add_gate(battery_slots, c, sum(b.max_charge_w for b in batteries), None, battery_after)
    starts = iter(on_off_starts)
    for k, slots in on_off_slots:
        previous: tuple[int, int] | None = None
        for t, b in slots:
            power = upper[f[k][t]]
            # Off, or between the minimum share and full power
            gate_rows.append(({f[k][t]: 1, b: -power}, 0.0))
            gate_rows.append(({f[k][t]: -1, b: ON_OFF_MIN_SHARE * power}, 0.0))
            # Switched on in this slot: on now and not in the slot right before
            start = next(starts)
            cost[start] = load.start_cost if load.start_cost is not None else ON_OFF_START_COST
            before = {previous[1]: -1} if previous is not None and previous[0] == t - 1 else {}
            # Running now: on in the first slot is no new start
            on_before = 1.0 if t == 0 and load.running else 0.0
            gate_rows.append(({b: 1, **before, start: -1}, on_before))
            previous = (t, b)
    for over, flow, limit in (
        (over_export, ge, settings.export_soft_limit_w),
        (over_import, gi, settings.import_soft_limit_w),
    ):
        if over is not None:
            for t in range(n):
                # flow - over <= limit: only the part above the limit costs the penalty
                gate_rows.append(({flow[t]: 1, over[t]: -1}, limit))
    for k in range(m):
        if hs[k] is not None:
            # soc - above <= threshold: only the energy above it costs
            gate_rows.extend(({s[k][t]: 1, hs[k][t]: -1}, high[k]) for t in range(n))
    if full_short is not None:
        # soc at the target slot plus shortfall reaches (almost) full
        gate_rows.append(({s[0][settings.full_charge_slot]: -1, full_short: -1}, -batteries[0].capacity_wh * FULL_FRACTION))
    if reserve_short is not None:
        # soc of all batteries plus shortfall reaches their floors in every slot
        gate_rows.extend(
            ({**{s[k][t]: -1 for k in range(m)}, reserve_short[t]: -1}, -floor_sum[t]) for t in range(n)
        )
    # soc of all batteries plus shortfall reaches the floor plus the buffer; the buffer lies
    # above the backup reserve, also while the battery is below the reserve now
    gate_rows.extend(
        ({**{s[k][t]: -1 for k in range(m)}, index: -1}, -(floor_sum[t] + wh))
        for index, (t, wh, _) in zip(buffer_short, buffers, strict=True)
    )
    # soc of all batteries minus what lies above stays within the room left for more PV
    gate_rows.extend(
        ({**{s[k][t]: 1 for k in range(m)}, index: -1}, wh)
        for index, (t, wh, _) in zip(headroom_over, headrooms, strict=True)
    )
    if m > 1:
        for t, slot in enumerate(inputs):
            surplus = max(slot.pv_w - slot.load_w, 0.0)
            # Batteries charge from PV or the grid, not from each other (that only loses energy)
            gate_rows.append(({**{c[k][t]: 1 for k in range(m)}, gi[t]: -1}, surplus))
            if split_grid:
                # Together, the PV shares cannot exceed the surplus
                gate_rows.append(
                    ({**{c[k][t]: 1 for k in range(m)}, **{cg[k][t]: -1 for k in range(m) if cg[k] is not None}}, surplus)
                )
    integrality = np.zeros(total)
    integrality[count:] = 1
    # A start follows from the on/off binaries (minimized, it is 0 or 1 anyway): no branching on it
    integrality[on_off_starts] = 0
    binary_total = total
    smoothed = [gi, ge, *f]

    def solve(smooth: bool, fixed_binaries: np.ndarray | None = None) -> Any:
        """Solve; smoothing adds delta[t] >= |v[t] - v[t-1]| for grid flows and flexible loads."""
        lo, up, integ = lower.copy(), upper.copy(), integrality.copy()
        obj = cost.copy()
        if fixed_binaries is not None:
            lo[count:binary_total] = up[count:binary_total] = fixed_binaries
            integ[count:binary_total] = 0
        size = binary_total
        if smooth:
            size += len(smoothed) * (n - 1)
            extra = size - binary_total
            obj = np.concatenate([obj, np.full(extra, SMOOTHING_COST)])
            lo = np.concatenate([lo, np.zeros(extra)])
            up = np.concatenate([up, np.full(extra, np.inf)])
            integ = np.concatenate([integ, np.zeros(extra)])

        restrict_battery = any(not load.uses_battery for load in flex)
        pv_only = [k for k, load in enumerate(flex) if load.pv_only]
        split_rows = sum(2 * n for x in cg if x is not None) if m == 1 else sum(n for x in cg if x is not None)
        a_ub = lil_matrix(
            (
                2 * (len(grid_binary) + len(battery_binary))
                + 2 * (size - binary_total)
                + (n if restrict_battery else 0)
                + (n if len(pv_only) > 1 else 0)
                + split_rows
                + len(gate_rows),
                size,
            )
        )
        b_ub = np.zeros(a_ub.shape[0])
        row = 0
        for k, t in enumerate(grid_binary):
            # import <= M * z, export <= M * (1 - z)
            a_ub[row, gi[t]], a_ub[row, z[k]] = 1, -upper[gi[t]]
            a_ub[row + 1, ge[t]], a_ub[row + 1, z[k]] = 1, upper[ge[t]]
            b_ub[row + 1] = upper[ge[t]]
            row += 2
        for i, (k, t) in enumerate(battery_binary):
            a_ub[row, c[k][t]], a_ub[row, y[i]] = 1, -upper[c[k][t]]
            a_ub[row + 1, d[k][t]], a_ub[row + 1, y[i]] = 1, upper[d[k][t]]
            b_ub[row + 1] = upper[d[k][t]]
            row += 2
        if restrict_battery:
            # The batteries only serve the house, allowed loads and export
            for t, slot in enumerate(inputs):
                for k in range(m):
                    a_ub[row, d[k][t]] = 1
                a_ub[row, ge[t]] = -1
                for k, load in enumerate(flex):
                    if load.uses_battery:
                        a_ub[row, f[k][t]] = -1
                b_ub[row] = slot.load_w
                row += 1
        if len(pv_only) > 1:
            # Together, PV-only loads cannot take more than the surplus
            for t, slot in enumerate(inputs):
                for k in pv_only:
                    a_ub[row, f[k][t]] = 1
                b_ub[row] = max(slot.pv_w - slot.load_w, 0.0)
                row += 1
        for k in range(m):
            if cg[k] is None:
                continue
            for t, slot in enumerate(inputs):
                # The grid share is part of the charge
                a_ub[row, cg[k][t]], a_ub[row, c[k][t]] = 1, -1
                row += 1
                if m == 1:
                    # The rest cannot exceed the PV surplus (several batteries: a joint row)
                    a_ub[row, c[k][t]], a_ub[row, cg[k][t]] = 1, -1
                    b_ub[row] = max(slot.pv_w - slot.load_w, 0.0)
                    row += 1
        for coefficients, bound in gate_rows:
            for index, value in coefficients.items():
                a_ub[row, index] = value
            b_ub[row] = bound
            row += 1
        if smooth:
            delta = binary_total
            for series in smoothed:
                for t in range(1, n):
                    a_ub[row, series[t]], a_ub[row, series[t - 1]], a_ub[row, delta] = 1, -1, -1
                    a_ub[row + 1, series[t]], a_ub[row + 1, series[t - 1]], a_ub[row + 1, delta] = -1, 1, -1
                    row += 2
                    delta += 1

        a_eq_full = lil_matrix((rows, size))
        a_eq_full[:, :count] = a_eq
        constraints = [LinearConstraint(a_eq_full.tocsr(), b_eq, b_eq)]
        if row:
            constraints.append(LinearConstraint(a_ub.tocsr(), -np.inf, b_ub))
        return milp(
            obj,
            constraints=constraints,
            integrality=integ,
            bounds=Bounds(lo, up),
            # Stop at 0.1 % from the optimum: the many equally cheap variants are not worth proving
            options={"time_limit": SOLVER_TIME_LIMIT, "mip_rel_gap": 1e-3},
        )

    if binary_total > count:
        # Smoothing makes the mixed-integer problem much harder: decide the binaries first,
        # then smooth the remaining linear problem with them fixed
        result = solve(smooth=False)
        if result.x is not None:
            smoothed_result = solve(smooth=True, fixed_binaries=np.round(result.x[count:binary_total]))
            if smoothed_result.success:
                result = smoothed_result
    else:
        result = solve(smooth=True)
    plan = Plan(status=result.message)
    # On the time limit the best solution found so far is still a valid plan
    if result.x is None:
        return plan
    x = result.x
    for t, slot in enumerate(inputs):
        flex_now = sum(x[f[k][t]] for k in range(len(flex)))
        curtail = float(x[cu[t]]) if cu is not None else 0.0
        net = [float(x[d[k][t]] - x[c[k][t]]) for k in range(m)]
        shares: dict[str, BatterySlot] = {}
        for k, b in enumerate(batteries):
            # What this battery sees: the house and loads after PV and the other batteries' plan
            action, power = choose_action(
                slot.pv_w - curtail + sum(net) - net[k],
                slot.load_w + flex_now,
                x[c[k][t]],
                x[d[k][t]],
                settings.action_threshold_w,
                # Only a really full battery stops by itself; at a care limit charging must be limited
                full=soc_upper[k] >= b.capacity_wh and x[s[k][t]] >= b.capacity_wh * FULL_FRACTION,
            )
            if m > 1 and action == "auto":
                # Batteries in self-consumption share every deficit and surplus among them;
                # one the plan leaves out must be told so
                own = net[k]
                house = slot.pv_w - curtail - slot.load_w - flex_now
                threshold = settings.action_threshold_w
                if house < -threshold and own < threshold:
                    action, power = "hold", None
                elif house > threshold and -own < threshold and x[s[k][t]] < b.capacity_wh * FULL_FRACTION:
                    action, power = "limit_charge", round(max(-own, 0.0))
            shares[b.key] = BatterySlot(
                charge_w=float(x[c[k][t]]),
                discharge_w=float(x[d[k][t]]),
                soc_wh=float(x[s[k][t]]),
                action=action,
                action_power_w=power,
            )
        plan.slots.append(
            PlanSlot(
                start=slot.start,
                end=slot.start + timedelta(minutes=15),
                pv_w=slot.pv_w,
                load_w=slot.load_w,
                price=slot.price,
                feed_in=slot.feed_in,
                charge_w=sum(share.charge_w for share in shares.values()),
                discharge_w=sum(share.discharge_w for share in shares.values()),
                import_w=float(x[gi[t]]),
                export_w=float(x[ge[t]]),
                soc_wh=sum(share.soc_wh for share in shares.values()),
                action="auto",
                action_power_w=None,
                flex_w={load.name: float(x[f[k][t]]) for k, load in enumerate(flex)},
                curtail_w=curtail,
                price_estimated=slot.price_estimated,
                batteries=shares,
            )
        )
    plan.flex = [
        FlexResult(
            name=load.name,
            energy_wh=sum(energy for _, _, energy in needs[k]),
            planned_wh=float(sum(x[f[k][t]] * dts[t] for t in range(n))),
            unmet_wh=float(sum(x[index] for index in u[k])),
        )
        for k, load in enumerate(flex)
    ]
    for b in batteries:
        smooth_actions([p.batteries[b.key] for p in plan.slots])
    first = batteries[0].key
    for p in plan.slots:
        p.action, p.action_power_w = p.batteries[first].action, p.batteries[first].action_power_w
    plan.terminal_value = sum(terminal[k] * plan.slots[-1].batteries[b.key].soc_wh for k, b in enumerate(batteries)) / 1000
    plan.alternative_cost = sum(
        r.unmet_wh / 1000 * load.unmet_cost for r, load in zip(plan.flex, flex, strict=True) if has_alternative(load)
    )
    plan.cost = plan.alternative_cost + sum(
        (p.price * p.import_w - p.feed_in * p.export_w) * dt / 1000 for p, dt in zip(plan.slots, dts, strict=False)
    )
    # The running slot for what is left of it, as in the plan: else the savings swing within every quarter hour
    baseline_inputs, plan.baseline_alternative_cost = immediate_flex(inputs, flex, dts)
    plan.baseline_cost, baseline_socs = simulate_self_consumption(baseline_inputs, batteries, plan.baseline_slot_costs, dts)
    plan.baseline_cost += plan.baseline_alternative_cost
    plan.baseline_terminal_value = sum(terminal[k] * baseline_socs[k] for k in range(m)) / 1000
    return plan
