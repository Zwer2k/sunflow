"""A small Modbus TCP client: read and write registers, nothing more."""

import asyncio
import contextlib
import itertools
import struct
from typing import Literal

RegisterType = Literal["holding", "input"]
READ_FUNCTIONS = {"holding": 3, "input": 4}
WRITE_SINGLE = 6
WRITE_MULTIPLE = 16
MAX_READ = 125


class ModbusError(Exception):
    """A request failed: no connection, timeout or an exception response."""


class ModbusTcpClient:
    """One connection to a device; requests are serialized.

    Many inverters (and their dongles) accept only one client and one request at a
    time, so the connection is kept open and reopened after an error.
    """

    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 5.0) -> None:
        """Initialize the client; it connects on the first request."""
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._transactions = itertools.cycle(range(1, 0x10000))
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def close(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(OSError):
                await self._writer.wait_closed()
        self._reader = self._writer = None

    async def read(self, address: int, count: int, kind: RegisterType = "holding") -> list[int]:
        """Read count registers starting at address."""
        if not 1 <= count <= MAX_READ:
            raise ModbusError(f"Cannot read {count} registers at once")
        pdu = await self._request(struct.pack(">BHH", READ_FUNCTIONS[kind], address, count))
        size = pdu[1]
        if size != 2 * count or len(pdu) < 2 + size:
            raise ModbusError(f"Unexpected response size {size} for {count} registers")
        return list(struct.unpack(f">{count}H", pdu[2 : 2 + size]))

    async def write(self, address: int, registers: list[int]) -> None:
        """Write registers; a single one with function 6, several with 16."""
        if len(registers) == 1:
            await self._request(struct.pack(">BHH", WRITE_SINGLE, address, registers[0]))
            return
        count = len(registers)
        await self._request(struct.pack(f">BHHB{count}H", WRITE_MULTIPLE, address, count, 2 * count, *registers))

    async def _request(self, pdu: bytes) -> bytes:
        async with self._lock:
            try:
                return await asyncio.wait_for(self._exchange(pdu), self._timeout)
            except (TimeoutError, OSError, asyncio.IncompleteReadError) as err:
                await self.close()
                raise ModbusError(f"{self.host}:{self.port}: {err or type(err).__name__}") from err

    async def _exchange(self, pdu: bytes) -> bytes:
        if self._writer is None:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        assert self._reader is not None
        transaction = next(self._transactions)
        self._writer.write(struct.pack(">HHHB", transaction, 0, len(pdu) + 1, self.unit_id) + pdu)
        await self._writer.drain()
        while True:
            header = await self._reader.readexactly(7)
            answer, _, length, _ = struct.unpack(">HHHB", header)
            body = await self._reader.readexactly(length - 1)
            # A late answer to a timed-out request is dropped
            if answer == transaction:
                break
        if body[0] & 0x80:
            raise ModbusError(f"Exception {body[1]} for function {body[0] & 0x7F}")
        return body
