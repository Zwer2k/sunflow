"""Manifests of ESPHome nodes: fetching, assigning, compatibility, storing, discovery (heat_pump_device.md 1)."""

import copy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from heat_pump_node import PAGES, FakeHA, Node, joined, snapshot
import pytest
from sunflow.api.common import display_name
from sunflow.devices.discovery import discover
from sunflow.devices.profiles import ProfileLibrary, load_profiles
from sunflow.devices.resolver import preview
from sunflow.heat_pump.compat import check_compatibility, role_requirements
from sunflow.heat_pump.defaults import DEADBAND_K, MAX_SETPOINT_STEP_K
from sunflow.heat_pump.manifest import (
    DeviceManifest,
    ManifestError,
    assign_device,
    fetch_manifest,
    manifest_hash,
    manifest_services,
)
from sunflow.heat_pump.manifest_store import FILE_NAME, ManifestStore
import yaml

PROFILE = "esphome/rotex_hpsu_can"
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)


def _without(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    return manifest | {"entities": [e for e in manifest["entities"] if e["key"] != key]}


def _changed(manifest: dict[str, Any], key: str, **fields: Any) -> dict[str, Any]:
    return manifest | {"entities": [e | fields if e["key"] == key else e for e in manifest["entities"]]}


# Fetching


async def test_fetch_joins_all_pages() -> None:
    """Page 0 tells the number of pages; the entities of all pages form the manifest."""
    node = Node()
    client = FakeHA(node)

    manifest = await fetch_manifest(client, node.service)

    assert client.calls == [(node.service, 0), (node.service, 1)]
    assert manifest == joined()
    assert "page" not in manifest and "pages" not in manifest
    assert {"tv", "thermal_power", "supply_setpoint_regulated"} <= {e["key"] for e in manifest["entities"]}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda pages: pages[0].pop("entities"), "entities missing"),
        (lambda pages: pages[1].update(id="other"), "another manifest"),
        (lambda pages: pages[1].update(page=0), "page 1"),
        (lambda pages: pages[0]["entities"].append({"key": "x"}), "without key"),
    ],
)
async def test_unreadable_manifest(change: Any, message: str) -> None:
    """A structurally broken manifest is an error, not a partial result."""
    node = Node()
    change(node.pages)

    with pytest.raises(ManifestError, match=message):
        await fetch_manifest(FakeHA(node), node.service)


def test_manifest_services_from_service_list() -> None:
    """Only the manifest actions count; the node name comes from the action name."""
    services = {
        "esphome": {"hpsu_can_rotex_can_manifest": {}, "hpsu_uart_rotex_uart_manifest": {}, "sniffer_restart": {}},
        "light": {"turn_on": {}},
    }

    found = manifest_services(services)

    assert [(s.node, s.component) for s in found] == [("hpsu_can", "can"), ("hpsu_uart", "uart")]


def test_hash_ignores_git_only() -> None:
    """A new build of the same component gives the same hash; any used change a new one."""
    manifest = joined()

    assert manifest_hash(manifest | {"git": "other"}) == manifest_hash(manifest)
    assert manifest_hash(_changed(manifest, "tv", name="Vorlauf")) != manifest_hash(manifest)


# Assigning to a Home Assistant device


def test_assign_unique_device_by_entities() -> None:
    """The one device holding all manifest entities is the node, whatever its name."""
    node = Node()
    snap = snapshot(node)
    snap.devices[0]["name"] = "Wärmepumpe"

    assignment = assign_device(snap, "hpsu_can", joined())

    assert (assignment.ha_device_id, assignment.mac, assignment.problem) == ("dev_can", "34:b7:da:5e:53:40", None)


def _twins(first_name: str, second_name: str, by_user: str | None = None) -> Any:
    """Two equal nodes without node names in their config entries, with these registry names."""
    first = Node(node="hpsu_a", mac="aa:aa:aa:aa:aa:01", device_id="dev_a", entry_id="e_a")
    second = Node(node="hpsu_b", mac="aa:aa:aa:aa:aa:02", device_id="dev_b", entry_id="e_b")
    snap = snapshot(first, second)
    snap.devices[0]["name"], snap.devices[1]["name"] = first_name, second_name
    snap.devices[1]["name_by_user"] = by_user
    return snap


def test_ambiguous_entities_decided_by_device_name() -> None:
    """Equal nodes: the node name of the action matches one device name as a slug (HPSU.A -> hpsu_a)."""
    snap = _twins("HPSU.A", "HPSU.B")

    assert assign_device(snap, "hpsu_a", joined()).ha_device_id == "dev_a"
    assert assign_device(snap, "hpsu_b", joined()).mac == "aa:aa:aa:aa:aa:02"


