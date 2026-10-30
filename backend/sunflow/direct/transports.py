"""How a direct device is read and written: Modbus TCP registers or an HTTP/JSON API."""

from typing import Any, Protocol

import aiohttp

from .codec import decode, encode, words
from .modbus import ModbusError, ModbusTcpClient


class TransportError(Exception):
    """A value could not be read or written."""


class Transport(Protocol):
    """Reads profile points and runs write steps of one device."""

    async def read(self, point: dict[str, Any]) -> float:
        """Value of a point of the profile's connection."""

    async def write(self, spec: dict[str, Any], value: Any) -> None:
        """Run a write step (its spec from the profile) with the resolved value."""

    async def close(self) -> None:
        """Release the connection."""


class ModbusTransport:
    """Points and steps are registers: address, type, encoding, scale."""

    def __init__(self, host: str, port: int, unit_id: int, word_order: str = "big") -> None:
        """Initialize the transport."""
        self.client = ModbusTcpClient(host, port, unit_id)
        self._word_order = word_order

    async def read(self, point: dict[str, Any]) -> float:
        """Read a register value."""
        encoding = point["encoding"]
        try:
            regs = await self.client.read(point["address"], words(encoding), point.get("type", "holding"))
        except ModbusError as err:
            raise TransportError(str(err)) from err
        return decode(regs, encoding, point.get("scale", 1.0), self._word_order)

    async def write(self, spec: dict[str, Any], value: Any) -> None:
        """Write a holding register."""
        regs = encode(float(value), spec.get("encoding", "uint16"), spec.get("scale", 1.0), self._word_order)
        try:
            await self.client.write(spec["address"], regs)
        except ModbusError as err:
            raise TransportError(str(err)) from err

    async def close(self) -> None:
        """Close the TCP connection."""
        await self.client.close()


def json_value(data: Any, path: str) -> Any:
    """Value at a dotted path ("Body.Data.Site.P_PV"; list items by index)."""
    for part in path.split(".") if path else []:
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


class HttpTransport:
    """Points are JSON values of GET requests; steps are requests with a templated body."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize the transport."""
        self.base = f"http://{host}:{port}"
        self._session: aiohttp.ClientSession | None = None
        # One poll reads several points of the same document
        self._documents: dict[str, Any] = {}

    def new_poll(self) -> None:
        """Forget the documents of the previous poll."""
        self._documents = {}

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        return self._session

    async def read(self, point: dict[str, Any]) -> float:
        """Read a JSON value."""
        path = point["path"]
        try:
            if path not in self._documents:
                session = await self._session_()
                async with session.get(self.base + path) as response:
                    response.raise_for_status()
                    self._documents[path] = await response.json(content_type=None)
            value = json_value(self._documents[path], point.get("json", ""))
            return float(value) * point.get("scale", 1.0)
        except (aiohttp.ClientError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as err:
            raise TransportError(f"{path}: {err}") from err

    async def write(self, spec: dict[str, Any], value: Any) -> None:
        """Send the request; value is the resolved body."""
        session = await self._session_()
        try:
            async with session.request(spec.get("method", "POST"), self.base + spec["path"], json=value) as response:
                response.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TransportError(f"{spec['path']}: {err}") from err

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None


def make_transport(connection: dict[str, Any], host: str, port: int | None, unit_id: int) -> Transport:
    """Transport for a profile connection and the device's address."""
    match connection["type"]:
        case "modbus_tcp":
            return ModbusTransport(host, port or connection.get("port", 502), unit_id, connection.get("word_order", "big"))
        case "http":
            return HttpTransport(host, port or connection.get("port", 80))
    raise ValueError(f"Unknown connection type {connection['type']}")
