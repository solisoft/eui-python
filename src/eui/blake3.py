"""BLAKE3, in Python, because an asset is named by the hash of its content
and the client recomputes it (``spec/01-transport.md`` §2.2).

Only the plain hash: no keyed mode, no key derivation, no extendable output
past 32 bytes. That is every use the protocol has for it — an asset's name —
and each of the others is a footgun this library would rather not carry.
"""

from __future__ import annotations

import struct

OUT_LEN = 32
BLOCK_LEN = 64
CHUNK_LEN = 1024

CHUNK_START = 1 << 0
CHUNK_END = 1 << 1
PARENT = 1 << 2
ROOT = 1 << 3

IV = (0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
      0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19)

MSG_PERMUTATION = (2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8)

MASK = 0xFFFF_FFFF


def _rotr(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & MASK


def _g(s: list[int], a: int, b: int, c: int, d: int, mx: int, my: int) -> None:
    s[a] = (s[a] + s[b] + mx) & MASK
    s[d] = _rotr(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & MASK
    s[b] = _rotr(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b] + my) & MASK
    s[d] = _rotr(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & MASK
    s[b] = _rotr(s[b] ^ s[c], 7)


def _round(s: list[int], m) -> None:
    _g(s, 0, 4, 8, 12, m[0], m[1])
    _g(s, 1, 5, 9, 13, m[2], m[3])
    _g(s, 2, 6, 10, 14, m[4], m[5])
    _g(s, 3, 7, 11, 15, m[6], m[7])
    _g(s, 0, 5, 10, 15, m[8], m[9])
    _g(s, 1, 6, 11, 12, m[10], m[11])
    _g(s, 2, 7, 8, 13, m[12], m[13])
    _g(s, 3, 4, 9, 14, m[14], m[15])


def compress(cv, block, counter: int, block_len: int, flags: int) -> list[int]:
    """The one primitive: a chaining value and a block in, sixteen words out.
    The first eight are the next chaining value; all sixteen are the root's
    output."""
    s = [cv[0], cv[1], cv[2], cv[3], cv[4], cv[5], cv[6], cv[7],
         IV[0], IV[1], IV[2], IV[3],
         counter & MASK, (counter >> 32) & MASK, block_len, flags]
    m = block
    for r in range(7):
        _round(s, m)
        if r < 6:
            m = [m[i] for i in MSG_PERMUTATION]
    for i in range(8):
        s[i] ^= s[i + 8]
        s[i + 8] ^= cv[i]
    return s


def words_of(block: bytes) -> list[int]:
    if len(block) < BLOCK_LEN:
        block = block + bytes(BLOCK_LEN - len(block))
    return list(struct.unpack("<16I", block))


class _Output:
    """A node's output, before anybody has decided whether it is the root.
    Which it is changes the flags, and so changes the bytes: that is what
    keeps a chunk's hash from being a tree's hash."""

    __slots__ = ("input_cv", "block_words", "counter", "block_len", "flags")

    def __init__(self, input_cv, block_words, counter, block_len, flags) -> None:
        self.input_cv = input_cv
        self.block_words = block_words
        self.counter = counter
        self.block_len = block_len
        self.flags = flags

    def chaining_value(self) -> list[int]:
        return compress(self.input_cv, self.block_words, self.counter,
                        self.block_len, self.flags)[:8]

    def root_bytes(self, length: int = OUT_LEN) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            words = compress(self.input_cv, self.block_words, counter,
                             self.block_len, self.flags | ROOT)
            out += struct.pack("<16I", *words)
            counter += 1
        return bytes(out[:length])


def _parent_output(left, right, flags) -> _Output:
    return _Output(list(IV), list(left) + list(right), 0, BLOCK_LEN, PARENT | flags)


def _parent_cv(left, right, flags) -> list[int]:
    return _parent_output(left, right, flags).chaining_value()


class _ChunkState:
    """One chunk of at most 1024 bytes, compressed a block at a time."""

    __slots__ = ("cv", "chunk_counter", "block", "blocks_compressed", "flags")

    def __init__(self, key, chunk_counter: int, flags: int) -> None:
        self.cv = list(key)
        self.chunk_counter = chunk_counter
        self.block = bytearray()
        self.blocks_compressed = 0
        self.flags = flags

    def __len__(self) -> int:
        return BLOCK_LEN * self.blocks_compressed + len(self.block)

    @property
    def start_flag(self) -> int:
        return CHUNK_START if self.blocks_compressed == 0 else 0

    def update(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            if len(self.block) == BLOCK_LEN:
                self.cv = compress(self.cv, words_of(bytes(self.block)),
                                   self.chunk_counter, BLOCK_LEN,
                                   self.flags | self.start_flag)[:8]
                self.blocks_compressed += 1
                self.block = bytearray()
            take = min(BLOCK_LEN - len(self.block), len(data) - offset)
            self.block += data[offset : offset + take]
            offset += take

    def output(self) -> _Output:
        return _Output(self.cv, words_of(bytes(self.block)), self.chunk_counter,
                       len(self.block), self.flags | self.start_flag | CHUNK_END)


class Hasher:
    """The streaming hasher. Chunks are merged into a binary tree as they
    complete, so hashing a 200 MB file costs a stack of at most 54 chaining
    values."""

    __slots__ = ("_key", "_flags", "_chunk", "_stack")

    def __init__(self) -> None:
        self._key = list(IV)
        self._flags = 0
        self._chunk = _ChunkState(self._key, 0, 0)
        self._stack: list[list[int]] = []

    def update(self, data: bytes) -> "Hasher":
        data = bytes(data)
        offset = 0
        while offset < len(data):
            if len(self._chunk) == CHUNK_LEN:
                self._add_chunk(self._chunk.output().chaining_value(),
                                self._chunk.chunk_counter + 1)
                self._chunk = _ChunkState(self._key, self._chunk.chunk_counter + 1, self._flags)
            take = min(CHUNK_LEN - len(self._chunk), len(data) - offset)
            self._chunk.update(data[offset : offset + take])
            offset += take
        return self

    def digest(self, length: int = OUT_LEN) -> bytes:
        output = self._chunk.output()
        for left in reversed(self._stack):
            output = _parent_output(left, output.chaining_value(), self._flags)
        return output.root_bytes(length)

    def hexdigest(self, length: int = OUT_LEN) -> str:
        return self.digest(length).hex()

    def _add_chunk(self, cv, total_chunks: int) -> None:
        """A chunk's chaining value joins the tree, merging with everything
        to its left that is now complete — which is what the low bits of the
        chunk count say."""
        while total_chunks & 1 == 0:
            cv = _parent_cv(self._stack.pop(), cv, self._flags)
            total_chunks >>= 1
        self._stack.append(cv)


def digest(data: bytes) -> bytes:
    return Hasher().update(data).digest()


def hexdigest(data: bytes) -> str:
    return digest(data).hex()


def digest_file(path: str) -> bytes:
    hasher = Hasher()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(65_536)
            if not block:
                break
            hasher.update(block)
    return hasher.digest()
