"""Compatibility of a manifest with a profile (design/heat_pump.md, section 3.6).

Sunflow checks on every load exactly what it uses: per role of the profile (companion
included) the key, its domain, unit and value keys, and for written roles the range and
step. What a role needs is derived from the profile in one place, role_requirements.
"""

from dataclasses import dataclass
from typing import Any

from sunflow.i18n import language, t

from .defaults import MAX_SETPOINT_STEP_K
from .manifest import entities_by_key, valid_manifest

# Domains whose state is on/off without a value map in the manifest
ON_OFF_DOMAINS = {"switch", "binary_sensor"}
# Sunflow writes a set value only to a number entity
WRITABLE_DOMAIN = "number"


@dataclass(frozen=True, kw_only=True)
class RoleRequirement:
    """What Sunflow needs from the manifest entity of a role."""

    role: str
    key: str
    domains: tuple[str, ...] | None
    unit: str | None
    # Value keys Sunflow reacts to (from states and flow_control of the profile)
    values: frozenset[str]
    # Set value range the entity must cover (roles Sunflow writes)
    cover: tuple[float, float] | None
    # Coarsest step Sunflow can write with (roles Sunflow writes)
    max_step: float | None
    optional: bool
    companion: bool = False
    # Sunflow writes this role: domain number, range and step are checked
    written: bool = False


@dataclass(frozen=True, kw_only=True)
class RoleProblem:
    """A role the manifest does not serve as needed."""

    role: str
    key: str
    optional: bool
    reason: str


@dataclass(frozen=True, kw_only=True)
class Compatibility:
    """Result of the check; incompatible when a required role has a problem."""

    problems: tuple[RoleProblem, ...] = ()

    @property
    def compatible(self) -> bool:
        """No required role has a problem."""
        return not any(not p.optional for p in self.problems)

    def reasons(self, optional: bool | None = None) -> list[str]:
        """Problem texts; optional=False only required roles, True only optional ones."""
        return [p.reason for p in self.problems if optional is None or p.optional == optional]

    def for_role(self, role: str) -> RoleProblem | None:
        """The problem of a role, if any."""
        return next((p for p in self.problems if p.role == role), None)


def role_requirements(profile: dict[str, Any]) -> dict[str, RoleRequirement]:
    """Domain, unit, value keys and range per role, derived from the profile."""
    flow = profile.get("flow_control", {})
    strategies = flow.get("strategies", {})
    values: dict[str, set[str]] = {}
    for role, mapping in profile.get("states", {}).items():
        for keys in mapping.values():
            values.setdefault(role, set()).update(keys)
    for strategy in strategies.values():
        for role, value in strategy["when"].items():
            values.setdefault(role, set()).add(value)
    if block := flow.get("heating_block"):
        values.setdefault("heating_block", set()).update((block["on"], block["off"]))
    written = {s["write"]["role"] for s in strategies.values() if "write" in s and s.get("enabled", True)}
    limits = flow.get("limits")

    result: dict[str, RoleRequirement] = {}
    for companion, roles in ((False, profile["roles"]), (True, profile.get("companion", {}).get("roles", {}))):
        for role, spec in roles.items():
            is_written = not companion and role in written
            result[role] = RoleRequirement(
                role=role,
                key=spec["key"],
                # A written role is a number, whatever else the profile allows
                domains=(WRITABLE_DOMAIN,) if is_written else tuple(spec["domain"]) if "domain" in spec else None,
                unit=spec.get("unit"),
                values=frozenset(values.get(role, set())),
                cover=(limits["min"], limits["max"]) if is_written and limits else None,
                max_step=MAX_SETPOINT_STEP_K if is_written else None,
                optional=spec.get("optional", False),
                companion=companion,
                written=is_written,
            )
    return result


def _number(value: float) -> str:
    text = f"{value:g}"
    return text.replace(".", ",") if language() == "de" else text


def _value_keys(entity: dict[str, Any]) -> set[str]:
    """Keys a manifest entity can take: translation keys, else raw values."""
    if entity["domain"] in ON_OFF_DOMAINS:
        return {"on", "off"}
    keys = set()
    for item in entity.get("values") or []:
        if isinstance(item, list) and item:
            keys.add(str(item[0]))
            if len(item) > 2 and isinstance(item[2], str):
                keys.add(item[2])
    return keys


def role_problems(requirement: RoleRequirement, entity: dict[str, Any] | None) -> list[str]:
    """Why the manifest entity does not serve the role; empty when it does."""
    key = requirement.key
    if entity is None:
        return [t("Schlüssel {key} fehlt", key=key)]
    problems = []
    if requirement.domains and entity["domain"] not in requirement.domains:
        problems.append(
            t("Schlüssel {key} ist {domain} statt {expected}", key=key, domain=entity["domain"], expected="/".join(requirement.domains))
        )
    if requirement.unit is not None and entity.get("unit") != requirement.unit:
        problems.append(
            t("Schlüssel {key} hat die Einheit {unit} statt {expected}", key=key, unit=entity.get("unit") or "–", expected=requirement.unit)
        )
    if missing := sorted(requirement.values - _value_keys(entity)):
        problems.append(t("Schlüssel {key}: Auswahlwerte fehlen: {values}", key=key, values=", ".join(missing)))
    if requirement.cover is not None:
        low, high = requirement.cover
        lowest, highest = entity.get("min"), entity.get("max")
        if not isinstance(lowest, int | float) or not isinstance(highest, int | float) or lowest > low or highest < high:
            problems.append(
                t(
                    "Schlüssel {key}: Bereich {min}–{max} deckt {low}–{high} nicht ab",
                    key=key,
                    min=_number(lowest) if isinstance(lowest, int | float) else "?",
                    max=_number(highest) if isinstance(highest, int | float) else "?",
                    low=_number(low),
                    high=_number(high),
                )
            )
    if requirement.max_step is not None:
        step = entity.get("step")
        if not isinstance(step, int | float) or not 0 < step <= requirement.max_step:
            problems.append(
                t(
                    "Schrittweite von {name} zu grob ({step} K, höchstens {max} K) – Firmware der Komponente aktualisieren",
                    name=entity["name"],
                    step=_number(step) if isinstance(step, int | float) else "?",
                    max=_number(requirement.max_step),
                )
            )
    return problems


def check_compatibility(
    profile: dict[str, Any], manifest: dict[str, Any] | None, companion: dict[str, Any] | None = None
) -> Compatibility:
    """Check every role of the profile against the manifest; companion roles only with a companion manifest."""
    if not valid_manifest(manifest):
        return Compatibility(problems=(RoleProblem(role="", key="", optional=False, reason=t("Manifest nicht lesbar")),))
    expected = profile.get("match", {}).get("manifest", {}).get("id")
    if expected is not None and manifest["id"] != expected:
        return Compatibility(
            problems=(RoleProblem(role="", key="", optional=False, reason=t("Manifest {id} statt {expected}", id=manifest["id"], expected=expected)),)
        )
    own, other = entities_by_key(manifest), entities_by_key(companion) if valid_manifest(companion) else None
    problems = []
    for requirement in role_requirements(profile).values():
        entities = other if requirement.companion else own
        if entities is None:
            continue
        if reasons := role_problems(requirement, entities.get(requirement.key)):
            problems.append(
                RoleProblem(role=requirement.role, key=requirement.key, optional=requirement.optional, reason="; ".join(reasons))
            )
    return Compatibility(problems=tuple(problems))
