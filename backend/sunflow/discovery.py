"""Discovery of integrations and devices in Home Assistant."""

import asyncio
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from .ha import HAClient
from .profiles import ProfileLibrary


@dataclass(kw_only=True)
class DiscoveredDevice:
    """A Home Assistant device and the profile matched to it."""

    device_id: str
    name: str
    manufacturer: str | None
    model: str | None
    entity_count: int
    profile: str | None
    capabilities: list[str] = field(default_factory=list)
    components: list[DiscoveredDevice] = field(default_factory=list)


@dataclass(kw_only=True)
class DiscoveredIntegration:
    """A config entry of an integration."""

    entry_id: str
    domain: str
    name: str
    title: str
    state: str
    supported: bool
    in_energy_dashboard: bool
    devices: list[DiscoveredDevice] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    """Registry data needed for matching."""

    entries: list[dict[str, Any]]
    devices: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    states: dict[str, dict[str, Any]]
    energy_prefs: dict[str, Any] | None
    integration_names: dict[str, str] = field(default_factory=dict)


async def fetch_snapshot(client: HAClient) -> Snapshot:
    """Fetch registries, states and energy preferences."""
    entries, devices, entities, states, energy_prefs = await asyncio.gather(
        client.get_config_entries(),
        client.get_device_registry(),
        client.get_entity_registry(),
        client.get_states(),
        client.get_energy_prefs(),
    )
    manifests = await client.command(
        "manifest/list", integrations=sorted({e["domain"] for e in entries})
    )
    return Snapshot(
        entries=entries,
        devices=devices,
        entities=entities,
        states={s["entity_id"]: s for s in states},
        energy_prefs=energy_prefs,
        integration_names={m["domain"]: m["name"] for m in manifests},
    )


def energy_entity_ids(prefs: dict[str, Any] | None) -> set[str]:
    """Return all entity ids referenced by the energy dashboard."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and "." in value and " " not in value:
            found.add(value)

    walk(prefs)
    return found


def entity_has_key(entity: dict[str, Any], key: str) -> bool:
    """Return True if the entity's unique_id ends with the given key."""
    unique_id = entity.get("unique_id") or ""
    return unique_id == key or unique_id.endswith(f"_{key}")


def match_profile(
    library: ProfileLibrary,
    domain: str,
    device: dict[str, Any],
    device_entities: list[dict[str, Any]],
    entry_has_battery: bool,
) -> str | None:
    """Return the id of the first profile matching the device."""
    for profile in library.for_integration(domain):
        match = profile["match"]
        if "has_battery" in match and match["has_battery"] != entry_has_battery:
            continue
        if "manufacturer" in match and match["manufacturer"] != device.get(
            "manufacturer"
        ):
            continue
        if "model" in match and not fnmatch(device.get("model") or "", match["model"]):
            continue
        if not all(
            any(entity_has_key(e, key) for e in device_entities)
            for key in match.get("requires", [])
        ):
            continue
        return profile["profile"]
    return None


def discover(snapshot: Snapshot, library: ProfileLibrary) -> list[DiscoveredIntegration]:
    """Build the integration and device overview for the setup wizard."""
    energy_ids = energy_entity_ids(snapshot.energy_prefs)
    supported_domains = library.integrations

    entities_by_device: dict[str, list[dict[str, Any]]] = {}
    entities_by_entry: dict[str, list[dict[str, Any]]] = {}
    for entity in snapshot.entities:
        if entity.get("disabled_by"):
            continue
        if device_id := entity.get("device_id"):
            entities_by_device.setdefault(device_id, []).append(entity)
        if entry_id := entity.get("config_entry_id"):
            entities_by_entry.setdefault(entry_id, []).append(entity)

    result: list[DiscoveredIntegration] = []
    for entry in snapshot.entries:
        entry_id = entry["entry_id"]
        domain = entry["domain"]
        entry_entities = entities_by_entry.get(entry_id, [])
        in_energy = any(e["entity_id"] in energy_ids for e in entry_entities)
        supported = domain in supported_domains
        if not supported and not in_energy:
            continue

        entry_has_battery = any(
            snapshot.states.get(e["entity_id"], {}).get("attributes", {}).get(
                "device_class"
            )
            == "battery"
            for e in entry_entities
        )
        integration = DiscoveredIntegration(
            entry_id=entry_id,
            domain=domain,
            name=snapshot.integration_names.get(domain, domain),
            title=entry["title"],
            state=entry["state"],
            supported=supported,
            in_energy_dashboard=in_energy,
        )
        entry_devices = [
            d
            for d in snapshot.devices
            if entry_id in d.get("config_entries", [])
            and entities_by_device.get(d["id"])
        ]
        entry_device_ids = {d["id"] for d in entry_devices}
        by_id: dict[str, DiscoveredDevice] = {}
        for device in entry_devices:
            device_entities = entities_by_device[device["id"]]
            # Sub-devices such as battery modules are matched as part of their parent
            is_component = device.get("via_device_id") in entry_device_ids
            profile_id = (
                None
                if is_component
                else match_profile(
                    library, domain, device, device_entities, entry_has_battery
                )
            )
            by_id[device["id"]] = DiscoveredDevice(
                device_id=device["id"],
                name=device.get("name_by_user") or device.get("name") or device["id"],
                manufacturer=device.get("manufacturer"),
                model=device.get("model"),
                entity_count=len(device_entities),
                profile=profile_id,
                capabilities=library.profiles[profile_id]["capabilities"]
                if profile_id
                else [],
            )
        for device in entry_devices:
            discovered = by_id[device["id"]]
            if (parent_id := device.get("via_device_id")) in by_id:
                by_id[parent_id].components.append(discovered)
            else:
                integration.devices.append(discovered)
        result.append(integration)

    result.sort(key=lambda i: (not i.supported, not i.in_energy_dashboard, i.title))
    return result
