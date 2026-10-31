"""Log of Sunflow's writes to the devices it controls and of changes others made to them.

Home Assistant tells who changed an entity only through the context of its state:
a user (or a program with that user's token), an automation or script (parent
context), or nobody - then the device or its integration changed it by itself.
"""

from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any

from sunflow.devices.executor import PlannedCall
from sunflow.ha import HAClient, HAError

_LOGGER = logging.getLogger(__name__)

JOURNAL_FILE = "control_log.jsonl"
KEEP = timedelta(days=14)
# The file is rewritten without the old entries this often
TRIM_EVERY = timedelta(hours=6)
# Sunflow's write shows up in a later snapshot; the same value until then is Sunflow's
OWN_WRITE_WINDOW = timedelta(minutes=3)
# Entities that set something; sensors change all the time
WATCHED_DOMAINS = ("select", "switch", "number", "input_select", "input_boolean", "input_number", "button")
# Not a change anybody made: the entity could not be read
UNREADABLE = ("unavailable", "unknown")
IDENTITY_REFRESH = timedelta(hours=1)
CAUSERS = {"automation", "script", "scene"}


def expected_state(call: PlannedCall) -> str | None:
    """The state the entity shows after the call, None if it cannot be told."""
    if call.service in ("turn_on", "turn_off"):
        return call.service.removeprefix("turn_")
    for key in ("option", "value"):
        if key in call.data:
            return str(call.data[key])
    return None


def same_state(a: str | None, b: str | None) -> bool:
    """Equal states, numbers compared as numbers ("10" and "10.0")."""
    if a == b:
        return True
    try:
        return a is not None and b is not None and float(a) == float(b)
    except ValueError:
        return False


