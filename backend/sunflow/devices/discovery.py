"""Discovery of integrations and devices in Home Assistant."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import Any, Protocol

from sunflow.ha import HAClient

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


async def _fetch_registries(client: HAClient) -> Snapshot:
    entries, devices, entities, energy_prefs = await asyncio.gather(
        client.get_config_entries(),
        client.get_device_registry(),
        client.get_entity_registry(),
        client.get_energy_prefs(),
    )
    manifests = await client.command(
        "manifest/list", integrations=sorted({e["domain"] for e in entries})
    )
    return Snapshot(
        entries=entries,
        devices=devices,
        entities=entities,
        states={},
        energy_prefs=energy_prefs,
        integration_names={m["domain"]: m["name"] for m in manifests},
    )


class Contributor(Protocol):
    """Adds devices that are not in Home Assistant (sunflow.direct.hub.DirectHub)."""

    def contribute(self, snapshot: Snapshot) -> Snapshot:
        """Snapshot with the own devices added."""


async def fetch_snapshot(client: HAClient, direct: Contributor | None = None) -> Snapshot:
    """Fetch registries, states and energy preferences."""
    registries, states = await asyncio.gather(_fetch_registries(client), client.get_states())
    snapshot = replace(registries, states={s["entity_id"]: s for s in states})
    return direct.contribute(snapshot) if direct else snapshot


GAP_STATES = {"unknown", "unavailable"}
# Inverter integrations report single failed reads as "unknown"; longer outages stay visible
GAP_BRIDGE = timedelta(minutes=15)


class SnapshotCache:
    """Shares snapshots between requests to spare Home Assistant.

    Registries and all states are several MB on large installations; fetching them
    for every poll of every open browser tab slows Home Assistant itself down.
    Registries change rarely and are reused for longer than the states.
    """

    def __init__(
        self,
        client: HAClient,
        registry_ttl: timedelta = timedelta(minutes=2),
        states_ttl: timedelta = timedelta(seconds=4),
        direct: Contributor | None = None,
    ) -> None:
        """Initialize the cache."""
        self._client = client
        self._direct = direct
        self._registry_ttl = registry_ttl
        self._states_ttl = states_ttl
        self._lock = asyncio.Lock()
        self._registries: Snapshot | None = None
        self._registries_at = datetime.min.replace(tzinfo=UTC)
        self._snapshot: Snapshot | None = None
        self._states_at = datetime.min.replace(tzinfo=UTC)
        self._generation = -1
        # Last valid state per entity, to bridge short "unknown" phases (e.g. Modbus read errors)
        self._last_good: dict[str, tuple[dict[str, Any], datetime]] = {}

    async def get(self, fresh: bool = False) -> Snapshot:
        """Return a snapshot; fresh refetches registries and states."""
        # One fetch at a time: concurrent requests wait and reuse its result
        async with self._lock:
            now = datetime.now(UTC)
            # After a reconnect (e.g. Home Assistant restarted) everything may have changed
            reconnected = getattr(self._client, "generation", 0) != self._generation
            if fresh or reconnected or self._registries is None or now - self._registries_at > self._registry_ttl:
                self._registries = await _fetch_registries(self._client)
                self._registries_at = now
                self._generation = getattr(self._client, "generation", 0)
                self._snapshot = None
            if fresh or self._snapshot is None or now - self._states_at > self._states_ttl:
                states = await self._client.get_states()
                self._snapshot = replace(self._registries, states=self._bridge_gaps(states, now))
                self._states_at = now
            # Direct devices are polled on their own; their latest values are always added
            return self._direct.contribute(self._snapshot) if self._direct else self._snapshot

    def _bridge_gaps(self, states: list[dict[str, Any]], now: datetime) -> dict[str, dict[str, Any]]:
        """States by entity; a briefly unavailable value keeps its last valid state."""
        result = {}
        for state in states:
            entity_id = state["entity_id"]
            if state.get("state") not in GAP_STATES:
                self._last_good[entity_id] = (state, now)
            elif (good := self._last_good.get(entity_id)) is not None and now - good[1] <= GAP_BRIDGE:
                state = good[0]
            result[entity_id] = state
        return result


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
    """Return True if the entity's unique_id ends with the given key.

    A key may hold wildcards for parts the integration fills in, e.g. the serial of an
    attached battery in "battery_*_dc_power".
    """
    unique_id = entity.get("unique_id") or ""
    if any(c in key for c in "*?["):
        return fnmatch(unique_id, key) or fnmatch(unique_id, f"*_{key}") or fnmatch(unique_id, f"*-{key}")
    # Integrations join with "_" (solax_modbus, evcc) or "-" (go-e: "216505-select-frc-frc")
    return unique_id == key or unique_id.endswith((f"_{key}", f"-{key}"))


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
        if "model" in match:
            patterns = match["model"] if isinstance(match["model"], list) else [match["model"]]
            if not any(fnmatch(device.get("model") or "", pattern) for pattern in patterns):
                continue
        if not all(
            any(entity_has_key(e, key) for e in device_entities)
            for key in match.get("requires", [])
        ):
            continue
        return profile["profile"]
    return None


# Pseudo integration of the devices Sunflow talks to itself (sunflow.direct); their model is the profile id
DIRECT_DOMAIN = "sunflow_direct"


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
        supported = domain in supported_domains or domain == DIRECT_DOMAIN
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
            if domain == DIRECT_DOMAIN:
                profile_id = device.get("model") if device.get("model") in library.profiles else None
            else:
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
