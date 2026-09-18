"""One socket, one component instance, one tree.

The shape is the whole protocol in twenty lines: the client says Hello, the
server answers Welcome and mounts a tree, and from then on every event is a
handler, a render, and the *difference* between what the client holds and
what the view now says.
"""

from __future__ import annotations

import os
import queue
import secrets
import threading
import traceback

from .errors import DecodeError, ViewError
from .proto import (ACK, BATCH, BLOB, ERROR, EVENT, HELLO, PING, PONG,
                    PROTOCOL_VERSION, RESYNC, UPLOAD, VIEWPORT, Batch, Frame,
                    Op, Value, Viewport, Welcome, cap_bit, event_name)
from .view import Encoder
from .websocket import ClosedError, ProtocolError

#: A client that connects and says nothing is not a client.
HELLO_TIMEOUT = 10.0
#: Whichever side has been silent for this long sends a Ping. Nothing else
#: wakes: the zero-wakeup idle budget is a property of that rule.
IDLE_PING = 30.0
#: Two unanswered pings and the socket is gone, whatever it still says.
MAX_UNANSWERED_PINGS = 2
#: The code on the ``Error`` that ends a session the application itself
#: closed. Codes 1–8 are the decoder's and 100–104 the client's; this is a
#: server's, and it says the session ended on purpose.
CLOSED_BY_APPLICATION = 200


