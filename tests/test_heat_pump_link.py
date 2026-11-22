"""Configured heat pumps recognized by their manifest: reload, status, journal, MAC, API (heat_pump.md 3.4-3.6)."""

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from heat_pump_node import FakeHA, Node, snapshot
import pytest
from sunflow.analysis.summary import build_summary
from sunflow.api import create_app
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.control.journal import ControlJournal
from sunflow.devices.profiles import ProfileLibrary
from sunflow.heat_pump.link import ManifestManager
from sunflow.heat_pump.manifest_store import ManifestStore

PROFILE = "esphome/rotex_hpsu_can"
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)


class Setup:
    """A configured HPSU, its node, the manager and the journal."""

    def __init__(self, tmp_path: Path, library: ProfileLibrary, profile_version: int = 3) -> None:
        self.tmp_path, self.library = tmp_path, library
        self.node = Node()
        self.client = FakeHA(self.node)
        self.store = ConfigStore(tmp_path, lambda: library.profiles)
        config = self.store.load()
        config.devices.append(
            DeviceConfig(
                id="hp", ha_device_id=self.node.device_id, profile=PROFILE, profile_version=profile_version,
                name="HPSU CAN", mac=self.node.mac, component="can",
            )
        )
        self.store.save(config)
        self.journal = ControlJournal(tmp_path)
        self.manager = self.new_manager()

    def new_manager(self) -> ManifestManager:
        """A manager as after a restart of Sunflow (same data directory)."""
        return ManifestManager(self.client, ManifestStore(self.tmp_path), lambda: self.library, self.journal)

    async def check(self, now: datetime = NOW, **kwargs: Any) -> None:
        await self.manager.check(self.store, snapshot(self.node, **kwargs), now)

    def notes(self) -> list[str]:
        return [e["reason"] for e in reversed(self.journal.entries(NOW - timedelta(days=1), "hp"))]

    def status(self) -> dict[str, Any]:
        return self.manager.status()["hp"]


@pytest.fixture
def hp(tmp_path: Path, library: ProfileLibrary) -> Setup:
    return Setup(tmp_path, library)


async def test_first_check_loads_and_is_compatible(hp: Setup) -> None:
    """The first check loads the manifest once and finds the device compatible."""
    await hp.check()

    assert hp.status()["status"] == "compatible"
    assert hp.status()["usable"]
    assert hp.status()["manifest"]["entities"] == 115
    assert hp.notes() == ["Manifest geladen: 115 Entities, Komponente 8493cac"]


async def test_unchanged_manifest_no_duplicate_note(hp: Setup) -> None:
    """Loading the same manifest again (hourly) adds nothing to the journal."""
    await hp.check()
    await hp.check(NOW + timedelta(hours=1, minutes=1))
    await hp.check(NOW + timedelta(hours=2, minutes=2))

    assert len(hp.client.calls) == 6
    assert len(hp.notes()) == 1


async def test_reload_only_when_due(hp: Setup) -> None:
    """Between the hourly reloads the manifest is not fetched again."""
    await hp.check()
    await hp.check(NOW + timedelta(minutes=30))

    assert len(hp.client.calls) == 2


async def test_changed_manifest_is_noted_and_checked_again(hp: Setup) -> None:
    """A reflashed node without a required key: noted once, incompatible, not usable."""
    await hp.check()
    hp.node.pages[0]["entities"] = [e for e in hp.node.pages[0]["entities"] if e["key"] != "max_target_flow_temp"]
    hp.node.sw_version = "2026.11.0 (2026-11-21 10:00:00 +0100)"

    await hp.check(NOW + timedelta(minutes=5))
    await hp.check(NOW + timedelta(hours=2))

    assert hp.status()["status"] == "incompatible"
    assert not hp.status()["usable"]
    assert hp.status()["problems"] == [
        {"role": "max_flow_setpoint", "key": "max_target_flow_temp", "optional": False, "reason": "Schlüssel max_target_flow_temp fehlt"}
    ]
    assert hp.notes()[1:] == [
        "Manifest geändert, wird neu geprüft",
        "Nicht kompatibel – Sunflow nutzt das Gerät nicht: Schlüssel max_target_flow_temp fehlt",
    ]


async def test_compatible_again_is_noted(hp: Setup) -> None:
    """After an incompatible firmware a fitting one is noted as compatible again."""
    entities = list(hp.node.pages[1]["entities"])
    hp.node.pages[1]["entities"] = [e for e in entities if e["key"] != "thermal_power"]
    await hp.check()
    hp.node.pages[1]["entities"] = entities
    hp.node.sw_version = "new"

    await hp.check(NOW + timedelta(minutes=5))

    assert hp.status()["status"] == "compatible"
    assert hp.notes()[-1] == "Kompatibel"


