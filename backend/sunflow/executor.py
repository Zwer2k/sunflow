"""Translate profile actions into Home Assistant action calls and run them."""

from dataclasses import dataclass, field
from datetime import date
import json
import logging
from pathlib import Path
import re
from typing import Any

from .discovery import entity_has_key
from .ha import HAClient

_LOGGER = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{\{\s*(-?)([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
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
    variables: dict[str, float],
    entities: list[dict[str, Any]],
    default_storage: str,
) -> list[PlannedCall]:
    """Turn profile steps into planned action calls."""
    calls: list[PlannedCall] = []
    for step in steps:
        ((kind, spec),) = step.items()
        storage = spec.get("storage", default_storage)
        write = spec.get("write", "always")
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

        domain = STEP_DOMAINS[kind]
        entity_id = find_entity(entities, domain, spec["key"])
        if entity_id is None:
            raise ExecutorError(f"No {domain} entity for key {spec['key']}")
        match kind:
            case "number":
                service, data = "set_value", {"value": render(spec["value"], variables)}
            case "select":
                service, data = "select_option", {"option": spec["option"]}
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
    variables = {**profile.get("defaults", {}), **parameters, **runtime}
    params = {
        name: resolve_value(expr, variables)
        for name, expr in profile["actions"][action].items()
    }
    calls = plan_steps(
        primitive["steps"], params, entities, primitive.get("storage", "eeprom")
    )
    return params, calls


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


def current_matches(call: PlannedCall, states: dict[str, dict[str, Any]]) -> bool:
    """Return True if the entity already has the value the call would write."""
    state = states.get(call.entity_id or "", {}).get("state")
    match call.service:
        case "select_option":
            return state == call.data["option"]
        case "set_value":
            try:
                return float(state) == float(call.data["value"])
            except TypeError, ValueError:
                return False
        case "turn_on" | "turn_off":
            return state == call.service.removeprefix("turn_")
    return False


async def execute(
    client: HAClient,
    calls: list[PlannedCall],
    states: dict[str, dict[str, Any]],
    guard: EepromGuard,
    dry_run: bool,
) -> None:
    """Run planned calls in order, honoring if_differs and the EEPROM guard.

    A press with write=if_differs commits preceding values and is skipped
    when none of them was written.
    """
    wrote_since_press = False
    for call in calls:
        if call.write == "if_differs":
            if call.service == "press":
                if not wrote_since_press:
                    call.status, call.reason = "skipped", "nothing to commit"
                    continue
            elif current_matches(call, states):
                call.status, call.reason = "skipped", "already set"
                continue
        if call.storage == "eeprom" and call.entity_id and not guard.allowed(call.entity_id):
            call.status, call.reason = "blocked", "EEPROM write limit reached"
            _LOGGER.warning("EEPROM write limit reached for %s", call.entity_id)
            continue
        if dry_run:
            call.status = "dry_run"
        else:
            target = {"entity_id": call.entity_id} if call.entity_id else None
            await client.call_service(call.domain, call.service, call.data, target)
            call.status = "done"
            if call.storage == "eeprom" and call.entity_id:
                guard.record(call.entity_id)
        wrote_since_press = call.service != "press"
