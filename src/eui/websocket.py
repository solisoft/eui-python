"""The server half of RFC 6455, with only what an EUI session needs.

A session carries **binary** frames and nothing else: a text frame is not a
protocol extension point, it is a sign that something other than an EUI
client is talking, and the session ends (01 §2.3).
"""

from __future__ import annotations

import base64
import hashlib
import socket
import threading

from .errors import EUIError
from .proto import limits

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = limits.MAX_FRAME_BYTES + 16

CONTINUATION = 0x0
TEXT = 0x1
BINARY = 0x2
CLOSE = 0x8
PING = 0x9
PONG = 0xA


class ClosedError(EUIError):
    pass


class ProtocolError(EUIError):
    pass


def accept_key(key: str) -> str:
    """The key is not a secret and proves nothing: it is there so that a
    cache between the two ends cannot mistake this for a reply it may serve
    to somebody else."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


class WebSocket:
    def __init__(self, sock: socket.socket, path: str, headers: dict[str, str]) -> None:
        self._sock = sock
        self.path = path
        self.headers = headers
        self._write_lock = threading.Lock()
        self.closed = False

    @staticmethod
    def accept(sock: socket.socket, path: str, headers: dict[str, str]) -> "WebSocket":
        key = headers.get("sec-websocket-key")
        if not key:
            raise ProtocolError("no Sec-WebSocket-Key")
        sock.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept_key(key).encode("ascii") + b"\r\n\r\n")
        return WebSocket(sock, path, headers)

    # -- reading ------------------------------------------------------------
    def recv(self):
        """The next application message as ``("binary" | "text", bytes)``, or
        ``None`` once the peer has gone. Control frames are answered here and
        never surface."""
        message = bytearray()
        kind = None
        while True:
            frame = self._read_frame()
            if frame is None:
                return None
            opcode, payload, fin = frame
            if opcode == CLOSE:
                self.send_close(1000)
                return None
            if opcode == PING:
                self._send_frame(PONG, payload)
                continue
            if opcode == PONG:
                continue
            if opcode in (TEXT, BINARY):
                if message:
                    raise ProtocolError("a frame arrived inside a fragmented message")
                kind = opcode
                message += payload
            elif opcode == CONTINUATION:
                if kind is None:
                    raise ProtocolError("a continuation with nothing to continue")
                message += payload
            else:
                raise ProtocolError(f"unknown opcode {opcode}")

            if len(message) > MAX_MESSAGE:
                raise ProtocolError("message too large")
            if fin:
                return ("text" if kind == TEXT else "binary", bytes(message))

    def _read_frame(self):
        head = self._read_exactly(2)
        if head is None:
            return None
        b0, b1 = head
        fin = bool(b0 & 0x80)
        if b0 & 0x70:
            raise ProtocolError("reserved bits set")
        opcode = b0 & 0x0F
        # Every frame from a client is masked; one that is not is either a
        # proxy rewriting traffic or something that is not a browser stack.
        if not b1 & 0x80:
            raise ProtocolError("a client frame must be masked")
        length = b1 & 0x7F
        if length == 126:
            raw = self._read_exactly(2)
            if raw is None:
                return None
            length = int.from_bytes(raw, "big")
        elif length == 127:
            raw = self._read_exactly(8)
            if raw is None:
                return None
            length = int.from_bytes(raw, "big")
        if length > MAX_MESSAGE:
            raise ProtocolError("frame too large")
        mask = self._read_exactly(4)
        if mask is None:
            return None
        payload = b"" if length == 0 else self._read_exactly(length)
        if payload is None:
            return None
        return opcode, _unmask(payload, mask), fin

    def _read_exactly(self, count: int) -> bytes | None:
        data = bytearray()
        while len(data) < count:
            try:
                chunk = self._sock.recv(count - len(data))
            except (OSError, ValueError):
                return None
            if not chunk:
                return None
            data += chunk
        return bytes(data)

    # -- writing ------------------------------------------------------------
    def send_binary(self, data: bytes) -> None:
        self._send_frame(BINARY, data)

    def send_close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        payload = code.to_bytes(2, "big") + reason.encode("utf-8")[:123]
        try:
            self._send_frame(CLOSE, payload)
        except (ClosedError, OSError):
            pass
        self.closed = True

    def close(self) -> None:
        self.send_close()
        try:
            self._sock.close()
        except OSError:
            pass

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        payload = bytes(payload)
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65_536:
            header.append(126)
            header += length.to_bytes(2, "big")
        else:
            header.append(127)
            header += length.to_bytes(8, "big")
        with self._write_lock:
            try:
                self._sock.sendall(bytes(header) + payload)
            except OSError as exc:
                self.closed = True
                raise ClosedError(str(exc)) from exc


def _unmask(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return payload
    repeated = mask * (len(payload) // 4 + 1)
    return bytes(a ^ b for a, b in zip(payload, repeated))