async def test_restart_compares_with_stored_manifest(hp: Setup) -> None:
    """After a restart the stored manifest and status are the reference: nothing is noted again."""
    hp.node.pages[1]["entities"] = [e for e in hp.node.pages[1]["entities"] if e["key"] != "thermal_power"]
    await hp.check()
    hp.manager = hp.new_manager()

    await hp.check(NOW + timedelta(minutes=1))

    assert hp.status()["status"] == "incompatible"
    assert len(hp.notes()) == 2


async def test_restart_with_changed_manifest(hp: Setup) -> None:
    """A manifest changed while Sunflow was not running is noted at the first check."""
    await hp.check()
    hp.manager = hp.new_manager()
    hp.node.pages[0]["entities"][0]["name"] = "Logfilter"

    await hp.check(NOW + timedelta(minutes=1))

    assert hp.notes()[-1] == "Manifest geändert, wird neu geprüft"


async def test_flashed_node_reloads_at_once(hp: Setup) -> None:
    """A new firmware version (device registry) reloads the manifest before the hour is over."""
    await hp.check()
    hp.node.sw_version = "2026.12.0 (2026-12-01 08:00:00 +0100)"

    await hp.check(NOW + timedelta(minutes=2))

    assert len(hp.client.calls) == 4
    assert hp.status()["sw_version"] == hp.node.sw_version


async def test_registry_event_wakes_the_check(hp: Setup) -> None:
    """device_registry_updated with a new sw_version asks for a fresh check."""
    await hp.check()
    [(event_type, callback)] = hp.client.subscriptions
    assert event_type == "device_registry_updated"

    callback({"data": {"action": "update", "device_id": "other", "changes": {"sw_version": "x"}}})
    assert not hp.manager.wants_fresh
    callback({"data": {"action": "update", "device_id": hp.node.device_id, "changes": {"sw_version": "x"}}})

    assert hp.manager.wants_fresh
    await hp.manager.wait(0)
    await hp.check(NOW + timedelta(minutes=1))
    assert len(hp.client.calls) == 4
    assert not hp.manager.wants_fresh


async def test_unavailable_until_back_and_checked(hp: Setup) -> None:
    """A role entity going unavailable makes the device unusable; coming back reloads the manifest."""
    await hp.check()
    entity_id = hp.node.entity_id("tv")

    hp.manager.on_state_changed({"data": {"entity_id": entity_id, "new_state": {"state": "unavailable"}}})
    assert hp.status()["status"] == "unavailable"
    assert not hp.manager.usable("hp")

    hp.manager.on_state_changed({"data": {"entity_id": entity_id, "new_state": {"state": "31.2"}}})
    assert hp.manager.wants_fresh
    await hp.check(NOW + timedelta(minutes=1))

    assert hp.status()["status"] == "compatible"
    assert len(hp.client.calls) == 4
    # A restart of the node is normal: nothing in the journal
    assert len(hp.notes()) == 1


async def test_unreachable_node_keeps_state(hp: Setup) -> None:
    """A node that does not answer is unavailable, not incompatible."""
    hp.node.offline = True

    await hp.check(state="unavailable")

    assert hp.status()["status"] == "unavailable"
    assert hp.notes() == []


async def test_node_without_manifest_is_incompatible(hp: Setup) -> None:
    """A reachable node that offers no manifest (older firmware) is incompatible."""
    hp.client.nodes.clear()

    await hp.check()

    assert hp.status()["status"] == "incompatible"
    assert hp.notes() == ["Kein Manifest vom Gerät – Firmware ohne Manifest? Sunflow nutzt das Gerät nicht."]


async def test_rebind_by_mac(hp: Setup) -> None:
    """The ESPHome integration was added again: the device is found by its MAC and assigned anew."""
    await hp.check()
    hp.node.device_id = "dev_new"

    await hp.check(NOW + timedelta(minutes=1))
    await hp.check(NOW + timedelta(minutes=2))

    assert hp.store.load().device("hp").ha_device_id == "dev_new"
    assert hp.status()["status"] == "compatible"
    rebound = [n for n in hp.notes() if "MAC" in n]
    assert rebound == ["Über die MAC 34:b7:da:5e:53:40 neu zugeordnet (Home-Assistant-Gerät dev_can → dev_new)"]


