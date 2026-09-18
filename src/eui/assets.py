"""The content-addressed store behind ``/_eui/asset/<blake3-hex>``.

An asset is named by the hash of its bytes, so the name *is* the content:
the client recomputes it and discards a mismatch, a proxy may serve it to
anyone, and ``immutable`` is always the right cache header. A file somebody
uploaded is not an asset — that travels in the session (01 §6), because it
is one person's and not the same for everyone.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from . import blake3
from .errors import ViewError

TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".ttf": "font/ttf", ".otf": "font/otf", ".woff2": "font/woff2",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".mp4": "video/mp4", ".wgsl": "text/wgsl",
}


@dataclass(frozen=True, slots=True)
class Entry:
    bytes_: bytes
    content_type: str


class Assets:
    def __init__(self, root: str | None = None) -> None:
        self.root = os.path.realpath(root or os.getcwd())
        self._by_hash: dict[bytes, Entry] = {}
        self._by_path: dict[str, tuple[tuple[float, int], bytes]] = {}
        self._lock = threading.Lock()

    def add_file(self, path: str) -> bytes:
        """Take a file into the store and answer its hash. Cheap to call on
        every render: a path whose mtime and size have not moved is not read
        again."""
        full = os.path.realpath(os.path.join(self.root, path))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise ViewError(f"an asset must live under {self.root}, got {path}")
        if not os.path.isfile(full):
            raise ViewError(f"no such asset: {path}")

        stat = os.stat(full)
        stamp = (stat.st_mtime, stat.st_size)
        with self._lock:
            cached = self._by_path.get(full)
            if cached and cached[0] == stamp:
                return cached[1]
            with open(full, "rb") as handle:
                raw = handle.read()
            digest = blake3.digest(raw)
            extension = os.path.splitext(full)[1].lower()
            self._by_hash[digest] = Entry(raw, TYPES.get(extension, "application/octet-stream"))
            self._by_path[full] = (stamp, digest)
            return digest

    def add_bytes(self, raw: bytes, content_type: str = "application/octet-stream") -> bytes:
        """Bytes that have no file — a picture out of a database, a chart
        this process drew — reach a window the same way."""
        raw = bytes(raw)
        digest = blake3.digest(raw)
        with self._lock:
            self._by_hash[digest] = Entry(raw, content_type)
        return digest

    def fetch(self, hex_digest: str) -> Entry | None:
        if len(hex_digest) != 64 or any(c not in "0123456789abcdef" for c in hex_digest):
            return None
        with self._lock:
            return self._by_hash.get(bytes.fromhex(hex_digest))

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_hash)

    @staticmethod
    def hex(digest: bytes) -> str:
        return digest.hex()
