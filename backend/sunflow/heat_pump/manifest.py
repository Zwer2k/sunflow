"""Manifests of ESPHome nodes: fetching, assigning to Home Assistant devices, resolving roles.

A component (e.g. daikin_rotex_can) registers a Native API action with a response that
returns its manifest in pages: its stable entity keys with the names, domains, units and
value maps the firmware actually uses. Home Assistant offers it as the action
esphome.<node>_<action> (design/heat_pump_device.md, 1).
"""

from dataclasses import dataclass
import hashlib
import json
import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from sunflow.ha import HAClient
from sunflow.i18n import t

if TYPE_CHECKING:
    from sunflow.devices.discovery import Snapshot

_LOGGER = logging.getLogger(__name__)

ESPHOME = "esphome"
# Manifest action of a component -> component the device configuration stores
MANIFEST_ACTIONS = {"rotex_can_manifest": "can", "rotex_uart_manifest": "uart"}
# More pages than this is no manifest but a fault
MAX_PAGES = 32
# Domain of an ESPHome entity -> domain of its Home Assistant entity
HA_DOMAINS = {"text_sensor": "sensor"}


class ManifestError(Exception):
    """A manifest cannot be read."""


@dataclass(frozen=True, kw_only=True)
class ManifestService:
    """An action offering a manifest."""

    service: str
    # Node name as in the action name ("-" of the ESPHome name becomes "_")
    node: str
    component: str


@dataclass(frozen=True, kw_only=True)
class DeviceManifest:
    """A manifest fetched from a node and the Home Assistant device it belongs to."""

    service: str
    node: str
    component: str
    manifest: dict[str, Any]
    hash: str
    ha_device_id: str | None = None
    mac: str | None = None
    # Why no device was assigned
    problem: str | None = None

    @property
    def id(self) -> str:
        """Manifest id, e.g. rotex_hpsu_can."""
        return self.manifest["id"]


def manifest_services(services: dict[str, Any]) -> list[ManifestService]:
    """The manifest actions in Home Assistant's service list (get_services)."""
    result = []
    for service in sorted((services or {}).get(ESPHOME, {})):
        for action, component in MANIFEST_ACTIONS.items():
            if service.endswith(f"_{action}") and len(service) > len(action) + 1:
                result.append(ManifestService(service=service, node=service[: -len(action) - 1], component=component))
    return result


def _check_page(page: Any, number: int) -> dict[str, Any]:
    """Raise ManifestError unless the page is structurally a manifest page."""
    if not isinstance(page, dict):
        raise ManifestError("no response")
    if not isinstance(page.get("id"), str) or not page["id"]:
        raise ManifestError("id missing")
    if not isinstance(page.get("entities"), list):
        raise ManifestError("entities missing")
    pages = page.get("pages", 1)
    if not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES or page.get("page", 0) != number or number >= pages:
        raise ManifestError(f"page {number} of {pages!r} invalid")
    for entity in page["entities"]:
        if not isinstance(entity, dict) or not all(isinstance(entity.get(k), str) for k in ("key", "domain", "name")):
            raise ManifestError(f"entity without key, domain or name: {entity!r}")
    return page


def valid_manifest(manifest: Any) -> bool:
    """Structurally readable: id and entities with key, domain and name."""
    try:
        _check_page({**manifest, "page": 0, "pages": 1} if isinstance(manifest, dict) else manifest, 0)
    except ManifestError:
        return False
    return True


async def _fetch_page(client: HAClient, service: str, number: int) -> dict[str, Any]:
    result = await client.call_service(ESPHOME, service, {"page": number}, return_response=True)
    return _check_page(result.get("response") if isinstance(result, dict) else None, number)


async def fetch_manifest(client: HAClient, service: str) -> dict[str, Any]:
    """All pages of a manifest joined to one (without page and pages)."""
    first = await _fetch_page(client, service, 0)
    pages = first.get("pages", 1)
    entities = list(first["entities"])
    for number in range(1, pages):
        page = await _fetch_page(client, service, number)
        if page["id"] != first["id"] or page.get("pages") != pages:
            raise ManifestError(f"page {number} belongs to another manifest")
        entities.extend(page["entities"])
    return {k: v for k, v in first.items() if k not in ("page", "pages", "entities")} | {"entities": entities}


