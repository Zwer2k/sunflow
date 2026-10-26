"""Battery schedule optimization as a linear program (HiGHS via SciPy).

Per 15 minute slot t (powers in W, energy in Wh):
    pv + import + discharge = load + charge + export
    soc[t] = soc[t-1] + (charge * eta_c - grid_charge * (eta_c - eta_g) - discharge / eta_d) * dt
minimize   sum(price * import - feed_in * export + wear * discharge) * dt / 1000
           - value of the energy left at the end
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

DT_H = 0.25

type Action = Literal["auto", "hold", "force_charge", "force_discharge", "limit_charge", "limit_discharge"]


@dataclass(frozen=True, kw_only=True)
class BatterySpec:
    """Battery characteristics in W, Wh and fractions."""

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


@dataclass(frozen=True, kw_only=True)
class Settings:
    """Optimization settings."""

    wear_ct_per_kwh: float = 3.0
    allow_grid_charge: bool = True
    # Extra cost per exported kWh so the battery keeps room for PV ("pv_absorption")
    export_penalty_ct_per_kwh: float = 0.0
    # Energy that must stay in the battery, e.g. for backup power ("backup")
    reserve_wh: float = 0.0
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

    @property
    def gated(self) -> bool:
        """Whether the load waits for other storages."""
        return self.gate_battery_wh is not None or bool(self.gate_after)

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
# Above this share of the capacity the battery counts as full when choosing actions
FULL_FRACTION = 0.98
# Cost per W of change between consecutive slots (import, export, flexible loads): far below
# any price difference, it only picks the calm solution among equally cheap ones
SMOOTHING_COST = 1e-8


@dataclass(kw_only=True)
class PlanSlot:
    """Optimized values and the resulting control action for one slot."""

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


def smooth_actions(slots: list[PlanSlot], min_slots: int = 3) -> None:
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


def simulate_self_consumption(inputs: list[SlotInput], battery: BatterySpec) -> tuple[float, float]:
    """Cost in € and final SoC in Wh without Sunflow: the battery only follows surplus or deficit."""
    soc = battery.soc_wh
    cost = 0.0
    for slot in inputs:
        surplus = slot.pv_w - slot.load_w
        if surplus >= 0:
            room = (battery.capacity_wh - soc) / (battery.efficiency_charge * DT_H)
            charge = min(surplus, battery.max_charge_w, max(room, 0))
            soc += charge * battery.efficiency_charge * DT_H
            export, imported = surplus - charge, 0.0
        else:
            available = (soc - battery.min_soc_wh) * battery.efficiency_discharge / DT_H
            discharge = min(-surplus, battery.max_discharge_w, max(available, 0))
            soc -= discharge / battery.efficiency_discharge * DT_H
            imported, export = -surplus - discharge, 0.0
        cost += (slot.price * imported - slot.feed_in * export) * DT_H / 1000
    return cost, soc


def has_alternative(load: FlexLoad) -> bool:
    """The need can be covered otherwise at a real price (e.g. district heating)."""
    return load.unmet_cost < 1.0


def immediate_flex(inputs: list[SlotInput], flex: list[FlexLoad]) -> tuple[list[SlotInput], float]:
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
                power = min(limit, remaining / DT_H)
                extra[t] += power
                remaining -= power * DT_H
            if has_alternative(load):
                alternative += max(remaining, 0.0) / 1000 * load.unmet_cost
    return [replace(slot, load_w=slot.load_w + extra[t]) for t, slot in enumerate(inputs)], alternative


def optimize(
    inputs: list[SlotInput],
    battery: BatterySpec,
    settings: Settings = Settings(),
    flex: list[FlexLoad] | tuple[FlexLoad, ...] = (),
) -> Plan:
    """Compute the cost-optimal battery and flexible load schedule."""
    n = len(inputs)
    if n == 0:
        return Plan(status="no input")
    flex = list(flex)
    # Variable layout: charge, discharge, import, export, soc (each n),
    # then per flexible load its power per slot, then the unmet energy of every need
    c, d, gi, ge, s = (np.arange(n) + k * n for k in range(5))
    f = [5 * n + k * n + np.arange(n) for k in range(len(flex))]
    needs = [load.needs(n) for load in flex]
    u: list[list[int]] = []
    for load_needs in needs:
        first = 5 * n + len(flex) * n + sum(len(x) for x in u)
        u.append(list(range(first, first + len(load_needs))))
    count = 5 * n + len(flex) * n + sum(len(x) for x in u)
    eta_c, eta_d = battery.efficiency_charge, battery.efficiency_discharge
    eta_g = battery.efficiency_charge_grid or eta_c
    # Grid charging loses more than PV charging: the share of the charge coming from the
    # grid gets its own variable (only when the efficiencies differ)
    split_grid = eta_g < eta_c - 1e-9
    cg = count + np.arange(n) if split_grid else None
    if split_grid:
        count += n
    # Shortfall (Wh) to a due full charge
    full_short = count if settings.full_charge_slot is not None and 0 <= settings.full_charge_slot < n else None
    if full_short is not None:
        count += 1
    battery = replace(battery, soc_wh=min(max(battery.soc_wh, 0), battery.capacity_wh))
    wear = settings.wear_ct_per_kwh / 100
    mean_price = float(np.mean([i.price for i in inputs]))
    # Energy left at the end is worth what it saves later, after losses and wear
    terminal = max(mean_price * eta_d - wear, 0.0)

    prices = [i.price for i in inputs]
    spread = max(prices) - min(prices)
    cost = np.zeros(count)
    for t, slot in enumerate(inputs):
        cost[gi[t]] = slot.price * DT_H / 1000
        # Risk premium (max 0.5 ct/kWh): export late, after the battery is full. Equally
        # cheap if the forecast holds, but robust when PV falls short and like
        # self-consumption mode; must outweigh the smoothing of a flat export
        export_tie = 0.005 * (1 - t / n)
        cost[ge[t]] = (settings.export_penalty_ct_per_kwh / 100 - slot.feed_in + export_tie) * DT_H / 1000
        # Tie-breakers (max ~0.1 ct/kWh): when the timing does not matter, discharge in the
        # more expensive slots and, at equal prices, earlier, so the plan does not flip
        # between auto and hold every few minutes
        tie = 0.001 * (max(prices) - slot.price) / spread if spread > 0 else 0.0
        tie += 0.0001 * t / n
        cost[d[t]] = (wear + tie) * DT_H / 1000
    cost[s[-1]] -= terminal / 1000
    if full_short is not None:
        cost[full_short] = settings.full_charge_penalty / 1000
    # PV stored now is worth this much later; feed-in is what surplus earns otherwise
    stored_value = terminal * eta_c
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

    rows = 2 * n + sum(len(x) for x in u)
    a_eq = lil_matrix((rows, count))
    b_eq = np.zeros(rows)
    for t, slot in enumerate(inputs):
        # Power balance
        a_eq[t, gi[t]] = 1
        a_eq[t, d[t]] = 1
        a_eq[t, c[t]] = -1
        a_eq[t, ge[t]] = -1
        for k in range(len(flex)):
            a_eq[t, f[k][t]] = -1
        b_eq[t] = slot.load_w - slot.pv_w
        # State of charge
        row = n + t
        a_eq[row, s[t]] = 1
        a_eq[row, c[t]] = -eta_c * DT_H
        a_eq[row, d[t]] = DT_H / eta_d
        if split_grid:
            a_eq[row, cg[t]] = (eta_c - eta_g) * DT_H
        if t == 0:
            b_eq[row] = battery.soc_wh
        else:
            a_eq[row, s[t - 1]] = -1
    row = 2 * n
    for k in range(len(flex)):
        for (first, end, energy), unmet_index in zip(needs[k], u[k], strict=True):
            # Energy delivered in the need's window plus unmet rest equals the need
            for t in range(first, end):
                a_eq[row, f[k][t]] = DT_H
            a_eq[row, unmet_index] = 1
            b_eq[row] = energy
            row += 1

    # A battery below its floor may stay there but must not be drained further
    soc_min = min(max(battery.min_soc_wh, settings.reserve_wh), battery.soc_wh)
    # Charge limit for battery care; lifted while a full charge is due. Above it already,
    # the battery may stay there but not charge further
    soc_upper = battery.capacity_wh
    if settings.soc_limit_wh is not None and full_short is None:
        soc_upper = min(battery.capacity_wh, max(settings.soc_limit_wh, battery.soc_wh, soc_min))
    lower = np.zeros(count)
    upper = np.full(count, np.inf)
    for t, slot in enumerate(inputs):
        # Without grid charging only the surplus over the house load may go into the battery
        upper[c[t]] = battery.max_charge_w if settings.allow_grid_charge else min(
            battery.max_charge_w, max(slot.pv_w - slot.load_w, 0)
        )
        upper[d[t]] = battery.max_discharge_w
        if split_grid:
            upper[cg[t]] = battery.max_charge_w if settings.allow_grid_charge else 0.0
        # The grid only carries what the house, loads and battery can take or give
        upper[gi[t]] = max(slot.load_w, 0) + battery.max_charge_w + sum(
            load.max_power_w for load in flex if load.available[t]
        )
        # Export beyond the PV surplus would be stored energy sold at the feed-in price
        upper[ge[t]] = max(slot.pv_w - slot.load_w, 0) + (
            battery.max_discharge_w if settings.allow_battery_export else 0.0
        )
        lower[s[t]] = soc_min
        upper[s[t]] = soc_upper
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
    battery_binary = [t for t, i in enumerate(inputs) if i.price < 0 or export_value[t] < 0]
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

    def add_gate(slots: list[tuple[int, int]], flow: Any, flow_max: float, soc_wh: float | None,
                 after: list[tuple[int, float, int]]) -> None:
        previous = None
        for t, b in slots:
            gate_rows.append(({flow[t]: 1, gi[t]: -1, b: -flow_max}, 0.0))
            if soc_wh is not None:
                if t == 0:
                    upper[b] = min(upper[b], 1.0 if battery.soc_wh >= soc_wh else 0.0)
                else:
                    # SoC at the slot start; the gate closes again when the battery drops below
                    gate_rows.append(({b: soc_wh, s[t - 1]: -1}, 0.0))
            for k, required, last in after:
                if t > last:
                    continue  # the named load cannot receive more: no longer waiting for it
                if t == 0:
                    upper[b] = 0.0
                    continue
                gate_rows.append(({b: required, **{f[k][tau]: -DT_H for tau in range(t)}}, 0.0))
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
    battery_after = waits_for(settings.battery_gate_after)
    battery_slots = [(t, total + t) for t in range(n)] if battery_after else []
    total += len(battery_slots)
    cost = np.concatenate([cost, np.zeros(total - count)])
    lower = np.concatenate([lower, np.zeros(total - count)])
    upper = np.concatenate([upper, np.ones(total - count)])
    for k, slots in gate_slots:
        load = flex[k]
        add_gate(slots, f[k], load.max_power_w, load.gate_battery_wh, waits_for(load.gate_after))
    if battery_slots:
        add_gate(battery_slots, c, battery.max_charge_w, None, battery_after)
    if full_short is not None:
        # soc at the target slot plus shortfall reaches (almost) full
        gate_rows.append(({s[settings.full_charge_slot]: -1, full_short: -1}, -battery.capacity_wh * FULL_FRACTION))
    integrality = np.zeros(total)
    integrality[count:] = 1
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
        a_ub = lil_matrix(
            (
                2 * (len(grid_binary) + len(battery_binary))
                + 2 * (size - binary_total)
                + (n if restrict_battery else 0)
                + (n if len(pv_only) > 1 else 0)
                + (2 * n if split_grid else 0)
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
        for k, t in enumerate(battery_binary):
            a_ub[row, c[t]], a_ub[row, y[k]] = 1, -upper[c[t]]
            a_ub[row + 1, d[t]], a_ub[row + 1, y[k]] = 1, upper[d[t]]
            b_ub[row + 1] = upper[d[t]]
            row += 2
        if restrict_battery:
            # The battery only serves the house, allowed loads and export
            for t, slot in enumerate(inputs):
                a_ub[row, d[t]] = 1
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
        if split_grid:
            for t, slot in enumerate(inputs):
                # The grid share is part of the charge; the rest cannot exceed the PV surplus
                a_ub[row, cg[t]], a_ub[row, c[t]] = 1, -1
                a_ub[row + 1, c[t]], a_ub[row + 1, cg[t]] = 1, -1
                b_ub[row + 1] = max(slot.pv_w - slot.load_w, 0.0)
                row += 2
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
        action, power = choose_action(
            slot.pv_w,
            slot.load_w + flex_now,
            x[c[t]],
            x[d[t]],
            settings.action_threshold_w,
            # Only a really full battery stops by itself; at a care limit charging must be limited
            full=soc_upper >= battery.capacity_wh and x[s[t]] >= battery.capacity_wh * FULL_FRACTION,
        )
        plan.slots.append(
            PlanSlot(
                start=slot.start,
                end=slot.start + timedelta(minutes=15),
                pv_w=slot.pv_w,
                load_w=slot.load_w,
                price=slot.price,
                feed_in=slot.feed_in,
                charge_w=float(x[c[t]]),
                discharge_w=float(x[d[t]]),
                import_w=float(x[gi[t]]),
                export_w=float(x[ge[t]]),
                soc_wh=float(x[s[t]]),
                action=action,
                action_power_w=power,
                flex_w={load.name: float(x[f[k][t]]) for k, load in enumerate(flex)},
            )
        )
    plan.flex = [
        FlexResult(
            name=load.name,
            energy_wh=sum(energy for _, _, energy in needs[k]),
            planned_wh=float(sum(x[f[k]]) * DT_H),
            unmet_wh=float(sum(x[index] for index in u[k])),
        )
        for k, load in enumerate(flex)
    ]
    smooth_actions(plan.slots)
    plan.terminal_value = terminal * plan.slots[-1].soc_wh / 1000
    plan.alternative_cost = sum(
        r.unmet_wh / 1000 * load.unmet_cost for r, load in zip(plan.flex, flex, strict=True) if has_alternative(load)
    )
    plan.cost = plan.alternative_cost + sum(
        (p.price * p.import_w - p.feed_in * p.export_w) * DT_H / 1000 for p in plan.slots
    )
    baseline_inputs, plan.baseline_alternative_cost = immediate_flex(inputs, flex)
    plan.baseline_cost, baseline_soc = simulate_self_consumption(baseline_inputs, battery)
    plan.baseline_cost += plan.baseline_alternative_cost
    plan.baseline_terminal_value = terminal * baseline_soc / 1000
    return plan
