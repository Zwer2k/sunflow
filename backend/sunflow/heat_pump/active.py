"""Writing to the heat pump for real (design/heat_pump.md, 4, 12).

Switched on per heat pump only by the user (DeviceConfig.flow_active, default off). While it is on, what the
protection and write layers decide goes to the heat pump: the set value through the profile's write role of
the strategy fixed (storage eeprom, counted by EepromGuard) and the room thermostat block (not stored in the
heat pump's EEPROM, not counted). Switching off, mode off and failures reset nothing (4, 12).

- WriteTargets: the entities and values to write, from the profile, the manifest and the bound entities.
- AdoptCheck: after a real write of the set value and the profile's adopt_delay, whether the heat pump took
  it over - the real Max T-Vorlauf shows it, and the effective setpoint follows it while heating.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sunflow.devices.executor import PlannedCall
from sunflow.i18n import number, t

from .manifest import entities_by_key
from .states import FIXED, HEATING, Reading

IDLE = "idle"
_EPS = 1e-6
# Outcome of the check of a written value
PENDING = "pending"
ADOPTED = "adopted"
# The real Max T-Vorlauf does not show the written value
MISSING = "missing"
# The effective setpoint stays below the written value: the heat pump limits itself (15, point 9)
LIMITED = "limited"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


@dataclass(frozen=True, kw_only=True)
class WriteTargets:
    """Where the set value and the block are written; None where the heat pump has no such entity."""

    setpoint_entity: str | None = None
    setpoint_storage: str = "eeprom"
    block_entity: str | None = None
    # Value written to block / release: the option of a select, "on"/"off" of a switch
    block_on: str | None = None
    block_off: str | None = None

    def setpoint_call(self, value: float) -> PlannedCall | None:
        """Writing the set value (number entity)."""
        if self.setpoint_entity is None:
            return None
        return PlannedCall(
            domain=self.setpoint_entity.split(".", 1)[0], service="set_value", data={"value": value},
            entity_id=self.setpoint_entity, storage=self.setpoint_storage, write="always",
        )

    def block_call(self, on: bool) -> PlannedCall | None:
        """Setting (on) or releasing the room thermostat block."""
        value = self.block_on if on else self.block_off
        if self.block_entity is None or value is None:
            return None
        domain = self.block_entity.split(".", 1)[0]
        if domain == "select":
            service, data = "select_option", {"option": value}
        elif value in ("on", "off"):
            service, data = f"turn_{value}", {}
        else:
            return None
        return PlannedCall(domain=domain, service=service, data=data, entity_id=self.block_entity, storage="volatile", write="always")


def write_targets(profile: dict[str, Any], manifest: dict[str, Any] | None, entities: dict[str, str | None]) -> WriteTargets:
    """The entities Sunflow writes to: the write role of the strategy fixed and the block (role heating_block).

    A select is written with the option the manifest shows for the profile's key ("on" → "An"); a switch with
    on/off."""
    flow = profile.get("flow_control") or {}
    write = ((flow.get("strategies") or {}).get(FIXED) or {}).get("write") or {}
    role = write.get("role")
    setpoint = entities.get(role) if isinstance(role, str) else None
    if setpoint is not None and not setpoint.startswith(("number.", "input_number.")):
        setpoint = None
    block = flow.get("heating_block") or {}
    block_entity = entities.get("heating_block") if block else None
    on = off = None
    if block_entity is not None:
        domain = block_entity.split(".", 1)[0]
        if domain == "select":
            key = (profile.get("roles", {}).get("heating_block") or {}).get("key")
            entity = entities_by_key(manifest).get(key) if key else None
            options = {}
            for item in (entity or {}).get("values") or []:
                if isinstance(item, list) and len(item) > 1:
                    options[item[2] if len(item) > 2 and isinstance(item[2], str) else str(item[0])] = str(item[1])
            on, off = options.get(str(block.get("on"))), options.get(str(block.get("off")))
        else:
            on, off = str(block.get("on")), str(block.get("off"))
    return WriteTargets(
        setpoint_entity=setpoint, setpoint_storage=str(write.get("storage") or "eeprom"),
        block_entity=block_entity, block_on=on, block_off=off,
    )


@dataclass(kw_only=True)
class AdoptResult:
    """What the check of the last real write found this minute."""

    # The real Max T-Vorlauf when it does not show the written value (MISSING)
    actual_c: float | None = None
    notes: list[tuple[str, bool]] | None = None


class AdoptCheck:
    """Whether the heat pump took the set value Sunflow wrote over (in memory; a restart drops a check)."""

    def __init__(self) -> None:
        """Nothing written yet."""
        self.value_c: float | None = None
        self.at: datetime | None = None
        self.state: str | None = None
        self.checked_at: datetime | None = None
        self.effective_c: float | None = None
        # A warning of this kind was noted; again only after a check passed
        self._noted: set[str] = set()

    def wrote(self, value: float, at: datetime) -> None:
        """Sunflow wrote value for real: checked after the adopt delay."""
        self.value_c, self.at, self.state, self.checked_at, self.effective_c = value, at, PENDING, None, None

    def reset(self) -> None:
        """Writing switched off: nothing to check."""
        self.__init__()

    def step(self, now: datetime, reading: Reading, delay_s: float, step: float, deadband: float) -> AdoptResult:
        """Check once the delay passed: the real value first, the effective setpoint while heating."""
        result = AdoptResult(notes=[])
        if self.state != PENDING or self.at is None or self.value_c is None or now - self.at < timedelta(seconds=delay_s):
            return result
        value = self.value_c
        actual = reading.values.get("max_flow_setpoint")
        if actual is None:
            return result
        if abs(actual - value) > max(step, 0.0) / 2 + _EPS:
            self._done(now, MISSING)
            result.actual_c = actual
            if MISSING not in self._noted:
                self._noted.add(MISSING)
                result.notes.append((t(
                    "Max T-Vorlauf {value} °C geschrieben, die Wärmepumpe zeigt aber {actual} °C: nicht übernommen. Sunflow geht vom angezeigten Wert aus.",
                    value=number(value), actual=number(actual),
                ), True))
            return result
        effective = reading.values.get("flow_setpoint")
        heating = (
            reading.program == HEATING and reading.activity in (HEATING, IDLE) and reading.bypass == 0
            and not reading.fault and effective is not None
        )
        if not heating:
            # Only while heating the effective setpoint tells; checked again next minute
            return result
        assert effective is not None
        self.effective_c = effective
        if effective < value - deadband - _EPS:
            self._done(now, LIMITED)
            if LIMITED not in self._noted:
                self._noted.add(LIMITED)
                result.notes.append((t(
                    "Max T-Vorlauf {value} °C geschrieben, der wirksame Vorlauf-Soll bleibt bei {effective} °C: die Wärmepumpe begrenzt selbst (z. B. T-Vorlauf Tag). Obergrenze des Stellwerts höchstens auf diese Grenze stellen.",
                    value=number(value), effective=number(effective),
                ), True))
            return result
        self._done(now, ADOPTED)
        self._noted.clear()
        return result

    def _done(self, now: datetime, state: str) -> None:
        self.state, self.checked_at = state, now

    def status(self) -> dict[str, Any] | None:
        """For the status: the value checked, when written, the outcome."""
        if self.value_c is None or self.at is None:
            return None
        return {
            "value_c": self.value_c, "written_at": _iso(self.at), "state": self.state,
            "checked_at": _iso(self.checked_at) if self.checked_at else None, "effective_c": self.effective_c,
        }
