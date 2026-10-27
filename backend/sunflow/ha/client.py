"""Websocket client for the Home Assistant API."""

import asyncio
from collections.abc import Callable
import itertools
import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# A command without an answer must not block callers forever (e.g. a stalled Core
# behind a proxy that still answers the heartbeat); large get_states take seconds
COMMAND_TIMEOUT = 60
# Statistics and history are read from the recorder's database. When it is gone or
# behind, every query waits in Home Assistant; more queries only add to the load
RECORDER_COMMANDS = ("recorder/", "history/")
RECORDER_CHECK_SECONDS = 60
RECORDER_MAX_BACKLOG = 1000
RECORDER_PAUSE_SECONDS = 120

type EventCallback = Callable[[dict[str, Any]], None]


class HAError(Exception):
    """Error returned by Home Assistant or the connection."""


class HACommandError(HAError):
    """Home Assistant answered, but the command or action failed (the connection is fine)."""


class RecorderUnavailable(HACommandError):
    """The recorder database is not keeping up; its queries are paused."""


class HATimeout(HAError):
    """No answer within COMMAND_TIMEOUT."""


class HAClient:
    """Minimal Home Assistant websocket client.

    One reader task dispatches results to pending commands and events to
    subscription callbacks.
    """

    def __init__(
        self, url: str, token: str, session: aiohttp.ClientSession | None = None
    ) -> None:
        """Initialize the client."""
        self._url = url
        self._token = token
        self._session = session
        self._owns_session = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._subscriptions: dict[int, EventCallback] = {}
        self._reader: asyncio.Task[None] | None = None
        self._closing = False
        self.ha_version: str | None = None
        # Increases with every successful connect so caches can detect reconnects
        self.generation = 0
        self._recorder_checked = float("-inf")
        self._recorder_paused_until = float("-inf")
        self._recorder_pause_reason = ""

    @property
    def connected(self) -> bool:
        """Return True if the websocket is open and its reader still runs."""
        return (
            self._ws is not None
            and not self._ws.closed
            and self._reader is not None
            and not self._reader.done()
        )

    async def connect(self) -> None:
        """Connect and authenticate."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        self._closing = False
        try:
            # Registries and states of large installations exceed aiohttp's 4 MB default
            self._ws = await self._session.ws_connect(self._url, heartbeat=30, max_msg_size=0)
            msg = await self._ws.receive_json()
            if msg["type"] != "auth_required":
                raise HAError(f"Unexpected message: {msg['type']}")
            await self._ws.send_json({"type": "auth", "access_token": self._token})
            msg = await self._ws.receive_json()
        except Exception as err:
            # Also a server closing during auth (receive_json then fails with a type error)
            await self.close()
            raise HAError(f"Cannot connect to {self._url}: {err}") from err
        if msg["type"] != "auth_ok":
            await self.close()
            raise HAError(msg.get("message", "Authentication failed"))
        self.ha_version = msg.get("ha_version")
        self.generation += 1
        self._reader = asyncio.create_task(self._read_loop())
        _LOGGER.info("Connected to Home Assistant %s", self.ha_version)

    async def close(self) -> None:
        """Close the connection."""
        self._closing = True
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
        self._fail_pending(HAError("Connection closed"))

    async def command(self, msg_type: str, **data: Any) -> Any:
        """Send a command and return its result; database queries only while the recorder keeps up."""
        if not msg_type.startswith(RECORDER_COMMANDS) or msg_type == "recorder/info":
            return await self._send(msg_type, **data)
        await self._check_recorder()
        try:
            return await self._send(msg_type, **data)
        except HATimeout as err:
            # A hanging database leaves every query waiting
            self._pause_recorder(f"{msg_type}: {err}")
            raise
        except HACommandError as err:
            # Often a single query failing (e.g. one statistic), not the database: the
            # next query asks the recorder first and only pauses if it is really behind
            if str(err).startswith("unknown_error"):
                self._recorder_checked = 0.0
            raise

    def _pause_recorder(self, reason: str) -> None:
        self._recorder_paused_until = time.monotonic() + RECORDER_PAUSE_SECONDS
        self._recorder_pause_reason = reason
        _LOGGER.warning("Pausing database queries for %s s: %s", RECORDER_PAUSE_SECONDS, reason)

    async def _check_recorder(self) -> None:
        now = time.monotonic()
        if now < self._recorder_paused_until:
            raise RecorderUnavailable(
                "recorder_unavailable: Datenbank von Home Assistant antwortet nicht, Abfragen für "
                f"{int(self._recorder_paused_until - now)} s pausiert (Auslöser: {self._recorder_pause_reason})"
            )
        if now - self._recorder_checked < RECORDER_CHECK_SECONDS:
            return
        self._recorder_checked = now
        try:
            info = await self._send("recorder/info")
        except HACommandError:
            # Without the info command nothing is known; do not block
            return
        except HATimeout as err:
            # The info waits for the database connection
            self._pause_recorder(f"recorder/info: {err}")
            raise RecorderUnavailable("recorder_unavailable: Datenbank von Home Assistant antwortet nicht, Abfragen pausiert") from err
        backlog = info.get("backlog") or 0
        if not info.get("thread_running", True) or info.get("migration_in_progress") or backlog > RECORDER_MAX_BACKLOG:
            self._pause_recorder(f"recorder busy (backlog {backlog}, running {info.get('thread_running')})")
            raise RecorderUnavailable("recorder_unavailable: Datenbank von Home Assistant ist ausgelastet, Abfragen pausiert")

    async def _send(self, msg_type: str, **data: Any) -> Any:
        if self._ws is None:
            raise HAError("Not connected")
        msg_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._ws.send_json({"id": msg_id, "type": msg_type, **data})
            return await asyncio.wait_for(future, COMMAND_TIMEOUT)
        except TimeoutError as err:
            raise HATimeout(f"No answer to {msg_type} within {COMMAND_TIMEOUT} s") from err
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as err:
            raise HAError(f"Sending {msg_type} failed: {err}") from err
        finally:
            self._pending.pop(msg_id, None)

    async def subscribe(
        self, callback: EventCallback, msg_type: str = "subscribe_events", **data: Any
    ) -> Callable[[], Any]:
        """Subscribe and return a coroutine function that unsubscribes."""
        if self._ws is None:
            raise HAError("Not connected")
        msg_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        self._subscriptions[msg_id] = callback
        await self._ws.send_json({"id": msg_id, "type": msg_type, **data})
        try:
            await future
        except HAError:
            self._subscriptions.pop(msg_id, None)
            raise

        async def unsubscribe() -> None:
            self._subscriptions.pop(msg_id, None)
            await self.command("unsubscribe_events", subscription=msg_id)

        return unsubscribe

    async def get_states(self) -> list[dict[str, Any]]:
        """Return all entity states."""
        return await self.command("get_states")

    async def get_config_entries(self) -> list[dict[str, Any]]:
        """Return all config entries."""
        return await self.command("config_entries/get")

    async def get_device_registry(self) -> list[dict[str, Any]]:
        """Return all devices."""
        return await self.command("config/device_registry/list")

    async def get_entity_registry(self) -> list[dict[str, Any]]:
        """Return all registry entries."""
        return await self.command("config/entity_registry/list")

    async def get_energy_prefs(self) -> dict[str, Any] | None:
        """Return the energy dashboard preferences, None if not configured."""
        try:
            return await self.command("energy/get_prefs")
        except HAError as err:
            if "not_found" in str(err):
                return None
            raise

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
        return_response: bool = False,
    ) -> Any:
        """Call an action."""
        msg: dict[str, Any] = {"domain": domain, "service": service}
        if data:
            msg["service_data"] = data
        if target:
            msg["target"] = target
        if return_response:
            msg["return_response"] = True
        return await self.command("call_service", **msg)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if raw.type is not aiohttp.WSMsgType.TEXT:
                    _LOGGER.warning(
                        "Websocket closed: %s %s (close code %s)",
                        raw.type.name,
                        raw.data or self._ws.exception() or "",
                        self._ws.close_code,
                    )
                    break
                payload = raw.json()
                for msg in payload if isinstance(payload, list) else [payload]:
                    try:
                        self._dispatch(msg)
                    except Exception:
                        # One malformed message must not end the reader
                        _LOGGER.exception("Cannot handle message from Home Assistant")
        finally:
            self._fail_pending(HAError("Connection lost"))
            if not self._closing:
                _LOGGER.warning("Connection to Home Assistant lost")
                # Close so connected turns False and the keeper reconnects
                ws, self._ws = self._ws, None
                if ws is not None:
                    await ws.close()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        if msg["type"] == "event":
            if (callback := self._subscriptions.get(msg_id)) is not None:
                callback(msg["event"])
            return
        if msg["type"] != "result" or (future := self._pending.pop(msg_id, None)) is None:
            return
        if future.done():
            return
        if msg.get("success"):
            future.set_result(msg.get("result"))
        else:
            error = msg.get("error", {})
            future.set_exception(
                HACommandError(f"{error.get('code')}: {error.get('message')}")
            )

    def _fail_pending(self, err: HAError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(err)
        self._pending.clear()