async def test_device_gone(hp: Setup) -> None:
    """Neither the device nor its MAC exist any more: not found, noted once."""
    await hp.check()
    hp.node.device_id, hp.node.mac = "dev_other", "11:22:33:44:55:66"

    await hp.check(NOW + timedelta(minutes=1))
    await hp.check(NOW + timedelta(minutes=2))

    assert hp.status()["status"] == "not_found"
    assert hp.notes()[1:] == ["ESPHome-Gerät nicht gefunden (MAC 34:b7:da:5e:53:40) – bitte im Assistenten neu zuordnen."]


async def test_profile_version_noted_once(tmp_path: Path, library: ProfileLibrary) -> None:
    """A device added with an older profile version is noted once, also across restarts."""
    hp = Setup(tmp_path, library, profile_version=0)

    await hp.check()
    await hp.check(NOW + timedelta(hours=2))
    hp.manager = hp.new_manager()
    await hp.check(NOW + timedelta(hours=3))

    noted = [n for n in hp.notes() if "Version" in n]
    assert noted == [
        "Profil esphome/rotex_hpsu_can liegt in Version 3 vor, übernommen wurde Version 0 – bitte die Rollen in der Vorschau prüfen."
    ]
    assert hp.status()["profile_version_noted"] == [0, 3]


async def test_removed_device_is_pruned(hp: Setup) -> None:
    """The stored manifest of a removed device is dropped."""
    await hp.check()
    config = hp.store.load()
    config.devices.clear()
    hp.store.save(config)

    await hp.check(NOW + timedelta(minutes=1))

    assert ManifestStore(hp.tmp_path).get("hp") is None
    assert hp.manager.status() == {}


async def test_scan_reuses_recent_result(hp: Setup) -> None:
    """Previews and discovery within minutes reuse the scan; force asks the nodes again."""
    snap = snapshot(hp.node)
    await hp.manager.scan(snap, NOW)
    await hp.manager.scan(snap, NOW + timedelta(minutes=1))
    assert len(hp.client.calls) == 2

    await hp.manager.scan(snap, NOW + timedelta(minutes=1), force=True)
    assert len(hp.client.calls) == 4
    assert hp.manager.contribute(snap).manifests["dev_can"]["rotex_hpsu_can"].mac == hp.node.mac


async def test_concurrent_scans_ask_the_node_once(hp: Setup) -> None:
    """Discovery, preview and the background check at the same time: one scan, the others take its result."""
    snap = snapshot(hp.node)

    results = await asyncio.gather(*(hp.manager.scan(snap, NOW, force=True) for _ in range(3)))

    assert hp.client.calls == [(hp.node.service, 0), (hp.node.service, 1)]
    assert all(r[0].ha_device_id == "dev_can" for r in results)


async def test_single_failure_is_no_warning(hp: Setup, caplog: pytest.LogCaptureFixture) -> None:
    """One timeout keeps the last manifest silently; failing again warns once."""
    snap = snapshot(hp.node)
    await hp.manager.scan(snap, NOW)
    hp.node.offline = True
    caplog.set_level(logging.DEBUG, logger="sunflow.heat_pump.link")

    first = await hp.manager.scan(snap, NOW, force=True)
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
    assert first[0].ha_device_id == "dev_can"

    await hp.manager.scan(snap, NOW, force=True)
    await hp.manager.scan(snap, NOW, force=True)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["Manifest of hpsu_can not readable (2 times in a row): Timeout waiting for response from action call"]

    hp.node.offline = False
    await hp.manager.scan(snap, NOW, force=True)
    hp.node.offline = True
    await hp.manager.scan(snap, NOW, force=True)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


# Summary and API


def test_summary_reports_link_status(library: ProfileLibrary) -> None:
    """Incompatible is an error with the reason; missing optional roles and unassigned manifests are warnings."""
    device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=1, name="HPSU CAN")
    config = SunflowConfig(devices=[device])
    status = {
        "hp": {
            "status": "incompatible",
            "problems": [
                {"role": "max_flow_setpoint", "key": "max_target_flow_temp", "optional": False, "reason": "Schlüssel max_target_flow_temp fehlt"},
                {"role": "electric_energy", "key": "total_electrical_energy", "optional": True, "reason": "Schlüssel total_electrical_energy fehlt"},
            ],
        }
    }

    summary = build_summary(config, library.profiles, manifest_status=status, manifest_notes=["Kein ESPHome-Gerät passt zum Manifest von x."])

    messages = {(i.level, i.message) for i in summary.issues}
    assert ("error", "HPSU CAN: nicht kompatibel – Schlüssel max_target_flow_temp fehlt") in messages
    assert ("warning", "HPSU CAN: optionale Funktionen fehlen – Schlüssel total_electrical_energy fehlt") in messages
    assert ("warning", "Kein ESPHome-Gerät passt zum Manifest von x.") in messages
    assert not summary.can_complete


