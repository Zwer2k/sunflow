"""Modbus register encoding: values <-> 16-bit registers."""

import math
from typing import Literal

Encoding = Literal["int16", "uint16", "int32", "uint32"]
WordOrder = Literal["big", "little"]

WORDS = {"int16": 1, "uint16": 1, "int32": 2, "uint32": 2}


def words(encoding: Encoding) -> int:
    """Number of registers a value occupies."""
    return WORDS[encoding]


def decode(registers: list[int], encoding: Encoding, scale: float = 1.0, word_order: WordOrder = "big") -> float:
    """Value of the registers, times scale."""
    regs = registers[: words(encoding)]
    if word_order == "little":
        regs = regs[::-1]
    raw = 0
    for reg in regs:
        raw = raw << 16 | reg
    bits = 16 * len(regs)
    if encoding.startswith("int") and raw >= 1 << (bits - 1):
        raw -= 1 << bits
    return raw * scale


def encode(value: float, encoding: Encoding, scale: float = 1.0, word_order: WordOrder = "big") -> list[int]:
    """Registers holding value / scale, rounded and clamped to the encoding's range."""
    bits = 16 * words(encoding)
    signed = encoding.startswith("int")
    low, high = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    if not math.isfinite(value):
        raise ValueError(f"Cannot encode {value}")
    raw = min(max(round(value / scale), low), high) & ((1 << bits) - 1)
    regs = [(raw >> (16 * i)) & 0xFFFF for i in reversed(range(words(encoding)))]
    return regs[::-1] if word_order == "little" else regs