def test_name_by_user_when_device_has_no_name() -> None:
    """Without a registry name the user's name counts; umlauts become ASCII like in Home Assistant."""
    snap = _twins("Wärmepumpe Keller", "", by_user="HPSU B")

    assert assign_device(snap, "hpsu_b", joined()).ha_device_id == "dev_b"
    assert assign_device(snap, "warmepumpe_keller", joined()).ha_device_id == "dev_a"


@pytest.mark.parametrize(("first", "second"), [("Wärmepumpe", "Keller"), ("HPSU.A", "hpsu-a")])
def test_ambiguous_without_unique_name_stays_unassigned(first: str, second: str) -> None:
    """No device name or both fit the node name: nothing is assigned, with a note."""
    assignment = assign_device(_twins(first, second), "hpsu_a", joined())

    assert assignment.ha_device_id is None
    assert "Mehrere ESPHome-Geräte" in assignment.problem


def test_service_without_matching_device() -> None:
    """A manifest whose entities no ESPHome device has is not assigned."""
    node = Node()
    other = _changed(joined(), "tv", name="Vorlauftemperatur")

    assignment = assign_device(snapshot(node), "hpsu_can", other)

    assert assignment.ha_device_id is None
    assert "Kein ESPHome-Gerät" in assignment.problem


def test_manifest_names_missing_at_device() -> None:
    """A device lacking one of the manifest's entities is not the node: not assigned."""
    node = Node()
    snap = snapshot(node)
    snap.entities.pop(0)

    assignment = assign_device(snap, "hpsu_can", joined())

    assert assignment.ha_device_id is None
    assert "Kein ESPHome-Gerät" in assignment.problem


def test_original_name_is_the_fallback() -> None:
    """An entity whose unique_id does not end with domain/name is found by its original name."""
    node = Node()
    snap = snapshot(node)
    entity = next(e for e in snap.entities if e["entity_id"] == "sensor.hpsu_can_tv")
    entity["unique_id"] = "34B7DA5E5340-sensor-tv"

    assert assign_device(snap, "hpsu_can", joined()).ha_device_id == "dev_can"


# Compatibility


def test_full_manifest_is_compatible(library: ProfileLibrary) -> None:
    """The complete manifest of the CAN component serves every role of the profile."""
    compatibility = check_compatibility(library.profiles[PROFILE], joined())

    assert compatibility.compatible
    assert compatibility.problems == ()


def test_requirements_derived_from_profile(library: ProfileLibrary) -> None:
    """Values come from states and flow_control; runtime-only values are not required."""
    requirements = role_requirements(library.profiles[PROFILE])

    assert requirements["program"].values == {"heating", "lowering", "automatic_1", "automatic_2", "summer", "standby", "cooling"}
    assert requirements["activity"].values == {"heating", "hot_water_production", "defrosting", "standby", "cooling"}
    assert requirements["strategy_select"].values == {"fixed", "weather_dependent"}
    assert requirements["heating_block"].values == {"on", "off"}
    written = requirements["max_flow_setpoint"]
    assert (written.written, written.domains, written.unit, written.cover) == (True, ("number",), "°C", (25, 40))
    assert written.max_step == MAX_SETPOINT_STEP_K == DEADBAND_K
    assert not requirements["flow_temperature"].written
    assert requirements["flow_temperature"].cover is None
    assert requirements["compressor_current"].companion


@pytest.mark.parametrize(
    ("manifest", "role", "reason"),
    [
        (_without(joined(), "max_target_flow_temp"), "max_flow_setpoint", "Schlüssel max_target_flow_temp fehlt"),
        (
            _changed(joined(), "hk_function", values=[[0, "Witterungsgeführt", "weather_dependent"]]),
            "strategy_select",
            "Schlüssel hk_function: Auswahlwerte fehlen: fixed",
        ),
        (_changed(joined(), "mode_of_operating", domain="sensor"), "activity", "Schlüssel mode_of_operating ist sensor statt text_sensor"),
        (_changed(joined(), "thermal_power", unit="W"), "heat_power", "Schlüssel thermal_power hat die Einheit W statt kW"),
        (
            _changed(joined(), "max_target_flow_temp", min=30),
            "max_flow_setpoint",
            "Schlüssel max_target_flow_temp: Bereich 30–90 deckt 25–40 nicht ab",
        ),
        (
            _changed(joined(), "max_target_flow_temp", domain="sensor"),
            "max_flow_setpoint",
            "Schlüssel max_target_flow_temp ist sensor statt number",
        ),
        (
            _changed(joined(), "max_target_flow_temp", unit="K"),
            "max_flow_setpoint",
            "Schlüssel max_target_flow_temp hat die Einheit K statt °C",
        ),
    ],
)
def test_incompatible_with_reason(library: ProfileLibrary, manifest: dict[str, Any], role: str, reason: str) -> None:
    """A required role that is missing or does not fit makes the device incompatible, with the reason."""
    compatibility = check_compatibility(library.profiles[PROFILE], manifest)

    assert not compatibility.compatible
    assert compatibility.for_role(role).reason == reason
    assert compatibility.reasons(optional=False) == [reason]


