"""Battery schedule optimization as a linear program (HiGHS via SciPy).

Per 15 minute slot t (powers in W, energy in Wh):
    pv + import + discharge = load + charge + export
    soc[t] = soc[t-1] + (charge * eta_c - discharge / eta_d) * dt
minimize   sum(price * import - feed_in * export + wear * discharge) * dt / 1000
           - value of the energy left at the end
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

DT_H = 0.25

type Action = Literal["auto", "hold", "force_charge", "force_discharge"]


@dataclass(frozen=True, kw_only=True)
class BatterySpec:
    """Battery characteristics in W, Wh and fractions."""

    capacity_wh: float
    soc_wh: float
    min_soc_wh: float
    max_charge_w: float
    max_discharge_w: float
    efficiency_charge: float = 0.95
    efficiency_discharge: float = 0.95


@dataclass(frozen=True, kw_only=True)
class Settings:
    """Optimization settings."""

    wear_ct_per_kwh: float = 3.0
    allow_grid_charge: bool = True
    # Extra cost per exported kWh so the battery keeps room for PV ("pv_absorption")
    export_penalty_ct_per_kwh: float = 0.0
    # Energy that must stay in the battery, e.g. for backup power ("backup")
    reserve_wh: float = 0.0
    # Below this the battery is considered idle when choosing actions
    action_threshold_w: float = 100.0


@dataclass(frozen=True, kw_only=True)
class SlotInput:
    """Forecasts and prices for one slot."""

    start: datetime
    pv_w: float
    load_w: float
    price: float  # import €/kWh
    feed_in: float  # export €/kWh


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


@dataclass(kw_only=True)
class Plan:
    """Result of an optimization run."""

    slots: list[PlanSlot] = field(default_factory=list)
    cost: float = 0.0
    baseline_cost: float = 0.0
    # Value of the energy left at the end; savings compare cost minus this value
    terminal_value: float = 0.0
    baseline_terminal_value: float = 0.0
    status: str = ""

    @property
    def savings(self) -> float:
        """Savings in € against self-consumption, including the energy left at the end."""
        return (self.baseline_cost - self.baseline_terminal_value) - (self.cost - self.terminal_value)


def choose_action(
    pv_w: float, load_w: float, charge_w: float, discharge_w: float, threshold: float
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
        # Deficit but the battery should not (fully) cover it
        return "hold", None
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


def optimize(inputs: list[SlotInput], battery: BatterySpec, settings: Settings = Settings()) -> Plan:
    """Compute the cost-optimal battery schedule."""
    n = len(inputs)
    if n == 0:
        return Plan(status="no input")
    # Variable layout: charge, discharge, import, export, soc (each n)
    c, d, gi, ge, s = (np.arange(n) + k * n for k in range(5))
    count = 5 * n
    eta_c, eta_d = battery.efficiency_charge, battery.efficiency_discharge
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
        cost[ge[t]] = (settings.export_penalty_ct_per_kwh / 100 - slot.feed_in) * DT_H / 1000
        # Tie-breakers (max ~0.1 ct/kWh): when the timing does not matter, discharge in the
        # more expensive slots and, at equal prices, earlier, so the plan does not flip
        # between auto and hold every few minutes
        tie = 0.001 * (max(prices) - slot.price) / spread if spread > 0 else 0.0
        tie += 0.0001 * t / n
        cost[d[t]] = (wear + tie) * DT_H / 1000
    cost[s[-1]] -= terminal / 1000

    a_eq = lil_matrix((2 * n, count))
    b_eq = np.zeros(2 * n)
    for t, slot in enumerate(inputs):
        # Power balance
        a_eq[t, gi[t]] = 1
        a_eq[t, d[t]] = 1
        a_eq[t, c[t]] = -1
        a_eq[t, ge[t]] = -1
        b_eq[t] = slot.load_w - slot.pv_w
        # State of charge
        row = n + t
        a_eq[row, s[t]] = 1
        a_eq[row, c[t]] = -eta_c * DT_H
        a_eq[row, d[t]] = DT_H / eta_d
        if t == 0:
            b_eq[row] = battery.soc_wh
        else:
            a_eq[row, s[t - 1]] = -1

    # A battery below its floor may stay there but must not be drained further
    soc_min = min(max(battery.min_soc_wh, settings.reserve_wh), battery.soc_wh)
    lower = np.zeros(count)
    upper = np.full(count, np.inf)
    for t, slot in enumerate(inputs):
        upper[c[t]] = battery.max_charge_w if settings.allow_grid_charge else min(
            battery.max_charge_w, max(slot.pv_w, 0)
        )
        upper[d[t]] = battery.max_discharge_w
        # The grid only carries what the house and battery can take or give
        upper[gi[t]] = max(slot.load_w, 0) + battery.max_charge_w
        upper[ge[t]] = max(slot.pv_w, 0) + battery.max_discharge_w
        lower[s[t]] = soc_min
        upper[s[t]] = battery.capacity_wh

    # Import and export (charge and discharge) at the same time is physically impossible.
    # A linear model only does it when it pays: import cheaper than export, or negative
    # prices that reward burning energy in losses. Only those slots get binaries.
    export_value = [i.feed_in - settings.export_penalty_ct_per_kwh / 100 for i in inputs]
    grid_binary = [t for t, i in enumerate(inputs) if i.price < export_value[t]]
    battery_binary = [t for t, i in enumerate(inputs) if i.price < 0 or export_value[t] < 0]
    z = count + np.arange(len(grid_binary))
    y = count + len(grid_binary) + np.arange(len(battery_binary))
    total = count + len(grid_binary) + len(battery_binary)
    cost = np.concatenate([cost, np.zeros(total - count)])
    lower = np.concatenate([lower, np.zeros(total - count)])
    upper = np.concatenate([upper, np.ones(total - count)])
    integrality = np.zeros(total)
    integrality[count:] = 1

    a_ub = lil_matrix((2 * (len(grid_binary) + len(battery_binary)), total))
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

    a_eq_full = lil_matrix((2 * n, total))
    a_eq_full[:, :count] = a_eq
    constraints = [LinearConstraint(a_eq_full.tocsr(), b_eq, b_eq)]
    if row:
        constraints.append(LinearConstraint(a_ub.tocsr(), -np.inf, b_ub))
    result = milp(
        cost,
        constraints=constraints,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        options={"time_limit": 20},
    )
    plan = Plan(status=result.message)
    if not result.success:
        return plan
    x = result.x
    for t, slot in enumerate(inputs):
        action, power = choose_action(
            slot.pv_w, slot.load_w, x[c[t]], x[d[t]], settings.action_threshold_w
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
            )
        )
    smooth_actions(plan.slots)
    plan.terminal_value = terminal * plan.slots[-1].soc_wh / 1000
    plan.cost = sum(
        (p.price * p.import_w - p.feed_in * p.export_w) * DT_H / 1000 for p in plan.slots
    )
    plan.baseline_cost, baseline_soc = simulate_self_consumption(inputs, battery)
    plan.baseline_terminal_value = terminal * baseline_soc / 1000
    return plan
