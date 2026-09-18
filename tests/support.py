"""Shared helpers, and a client of the session protocol in as little code as
it takes: enough to drive a server in a test, and a readable answer to "what
does a client actually do?"."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from eui import proto  # noqa: E402
from eui.proto import Frame, Hello, Viewport  # noqa: E402
from eui.server import Server  # noqa: E402
from eui.websocket import GUID  # noqa: E402


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestClient:
    def __init__(self, port: int, path: str) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode("ascii"))
        head = self._read_until(b"\r\n\r\n").decode("latin-1")
        if "101" not in head.split("\r\n")[0]:
            raise AssertionError(f"handshake: {head.splitlines()[0]!r}")
        accept = next((line.split(":", 1)[1].strip() for line in head.split("\r\n")
                       if line.lower().startswith("sec-websocket-accept")), None)
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if accept != expected:
            raise AssertionError("the accept key does not match")

    # -- reading ------------------------------------------------------------
    def _read_until(self, marker: bytes) -> bytes:
        buffer = bytearray()
        while marker not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
        return bytes(buffer)

    def _read(self, count: int) -> bytes | None:
        data = bytearray()
        while len(data) < count:
            try:
                chunk = self.sock.recv(count - len(data))
            except (TimeoutError, OSError):
                return None
            if not chunk:
                return None
            data += chunk
        return bytes(data)

    def recv(self, timeout: float = 3.0):
        """The next frame, or None once the server has gone quiet or gone
        away. A Ping is answered here rather than surfaced: a client that
        does not answer one is a client the server hangs up on after two."""
        while True:
            self.sock.settimeout(timeout)
            head = self._read(2)
            if head is None:
                return None
            length = head[1] & 0x7F
            if length == 126:
                raw = self._read(2)
                if raw is None:
                    return None
                length = int.from_bytes(raw, "big")
            elif length == 127:
                raw = self._read(8)
                if raw is None:
                    return None
                length = int.from_bytes(raw, "big")
            payload = b"" if length == 0 else self._read(length)
            if payload is None:
                return None
            frame = Frame.decode(payload)
            if frame.kind == proto.PING:
                self.send(Frame.pong(frame.body))
                continue
            return frame

    def recv_until(self, kind: int, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.recv(timeout=timeout)
            if frame is None or frame.kind == kind:
                return frame
        return None

    # -- writing ------------------------------------------------------------
    def send(self, frame: Frame) -> "TestClient":
        payload = frame.encode()
        header = bytearray([0x82])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65_536:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)
        return self

    def hello(self, width: int = 1000, height: int = 700, granted: int = 0) -> "TestClient":
        viewport = Viewport(width, height, 100, 0, 1, 100)
        return self.send(Frame.hello(Hello(proto.PROTOCOL_VERSION, viewport, granted, None)))

    def click(self, node: int) -> "TestClient":
        return self.send(Frame.event(proto.EventFrame(
            node, proto.event_code("click"), 0, proto.Value.null())))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class RunningApp:
    """An application, on a port of its own, stopped when the block ends."""

    def __init__(self, app) -> None:
        self.app = app
        self.port = free_port()
        self.server = Server(app, host="127.0.0.1", port=self.port, logger=lambda line: None)
        self.thread: threading.Thread | None = None

    def __enter__(self) -> int:
        self.thread = threading.Thread(target=self.server.start, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return self.port
            except OSError:
                time.sleep(0.02)
        raise AssertionError("the server never came up")

    def __exit__(self, *_exc) -> None:
        self.server.stop()