class Session:
    def __init__(self, socket, component_class, app=None, logger=None) -> None:
        self._ws = socket
        self._component_class = component_class
        self._app = app
        self._logger = logger
        self.id = secrets.token_bytes(16)
        self._encoder = Encoder(assets=getattr(app, "assets", None))
        self._inbox: queue.Queue = queue.Queue()
        self._seq = 0
        self._acked = 0
        self.protocol = PROTOCOL_VERSION
        self._granted = 0
        self.viewport = Viewport()
        self._open = True
        self.component = None

    # -- the life of a session ---------------------------------------------
    def run(self) -> None:
        try:
            hello = self._handshake()
            if hello is None:
                return

            self.protocol = min(hello.version, PROTOCOL_VERSION)
            self._granted = hello.granted
            self.viewport = hello.viewport
            self._encoder.protocol = self.protocol
            # A session that starts empty, which is every first Hello's
            # answer. Resuming one whose socket broke is the server's to
            # offer, and this one does not yet: a client that reconnects
            # gets a fresh Mount.
            self._send(Frame.welcome(Welcome(self.protocol, self.id, False)))

            # The faces this application draws in, bound to their roles
            # before any view names one.
            for family, hashes in (getattr(self._app, "fonts", {}) or {}).items():
                self._encoder.font(family, hashes)

            self.component = self._component_class(session=self)
            self.component.mount({"viewport": self.viewport.to_dict()})
            self._render()

            reader = threading.Thread(target=self._read_loop, daemon=True)
            reader.start()
            self._pump()
            if self.component:
                self.component.unmount()
        except (ClosedError, ProtocolError) as exc:
            self._log(f"session ended: {exc}")
        finally:
            self._open = False
            self._ws.close()

    def refresh(self) -> None:
        if self._open:
            self._inbox.put(("render", None))

    def notify(self, title: str, body: str = "", tag: str = "") -> None:
        """Say one line to the person through the machine they are using.
        Shown only if they granted ``notifications``, and nothing comes back
        either way — not that it was shown, not that it was not."""
        if self._open:
            self._inbox.put(("notify", (str(title), str(body), str(tag))))

    def close(self, reason: str = "the application closed the session") -> None:
        if self._open:
            self._inbox.put(("close", reason))

    def granted(self, capability: str) -> bool:
        return bool(self._granted & cap_bit(capability))

    # -- frames in ----------------------------------------------------------
    def _handshake(self):
        message = self._ws.recv()
        if message is None:
            return None
        kind, raw = message
        if kind == "text":
            # A text frame is not an extension point; it is something that
            # is not an EUI client.
            self._ws.send_close(1003, "binary frames only")
            return None
        try:
            frame = Frame.decode(raw)
        except DecodeError as exc:
            self._fail(400, str(exc))
            return None
        if frame.kind != HELLO or frame.body.version < 1:
            self._fail(400, "the first frame is a Hello")
            return None
        return frame.body

    def _read_loop(self) -> None:
        try:
            while True:
                message = self._ws.recv()
                if message is None:
                    break
                kind, raw = message
                if kind == "text":
                    self._inbox.put(("close", "binary frames only"))
                    return
                self._inbox.put(("frame", raw))
            self._inbox.put(("eof", None))
        except ProtocolError as exc:
            self._inbox.put(("close", str(exc)))
        except Exception as exc:  # noqa: BLE001 - the socket is gone either way
            self._log(f"read: {type(exc).__name__}: {exc}")
            self._inbox.put(("eof", None))

    def _pump(self) -> None:
        """The one thread that writes. Everything that changes the tree comes
        through here, in the order it arrived, so two events never render on
        top of each other."""
        unanswered = 0
        while True:
            try:
                what, payload = self._inbox.get(timeout=IDLE_PING)
            except queue.Empty:
                unanswered += 1
                if unanswered > MAX_UNANSWERED_PINGS:
                    return
                self._send(Frame.ping(secrets.token_bytes(8)))
                continue

            if what == "eof":
                return
            if what == "close":
                self._fail(CLOSED_BY_APPLICATION, str(payload))
                return
            if what == "render":
                self._render()
            elif what == "notify":
                self._send_batch([Op.notify(*payload)])
            elif what == "frame":
                unanswered = 0
                if not self._handle(payload):
                    return

    def _handle(self, raw: bytes) -> bool:
        try:
            frame = Frame.decode(raw)
        except DecodeError as exc:
            self._fail(400, str(exc))
            return False

        self._trace(lambda: f"frame kind 0x{frame.kind:x}")
        kind = frame.kind
        if kind == EVENT:
            self._dispatch(frame.body)
        elif kind == ACK:
            self._acked = frame.body
        elif kind == PING:
            self._send(Frame.pong(frame.body))
        elif kind == PONG:
            pass
        elif kind == VIEWPORT:
            self.viewport = frame.body
            self._post("viewport", {"viewport": self.viewport.to_dict()})
        elif kind == RESYNC:
            # Not an error, and never answered with one: the client's tree is
            # unrecoverable and it wants the document again. The tables it
            # already holds are not repeated — they were never cleared.
            self._encoder.forget_tree()
            self._render()
        elif kind == ERROR:
            self._log(f"client error {frame.body[0]}: {frame.body[1]}")
            return False
        elif kind in (UPLOAD, BLOB):
            self._log("file transfers are not implemented yet; the chunk was dropped")
        else:
            self._fail(400, "that frame is the server's to send")
            return False
        return True

    def _dispatch(self, event) -> None:
        """An event on a node that carries no handler for it *now* is
        dropped. Usually that is a race rather than an attack — a handler a
        render removed is still in the client's tree for the one round trip
        it takes the new one to arrive — and nothing is looked up for it
        either way."""
        target = self._encoder.event_target(event.node, event.event)
        if target is None:
            self._trace(lambda: f"event on node {event.node} "
                                f"({event_name(event.event)}) names no handler in the tree we last sent")
            return
        name, props = target
        self._trace(lambda: f"event {event_name(event.event)} on node {event.node} -> {name}")
        self._post(name, {
            "node": event.node,
            "kind": event_name(event.event),
            "payload": self._resolve(event.payload),
            "props": props,
        })

    def _post(self, name: str, params: dict) -> None:
        try:
            self.component.handle(name, params)
        except ViewError:
            raise
        except Exception as exc:  # noqa: BLE001
            # A handler that raised leaves the state unchanged and the screen
            # right; the next click still works. It is a log line, not the
            # end of somebody's session.
            self._log(f"{name}: {type(exc).__name__}: {exc}")
            self._log("".join(traceback.format_exc().splitlines(keepends=True)[-3:]).rstrip())
        self._render()

    # -- frames out ---------------------------------------------------------
    def _render(self) -> None:
        try:
            ops = self._encoder.render(self.component.render())
        except ViewError as exc:
            # A view that cannot be encoded fails the same way on every later
            # render, and a server that only logged it would leave a window
            # that looks alive and answers nothing.
            self._log(f"view: {exc}")
            self._fail(400, str(exc))
            self._open = False
            return
        if ops:
            self._send_batch(ops)

    def _send_batch(self, ops: list) -> None:
        self._trace(lambda: f"batch of {len(ops)}: " + " ".join(f"0x{op.opcode:02X}" for op in ops))
        self._seq += 1
        self._send(Frame.batch(Batch(self._seq, ops)))

    def _send(self, frame: Frame) -> None:
        self._ws.send_binary(frame.encode())

    def _fail(self, code: int, message: str) -> None:
        try:
            self._send(Frame.error(code, message))
            self._ws.send_close(1000, "session ended")
        except ClosedError:
            pass

    # -- odds and ends ------------------------------------------------------
    def _resolve(self, value: Value):
        """Atoms the client sent back are ids in *this* session's table, so
        they are resolved here rather than handed to a view as numbers."""
        from .proto.node import ATOM, LIST
        if value.tag == ATOM:
            return self._encoder.atom_value(value.value)
        if value.tag == LIST:
            return [self._resolve(item) for item in value.value]
        return value.to_python()

    def _log(self, message: str) -> None:
        if self._logger:
            self._logger(f"[EUI] {message}")

    def _trace(self, message) -> None:
        """``EUI_TRACE=1`` prints every frame and every event this session
        sees. The one thing worth watching when a click does nothing."""
        if os.environ.get("EUI_TRACE"):
            self._log(f"trace: {message()}")
