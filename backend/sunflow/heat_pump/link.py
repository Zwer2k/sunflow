"""Keeping devices recognized by their manifest linked and checked (design/heat_pump.md, 3.4-3.6).

The manager fetches the manifests of all nodes that offer one, assigns them to Home Assistant
devices and adds them to the snapshot, so roles resolve through manifest keys. For configured
devices it reloads the manifest when the node was flashed (firmware version changed), when it
comes back after being unavailable, and hourly; checks compatibility; finds a node again by its
MAC address; and notes changes once in the journal and the log. Nothing is written to a device.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
from typing import Any

from sunflow.config.models import DeviceConfig
from sunflow.config.store import ConfigStore
from sunflow.control.journal import ControlJournal
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary, manifest_id
from sunflow.devices.resolver import UNAVAILABLE_STATES, bind_roles, device_entities
from sunflow.ha import HAClient, HAError
from sunflow.i18n import t

from .compat import RoleProblem, check_compatibility
from .manifest import (
    DeviceManifest,
    ManifestError,
    assign_device,
    device_mac,
    fetch_manifest,
    manifest_hash,
    manifest_services,
)
from .manifest_store import ManifestStore

_LOGGER = logging.getLogger(__name__)

# Fallback when no event shows a change
RELOAD_EVERY = timedelta(hours=1)
# Discovery and previews reuse a scan this long before asking the nodes again
SCAN_REUSE = timedelta(minutes=5)
JOURNAL_ACTION = "manifest"
# Failed fetches of a node's manifest in a row before the log warns
WARN_AFTER_FAILURES = 2

Status = str  # pending | compatible | incompatible | unavailable | not_found


@dataclass(kw_only=True)
class DeviceLink:
    """Runtime state of one configured device."""

    status: Status = "pending"
    problems: tuple[RoleProblem, ...] = ()
    manifest: DeviceManifest | None = None
    loaded_at: datetime | None = None
    sw_version: str | None = None
    # A role entity went unavailable; nothing may rely on the device until the manifest was checked again
    unavailable: bool = False
    # Last status reported to the journal and the profile version difference noted
    reported: list[Any] | None = None
    version_noted: list[int] | None = None
    entity_ids: set[str] = field(default_factory=set)


class ManifestManager:
    """Manifests of all nodes and the link status of configured devices."""

    def __init__(
        self,
        client: HAClient,
        store: ManifestStore,
        profiles: Callable[[], ProfileLibrary],
        journal: ControlJournal | None = None,
    ) -> None:
        """Initialize; profiles returns the current profile library."""
        self._client = client
        self._store = store
        self._profiles = profiles
        self.journal = journal
        self._scanned: dict[str, DeviceManifest] = {}
        self._scanned_at: datetime | None = None
        # Consecutive failed fetches per action and the error last warned about
        self._failures: dict[str, int] = {}
        self._warned: dict[str, str] = {}
        # One scan at a time: a node answers one manifest request after the other, and the
        # discovery, a preview, taking over and the background check may ask at the same time
        self._scan_lock = asyncio.Lock()
        self._scans = 0
        self._links: dict[str, DeviceLink] = {}
        self._loaded_at: datetime | None = None
        # Why the manifests are due again (flashed, available again, new device)
        self._reasons: set[str] = set()
        self._watched: dict[str, str] = {}
        self._ha_ids: set[str] = set()
        self._followed = -1
        self._wake = asyncio.Event()

    # Scanning all nodes

    async def scan(self, snapshot: Snapshot, now: datetime, force: bool = False) -> list[DeviceManifest]:
        """Fetch the manifests of all nodes offering one and assign them to devices.

        Without force a scan of the last minutes is reused. Only one scan runs at a time; a call
        that waited for another one takes its result. A node that does not answer keeps its last
        manifest.
        """
        requested = self._scans
        async with self._scan_lock:
            if self._scans != requested:
                return list(self._scanned.values())
            if not force and self._scanned_at is not None and now - self._scanned_at < SCAN_REUSE:
                return list(self._scanned.values())
            try:
                return await self._scan(snapshot, now)
            finally:
                self._scans += 1

    async def _scan(self, snapshot: Snapshot, now: datetime) -> list[DeviceManifest]:
        services = manifest_services(await self._client.command("get_services"))
        scanned: dict[str, DeviceManifest] = {}
        for service in services:
            try:
                manifest = await fetch_manifest(self._client, service.service)
            except (HAError, ManifestError) as err:
                self._fetch_failed(service.service, service.node, err)
                if (previous := self._scanned.get(service.service)) is not None:
                    scanned[service.service] = previous
                continue
            self._failures.pop(service.service, None)
            self._warned.pop(service.service, None)
            assignment = assign_device(snapshot, service.node, manifest)
            found = DeviceManifest(
                service=service.service,
                node=service.node,
                component=service.component,
                manifest=manifest,
                hash=manifest_hash(manifest),
                ha_device_id=assignment.ha_device_id,
                mac=assignment.mac,
                problem=assignment.problem,
            )
            previous = self._scanned.get(service.service)
            if found.problem and (previous is None or previous.problem != found.problem):
                _LOGGER.warning("%s", found.problem)
            scanned[service.service] = found
        self._scanned, self._scanned_at = scanned, now
        return list(scanned.values())

    def _fetch_failed(self, service: str, node: str, err: Exception) -> None:
        """A single failure (e.g. the node busy for a moment) is no warning; a repeated one is, once per error."""
        failures = self._failures[service] = self._failures.get(service, 0) + 1
        if failures < WARN_AFTER_FAILURES:
            _LOGGER.debug("Manifest of %s not readable, trying again later: %s", node, err)
        elif self._warned.get(service) != str(err):
            _LOGGER.warning("Manifest of %s not readable (%s times in a row): %s", node, failures, err)
            self._warned[service] = str(err)

    def contribute(self, snapshot: Snapshot) -> Snapshot:
        """The snapshot with the manifests of the assigned nodes."""
        by_device: dict[str, dict[str, DeviceManifest]] = {}
        for found in self._scanned.values():
            if found.ha_device_id is not None:
                by_device.setdefault(found.ha_device_id, {}).setdefault(found.id, found)
        return replace(snapshot, manifests=by_device) if by_device else snapshot

    def notes(self) -> list[str]:
        """Manifests no device could be assigned to, with the reason."""
        return [found.problem for found in self._scanned.values() if found.problem]

    # Configured devices

    async def check(self, store: ConfigStore, snapshot: Snapshot, now: datetime) -> None:
        """Reload manifests when due and check every configured device recognized by a manifest."""
        await self._follow()
        library = self._profiles()
        config = store.load()
        self._store.prune({d.id for d in [*config.devices, *config.disabled_devices]})
        devices = [d for d in config.devices if manifest_id(library.profiles.get(d.profile, {}))]
        for gone in set(self._links) - {d.id for d in devices}:
            del self._links[gone]
        if not devices:
            self._watched, self._ha_ids = {}, set()
            return
        registry = {d["id"]: d for d in snapshot.devices}
        for device in devices:
            link = self._links.setdefault(device.id, DeviceLink())
            version = (registry.get(device.ha_device_id) or {}).get("sw_version")
            if link.sw_version is not None and version is not None and version != link.sw_version:
                self._reasons.add("flashed")
            if device.ha_device_id not in registry and device.mac and any(device_mac(d) == device.mac for d in snapshot.devices):
                # The node got a new device id: its manifest is assigned anew
                self._reasons.add("moved")
        due = self._loaded_at is None or bool(self._reasons) or now - self._loaded_at >= RELOAD_EVERY
        if due:
            try:
                await self.scan(snapshot, now, force=True)
            except HAError as err:
                _LOGGER.warning("Manifests not reloaded: %s", err)
                return
            self._loaded_at = now
            self._reasons.clear()
        snapshot = self.contribute(snapshot)
        for device in devices:
            self._evaluate(store, device, snapshot, library, now, due)
        self._watched = {entity_id: device.id for device in devices for entity_id in self._links[device.id].entity_ids}
        self._ha_ids = {d.ha_device_id for d in store.load().devices}

    def _evaluate(
        self, store: ConfigStore, device: DeviceConfig, snapshot: Snapshot, library: ProfileLibrary, now: datetime, loaded: bool
    ) -> None:
        link = self._links[device.id]
        profile = library.profiles[device.profile]
        registry = {d["id"]: d for d in snapshot.devices}
        ha_device = registry.get(device.ha_device_id)
        if ha_device is None and device.mac:
            ha_device = next((d for d in snapshot.devices if device_mac(d) == device.mac), None)
            if ha_device is not None:
                device = self._rebind(store, device, ha_device["id"], now)
        if ha_device is None:
            link.status, link.problems = "not_found", ()
            self._report(device, link, ["not_found"], t("ESPHome-Gerät nicht gefunden (MAC {mac}) – bitte im Assistenten neu zuordnen.", mac=device.mac or "–"), now)
            return
        link.sw_version = ha_device.get("sw_version")
        wanted = manifest_id(profile)
        found = next(
            (f for f in self._scanned.values() if f.ha_device_id == ha_device["id"] and f.id == wanted), None
        )
        entities = device_entities(snapshot, ha_device["id"])
        bindings = bind_roles(profile, entities, {found.id: found} if found else None)
        link.entity_ids = {entity_id for entity_id in bindings.values() if entity_id}
        # Without a manifest the node's own entities tell whether it is reachable
        watched = link.entity_ids or {e["entity_id"] for e in entities if e.get("platform") == "esphome"}
        if link.unavailable or (watched and all(
            (snapshot.states.get(e) or {}).get("state") in UNAVAILABLE_STATES for e in watched
        )):
            # Not reported: a node restarting is normal; checked again once it is back
            link.status = "unavailable"
            return
        if found is None:
            link.status, link.problems = "incompatible", ()
            self._report(device, link, ["no_manifest"], t("Kein Manifest vom Gerät – Firmware ohne Manifest? Sunflow nutzt das Gerät nicht."), now)
            return
        if loaded or link.manifest is None or link.manifest.hash != found.hash:
            had = self._store.get(device.id) is not None
            if self._store.put(device.id, found.manifest, found.hash, link.sw_version, now):
                if had:
                    self._journal_note(device, t("Manifest geändert, wird neu geprüft"), now)
                else:
                    self._journal_note(
                        device,
                        t("Manifest geladen: {count} Entities, Komponente {git}", count=len(found.manifest["entities"]), git=found.manifest.get("git") or "–"),
                        now,
                    )
            link.manifest, link.loaded_at = found, now
        self._note_profile_version(device, link, profile, now)
        compatibility = check_compatibility(profile, found.manifest)
        link.problems = compatibility.problems
        if not compatibility.compatible:
            link.status = "incompatible"
            message = t("Nicht kompatibel – Sunflow nutzt das Gerät nicht: {reasons}", reasons="; ".join(compatibility.reasons(optional=False)))
        else:
            link.status = "compatible"
            optional = compatibility.reasons(optional=True)
            message = t("Kompatibel; optionale Funktionen fehlen: {reasons}", reasons="; ".join(optional)) if optional else t("Kompatibel")
        self._report(device, link, [link.status, *sorted(compatibility.reasons())], message, now)

    def _report(self, device: DeviceConfig, link: DeviceLink, signature: list[Any], message: str, now: datetime) -> None:
        """Journal and log a status once per change (also across restarts while a manifest is stored)."""
        previous = link.reported if link.reported is not None else self._store.note(device.id, "status")
        link.reported = signature
        self._store.set_note(device.id, "status", signature)
        if previous == signature:
            return
        # A first check that finds everything in order is no news
        if previous is None and signature == ["compatible"]:
            return
        self._journal_note(device, message, now, warn=signature[0] != "compatible")

    def _note_profile_version(self, device: DeviceConfig, link: DeviceLink, profile: dict[str, Any], now: datetime) -> None:
        current = profile["version"]
        if device.profile_version == current:
            return
        noted = [device.profile_version, current]
        known = noted in (link.version_noted, self._store.note(device.id, "profile_version"))
        link.version_noted = noted
        self._store.set_note(device.id, "profile_version", noted)
        if known:
            return
        self._journal_note(
            device,
            t(
                "Profil {profile} liegt in Version {current} vor, übernommen wurde Version {stored} – bitte die Rollen in der Vorschau prüfen.",
                profile=device.profile, current=current, stored=device.profile_version,
            ),
            now,
            warn=True,
        )

    def _rebind(self, store: ConfigStore, device: DeviceConfig, ha_device_id: str, now: datetime) -> DeviceConfig:
        """The node got a new Home Assistant device id (e.g. integration added again): follow it by MAC."""
        config = store.load()
        stored = config.device(device.id)
        if stored is None or any(d.ha_device_id == ha_device_id for d in [*config.devices, *config.disabled_devices]):
            return device
        old, stored.ha_device_id = stored.ha_device_id, ha_device_id
        store.save(config)
        self._journal_note(
            stored, t("Über die MAC {mac} neu zugeordnet (Home-Assistant-Gerät {old} → {new})", mac=device.mac, old=old, new=ha_device_id), now
        )
        return stored

    def _journal_note(self, device: DeviceConfig, message: str, now: datetime, warn: bool = False) -> None:
        (_LOGGER.warning if warn else _LOGGER.info)("%s: %s", device.name, message)
        if self.journal is not None:
            self.journal.note(device.id, device.name, JOURNAL_ACTION, message, now)

    # Events

    async def _follow(self) -> None:
        """Subscribe to device registry changes once per connection."""
        generation = getattr(self._client, "generation", 0)
        if self._followed == generation:
            return
        try:
            await self._client.subscribe(self.on_device_registry_updated, event_type="device_registry_updated")
        except HAError as err:
            _LOGGER.warning("Cannot follow device registry changes, manifests reload hourly: %s", err)
        self._followed = generation

    def on_device_registry_updated(self, event: dict[str, Any]) -> None:
        """A node was flashed (firmware version changed) or a device was added: reload soon."""
        data = event.get("data", {})
        flashed = data.get("action") == "update" and data.get("device_id") in self._ha_ids and "sw_version" in (data.get("changes") or {})
        if flashed or data.get("action") == "create":
            self._reasons.add("registry")
            self._wake.set()

    def on_state_changed(self, event: dict[str, Any]) -> None:
        """A role entity went unavailable (stop relying on the device) or came back (reload)."""
        data = event.get("data", {})
        if (device_id := self._watched.get(data.get("entity_id"))) is None or (link := self._links.get(device_id)) is None:
            return
        new = (data.get("new_state") or {}).get("state")
        if new == "unavailable":
            link.unavailable = True
            if link.status != "not_found":
                link.status = "unavailable"
        elif link.unavailable and new is not None:
            link.unavailable = False
            self._reasons.add("available")
            self._wake.set()

    @property
    def wants_fresh(self) -> bool:
        """The next check should read the registries anew (an event showed a change)."""
        return bool(self._reasons)

    async def wait(self, timeout: float) -> None:
        """Until an event asks for a check, at most timeout seconds."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout)
        except TimeoutError:
            pass
        self._wake.clear()

    # Status

    def usable(self, device_id: str) -> bool:
        """The device was checked compatible and is reachable since."""
        link = self._links.get(device_id)
        return link is not None and link.status == "compatible" and not link.unavailable

    def status(self) -> dict[str, dict[str, Any]]:
        """Per configured device: status, problems, manifest and firmware."""
        result = {}
        for device_id, link in self._links.items():
            found = link.manifest
            result[device_id] = {
                "status": link.status,
                "usable": self.usable(device_id),
                "problems": [
                    {"role": p.role, "key": p.key, "optional": p.optional, "reason": p.reason} for p in link.problems
                ],
                "manifest": {
                    "id": found.id,
                    "service": found.service,
                    "component": found.component,
                    "git": found.manifest.get("git"),
                    "language": found.manifest.get("language"),
                    "entities": len(found.manifest["entities"]),
                    "hash": found.hash,
                } if found else None,
                "loaded_at": link.loaded_at.isoformat() if link.loaded_at else None,
                "sw_version": link.sw_version,
                "profile_version_noted": link.version_noted,
            }
        return result
