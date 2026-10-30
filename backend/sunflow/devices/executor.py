"""Translate profile actions into Home Assistant action calls and run them."""

from dataclasses import dataclass, field
from datetime import date
import json
import logging
from pathlib import Path
import re
from typing import Any, Protocol

from sunflow.ha import HAClient

from .discovery import entity_has_key

_LOGGER = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{\{\s*(-?)([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# Calls for a directly connected device (sunflow.direct) instead of Home Assistant
DIRECT = "direct"
DIRECT_STEPS = {"modbus", "http"}
STEP_DOMAINS = {"number": "number", "select": "select", "press": "button", "switch": "switch"}


class ExecutorError(Exception):
    """An action cannot be planned or executed."""


@dataclass(kw_only=True)
class PlannedCall:
    """One Home Assistant action call derived from a profile step."""

    domain: str
    service: str
    data: dict[str, Any]
    entity_id: str | None
    storage: str
    write: str
    status: str = "planned"
    reason: str | None = None
    # A number this close to the current value counts as already set (spares flash writes)
    tolerance: float = 0.0


@dataclass(kw_only=True)
class ActionResult:
    """Outcome of planning or executing an action."""

    action: str
    params: dict[str, float]
    dry_run: bool
    calls: list[PlannedCall] = field(default_factory=list)


def resolve_value(expr: float | str, variables: dict[str, float]) -> float:
    """Resolve a number or a (negated) variable name."""
    if isinstance(expr, int | float):
        return expr
    negate = expr.startswith("-")
    name = expr.removeprefix("-")
    if name not in variables:
        raise ExecutorError(f"Missing value for {name}")
    return -variables[name] if negate else variables[name]


def render(template: Any, variables: dict[str, float]) -> Any:
    """Replace {{ name }} / {{ -name }} placeholders in a step value."""
    if not isinstance(template, str):
        return template
    if match := PLACEHOLDER.fullmatch(template.strip()):
        return resolve_value(f"{match[1]}{match[2]}", variables)
    return PLACEHOLDER.sub(
        lambda m: str(resolve_value(f"{m[1]}{m[2]}", variables)), template
    )


def render_deep(template: Any, variables: dict[str, float]) -> Any:
    """render() for every string inside lists and dicts (request bodies)."""
    if isinstance(template, dict):
        return {k: render_deep(v, variables) for k, v in template.items()}
    if isinstance(template, list):
        return [render_deep(v, variables) for v in template]
    return render(template, variables)


def find_entity(
    entities: list[dict[str, Any]], domain: str, key: str
) -> str | None:
    """Return the entity of the given domain whose unique_id ends with key."""
    matches = [
        e
        for e in entities
        if e["entity_id"].startswith(f"{domain}.") and entity_has_key(e, key)
    ]
    matches.sort(key=lambda e: len(e.get("unique_id") or ""))
    return matches[0]["entity_id"] if matches else None


def plan_steps(
    steps: list[dict[str, Any]],
    variables: dict[str, Any],
    entities: list[dict[str, Any]],
    default_storage: str,
    roles: dict[str, str] | None = None,
) -> list[PlannedCall]:
    """Turn profile steps into planned action calls.

    A step names its entity by key (unique id suffix) or by role; the latter for
    devices whose entities the user chose (own consumers). A step with an optional
    role that is not bound is left out.
    """
    calls: list[PlannedCall] = []
    for step in steps:
        ((kind, spec),) = step.items()
        storage = spec.get("storage", default_storage)
        write = spec.get("write", "always")
        if kind in DIRECT_STEPS:
            if kind == "modbus":
                value = render(spec["value"], variables)
                if isinstance(value, int | float):
                    value = min(max(value, spec.get("min", value)), spec.get("max", value))
                target = f"modbus:{spec['address']}"
            else:
                value = render_deep(spec.get("body"), variables)
                target = f"http:{spec['path']}"
            calls.append(
                PlannedCall(
                    domain=DIRECT,
                    service=kind,
                    data={"spec": {k: v for k, v in spec.items() if k not in ("value", "body")}, "value": value},
                    entity_id=f"{DIRECT}:{target}",
                    storage=storage,
                    write=write,
                    tolerance=spec.get("tolerance", 0.0),
                )
            )
            continue
        if kind == "service":
            domain, service = spec["action"].split(".", 1)
            calls.append(
                PlannedCall(
                    domain=domain,
                    service=service,
                    data={k: render(v, variables) for k, v in spec.get("data", {}).items()},
                    entity_id=None,
                    storage=storage,
                    write=write,
                )
            )
            continue

        if "role" in spec:
            entity_id = (roles or {}).get(spec["role"])
            if entity_id is None:
                if spec.get("optional"):
                    continue
                raise ExecutorError(f"No entity for role {spec['role']}")
            # A switch role may be an input_boolean as well
            domain = entity_id.split(".", 1)[0]
        else:
            domain = STEP_DOMAINS[kind]
            entity_id = find_entity(entities, domain, spec["key"])
            if entity_id is None:
                raise ExecutorError(f"No {domain} entity for key {spec['key']}")
        match kind:
            case "number":
                value = render(spec["value"], variables)
                # Number entities have limits (e.g. 6..32 A)
                if isinstance(value, int | float):
                    value = min(max(value, spec.get("min", value)), spec.get("max", value))
                service, data = "set_value", {"value": value}
            case "select":
                service, data = "select_option", {"option": str(render(spec["option"], variables))}
            case "switch":
                service, data = f"turn_{spec['state']}", {}
            case _:
                service, data = "press", {}
        calls.append(
            PlannedCall(
                domain=domain,
                service=service,
                data=data,
                entity_id=entity_id,
                storage=storage,
                write=write,
                tolerance=spec.get("tolerance", 0.0),
            )
        )
    return calls


def plan_action(
    profile: dict[str, Any],
    action: str,
    parameters: dict[str, float],
    runtime: dict[str, float],
    entities: list[dict[str, Any]],
) -> tuple[dict[str, float], list[PlannedCall]]:
    """Resolve an action preset to primitive parameters and planned calls."""
    if action not in profile.get("actions", {}):
        raise ExecutorError(f"Unknown action {action}")
    ((_, primitive),) = profile["primitive"].items()
    defaults = {
        name: spec["default"]
        for name, spec in profile.get("parameters", {}).items()
        if "default" in spec
    }
    variables = {**defaults, **parameters, **runtime}
    params = {
        name: resolve_value(expr, variables)
        for name, expr in profile["actions"][action].items()
    }
    calls = plan_steps(
        primitive["steps"], params, entities, primitive.get("storage", "eeprom")
    )
    return params, calls


def plan_charge(
    profile: dict[str, Any],
    action: str,
    parameters: dict[str, float],
    runtime: dict[str, Any],
    entities: list[dict[str, Any]],
    roles: dict[str, str] | None = None,
) -> list[PlannedCall]:
    """Calls of a charge control action ("charge", "stop", "release") of a loadpoint or own consumer."""
    control = profile.get("charge_control", {})
    if action not in control:
        raise ExecutorError(f"Unknown charge action {action}")
    defaults = {name: spec["default"] for name, spec in profile.get("parameters", {}).items() if "default" in spec}
    return plan_steps(control[action], {**defaults, **parameters, **runtime}, entities, "volatile", roles)


def plan_setup(
    profile: dict[str, Any], entities: list[dict[str, Any]]
) -> list[PlannedCall]:
    """Plan the one-time setup steps of a profile."""
    return plan_steps(profile.get("setup", []), {}, entities, "eeprom")


class EepromGuard:
    """Count EEPROM writes per entity and day, persisted in the data directory."""

    def __init__(self, data_dir: Path, max_writes_per_day: int) -> None:
        """Initialize the guard."""
        self._path = data_dir / "eeprom_writes.json"
        self._max = max_writes_per_day

    def _load(self) -> dict[str, int]:
        if not self._path.exists():
            return {}
        data = json.loads(self._path.read_text())
        today = date.today().isoformat()
        return data.get(today, {})

    def allowed(self, entity_id: str) -> bool:
        """Return True if another write is allowed today."""
        return self._load().get(entity_id, 0) < self._max

    def record(self, entity_id: str) -> None:
        """Record a write; only today's counters are kept."""
        counts = self._load()
        counts[entity_id] = counts.get(entity_id, 0) + 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({date.today().isoformat(): counts}))


def current_matches(
    call: PlannedCall, states: dict[str, dict[str, Any]], direct: DirectWriter | None = None
) -> bool:
    """Return True if the entity already has the value the call would write."""
    if call.domain == DIRECT:
        if direct is None or call.entity_id not in direct.values_written:
            return False
        last, value = direct.values_written[call.entity_id], call.data["value"]
        if isinstance(last, int | float) and isinstance(value, int | float):
            return abs(last - value) <= call.tolerance
        return last == value
    state = states.get(call.entity_id or "", {}).get("state")
    match call.service:
        case "select_option":
            return state == call.data["option"]
        case "set_value":
            try:
                return abs(float(state) - float(call.data["value"])) <= call.tolerance
            except TypeError, ValueError:
                return False
        case "turn_on" | "turn_off":
            return state == call.service.removeprefix("turn_")
    return False


class DirectWriter(Protocol):
    """A directly connected device (sunflow.direct.hub.DirectDevice)."""

    values_written: dict[str, Any]

    async def write(self, spec: dict[str, Any], value: Any) -> None:
        """Run a write step of the profile."""


async def execute(
    client: HAClient,
    calls: list[PlannedCall],
    states: dict[str, dict[str, Any]],
    guard: EepromGuard,
    dry_run: bool,
    direct: DirectWriter | None = None,
) -> None:
    """Run planned calls in order, honoring if_differs and the EEPROM guard.

    A press with write=if_differs commits preceding values and is skipped
    when none of them was written. Register writes go to the direct device;
    if_differs compares them with the value last written there.
    """
    wrote_since_press = False
    for call in calls:
        if call.write == "if_differs":
            if call.service == "press":
                if not wrote_since_press:
                    call.status, call.reason = "skipped", "nothing to commit"
                    continue
            elif current_matches(call, states, direct):
                call.status, call.reason = "skipped", "already set"
                continue
        if call.storage == "eeprom" and call.entity_id and not guard.allowed(call.entity_id):
            call.status, call.reason = "blocked", "EEPROM write limit reached"
            _LOGGER.warning("EEPROM write limit reached for %s", call.entity_id)
            continue
        if dry_run:
            call.status = "dry_run"
        else:
            if call.domain == DIRECT:
                if direct is None:
                    raise ExecutorError(f"{call.service} step for a device without a direct connection")
                await direct.write(call.data["spec"], call.data["value"])
                direct.values_written[call.entity_id] = call.data["value"]
            else:
                target = {"entity_id": call.entity_id} if call.entity_id else None
                await client.call_service(call.domain, call.service, call.data, target)
            call.status = "done"
            if call.storage == "eeprom" and call.entity_id:
                guard.record(call.entity_id)
        wrote_since_press = call.service != "press"