def test_summary_reports_profile_version(library: ProfileLibrary) -> None:
    """A device added with another profile version is a warning in the summary."""
    device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=0, name="HPSU CAN")

    summary = build_summary(SunflowConfig(devices=[device]), library.profiles)

    assert any("Version 3 vor, übernommen wurde Version 0" in i.message for i in summary.issues if i.level == "warning")


def _app(tmp_path: Path, library: ProfileLibrary, node: Node) -> tuple[Any, ConfigStore, ManifestManager]:
    app = create_app()
    store = ConfigStore(tmp_path, lambda: library.profiles)
    client = FakeHA(node)
    manager = ManifestManager(client, ManifestStore(tmp_path), lambda: library, ControlJournal(tmp_path))

    async def get(fresh: bool = False) -> Any:
        return manager.contribute(snapshot(node, state="30.0"))

    app.state.store, app.state.profiles, app.state.manifests = store, library, manager
    app.state.ha = client
    app.state.snapshots = SimpleNamespace(get=get)
    return app, store, manager


def test_add_device_stores_mac_and_component(tmp_path: Path, library: ProfileLibrary) -> None:
    """Taking over the node stores the device, its MAC and the component and checks it at once."""
    node = Node()
    app, store, _ = _app(tmp_path, library, node)

    with TestClient(app) as client:
        discovered = client.get("/api/v1/discovery").json()
        assert discovered[0]["devices"][0]["profile"] == PROFILE
        preview = client.get(f"/api/v1/devices/{node.device_id}/preview").json()
        added = client.post("/api/v1/config/devices", json={"ha_device_id": node.device_id}).json()
        status = client.get("/api/v1/heat_pump/status").json()
        summary = client.get("/api/v1/setup/summary").json()

    assert preview["complete"] and preview["compatibility"] == {"compatible": True, "reasons": []}
    assert (added["mac"], added["component"], added["profile"], added["name"]) == (node.mac, "can", PROFILE, "HPSU.CAN")
    stored = store.load().devices[0]
    assert (stored.ha_device_id, stored.mac, stored.component) == (node.device_id, node.mac, "can")
    [device] = status["devices"]
    assert (device["status"], device["usable"], device["profile_version"]) == ("compatible", True, {"configured": 3, "current": 3})
    assert not [i for i in summary["issues"] if "HPSU" in i["message"] and i["level"] == "error"]


def test_preview_shows_incompatible_role(tmp_path: Path, library: ProfileLibrary) -> None:
    """A role whose manifest entity does not fit shows the reason; the device cannot be taken over."""
    node = Node()
    entity = next(e for e in node.pages[1]["entities"] if e["key"] == "thermal_power")
    entity["unit"] = "W"
    app, _, _ = _app(tmp_path, library, node)

    with TestClient(app) as client:
        preview = client.get(f"/api/v1/devices/{node.device_id}/preview?profile={PROFILE}").json()

    roles = {r["role"]: r for r in preview["roles"]}
    assert roles["heat_power"]["problem"] == "Schlüssel thermal_power hat die Einheit W statt kW"
    assert roles["flow_temperature"]["problem"] is None
    assert not preview["complete"]
    assert not preview["compatibility"]["compatible"]


def test_status_api_reports_incompatible(tmp_path: Path, library: ProfileLibrary) -> None:
    """The status endpoint lists a configured incompatible device with its reason."""
    node = Node()
    node.pages[1]["entities"] = [e for e in node.pages[1]["entities"] if e["key"] != "thermal_power"]
    app, store, manager = _app(tmp_path, library, node)
    config = store.load()
    config.devices.append(DeviceConfig(id="hp", ha_device_id=node.device_id, profile=PROFILE, profile_version=3, name="HPSU CAN", mac=node.mac, component="can"))
    store.save(config)

    with TestClient(app) as client:
        client.portal.call(manager.check, store, snapshot(node), NOW)
        status = client.get("/api/v1/heat_pump/status").json()

    [device] = status["devices"]
    assert device["status"] == "incompatible"
    assert not device["usable"]
    assert device["problems"][0]["reason"] == "Schlüssel thermal_power fehlt"
