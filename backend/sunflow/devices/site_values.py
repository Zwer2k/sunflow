"""PV, battery, grid and house load of the site, the same for live values, recording, history and planning.

Each quantity is measured, or calculated from the balance when nothing measures it:
house load = PV + grid - battery, grid = house load - PV + battery (battery + = charge,
grid + = import). Grid and house load are measured by the device chosen under the
measurement sources, by default a meter of its own (e.g. one added by hand) before the
inverter. Every value comes with the statistics whose signed sum gives its history.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from sunflow.config.models import SunflowConfig

from .discovery import Snapshot
from .resolver import CANONICAL_SIGNS, RoleValue
from .site import SiteStatus, measurement_device, pv_output_roles, site_status

Origin = Literal["measured", "calculated"]


@dataclass(frozen=True, kw_only=True)
class Term:
    """One statistic of a quantity's history and its factor (sign)."""

    entity_id: str
    factor: float


@dataclass(frozen=True, kw_only=True)
class SiteValue:
    """A quantity now (W) and where it comes from; origin None: not known."""

    value: float | None
    origin: Origin | None
    terms: list[Term] = field(default_factory=list)
    # Unit and decimals of the measuring entity
    display: dict[str, Any] | None = None


def sign_factor(role: RoleValue) -> float:
    """-1 for a role whose sensor counts the other way round than Sunflow."""
    return -1.0 if role.sign is not None and role.sign not in CANONICAL_SIGNS else 1.0


def role_terms(role: RoleValue, roles: dict[str, RoleValue]) -> list[Term]:
    """The statistics of a role; measured by two sensors (minus) both, the other one negated."""
    if role.entity_id is None:
        return []
    terms = [Term(entity_id=role.entity_id, factor=sign_factor(role))]
    if role.minus_role and (other := roles.get(role.minus_role)) is not None and other.entity_id:
        terms.append(Term(entity_id=other.entity_id, factor=-sign_factor(other)))
    return terms


def _scaled(terms: list[Term], factor: float) -> list[Term]:
    return [Term(entity_id=t.entity_id, factor=t.factor * factor) for t in terms]


def _merged(terms: list[Term]) -> list[Term]:
    """Each statistic once (a history keeps one mean per statistic), cancelled ones left out."""
    factors: dict[str, float] = {}
    for term in terms:
        factors[term.entity_id] = factors.get(term.entity_id, 0.0) + term.factor
    return [Term(entity_id=entity_id, factor=factor) for entity_id, factor in factors.items() if factor]


def site_values(
    config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot, site: SiteStatus | None = None
) -> dict[str, SiteValue]:
    """PV, battery, grid and load of the site ("pv", "battery", "grid", "load")."""
    site = site or site_status(config.devices, profiles, snapshot, measured_by=config.measured_by)
    by_device = {status.device_id: status for status in site.devices}

    pv_terms: list[Term] = []
    battery_terms: list[Term] = []
    battery_values: list[float] = []
    has_battery = False
    for status in site.devices:
        roles = {r.role: r for r in status.roles}
        if "pv" in status.capabilities and (
            role := next((roles[r] for r in pv_output_roles(status.capabilities) if r in roles and roles[r].entity_id), None)
        ):
            pv_terms += role_terms(role, roles)
        if "battery" in status.capabilities and (battery := roles.get("battery_power")) is not None:
            has_battery = True
            battery_terms += role_terms(battery, roles)
            if battery.value is not None:
                battery_values.append(battery.value)
    has_pv = any("pv" in s.capabilities for s in site.devices)
    # At night inverters often report nothing: no PV is 0 W, as is a site without PV
    pv = SiteValue(value=site.pv_total or 0.0, origin="measured" if has_pv else None, terms=pv_terms)
    battery = SiteValue(
        value=sum(battery_values) if battery_values else (None if has_battery else 0.0),
        origin="measured" if has_battery else None,
        terms=battery_terms,
    )

    def measured(quantity: str, role_name: str) -> SiteValue:
        device = measurement_device(config, profiles, quantity)
        status = by_device.get(device.id) if device is not None else None
        roles = {r.role: r for r in status.roles} if status is not None else {}
        role = roles.get(role_name)
        if role is None or role.entity_id is None:
            return SiteValue(value=None, origin=None)
        return SiteValue(value=role.value, origin="measured", terms=role_terms(role, roles), display=role.display)

    grid = measured("grid", "grid_power")
    load = measured("load", "load_power")
    known = battery.value is not None
    if load.origin is None and grid.origin is not None and known:
        load = SiteValue(
            value=max(pv.value + grid.value - battery.value, 0.0) if grid.value is not None else None,  # type: ignore[operator]
            origin="calculated",
            terms=_merged([*pv.terms, *grid.terms, *_scaled(battery.terms, -1.0)]),
        )
    elif grid.origin is None and load.origin is not None and known:
        grid = SiteValue(
            value=load.value - pv.value + battery.value if load.value is not None else None,  # type: ignore[operator]
            origin="calculated",
            terms=_merged([*load.terms, *_scaled(pv.terms, -1.0), *battery.terms]),
        )
    return {"pv": pv, "battery": battery, "grid": grid, "load": load}
