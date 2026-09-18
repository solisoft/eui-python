"""The three endpoints an EUI application answers on (01 §2), and nothing
else::

    GET /.well-known/eui          the signed manifest
    GET /_eui/asset/<blake3-hex>  a content-addressed asset
    WSS /_eui/session[/<name>]    the session

TLS 1.3 is the protocol's floor, and a release client refuses ``ws://``
outright. Pass ``tls=`` in production; over loopback a debug client can be
told to come in the front door with ``EUI_ALLOW_INSECURE_LOOPBACK=1``.
"""

from __future__ import annotations

import socket
import sys
import threading

from .session import Session
from .websocket import ProtocolError, WebSocket

HEADER_LIMIT = 16 * 1024
_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}


class Server:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 5012,
                 tls: dict | None = None, logger=None) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.tls = tls
        self._logger = logger or (lambda line: print(line, file=sys.stderr, flush=True))
        self._sock: socket.socket | None = None
        self._context = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(128)
        self._sock = sock
        if self.tls:
            self._context = self._tls_context()

        scheme = "https" if self.tls else "http"
        self._log(f"listening on {scheme}://{self.host}:{self.port}")
        self._log(f"  manifest  {scheme}://{self.host}:{self.port}/.well-known/eui")
        for name in self.app.components:
            ws_scheme = "wss" if self.tls else "ws"
            self._log(f"  session   {ws_scheme}://{self.host}:{self.port}/_eui/session/{name}")

        while True:
            try:
                client, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def stop(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    # -- one connection -----------------------------------------------------
    def _tls_context(self):
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # The protocol's floor, not a preference: a server answering `wss://`
        # with TLS 1.2 is refused by a conforming client rather than
        # accommodated, so there is nothing to gain by offering it.
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(self.tls["cert"], self.tls["key"])
        return context

    def _serve(self, client: socket.socket) -> None:
        try:
            if self._context is not None:
                client = self._context.wrap_socket(client, server_side=True)
            request = _read_request(client)
            if request is None:
                client.close()
                return
            method, path, headers = request
            if method == "GET" and headers.get("upgrade", "").lower() == "websocket":
                self._session(client, path, headers)
            else:
                self._http(client, method, path)
                client.close()
        except ProtocolError as exc:
            self._log(f"websocket: {exc}")
            client.close()
        except Exception as exc:  # noqa: BLE001
            self._log(f"{type(exc).__name__}: {exc}")
            client.close()

    def _session(self, client: socket.socket, path: str, headers: dict) -> None:
        name = path[len("/_eui/session"):].lstrip("/") if path.startswith("/_eui/session") else None
        component = self.app.component_for(name or None)
        if not path.startswith("/_eui/session") or component is None:
            _respond(client, 404, "text/plain", f"no session at {path}\n")
            client.close()
            return
        ws = WebSocket.accept(client, path, headers)
        Session(ws, component, app=self.app, logger=self._log).run()

    def _http(self, client: socket.socket, method: str, path: str) -> None:
        if method != "GET":
            return _respond(client, 405, "text/plain", "GET only\n")

        if path == "/.well-known/eui":
            manifest = self.app.manifest
            if manifest is None:
                return _respond(client, 404, "text/plain", "this application has no manifest\n")
            return _respond(client, 200, "application/vnd.eui.manifest", manifest.encode())

        if path.startswith("/_eui/asset/"):
            entry = self.app.assets.fetch(path[len("/_eui/asset/"):])
            # A hash this server does not hold is a 404 and never a redirect:
            # the name is the content, so there is nowhere else it could be.
            if entry is None:
                return _respond(client, 404, "text/plain", "no such asset\n")
            return _respond(client, 200, entry.content_type, entry.bytes_,
                            {"Cache-Control": "public, max-age=31536000, immutable"})

        if path == "/health":
            return _respond(client, 200, "text/plain", "ok\n")
        return _respond(client, 404, "text/plain", "not found\n")

    def _log(self, line: str) -> None:
        self._logger(f"[EUI] {line}" if not line.startswith("[EUI]") else line)


def _read_request(sock: socket.socket):
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        try:
            chunk = sock.recv(4096)
        except OSError:
            return None
        if not chunk:
            return None
        buffer += chunk
        if len(buffer) > HEADER_LIMIT:
            raise ProtocolError("headers too long")

    head = bytes(buffer).split(b"\r\n\r\n", 1)[0].decode("latin-1")
    lines = head.split("\r\n")
    parts = lines[0].split()
    if len(parts) < 2:
        return None
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    return parts[0], parts[1], headers


def _respond(sock: socket.socket, status: int, content_type: str, body, extra: dict | None = None) -> None:
    if isinstance(body, str):
        body = body.encode("utf-8")
    head = f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\n"
    head += f"Content-Type: {content_type}\r\n"
    head += f"Content-Length: {len(body)}\r\n"
    for key, value in (extra or {}).items():
        head += f"{key}: {value}\r\n"
    head += "Connection: close\r\n\r\n"
    try:
        sock.sendall(head.encode("latin-1") + body)
    except OSError:
        pass
