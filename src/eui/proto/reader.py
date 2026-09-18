"""A cursor over bytes that refuses anything it was not promised."""

from __future__ import annotations

import struct

from ..errors import DecodeError


class Reader:
    """Two rules do most of the work: a varint must be minimally encoded,
    and a frame must account for every byte it declared. Both are the classic
    route to a parser disagreeing with itself."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def eof(self) -> bool:
        return self.remaining == 0

    def take(self, count: int) -> bytes:
        if count > self.remaining:
            raise DecodeError(f"short read: wanted {count}, {self.remaining} left")
        out = self.data[self.pos : self.pos + count]
        self.pos += count
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def f64(self) -> float:
        value = struct.unpack("<d", self.take(8))[0]
        if value != value or value in (float("inf"), float("-inf")):
            raise DecodeError("float must be finite")
        return value

    def varint(self, max_bytes: int = 10) -> int:
        """LEB128, and only the minimal spelling of it: a multi-byte encoding
        whose last byte is ``0x00`` is an error rather than a normalisation."""
        value = 0
        shift = 0
        count = 0
        while True:
            byte = self.u8()
            count += 1
            if count > max_bytes:
                raise DecodeError("varint too long")
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            if count == max_bytes:
                raise DecodeError("non-minimal varint")
            shift += 7
        if count > 1 and value < (1 << (7 * (count - 1))):
            raise DecodeError("non-minimal varint")
        return value

    def varint32(self) -> int:
        value = self.varint(5)
        if value > 0xFFFF_FFFF:
            raise DecodeError("varint overflows u32")
        return value

    def varint32_max(self, maximum: int, what: str) -> int:
        value = self.varint32()
        if value > maximum:
            raise DecodeError(f"{what} above {maximum}")
        return value

    def svarint(self) -> int:
        raw = self.varint(10)
        return (raw >> 1) ^ -(raw & 1)

    def bytes_(self, maximum: int, what: str) -> bytes:
        length = self.varint32()
        if length > maximum:
            raise DecodeError(f"{what} of {length} bytes, at most {maximum}")
        return self.take(length)

    def str_(self, maximum: int, what: str) -> str:
        raw = self.bytes_(maximum, what)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DecodeError(f"{what} is not valid UTF-8") from exc

    def finish(self) -> "Reader":
        """Trailing bytes are an error, not padding."""
        if not self.eof():
            raise DecodeError(f"{self.remaining} trailing bytes")
        return self
