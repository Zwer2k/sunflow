"""Resolve profile roles to entities and normalize their values."""

from dataclasses import dataclass
from typing import Any

from .discovery import Snapshot, entity_has_key

UNAVAILABLE_STATES = {"unavailable", "unknown", ""}

# Factor to the canonical unit (W, Wh, %)
UNIT_FACTORS: dict[str, tuple[str, float]] = {
    "W": ("W", 1),
    "kW": ("W", 1_000),
    "MW": ("W", 1_000_000),
    "Wh": ("Wh", 1),
    "kWh": ("Wh", 1_000),
    "MWh": ("Wh", 1_000_000),
    "%": ("%", 1),
}

# Sunflow conventions: battery + = charge, grid + = import
CANONICAL_SIGNS = {"charge_positive", "import_positive"}


@dataclass(frozen=True, kw_only=True)
class RoleValue:
    """A role bound to an entity with its raw and normalized value."""

    role: str
    key: str
    entity_id: str | None
    raw_state: str | None
    raw_unit: str | None
    value: float | None
    unit: str | None
    optional: bool

    @property
    def missing(self) -> bool:
        """Return True if no entity was found for the role."""
        return self.entity_id is None


def device_entities(snapshot: Snapshot, device_id: str) -> list[dict[str, Any]]:
    """Return enabled entities of a device and of its sub-devices."""
    device_ids = {device_id} | {
        d["id"] for d in snapshot.devices if d.get("via_device_id") == device_id
    }
    return [
        e
        for e in snapshot.entities
        if e.get("device_id") in device_ids and not e.get("disabled_by")
    ]


def bind_roles(
    profile: dict[str, Any], entities: list[dict[str, Any]]
) -> dict[str, str | None]:
    """Return role -> entity_id; parent device entities win over sub-devices."""
    bindings: dict[str, str | None] = {}
    for role, spec in profile["roles"].items():
        matches = [e for e in entities if entity_has_key(e, spec["key"])]
        # Prefer the shortest unique_id: "sofar_battery_power_total" over "sofar_battery_1_battery_power_total"
        matches.sort(key=lambda e: len(e.get("unique_id") or ""))
        bindings[role] = matches[0]["entity_id"] if matches else None
    return bindings


def normalize(
    raw_state: str | None, raw_unit: str | None, sign: str | None
) -> tuple[float | None, str | None]:
    """Convert a state to the canonical unit and sign convention."""
    if raw_state is None or raw_state in UNAVAILABLE_STATES:
        return None, None
    try:
        number = float(raw_state)
    except ValueError:
        return None, raw_unit
    unit, factor = UNIT_FACTORS.get(raw_unit or "", (raw_unit, 1))
    value = number * factor
    if sign is not None and sign not in CANONICAL_SIGNS:
        value = -value
    return value, unit


@dataclass(frozen=True, kw_only=True)
class Balance:
    """Load computed from the energy flows compared to the reported load."""

    method: str
    computed_load: float
    reported_load: float
    deviation: float
    plausible: bool


def check_balance(
    values: list[RoleValue], tolerance: float = 0.1, min_abs: float = 100
) -> Balance | None:
    """Compare the load computed from the energy flows with the reported load.

    AC method: load = grid + inverter AC output + pv_ext.
    DC method (no inverter output available): load = grid + pv + pv_ext - battery;
    it ignores conversion losses, so it gets a larger absolute floor.
    Returns None when a needed value is not available.
    """
    by_role = {v.role: v.value for v in values}
    if by_role.get("grid_power") is None or by_role.get("load_power") is None:
        return None
    pv_ext = by_role.get("pv_ext_power") or 0
    if by_role.get("inverter_power") is not None:
        method = "ac"
        computed = by_role["grid_power"] + by_role["inverter_power"] + pv_ext
    elif by_role.get("pv_power") is not None:
        method = "dc"
        computed = (
            by_role["grid_power"]
            + by_role["pv_power"]
            + pv_ext
            - (by_role.get("battery_power") or 0)
        )
        min_abs = max(min_abs, 500)
    else:
        return None
    reported = by_role["load_power"]
    deviation = computed - reported
    # Absolute floor so readings near zero do not fail on rounding
    allowed = max(abs(reported) * tolerance, min_abs)
    return Balance(
        method=method,
        computed_load=computed,
        reported_load=reported,
        deviation=deviation,
        plausible=abs(deviation) <= allowed,
    )


def preview(
    profile: dict[str, Any], snapshot: Snapshot, device_id: str
) -> list[RoleValue]:
    """Return the current normalized value of every role of a device."""
    bindings = bind_roles(profile, device_entities(snapshot, device_id))
    result: list[RoleValue] = []
    for role, spec in profile["roles"].items():
        entity_id = bindings[role]
        state = snapshot.states.get(entity_id) if entity_id else None
        raw_state = state["state"] if state else None
        raw_unit = (
            state["attributes"].get("unit_of_measurement") if state else None
        ) or spec.get("unit")
        value, unit = normalize(raw_state, raw_unit, spec.get("sign"))
        result.append(
            RoleValue(
                role=role,
                key=spec["key"],
                entity_id=entity_id,
                raw_state=raw_state,
                raw_unit=raw_unit,
                value=value,
                unit=unit,
                optional=spec.get("optional", False),
            )
        )
    return result
