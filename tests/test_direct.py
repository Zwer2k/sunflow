"""Tests for devices Sunflow talks to directly (Modbus TCP), against a dummy inverter."""

from pathlib import Path
from typing import Any

from dummy_inverter import DummyInverter
import jsonschema
import pytest
from sunflow.config.models import Connection, DeviceConfig, SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import EepromGuard, ExecutorError, execute, plan_steps
from sunflow.devices.profiles import ProfileLibrary, load_schema
from sunflow.devices.resolver import bind_roles, device_entities
from sunflow.direct.codec import decode, encode
from sunflow.direct.hub import DirectHub, direct_device_id
from sunflow.direct.modbus import ModbusError, ModbusTcpClient

SCHEMA_DIR = Path(__file__).parent.parent / "schema"

PROFILE: dict[str, Any] = {
    "profile": "modbus/dummy_hybrid",
    "version": 1,
    "connection": {
        "type": "modbus_tcp",
        "word_order": "big",
        "points": {
            "pv_power": {"address": 100, "encoding": "uint16", "unit": "W"},
            "battery_power": {"address": 101, "encoding": "int16", "unit": "W"},
            "battery_soc": {"address": 10, "type": "input", "encoding": "uint16", "scale": 0.1, "unit": "%"},
            "grid_power": {"address": 102, "encoding": "int32", "scale": -1, "unit": "W"},
        },
    },
    "capabilities": ["pv", "battery", "grid_meter"],
    "roles": {
        "pv_power": {"key": "pv_power", "unit": "W"},
        "battery_power": {"key": "battery_power", "unit": "W", "sign": "charge_positive"},
        "battery_soc": {"key": "battery_soc", "unit": "%"},
    },
    "battery_control": {
        "auto": [{"modbus": {"address": 200, "value": 0, "write": "if_differs"}}],
        "force_charge": [
            {"modbus": {"address": 200, "value": 1, "write": "if_differs"}},
            {"modbus": {"address": 201, "encoding": "uint32", "value": "{{ P }}", "max": 5000}},
        ],
        "refresh": "30s",
    },
}


def holding() -> dict[int, int]:
    """Registers of the dummy: 3 kW PV, 1.5 kW discharging, 800 W import; 200/201 writable."""
    return {100: 3000, 101: 0x10000 - 1500, 102: 0xFFFF, 103: 0x10000 - 800, 200: 0, 201: 0, 202: 0}


def test_profile_with_connection_is_valid() -> None:
    """A direct profile needs no match; one with neither is rejected."""
    validator = jsonschema.Draft202012Validator(load_schema(SCHEMA_DIR))
    assert list(validator.iter_errors(PROFILE)) == []
    without = {k: v for k, v in PROFILE.items() if k != "connection"}
    assert list(validator.iter_errors(without))


@pytest.mark.parametrize(
    ("value", "encoding", "scale", "order", "registers"),
    [
        pytest.param(-1500, "int16", 1, "big", [0xFA24], id="int16-negative"),
        pytest.param(70000, "uint32", 1, "big", [0x0001, 0x1170], id="uint32-big"),
        pytest.param(70000, "uint32", 1, "little", [0x1170, 0x0001], id="uint32-little"),
        pytest.param(-800, "int32", 1, "big", [0xFFFF, 0xFCE0], id="int32-negative"),
        pytest.param(52.5, "uint16", 0.1, "big", [525], id="scaled"),
    ],
)
def test_codec_round_trip(value: float, encoding: str, scale: float, order: str, registers: list[int]) -> None:
    """Values are encoded to registers and decoded back."""
    assert encode(value, encoding, scale, order) == registers
    assert decode(registers, encoding, scale, order) == pytest.approx(value)


def test_encode_clamps_to_range() -> None:
    """Out-of-range values are clamped, not wrapped around."""
    assert encode(-5, "uint16") == [0]
    assert encode(40000, "int16") == [0x7FFF]