@pytest.mark.parametrize(("step", "compatible"), [(0.1, True), (0.5, True), (1.0, False)])
def test_step_of_written_role(library: ProfileLibrary, step: float, compatible: bool) -> None:
    """Max T-Vorlauf needs a step of at most the deadband; 1.0 (firmware without tenths) is too coarse."""
    compatibility = check_compatibility(library.profiles[PROFILE], _changed(joined(), "max_target_flow_temp", step=step))

    assert compatibility.compatible == compatible
    if not compatible:
        assert compatibility.reasons() == [
            "Schrittweite von Max T-Vorlauf zu grob (1 K, höchstens 0,5 K) – Firmware der Komponente aktualisieren"
        ]


def test_written_role_must_be_number_even_if_profile_allows_more(library: ProfileLibrary) -> None:
    """A profile listing another domain for the written role still gets a number checked."""
    profile = copy.deepcopy(library.profiles[PROFILE])
    profile["roles"]["max_flow_setpoint"]["domain"] = ["number", "select"]

    compatibility = check_compatibility(profile, _changed(joined(), "max_target_flow_temp", domain="select"))

    assert compatibility.reasons() == ["Schlüssel max_target_flow_temp ist select statt number"]


def test_missing_optional_role_is_compatible(library: ProfileLibrary) -> None:
    """Without an optional role only that function is missing."""
    compatibility = check_compatibility(library.profiles[PROFILE], _without(joined(), "total_electrical_energy"))

    assert compatibility.compatible
    assert compatibility.reasons(optional=True) == ["Schlüssel total_electrical_energy fehlt"]


def test_switch_serves_on_off(library: ProfileLibrary) -> None:
    """The room thermostat created as a switch has on/off without a value map."""
    manifest = _changed(joined(), "room_therm", domain="switch")
    manifest["entities"] = [{k: v for k, v in e.items() if k != "values"} if e["key"] == "room_therm" else e for e in manifest["entities"]]

    assert check_compatibility(library.profiles[PROFILE], manifest).compatible


def test_unreadable_or_foreign_manifest(library: ProfileLibrary) -> None:
    """A manifest without entities or of another component is incompatible."""
    profile = library.profiles[PROFILE]

    assert check_compatibility(profile, {"id": "rotex_hpsu_can"}).reasons() == ["Manifest nicht lesbar"]
    assert check_compatibility(profile, None).reasons() == ["Manifest nicht lesbar"]
    assert check_compatibility(profile, joined() | {"id": "rotex_hpsu_uart"}).reasons() == ["Manifest rotex_hpsu_uart statt rotex_hpsu_can"]


# Profile


def test_profile_references_roles(tmp_path: Path, schema: dict[str, Any], library: ProfileLibrary) -> None:
    """states and flow_control may only name roles of the profile."""
    profile = copy.deepcopy(library.profiles[PROFILE])
    del profile["roles"]["strategy_select"]
    (tmp_path / "hp.yaml").write_text(yaml.safe_dump(profile, allow_unicode=True))
    loaded = load_profiles(tmp_path, schema)

    assert loaded.profiles == {}
    assert "flow_control/strategies/fixed/when: no role strategy_select" in loaded.errors[0].message


def test_profile_quotes_on_off(library: ProfileLibrary) -> None:
    """on/off of the heating block stay strings (YAML 1.1 would read booleans)."""
    assert library.profiles[PROFILE]["flow_control"]["heating_block"] == {"on": "on", "off": "off", "min_duration": "20min"}


# Storing


def test_store_reports_changes_only(tmp_path: Path) -> None:
    """The same manifest again is no change; a different one is."""
    store = ManifestStore(tmp_path)
    manifest = joined()

    assert store.put("hp", manifest, manifest_hash(manifest), "fw1", NOW)
    assert not store.put("hp", manifest, manifest_hash(manifest), "fw1", NOW)
    other = _without(manifest, "tv")
    assert store.put("hp", other, manifest_hash(other), "fw1", NOW)


def test_store_survives_restart(tmp_path: Path) -> None:
    """After a restart the stored manifest is the reference."""
    manifest = joined()
    ManifestStore(tmp_path).put("hp", manifest, manifest_hash(manifest), "fw1", NOW)

    store = ManifestStore(tmp_path)

    assert store.get("hp")["hash"] == manifest_hash(manifest)
    assert not store.put("hp", manifest, manifest_hash(manifest), "fw1", NOW)


