"""House and battery model shared by the device simulators."""

from datetime import datetime
import math


def site_now(peak_pv_w: float = 900.0) -> tuple[float, float]:
    """PV by time of day and a varying house load (W)."""
    hour = datetime.now().hour + datetime.now().minute / 60
    pv = max(0.0, math.sin((hour - 6) / 14 * math.pi)) * peak_pv_w
    load = 280 + 120 * math.sin(hour / 24 * 4 * math.pi)
    return pv, load


class SimBattery:
    """A battery whose power (+ = discharge) is set by the device's mode each step."""

    def __init__(self, capacity_wh: float, soc_pct: float, max_charge_w: float, max_discharge_w: float) -> None:
        self.capacity_wh = capacity_wh
        self.soc_wh = capacity_wh * soc_pct / 100
        self.max_charge_w = max_charge_w
        self.max_discharge_w = max_discharge_w
        self.power_w = 0.0
        self.charged_wh = 0.0
        self.discharged_wh = 0.0

    @property
    def soc_pct(self) -> float:
        return self.soc_wh / self.capacity_wh * 100

    def self_consumption(self, pv: float, load: float, floor_pct: float = 5.0) -> float:
        """Power covering the deficit or taking the surplus."""
        surplus = pv - load
        if surplus > 0:
            return -min(surplus, self.max_charge_w) if self.soc_pct < 100 else 0.0
        return min(-surplus, self.max_discharge_w) if self.soc_pct > floor_pct else 0.0

    def charge(self, power: float, target_pct: float = 100.0) -> float:
        return -min(power, self.max_charge_w) if self.soc_pct < target_pct else 0.0

    def discharge(self, power: float, floor_pct: float = 5.0) -> float:
        return min(power, self.max_discharge_w) if self.soc_pct > floor_pct else 0.0

    def step(self, power: float, seconds: float) -> None:
        """Run at this power (+ = discharge) for a while."""
        self.power_w = power
        energy = power * seconds / 3600
        self.soc_wh = min(max(self.soc_wh - energy, 0.0), self.capacity_wh)
        if power < 0:
            self.charged_wh -= energy
        else:
            self.discharged_wh += energy