async def test_client_reads_and_writes() -> None:
    """Holding and input registers are read; single and multiple writes reach the device."""
    async with DummyInverter(holding(), {10: 523}) as dummy:
        client = ModbusTcpClient("127.0.0.1", dummy.port)
        assert await client.read(100, 2) == [3000, 0x10000 - 1500]
        assert await client.read(10, 1, "input") == [523]
        await client.write(200, [1])
        await client.write(201, [0, 4000])
        assert dummy.writes == [(200, [1]), (201, [0, 4000])]
        with pytest.raises(ModbusError, match="Exception 2"):
            await client.read(500, 1)
        # An exception answer keeps the connection usable
        assert await client.read(200, 1) == [1]
        await client.close()


async def test_client_reports_unreachable_device() -> None:
    """A refused connection is a ModbusError."""
    async with DummyInverter() as dummy:
        port = dummy.port
    with pytest.raises(ModbusError):
        await ModbusTcpClient("127.0.0.1", port, timeout=1).read(0, 1)


def config_for(port: int) -> tuple[SunflowConfig, ProfileLibrary, DeviceConfig]:
    """Configuration with one direct device at the dummy."""
    device = DeviceConfig(
        id="abcdef1234",
        ha_device_id=direct_device_id("abcdef1234"),
        profile=PROFILE["profile"],
        profile_version=1,
        name="Dummy WR",
        connection=Connection(host="127.0.0.1", port=port),
    )
    return SunflowConfig(devices=[device]), ProfileLibrary(profiles={PROFILE["profile"]: PROFILE}), device


async def test_hub_shows_points_as_entities() -> None:
    """Polled points appear in the snapshot so roles bind by key as for integrations."""
    async with DummyInverter(holding(), {10: 523}) as dummy:
        config, library, device = config_for(dummy.port)
        hub = DirectHub()
        await hub.sync(config, library)
        await hub.poll()
        snapshot = hub.contribute(Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None))
        await hub.stop()
    entities = device_entities(snapshot, device.ha_device_id)
    roles = bind_roles(PROFILE, entities)
    values = {role: snapshot.states[entity_id]["state"] for role, entity_id in roles.items()}
    assert values == {"pv_power": "3000.0", "battery_power": "-1500.0", "battery_soc": "52.3"}
    grid = next(e for e in entities if e["unique_id"].endswith("_grid_power"))
    assert snapshot.states[grid["entity_id"]]["state"] == "800.0"


async def test_hub_marks_failed_points_unknown() -> None:
    """A point the device does not answer is unknown; the others stay readable."""
    registers = holding()
    del registers[100]
    async with DummyInverter(registers, {10: 523}) as dummy:
        config, library, device = config_for(dummy.port)
        hub = DirectHub()
        await hub.sync(config, library)
        await hub.poll()
        await hub.stop()
    direct = hub.by_ha_device(device.ha_device_id)
    assert direct is not None
    assert direct.values["pv_power"] is None
    assert direct.values["battery_power"] == -1500
    assert direct.error is not None
    assert "pv_power" in direct.error


async def test_register_steps_write_once_with_if_differs(tmp_path: Path) -> None:
    """Steps write registers of the direct device; unchanged if_differs values are not written again."""
    async with DummyInverter(holding(), {10: 523}) as dummy:
        config, library, device = config_for(dummy.port)
        hub = DirectHub()
        await hub.sync(config, library)
        direct = hub.by_ha_device(device.ha_device_id)
        guard = EepromGuard(tmp_path, 10)
        for power in (7000, 3000):
            calls = plan_steps(PROFILE["battery_control"]["force_charge"], {"P": power}, [], "volatile")
            await execute(None, calls, {}, guard, dry_run=False, direct=direct)
        await hub.stop()
    assert dummy.writes == [(200, [1]), (201, [0, 5000]), (201, [0, 3000])]
    assert [c.status for c in calls] == ["skipped", "done"]


async def test_register_step_needs_a_direct_device(tmp_path: Path) -> None:
    """A register step for a device of an integration is an error, never a HA call."""
    calls = plan_steps(PROFILE["battery_control"]["auto"], {}, [], "volatile")
    with pytest.raises(ExecutorError):
        await execute(None, calls, {}, EepromGuard(tmp_path, 10), dry_run=False)