class ControlJournal:
    """Entries of the last two weeks, in memory and as JSON lines in the data directory."""

    def __init__(self, data_dir: Path, client: HAClient | None = None) -> None:
        """Initialize and read the entries kept so far."""
        self._path = data_dir / JOURNAL_FILE
        self._client = client
        self._entries: deque[dict[str, Any]] = deque()
        self._trimmed_at = datetime.min.replace(tzinfo=UTC)
        # Entity -> (value Sunflow wrote, when)
        self._own: dict[str, tuple[str | None, datetime]] = {}
        # Entity -> state seen last
        self._seen: dict[str, str] = {}
        # Entities Sunflow wrote per device, watched besides the role entities
        self.written: dict[str, set[str]] = {}
        self.own_user: str | None = None
        self.users: dict[str, str] = {}
        self._identity_at = datetime.min.replace(tzinfo=UTC)
        self._load()

    @property
    def path(self) -> Path:
        """The journal file (for the export)."""
        return self._path

    def _load(self) -> None:
        try:
            lines = self._path.read_text().splitlines()
        except FileNotFoundError:
            return
        since = datetime.now(UTC) - KEEP
        for line in lines:
            try:
                entry = json.loads(line)
                if datetime.fromisoformat(entry["time"]) >= since:
                    self._entries.append(entry)
            except (ValueError, KeyError):
                continue

    def record(self, entry: dict[str, Any]) -> None:
        """Keep one entry; time is an aware datetime."""
        entry = {k: v for k, v in entry.items() if v is not None and v != []}
        entry["time"] = entry["time"].isoformat()
        self._entries.append(entry)
        now = datetime.now(UTC)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if now - self._trimmed_at >= TRIM_EVERY:
                self._trim(now)
            with self._path.open("a") as file:
                file.write(json.dumps(entry, default=str) + "\n")
        except OSError as err:
            _LOGGER.warning("Control log not written: %s", err)

    def _trim(self, now: datetime) -> None:
        self._trimmed_at = now
        since = now - KEEP
        while self._entries and datetime.fromisoformat(self._entries[0]["time"]) < since:
            self._entries.popleft()
        tmp = self._path.with_suffix(".tmp")
        # The newest entry is appended by record()
        tmp.write_text("".join(json.dumps(e, default=str) + "\n" for e in list(self._entries)[:-1]))
        tmp.replace(self._path)

    def wrote(
        self, device_id: str, device: str, action: str, power: float | None, reason: str | None,
        calls: list[PlannedCall], now: datetime, error: str | None = None,
    ) -> None:
        """Sunflow wrote to a device."""
        for call in calls:
            if call.entity_id and call.status == "done":
                self._own[call.entity_id] = (expected_state(call), now)
                self.written.setdefault(device_id, set()).add(call.entity_id)
        self.record({
            "time": now, "kind": "sunflow", "device_id": device_id, "device": device, "action": action,
            "power_w": round(power) if power is not None else None, "reason": reason, "error": error,
            "calls": [
                {"entity_id": c.entity_id or c.domain, "service": c.service, "data": c.data, "status": c.status}
                for c in calls if c.status != "skipped"
            ],
        })

    def note(self, device_id: str, device: str, action: str, reason: str, now: datetime) -> None:
        """A write without single calls to show (e.g. the control mode switched)."""
        self.record({"time": now, "kind": "sunflow", "device_id": device_id, "device": device, "action": action, "reason": reason})

    async def observe(
        self, device_id: str, device: str, entity_ids: Iterable[str], states: dict[str, dict[str, Any]], now: datetime
    ) -> None:
        """Note changes of the device's setting entities that Sunflow did not make."""
        await self._identify(now)
        for entity_id in {*entity_ids, *self.written.get(device_id, ())}:
            if entity_id.split(".")[0] not in WATCHED_DOMAINS or (state := states.get(entity_id)) is None:
                continue
            value = state.get("state")
            previous = self._seen.get(entity_id)
            if value in UNREADABLE:
                continue
            self._seen[entity_id] = value
            if previous is None or same_state(previous, value) or self._by_sunflow(entity_id, state, now):
                continue
            self.record({
                "time": now, "kind": "extern", "device_id": device_id, "device": device, "entity_id": entity_id,
                "name": state.get("attributes", {}).get("friendly_name"), "old": previous, "new": value,
                "changed": state.get("last_changed"), "source": self.source(state.get("context") or {}, states),
            })

    def _by_sunflow(self, entity_id: str, state: dict[str, Any], now: datetime) -> bool:
        context = state.get("context") or {}
        if self.own_user and context.get("user_id") == self.own_user:
            return True
        # Directly connected devices and an unknown own user: by the value just written
        own = self._own.get(entity_id)
        return own is not None and now - own[1] <= OWN_WRITE_WINDOW and (own[0] is None or same_state(own[0], state.get("state")))

    def source(self, context: dict[str, Any], states: dict[str, dict[str, Any]]) -> str:
        """Who made a change, as far as Home Assistant's context tells."""
        if user := context.get("user_id"):
            if user == self.own_user:
                return "Sunflow"
            name = self.users.get(user, f"Kennung {user[:8]}")
            return f"Benutzer „{name}“ – oder ein Programm mit dessen Zugangstoken (z. B. evcc, Node-RED)"
        if parent := context.get("parent_id"):
            causer = next(
                (s for s in states.values() if s["entity_id"].split(".")[0] in CAUSERS and (s.get("context") or {}).get("id") == parent),
                None,
            )
            if causer is not None:
                return f"{causer['entity_id'].split('.')[0].capitalize()} „{causer.get('attributes', {}).get('friendly_name', causer['entity_id'])}“"
            return "eine Automation, ein Skript oder eine Szene"
        return (
            "das Gerät bzw. seine Integration selbst – nicht über Home Assistant geschaltet "
            "(z. B. eigene Regelung des Geräts oder ein anderes System, das direkt aufs Gerät schreibt)"
        )

    async def _identify(self, now: datetime) -> None:
        """Sunflow's own Home Assistant user and the names of all users, now and then."""
        if self._client is None or now - self._identity_at < IDENTITY_REFRESH:
            return
        self._identity_at = now
        try:
            self.own_user = (await self._client.command("auth/current_user"))["id"]
        except (HAError, KeyError, TypeError) as err:
            _LOGGER.debug("Own user unknown: %s", err)
        try:
            self.users = {u["id"]: u["name"] for u in await self._client.command("config/auth/list")}
        except (HAError, KeyError, TypeError) as err:
            # Needs an admin user; ids are shown then
            _LOGGER.debug("User names unknown: %s", err)

    def entries(self, since: datetime, device_id: str | None = None) -> list[dict[str, Any]]:
        """Entries since then, newest first."""
        return [
            e for e in reversed(self._entries)
            if datetime.fromisoformat(e["time"]) >= since and (device_id is None or e["device_id"] == device_id)
        ]
