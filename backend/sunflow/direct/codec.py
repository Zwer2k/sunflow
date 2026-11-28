"""Modbus register encoding: values <-> 16-bit registers."""

import math
import struct
from typing import Literal

Encoding = Literal["int16", "uint16", "int32", "uint32", "int64", "uint64", "float32", "float64"]
WordOrder = Literal["big", "little"]

WORDS = {"int16": 1, "uint16": 1, "int32": 2, "uint32": 2, "int64": 4, "uint64": 4, "float32": 2, "float64": 4}
FLOATS = {"float32": ">f", "float64": ">d"}


def words(encoding: Encoding) -> int:
    """Number of registers a value occupies."""
    return WORDS[encoding]


def decode(registers: list[int], encoding: Encoding, scale: float = 1.0, word_order: WordOrder = "big") -> float:
    """Value of the registers, times scale."""
    regs = registers[: words(encoding)]
    if word_order == "little":
        regs = regs[::-1]
    if encoding in FLOATS:
        return struct.unpack(FLOATS[encoding], struct.pack(f">{len(regs)}H", *regs))[0] * scale
    raw = 0
    for reg in regs:
        raw = raw << 16 | reg
    bits = 16 * len(regs)
    if encoding.startswith("int") and raw >= 1 << (bits - 1):
        raw -= 1 << bits
    return raw * scale


def encode(value: float, encoding: Encoding, scale: float = 1.0, word_order: WordOrder = "big") -> list[int]:
    """Registers holding value / scale, rounded and clamped to the encoding's range."""
    if not math.isfinite(value):
        raise ValueError(f"Cannot encode {value}")
    if encoding in FLOATS:
        regs = list(struct.unpack(f">{words(encoding)}H", struct.pack(FLOATS[encoding], value / scale)))
        return regs[::-1] if word_order == "little" else regs
    bits = 16 * words(encoding)
    signed = encoding.startswith("int")
    low, high = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    raw = min(max(round(value / scale), low), high) & ((1 << bits) - 1)
    regs = [(raw >> (16 * i)) & 0xFFFF for i in reversed(range(words(encoding)))]
    return regs[::-1] if word_order == "little" else regs


def decode_text(registers: list[int]) -> str:
    """Text of registers holding two ASCII characters each (high byte first), without padding."""
    return b"".join(reg.to_bytes(2, "big") for reg in registers).decode("ascii", "ignore").strip("\x00 ")
