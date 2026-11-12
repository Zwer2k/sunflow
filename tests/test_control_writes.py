"""Writes that fail part way, values not readable back and devices that fall back on their own."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sunflow.control.controller import DeviceAccess, DeviceControl, apply
from sunflow.devices.executor import EepromGuard, PlannedCall

NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


class Inverter:
    """A window in a number committed by a press, and a service that cannot be read back."""

    def __init__(self) -> None:
        """Nothing written yet."""
        self.number: float | None = None
        self.fail_press = False
        self.ran: list[list[str]] = []
        self.forced: list[bool] = []

    def access(self) -> DeviceAccess:
        """Calls as the controller gets them."""

        async def plan(action: str, runtime: dict[str, float]) -> tuple[list[PlannedCall], dict[str, Any]]:
            calls = [
                PlannedCall(domain="number", service="set_value", data={"value": runtime.get("P", 0)}, entity_id="number.min", storage="volatile", write="always"),
                PlannedCall(domain="button", service="press", data={}, entity_id="button.commit", storage="volatile", write="always"),
                PlannedCall(domain="huawei", service="forcible_charge", data={"power": runtime.get("P", 0)}, entity_id=None, storage="volatile", write="always"),
            ]
            return calls, ({"number.min": {"state": str(self.number)}} if self.number is not None else {})

        async def run(calls: list[PlannedCall], states: dict[str, Any], force: bool = False) -> None:
            self.forced.append(force)
            done = []
            for call in calls:
                if call.service == "press" and self.fail_press:
                    self.ran.append(done)
                    raise RuntimeError("Modbus timeout")
                if call.entity_id == "number.min":
                    self.number = call.data["value"]
                call.status = "done"
                done.append(call.service)
            self.ran.append(done)

        return DeviceAccess(plan=plan, run=run)


async def test_failed_commit_is_written_again() -> None:
    """The numbers went through, the press did not: next minute everything is sent again although the numbers match."""
    inverter, status = Inverter(), DeviceControl(device_id="bat", name="Battery")
    inverter.fail_press = True
    with pytest.raises(RuntimeError):
        await apply(inverter.access(), status, "force_charge", 2000, NOW, services_on_change=True)
    inverter.fail_press = False

    await apply(inverter.access(), status, "force_charge", 2000, NOW + timedelta(minutes=1), services_on_change=True)
    await apply(inverter.access(), status, "force_charge", 2000, NOW + timedelta(minutes=2), services_on_change=True)

    assert inverter.ran == [["set_value"], ["set_value", "press", "forcible_charge"]]
    assert inverter.forced == [False, True]
    assert not status.incomplete


async def test_service_call_is_sent_again_when_its_power_changes() -> None:
    """An action call cannot be read back: sent when the action or its values change, not every minute."""
    inverter, status = Inverter(), DeviceControl(device_id="bat", name="Battery")
    access = inverter.access()
    await apply(access, status, "force_charge", 2000, NOW, services_on_change=True)
    await apply(access, status, "force_charge", 2000, NOW + timedelta(minutes=1), services_on_change=True)
    await apply(access, status, "force_charge", 3000, NOW + timedelta(minutes=2), services_on_change=True)

    assert [calls[-1] for calls in inverter.ran] == ["forcible_charge", "forcible_charge"]


async def test_refresh_is_due_after_a_restart() -> None:
    """The entity still shows the value, but nothing was written since the start: a device with a refresh gets it once."""
    inverter, status = Inverter(), DeviceControl(device_id="bat", name="Battery")
    inverter.number = 2000.0
    access = inverter.access()

    other = DeviceControl(device_id="other", name="Other", last_action="force_charge", last_runtime={"P": 2000})
    await apply(access, status, "force_charge", 2000, NOW, refresh=timedelta(minutes=10), services_on_change=True)
    await apply(access, status, "force_charge", 2000, NOW + timedelta(minutes=1), refresh=timedelta(minutes=10), services_on_change=True)
    await apply(access, other, "force_charge", 2000, NOW, services_on_change=True)

    # Written once for the device with a refresh, not again a minute later, and never without a refresh
    assert inverter.forced == [True]


def test_damaged_write_counter_does_not_block(tmp_path: Path) -> None:
    """A file cut off by a crash counts as no writes; the next record replaces it."""
    (tmp_path / "eeprom_writes.json").write_text('{"2026-11-1')
    guard = EepromGuard(tmp_path, 2)

    assert guard.allowed("number.x")
    guard.record("number.x")
    guard.record("number.x")
    assert not guard.allowed("number.x")
