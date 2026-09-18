"""Session frames (``spec/01-transport.md`` §3).

One frame per WebSocket binary message, and a text frame ends the session.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import DecodeError, EUIError
from . import limits
from .node import Value, event_name
from .op import Batch
from .reader import Reader
from .writer import Writer

THEME_MODES = {0: "light", 1: "dark", 2: "high_contrast"}
DENSITIES = {0: "compact", 1: "cozy", 2: "comfortable"}

# Capability bits, as granted by the person and reported to the server
# (01 §2.1). Nothing is granted by being asked for.
CAPS = {
    "camera": 1 << 0, "microphone": 1 << 1, "clipboard.read": 1 << 2,
    "clipboard.write": 1 << 3, "notifications": 1 << 4, "location": 1 << 5,
    "fs.pick": 1 << 6, "fs.save": 1 << 7, "nfc": 1 << 8, "scene": 1 << 9,
    "net.open": 1 << 10,
}
# Every bit this revision defines. A bit outside it is a decode error: a
# client that does not know what a bit means must not agree to it, and
# neither must a server.
CAPS_ALL = 0x7FF


def cap_bit(name: str) -> int:
    try:
        return CAPS[str(name)]
    except KeyError:
        raise EUIError(f"unknown capability '{name}'") from None


def cap_mask(names) -> int:
    if isinstance(names, int):
        return names
    mask = 0
    for name in names:
        mask |= cap_bit(name)
    return mask


def cap_names(mask: int) -> list[str]:
    return [name for name, bit in CAPS.items() if mask & bit]


@dataclass(slots=True)
class Viewport:
    """The viewer's presentation state."""

    width: int = 0
    height: int = 0
    scale: int = 100
    mode: int = 0
    density: int = 1
    font_scale: int = 100

    @staticmethod
    def decode(r: Reader) -> "Viewport":
        width = r.varint32()
        height = r.varint32()
        scale = r.u16()
        mode = r.u8()
        if mode not in THEME_MODES:
            raise DecodeError(f"unknown theme mode {mode}")
        density = r.u8()
        if density not in DENSITIES:
            raise DecodeError(f"unknown density {density}")
        return Viewport(width, height, scale, mode, density, r.u16())

    def encode(self, w: Writer) -> None:
        (w.varint(self.width).varint(self.height).u16(self.scale)
         .u8(self.mode).u8(self.density).u16(self.font_scale))

    def to_dict(self) -> dict:
        """What a view sees: pixels, a ratio, and names rather than codes."""
        return {
            "width": self.width, "height": self.height, "scale": self.scale / 100.0,
            "mode": THEME_MODES[self.mode], "density": DENSITIES[self.density],
            "font_scale": self.font_scale / 100.0,
        }


@dataclass(slots=True)
class Resume:
    """A session the client still holds a tree for, offered back after the
    socket broke (01 §4.1). An offer, not a claim."""

    session: bytes
    acked: int


@dataclass(slots=True)
class Hello:
    version: int
    viewport: Viewport
    granted: int
    resume: Resume | None = None


@dataclass(slots=True)
class Welcome:
    version: int
    session: bytes
    resumed: bool


@dataclass(slots=True)
class EventFrame:
    node: int
    event: int
    name: int
    payload: Value


MORE, LAST, ABORT = 0, 1, 2


@dataclass(slots=True)
class Transfer:
    id: int
    seq: int
    flag: int
    bytes_: bytes

    @staticmethod
    def decode(r: Reader) -> "Transfer":
        transfer_id = r.varint32()
        seq = r.varint32()
        flag = r.u8()
        if flag > ABORT:
            raise DecodeError(f"unknown chunk flag {flag}")
        cap = limits.MAX_ABORT_REASON if flag == ABORT else limits.MAX_TRANSFER_CHUNK_BYTES
        return Transfer(transfer_id, seq, flag, r.bytes_(cap, "transfer chunk"))

    def encode(self, w: Writer) -> None:
        w.varint(self.id).varint(self.seq).u8(self.flag).bytes_(self.bytes_)


HELLO = 0x01
WELCOME = 0x02
BATCH = 0x03
EVENT = 0x04
ACK = 0x05
PING = 0x06
PONG = 0x07
ERROR = 0x08
RESYNC = 0x09
VIEWPORT = 0x0A
UPLOAD = 0x0B
BLOB = 0x0C


