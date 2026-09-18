"""The protocol's primitives, written into a buffer (02 §1)."""

from __future__ import annotations

import struct

from ..errors import EUIError


class Writer:
    """An append-only byte buffer. Every method returns ``self``, so an
    encoder reads as one sentence."""

    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray()

    def u8(self, value: int) -> "Writer":
        self.buf.append(value & 0xFF)
        return self

    def u16(self, value: int) -> "Writer":
        self.buf += struct.pack("<H", value & 0xFFFF)
        return self

    def u32(self, value: int) -> "Writer":
        self.buf += struct.pack("<I", value & 0xFFFF_FFFF)
        return self

    def u64(self, value: int) -> "Writer":
        self.buf += struct.pack("<Q", value & 0xFFFF_FFFF_FFFF_FFFF)
        return self

    def f64(self, value: float) -> "Writer":
        self.buf += struct.pack("<d", value)
        return self

    def varint(self, value: int) -> "Writer":
        """LEB128, minimally encoded. A decoder rejects any other spelling of
        the same number, so there is only ever one."""
        if value < 0:
            raise EUIError(f"varint cannot carry {value}")
        while True:
            byte = value & 0x7F
            value >>= 7
            if value == 0:
                self.buf.append(byte)
                return self
            self.buf.append(byte | 0x80)

    def svarint(self, value: int) -> "Writer":
        """LEB128 over zigzag: the sign rides in the low bit, so a small
        negative number costs one byte like a small positive one."""
        return self.varint((value << 1) ^ (value >> 63))

    def bytes_(self, raw: bytes) -> "Writer":
        self.varint(len(raw))
        self.buf += raw
        return self

    def str_(self, value: str) -> "Writer":
        return self.bytes_(value.encode("utf-8"))

    def raw(self, raw: bytes) -> "Writer":
        self.buf += raw
        return self

    def __len__(self) -> int:
        return len(self.buf)

    def to_bytes(self) -> bytes:
        return bytes(self.buf)