@pytest.mark.parametrize("content", [None, "{broken", '{"devices": []}', '{"devices": {"hp": {"hash": 1}}}'])
def test_missing_or_broken_file_counts_as_new(tmp_path: Path, content: str | None) -> None:
    """Without a readable file the next load is new, without an error."""
    if content is not None:
        (tmp_path / FILE_NAME).write_text(content)
    store = ManifestStore(tmp_path)
    manifest = joined()

    assert store.get("hp") is None
    assert store.put("hp", manifest, manifest_hash(manifest), None, NOW)


def test_store_ignores_unknown_keys_and_prunes(tmp_path: Path) -> None:
    """Unknown keys of a newer version are ignored; devices that are gone are dropped."""
    manifest = joined()
    store = ManifestStore(tmp_path)
    store.put("hp", manifest, manifest_hash(manifest), None, NOW)
    store.put("old", manifest, manifest_hash(manifest), None, NOW)
    raw = (tmp_path / FILE_NAME).read_text().replace('"format": 1', '"format": 1, "future": true')
    (tmp_path / FILE_NAME).write_text(raw)

    store = ManifestStore(tmp_path)
    store.prune({"hp"})

    assert ManifestStore(tmp_path).get("old") is None
    assert ManifestStore(tmp_path).get("hp") is not None


# Discovery and roles


def _with_manifest(node: Node, snap: Any) -> Any:
    found = DeviceManifest(
        service=node.service, node=node.node, component="can", manifest=joined(node.pages),
        hash=manifest_hash(joined(node.pages)), ha_device_id=node.device_id, mac=node.mac,
    )
    return replace(snap, manifests={node.device_id: {found.id: found}})


def test_discovery_matches_by_manifest(library: ProfileLibrary) -> None:
    """A node with the manifest gets the profile, MAC and component; other ESPHome nodes do not count."""
    node = Node()
    sniffer = {"entry_id": "e_sniffer", "domain": "esphome", "title": "Sniffer", "state": "loaded"}
    snap = _with_manifest(node, snapshot(node, extra_entries=[sniffer]))

    found = discover(snap, library)

    assert [i.entry_id for i in found] == ["entry_can"]
    device = found[0].devices[0]
    assert (device.profile, device.mac, device.component, device.capabilities) == (PROFILE, node.mac, "can", ["heat_pump"])


@pytest.mark.parametrize(
    ("name", "by_user", "expected"),
    [("HPSU.CAN", None, "HPSU.CAN"), ("HPSU.CAN", "Wärmepumpe", "Wärmepumpe"), (None, None, "Rotex/Daikin HPSU – ESPHome CAN")],
)
def test_name_of_manifest_device(library: ProfileLibrary, name: str | None, by_user: str | None, expected: str) -> None:
    """The registry name (the user's first), else the manifest's; never manufacturer and model of the board."""
    node = Node()
    snap = _with_manifest(node, snapshot(node))
    snap.devices[0]["name"], snap.devices[0]["name_by_user"] = name, by_user

    device = discover(snap, library)[0].devices[0]

    assert device.name == expected
    assert display_name(device, library.profiles[PROFILE]) == expected


def test_esphome_without_manifest_is_not_supported(library: ProfileLibrary) -> None:
    """The same node without a manifest is no recognized device."""
    assert discover(snapshot(Node()), library) == []


def test_roles_resolve_through_manifest(library: ProfileLibrary) -> None:
    """Roles name manifest keys; the entities are found by domain and name in the unique_id."""
    node = Node()
    snap = _with_manifest(node, snapshot(node, state="31.5"))

    roles = {r.role: r for r in preview(library.profiles[PROFILE], snap, node.device_id)}

    assert roles["flow_temperature"].entity_id == "sensor.hpsu_can_tv"
    assert roles["activity"].entity_id == "sensor.hpsu_can_mode_of_operating"
    assert roles["heating_block"].entity_id == "select.hpsu_can_room_therm"
    assert roles["flow_temperature"].value == 31.5
    assert not any(r.missing for r in roles.values())


def test_roles_unbound_without_manifest(library: ProfileLibrary) -> None:
    """Without the manifest nothing is guessed from names."""
    node = Node()

    roles = preview(library.profiles[PROFILE], snapshot(node), node.device_id)

    assert all(r.missing for r in roles)


def test_disabled_entity_is_enabled_on_takeover(library: ProfileLibrary) -> None:
    """A role's entity disabled in Home Assistant is listed to be enabled when taken over."""
    node = Node()
    snap = _with_manifest(node, snapshot(node))
    next(e for e in snap.entities if e["entity_id"] == "sensor.hpsu_can_tv_max")["disabled_by"] = "integration"

    device = discover(snap, library)[0].devices[0]

    assert device.disabled_entities == ["sensor.hpsu_can_tv_max"]


def test_pages_fixture_is_the_component_output() -> None:
    """The fixture is a two-page manifest of the CAN component."""
    assert [p["page"] for p in PAGES] == [0, 1]
    assert {p["pages"] for p in PAGES} == {2}