@dataclass(slots=True)
class Frame:
    """A whole session message."""

    kind: int
    body: object = None

    @staticmethod
    def hello(h: Hello) -> "Frame":
        return Frame(HELLO, h)

    @staticmethod
    def welcome(w: Welcome) -> "Frame":
        return Frame(WELCOME, w)

    @staticmethod
    def batch(b: Batch) -> "Frame":
        return Frame(BATCH, b)

    @staticmethod
    def event(e: EventFrame) -> "Frame":
        return Frame(EVENT, e)

    @staticmethod
    def ack(seq: int) -> "Frame":
        return Frame(ACK, seq)

    @staticmethod
    def ping(nonce: bytes) -> "Frame":
        return Frame(PING, nonce)

    @staticmethod
    def pong(nonce: bytes) -> "Frame":
        return Frame(PONG, nonce)

    @staticmethod
    def error(code: int, message: str) -> "Frame":
        return Frame(ERROR, (code, message))

    @staticmethod
    def resync() -> "Frame":
        return Frame(RESYNC)

    @staticmethod
    def viewport(v: Viewport) -> "Frame":
        return Frame(VIEWPORT, v)

    @staticmethod
    def upload(t: Transfer) -> "Frame":
        return Frame(UPLOAD, t)

    @staticmethod
    def blob(t: Transfer) -> "Frame":
        return Frame(BLOB, t)

    @staticmethod
    def decode(message: bytes) -> "Frame":
        """Trailing bytes are an error: a length that does not account for
        every byte of the message is how one implementation's frame becomes
        another's smuggling channel."""
        r = Reader(message)
        kind = r.u8()
        length = r.varint()
        if length > limits.MAX_FRAME_BYTES:
            raise DecodeError("frame length")
        payload = r.take(length)
        r.finish()
        p = Reader(payload)

        if kind == HELLO:
            version = p.varint32()
            viewport = Viewport.decode(p)
            granted = p.varint32()
            if granted & ~CAPS_ALL:
                raise DecodeError("unknown capability bit")
            tag = p.u8()
            if tag == 0:
                resume = None
            elif tag == 1:
                resume = Resume(p.take(16), p.varint())
            else:
                raise DecodeError(f"unknown resume tag {tag}")
            frame = Frame.hello(Hello(version, viewport, granted, resume))
        elif kind == WELCOME:
            version = p.varint32()
            session = p.take(16)
            resumed = p.u8()
            if resumed > 1:
                raise DecodeError("resumed must be 0 or 1")
            frame = Frame.welcome(Welcome(version, session, bool(resumed)))
        elif kind == BATCH:
            frame = Frame.batch(Batch.decode(p))
        elif kind == EVENT:
            node = p.varint32()
            event = p.u8()
            event_name(event)
            frame = Frame.event(EventFrame(node, event, p.varint32(), Value.decode(p)))
        elif kind == ACK:
            frame = Frame.ack(p.varint())
        elif kind == PING:
            frame = Frame.ping(p.take(8))
        elif kind == PONG:
            frame = Frame.pong(p.take(8))
        elif kind == ERROR:
            frame = Frame.error(p.varint32(), p.str_(limits.MAX_INLINE_STR, "error message"))
        elif kind == RESYNC:
            frame = Frame.resync()
        elif kind == VIEWPORT:
            frame = Frame.viewport(Viewport.decode(p))
        elif kind == UPLOAD:
            frame = Frame.upload(Transfer.decode(p))
        elif kind == BLOB:
            frame = Frame.blob(Transfer.decode(p))
        else:
            raise DecodeError(f"unknown frame kind {kind}")
        p.finish()
        return frame

    def encode(self) -> bytes:
        body = Writer()
        kind = self.kind
        if kind == HELLO:
            h: Hello = self.body  # type: ignore[assignment]
            body.varint(h.version)
            h.viewport.encode(body)
            body.varint(h.granted)
            if h.resume is None:
                body.u8(0)
            else:
                body.u8(1).raw(h.resume.session).varint(h.resume.acked)
        elif kind == WELCOME:
            w: Welcome = self.body  # type: ignore[assignment]
            body.varint(w.version).raw(w.session).u8(1 if w.resumed else 0)
        elif kind == BATCH:
            self.body.encode(body)  # type: ignore[union-attr]
        elif kind == EVENT:
            e: EventFrame = self.body  # type: ignore[assignment]
            body.varint(e.node).u8(e.event).varint(e.name)
            e.payload.encode(body)
        elif kind == ACK:
            body.varint(self.body)  # type: ignore[arg-type]
        elif kind in (PING, PONG):
            body.raw(self.body)  # type: ignore[arg-type]
        elif kind == ERROR:
            body.varint(self.body[0]).str_(self.body[1])  # type: ignore[index]
        elif kind == RESYNC:
            pass
        elif kind in (VIEWPORT, UPLOAD, BLOB):
            self.body.encode(body)  # type: ignore[union-attr]
        else:
            raise EUIError(f"cannot encode frame kind {kind}")

        payload = body.to_bytes()
        out = Writer()
        out.u8(kind).varint(len(payload)).raw(payload)
        return out.to_bytes()
