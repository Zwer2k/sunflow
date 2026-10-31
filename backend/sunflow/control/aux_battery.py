"""Keep the 12 V battery of a plugged-in car charged.

Some cars (e.g. Fiat 500e) drain their 12 V battery while they stay plugged in
without charging. Below the set voltage the wallbox charges with its smallest
power for a few minutes, so the car's DC/DC converter recharges it; then the
plan takes over again. A pause keeps a sensor that updates rarely from starting
it over and over.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sunflow.config.models import AuxBatteryGuard, Vehicle


@dataclass
class AuxCharge:
    """A running or past top-up of one wallbox."""

    vehicle_id: str | None = None
    until: datetime | None = None
    last_end: datetime | None = None
    voltage: float | None = None


def aux_voltage(vehicle: Vehicle, states: dict[str, dict[str, Any]]) -> float | None:
    """Voltage of the car's 12 V battery, None while not watched or unreadable."""
    try:
        return float(states.get(vehicle.aux_battery.voltage_entity or "", {}).get("state"))
    except TypeError, ValueError:
        return None


def aux_charge_due(
    guard: AuxBatteryGuard, vehicle_id: str | None, voltage: float | None, connected: bool, state: AuxCharge, now: datetime
) -> bool:
    """Whether the wallbox charges for the 12 V battery now; starts and ends a top-up in state."""
    if state.until is not None and (not connected or vehicle_id != state.vehicle_id or now >= state.until):
        state.last_end, state.until = min(now, state.until), None
    if state.until is not None:
        return True
    if not connected or vehicle_id is None or guard.voltage_entity is None or voltage is None:
        return False
    if voltage >= guard.min_voltage_v:
        return False
    if state.last_end is not None and now - state.last_end < timedelta(minutes=guard.pause_min):
        return False
    state.vehicle_id, state.voltage = vehicle_id, voltage
    state.until = now + timedelta(minutes=guard.charge_min)
    return True
