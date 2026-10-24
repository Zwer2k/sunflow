"""Device parameters and the site-wide view across configured devices."""

from dataclasses import dataclass, field
from typing import Any

from .config_store import DeviceConfig
from .discovery import Snapshot, entity_has_key
from .resolver import Balance, RoleValue, check_balance, device_entities, preview

# Roles that carry a plant's AC output, in order of preference
PV_OUTPUT_ROLES = ("ac_power", "pv_power")


def derive_parameter(
    spec: dict[str, Any], snapshot: Snapshot, ha_device_id: str
) -> float | None:
    """Suggest a parameter value from sensors as defined by the profile."""
    derive = spec.get("derive")
    if derive is None:
        return None
    total = 0.0
    found = False
    for entity in device_entities(snapshot, ha_device_id):
        if not entity_has_key(entity, derive["sum_key"]):
            continue
        try:
            total += float(snapshot.states[entity["entity_id"]]["state"])
        except KeyError, TypeError, ValueError:
            continue
        found = True
    return round(total * derive.get("factor", 1)) if found else None


def device_parameters(
    profile: dict[str, Any], device: DeviceConfig, snapshot: Snapshot
) -> list[dict[str, Any]]:
    """Return every profile parameter with stored, derived and default value."""
    result = []
    for name, spec in profile.get("parameters", {}).items():
        derived = derive_parameter(spec, snapshot, device.ha_device_id)
        stored = device.parameters.get(name)
        default = spec.get("default")
        result.append(
            {
                "name": name,
                "unit": spec["unit"],
                "min": spec.get("min"),
                "max": spec.get("max"),
                "advanced": spec.get("advanced", False),
                "stored": stored,
                "derived": derived,
                "default": default,
                "effective": next(
                    (v for v in (stored, derived, default) if v is not None), None
                ),
            }
        )
    return result


@dataclass(kw_only=True)
class CrossCheck:
    """Output of one device compared with another device's measurement of it."""

    device_id: str
    measured_by: str
    role: str
    own_value: float | None
    measured_value: float | None
    deviation: float | None
    plausible: bool | None


@dataclass(kw_only=True)
class DeviceStatus:
    """Live roles and balance of one configured device."""

    device_id: str
    name: str
    profile: str
    capabilities: list[str]
    roles: list[RoleValue]
    balance: Balance | None
    pv_output: float | None


@dataclass(kw_only=True)
class SiteStatus:
    """Site-wide view: devices, cross checks and the PV total."""

    devices: list[DeviceStatus] = field(default_factory=list)
    cross_checks: list[CrossCheck] = field(default_factory=list)
    pv_total: float | None = None
    hints: list[str] = field(default_factory=list)


def pv_output(roles: list[RoleValue]) -> float | None:
    """Return the AC output of a PV plant from its roles."""
    by_role = {r.role: r.value for r in roles}
    return next((by_role[r] for r in PV_OUTPUT_ROLES if by_role.get(r) is not None), None)


def site_status(
    devices: list[DeviceConfig],
    profiles: dict[str, dict[str, Any]],
    snapshot: Snapshot,
    tolerance: float = 0.2,
    min_abs: float = 150,
) -> SiteStatus:
    """Build the site view; PV measured by another device is counted once."""
    site = SiteStatus()
    by_id: dict[str, DeviceStatus] = {}
    for device in devices:
        profile = profiles[device.profile]
        roles = preview(
            profile,
            snapshot,
            device.ha_device_id,
            {r: o.model_dump() for r, o in device.role_overrides.items()},
        )
        status = DeviceStatus(
            device_id=device.id,
            name=device.name,
            profile=device.profile,
            capabilities=profile["capabilities"],
            roles=roles,
            balance=check_balance(roles),
            pv_output=pv_output(roles) if "pv" in profile["capabilities"] else None,
        )
        site.devices.append(status)
        by_id[device.id] = status

    measured_roles: set[tuple[str, str]] = set()
    for device in devices:
        if device.measured_by is None:
            continue
        own = by_id[device.id].pv_output
        other = by_id.get(device.measured_by.device_id)
        measured = (
            next(
                (r.value for r in other.roles if r.role == device.measured_by.role),
                None,
            )
            if other
            else None
        )
        measured_roles.add((device.measured_by.device_id, device.measured_by.role))
        deviation = own - measured if own is not None and measured is not None else None
        site.cross_checks.append(
            CrossCheck(
                device_id=device.id,
                measured_by=device.measured_by.device_id,
                role=device.measured_by.role,
                own_value=own,
                measured_value=measured,
                deviation=deviation,
                plausible=None
                if deviation is None
                else abs(deviation) <= max(abs(own or 0) * tolerance, min_abs),
            )
        )

    for status in site.devices:
        # Only hint when external PV is actually producing; idle pv_ext reads ~0
        has_pv_ext = any(
            r.role == "pv_ext_power" and r.value is not None and abs(r.value) > 50
            for r in status.roles
        )
        if has_pv_ext and (status.device_id, "pv_ext_power") not in measured_roles:
            site.hints.append(
                f"{status.name} misst eine externe PV-Anlage, die noch keinem Gerät zugeordnet ist."
            )

    outputs = [s.pv_output for s in site.devices if s.pv_output is not None]
    site.pv_total = sum(outputs) if outputs else None
    return site
