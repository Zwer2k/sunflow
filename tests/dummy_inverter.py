"""A Modbus TCP server with a register map, standing in for an inverter in tests."""

import asyncio
import struct
from typing import Self

ILLEGAL_ADDRESS = 2


class DummyInverter:
    """Answers reads (3, 4) and writes (6, 16) from its register maps; records writes."""

    def __init__(self, holding: dict[int, int] | None = None, inputs: dict[int, int] | None = None) -> None:
        """Initialize with register values; unknown addresses answer with an exception."""
        self.holding = dict(holding or {})
        self.inputs = dict(inputs or {})
        self.writes: list[tuple[int, list[int]]] = []
        self.port = 0
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> Self:
        """Listen on a free local port."""
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        """Stop listening."""
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                transaction, _, length, unit = struct.unpack(">HHHB", await reader.readexactly(7))
                pdu = self._answer(await reader.readexactly(length - 1))
                writer.write(struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit) + pdu)
                await writer.drain()
        except asyncio.IncompleteReadError:
            writer.close()

    def _answer(self, pdu: bytes) -> bytes:
        function = pdu[0]
        if function in (3, 4):
            address, count = struct.unpack(">HH", pdu[1:5])
            table = self.holding if function == 3 else self.inputs
            if any(a not in table for a in range(address, address + count)):
                return bytes([function | 0x80, ILLEGAL_ADDRESS])
            values = [table[a] for a in range(address, address + count)]
            return struct.pack(f">BB{count}H", function, 2 * count, *values)
        if function == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            self._store(address, [value])
            return pdu[:5]
        if function == 16:
            address, count = struct.unpack(">HH", pdu[1:5])
            self._store(address, list(struct.unpack(f">{count}H", pdu[6 : 6 + 2 * count])))
            return pdu[:5]
        return bytes([function | 0x80, 1])

    def _store(self, address: int, values: list[int]) -> None:
        self.writes.append((address, values))
        for offset, value in enumerate(values):
            self.holding[address + offset] = value
