"""Websocket client for the Home Assistant API."""

import asyncio
from collections.abc import Callable
import itertools
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

type EventCallback = Callable[[dict[str, Any]], None]


class HAError(Exception):
    """Error returned by Home Assistant or the connection."""


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

    @property
    def connected(self) -> bool:
        """Return True if the websocket is open."""
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """Connect and authenticate."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        self._closing = False
        try:
            self._ws = await self._session.ws_connect(self._url, heartbeat=30)
            msg = await self._ws.receive_json()
            if msg["type"] != "auth_required":
                raise HAError(f"Unexpected message: {msg['type']}")
            await self._ws.send_json({"type": "auth", "access_token": self._token})
            msg = await self._ws.receive_json()
        except (aiohttp.ClientError, TimeoutError) as err:
            await self.close()
            raise HAError(f"Cannot connect to {self._url}: {err}") from err
        if msg["type"] != "auth_ok":
            await self.close()
            raise HAError(msg.get("message", "Authentication failed"))
        self.ha_version = msg.get("ha_version")
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
        """Send a command and return its result."""
        if self._ws is None:
            raise HAError("Not connected")
        msg_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._ws.send_json({"id": msg_id, "type": msg_type, **data})
        return await future

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
                    break
                payload = raw.json()
                for msg in payload if isinstance(payload, list) else [payload]:
                    self._dispatch(msg)
        finally:
            self._fail_pending(HAError("Connection lost"))
            if not self._closing:
                _LOGGER.warning("Connection to Home Assistant lost")

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
        if msg["success"]:
            future.set_result(msg.get("result"))
        else:
            error = msg.get("error", {})
            future.set_exception(
                HAError(f"{error.get('code')}: {error.get('message')}")
            )

    def _fail_pending(self, err: HAError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(err)
        self._pending.clear()
