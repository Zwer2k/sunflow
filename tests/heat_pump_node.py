"""A Rotex HPSU ESPHome node as Home Assistant sees it: manifest action, registries, states."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

from sunflow.devices.discovery import Snapshot
from sunflow.ha import HAError

# The manifest of the CAN component (two pages) as a node delivers it
PAGES: list[dict[str, Any]] = json.loads((Path(__file__).parent / "data" / "rotex_hpsu_can_manifest.json").read_text())
HA_DOMAINS = {"text_sensor": "sensor"}


def joined(pages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The manifest as fetch_manifest returns it."""
    pages = pages or PAGES
    head = {k: v for k, v in pages[0].items() if k not in ("page", "pages", "entities")}
    return head | {"entities": [e for page in pages for e in page["entities"]]}


class Node:
    """One ESPHome node with the CAN component."""

    def __init__(
        self,
        node: str = "hpsu_can",
        mac: str = "34:b7:da:5e:53:40",
        device_id: str = "dev_can",
        entry_id: str = "entry_can",
    ) -> None:
        """Initialize."""
        self.node, self.mac, self.device_id, self.entry_id = node, mac, device_id, entry_id
        self.pages = copy.deepcopy(PAGES)
        self.sw_version = "2026.11.0 (2026-11-18 22:50:38 +0100)"
        self.offline = False

    @property
    def service(self) -> str:
        """The manifest action in Home Assistant."""
        return f"{self.node}_rotex_can_manifest"

    def entity_id(self, key: str) -> str:
        """Entity id of a manifest key."""
        entity = next(e for page in self.pages for e in page["entities"] if e["key"] == key)
        return f"{HA_DOMAINS.get(entity['domain'], entity['domain'])}.{self.node}_{key}"

    def entities(self) -> list[dict[str, Any]]:
        """Registry entries as ESPHome creates them (unique_id <MAC>/0/<domain>/<name>)."""
        return [
            {
                "entity_id": f"{HA_DOMAINS.get(e['domain'], e['domain'])}.{self.node}_{e['key']}",
                "unique_id": f"{self.mac.upper()}/0/{e['domain']}/{e['name']}",
                "original_name": e["name"],
                "platform": "esphome",
                "device_id": self.device_id,
                "config_entry_id": self.entry_id,
                "disabled_by": None,
            }
            for page in self.pages
            for e in page["entities"]
        ]

    def entry(self) -> dict[str, Any]:
        """The ESPHome config entry."""
        return {"entry_id": self.entry_id, "domain": "esphome", "title": self.node.upper(), "state": "loaded", "unique_id": self.mac}

    def device(self) -> dict[str, Any]:
        """The device registry entry."""
        return {
            "id": self.device_id,
            "name": self.node.upper().replace("_", "."),
            "manufacturer": "Espressif",
            "model": "esp32-s3-devkitc-1",
            "sw_version": self.sw_version,
            "config_entries": [self.entry_id],
            "connections": [["mac", self.mac]],
            "via_device_id": None,
        }

    def states(self, state: str = "1") -> dict[str, dict[str, Any]]:
        """Every entity with the same state."""
        return {e["entity_id"]: {"entity_id": e["entity_id"], "state": state, "attributes": {}} for e in self.entities()}


def snapshot(*nodes: Node, state: str = "1", extra_entries: list[dict[str, Any]] | None = None) -> Snapshot:
    """Registries and states of the nodes."""
    states: dict[str, dict[str, Any]] = {}
    for node in nodes:
        states |= node.states(state)
    return Snapshot(
        entries=[n.entry() for n in nodes] + (extra_entries or []),
        devices=[n.device() for n in nodes],
        entities=[e for n in nodes for e in n.entities()],
        states=states,
        energy_prefs=None,
        integration_names={"esphome": "ESPHome"},
    )


class FakeHA:
    """Home Assistant answering get_services and the manifest actions of the nodes."""

    def __init__(self, *nodes: Node) -> None:
        """Initialize with the nodes that offer a manifest."""
        self.nodes = {n.service: n for n in nodes}
        self.generation = 1
        self.calls: list[tuple[str, int]] = []
        self.subscriptions: list[tuple[str | None, Any]] = []

    async def command(self, msg_type: str, **data: Any) -> Any:
        """Only the service list."""
        assert msg_type == "get_services"
        return {"esphome": {service: {} for service in self.nodes} | {"other_node_restart": {}}, "light": {"turn_on": {}}}

    async def call_service(
        self, domain: str, service: str, data: dict[str, Any] | None = None, target: Any = None, return_response: bool = False
    ) -> Any:
        """A page of the node's manifest."""
        assert domain == "esphome" and return_response
        node = self.nodes[service]
        self.calls.append((service, data["page"]))
        # Gives other tasks a turn, as a real request does (no waiting time)
        await asyncio.sleep(0)
        if node.offline:
            raise HAError("Timeout waiting for response from action call")
        return {"context": {"id": "x"}, "response": node.pages[data["page"]]}

    async def subscribe(self, callback: Any, msg_type: str = "subscribe_events", **data: Any) -> Any:
        """Remember the subscription."""
        self.subscriptions.append((data.get("event_type"), callback))

        async def unsubscribe() -> None:
            return None

        return unsubscribe