def manifest_hash(manifest: dict[str, Any]) -> str:
    """Hash of everything Sunflow may use; git (diagnosis only) and paging are left out."""
    content = {k: v for k, v in manifest.items() if k not in ("git", "page", "pages")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def entities_by_key(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Manifest entities by key; the first wins."""
    result: dict[str, dict[str, Any]] = {}
    for entity in (manifest or {}).get("entities", []):
        result.setdefault(entity["key"], entity)
    return result


def find_entity(entities: list[dict[str, Any]], manifest_entity: dict[str, Any]) -> dict[str, Any] | None:
    """The Home Assistant entity of a manifest entity.

    ESPHome builds the unique_id as <MAC>/<device>/<domain>/<name>; fallback: the original name
    in the matching Home Assistant domain.
    """
    domain, name = manifest_entity["domain"], manifest_entity["name"]
    own = [e for e in entities if e.get("platform") == ESPHOME]
    ending = f"/{domain}/{name}"
    if found := next((e for e in own if (e.get("unique_id") or "").endswith(ending)), None):
        return found
    ha_domain = HA_DOMAINS.get(domain, domain)
    return next(
        (e for e in own if e.get("original_name") == name and e["entity_id"].split(".")[0] == ha_domain), None
    )


def bind_manifest(
    profile: dict[str, Any], manifest: dict[str, Any] | None, entities: list[dict[str, Any]]
) -> dict[str, str | None]:
    """Role -> entity_id through the manifest's key; None without manifest, key or entity."""
    by_key = entities_by_key(manifest)
    bindings: dict[str, str | None] = {}
    for role, spec in profile["roles"].items():
        manifest_entity = by_key.get(spec["key"])
        entity = find_entity(entities, manifest_entity) if manifest_entity else None
        bindings[role] = entity["entity_id"] if entity else None
    return bindings


def key_entity(manifest: dict[str, Any] | None, key: str, entities: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Home Assistant entity of a manifest key."""
    manifest_entity = entities_by_key(manifest).get(key)
    return find_entity(entities, manifest_entity) if manifest_entity else None


@dataclass(frozen=True, kw_only=True)
class Assignment:
    """The Home Assistant device of a manifest, or why there is none."""

    ha_device_id: str | None = None
    mac: str | None = None
    problem: str | None = None


def device_mac(device: dict[str, Any]) -> str | None:
    """MAC address from the device registry's connections, lower case."""
    for kind, value in device.get("connections") or []:
        if kind == "mac" and isinstance(value, str):
            return value.lower()
    return None


def _root_device(snapshot: Snapshot, entry_id: str) -> dict[str, Any] | None:
    """The node itself among the devices of an ESPHome entry (sub-devices hang below it)."""
    devices = [d for d in snapshot.devices if entry_id in d.get("config_entries", [])]
    ids = {d["id"] for d in devices}
    roots = [d for d in devices if d.get("via_device_id") not in ids]
    return roots[0] if len(roots) == 1 else None


def _has_manifest_entities(snapshot: Snapshot, entry_id: str, manifest: dict[str, Any]) -> bool:
    """Every entity the firmware exposes is an entity of this entry (disabled ones count)."""
    entities = [e for e in snapshot.entities if e.get("config_entry_id") == entry_id]
    exposed = [e for e in manifest.get("entities", []) if not e.get("internal")]
    return bool(exposed) and all(find_entity(entities, e) is not None for e in exposed)


def slug(text: str) -> str:
    """Like Home Assistant's slugify: ASCII, lower case, runs of other characters as one "_"."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def _registry_name(device: dict[str, Any] | None) -> str | None:
    """The device's name in the registry as a slug: name, else name_by_user."""
    name = (device or {}).get("name") or (device or {}).get("name_by_user")
    return slug(name) if isinstance(name, str) and name else None


def assign_device(snapshot: Snapshot, node: str, manifest: dict[str, Any]) -> Assignment:
    """The Home Assistant device a manifest came from.

    The device whose entities include every entity of the manifest; Home Assistant does not
    tell the node name of an ESPHome config entry over its API. When several devices have them
    all, the node name in the action name decides, compared with the device names (slug of
    name, else name_by_user), and only when exactly one fits.
    """
    entries = [e for e in snapshot.entries if e.get("domain") == ESPHOME]
    matching = [
        (entry, device)
        for entry in entries
        if _has_manifest_entities(snapshot, entry["entry_id"], manifest)
        and (device := _root_device(snapshot, entry["entry_id"])) is not None
    ]
    if len(matching) > 1:
        by_name = [(entry, device) for entry, device in matching if _registry_name(device) == node]
        if len(by_name) != 1:
            return Assignment(problem=t("Mehrere ESPHome-Geräte passen zum Manifest von {node} – bitte das Gerät im Assistenten wählen.", node=node))
        matching = by_name
    if not matching:
        return Assignment(problem=t("Kein ESPHome-Gerät passt zum Manifest von {node}.", node=node))
    [(entry, device)] = matching
    mac = device_mac(device) or (entry.get("unique_id") or "").lower() or None
    return Assignment(ha_device_id=device["id"], mac=mac)
