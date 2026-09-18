"""Nodes, values, handlers, and the flat subtree they decode into."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import DecodeError, ViewError
from . import limits
from .reader import Reader
from .style import ColorRef
from .writer import Writer

# The closed set of primitive node kinds (02 §4.1). Everything a person would
# call a widget — button, dialog, table, date picker — is composed from these
# on the server, which is why the catalogue grows without a new client.
KIND_BY_NAME = {
    "box": 0x01, "text": 0x02, "image": 0x03, "icon": 0x04, "input": 0x05,
    "textarea": 0x06, "scroll": 0x07, "list": 0x08, "canvas": 0x09,
    "spacer": 0x0A, "divider": 0x0B, "overlay": 0x0C, "slot": 0x0D,
    "sizer": 0x0E, "audio": 0x0F, "video": 0x10, "scene": 0x11,
}
KIND_BY_CODE = {code: name for name, code in KIND_BY_NAME.items()}
LEAF_KINDS = {KIND_BY_NAME[n] for n in ("text", "icon", "spacer", "divider", "audio", "video", "scene")}
INERT_KINDS = {KIND_BY_NAME[n] for n in ("spacer", "divider")}

# Input and lifecycle events (spec/06-events.md).
EVENT_BY_NAME = {
    "click": 0x01, "double_click": 0x02, "pointer_down": 0x03, "pointer_up": 0x04,
    "pointer_move": 0x05, "pointer_enter": 0x06, "pointer_leave": 0x07,
    "key_down": 0x08, "key_up": 0x09, "text_input": 0x0A, "focus": 0x0B,
    "blur": 0x0C, "change": 0x0D, "submit": 0x0E, "scroll": 0x0F, "resize": 0x10,
    "context_menu": 0x11, "drag_start": 0x12, "drag_over": 0x13, "drop": 0x14,
    "long_press": 0x15, "window": 0x16, "ended": 0x17, "time_update": 0x18,
    "wake": 0x19, "file_pick": 0x1A, "file_save": 0x1B, "location": 0x1C,
    "nfc_tag": 0x1D, "file_drag": 0x1E, "back": 0x1F, "level": 0x20,
}
EVENT_BY_CODE = {code: name for name, code in EVENT_BY_NAME.items()}
# The protocol version an event arrived in: one the client cannot decode is
# left out at encode time rather than sent.
EVENT_SINCE = {0x20: 3}


def kind_code(name: str) -> int:
    try:
        return KIND_BY_NAME[str(name)]
    except KeyError:
        raise ViewError(f"unknown node kind '{name}'") from None


def kind_name(code: int) -> str:
    try:
        return KIND_BY_CODE[code]
    except KeyError:
        raise DecodeError(f"unknown node kind {code}") from None


def event_code(name: str) -> int:
    try:
        return EVENT_BY_NAME[str(name)]
    except KeyError:
        raise ViewError(f"unknown event '{name}'") from None


def event_name(code: int) -> str:
    try:
        return EVENT_BY_CODE[code]
    except KeyError:
        raise DecodeError(f"unknown event kind {code}") from None


def event_since(code: int) -> int:
    return EVENT_SINCE.get(code, 1)


@dataclass(frozen=True, slots=True)
class TextRef:
    """A string: interned in the session's atom table, or carried inline."""

    atom: int | None = None
    inline: str | None = None

    @staticmethod
    def of_atom(atom_id: int) -> "TextRef":
        return TextRef(atom=int(atom_id))

    @staticmethod
    def of_inline(text: str) -> "TextRef":
        return TextRef(inline=str(text))

    @staticmethod
    def decode(r: Reader) -> "TextRef":
        tag = r.u8()
        if tag == 0x00:
            return TextRef.of_atom(r.varint32())
        if tag == 0x01:
            return TextRef.of_inline(r.str_(limits.MAX_INLINE_STR, "inline string"))
        raise DecodeError(f"unknown TextRef tag {tag}")

    def encode(self, w: Writer) -> None:
        if self.atom is not None:
            w.u8(0x00).varint(self.atom)
        else:
            w.u8(0x01).str_(self.inline or "")


NULL, BOOL, INT, FLOAT, ATOM, STR, ASSET, COLOR, LIST = range(9)


@dataclass(frozen=True, slots=True)
class Value:
    """A property value (02 §4.4).

    Tagged rather than inferred from the Python object: a string may be an
    atom or an inline run, and 32 bytes may be an asset hash or a label.
    """

    tag: int
    value: object = None

    @staticmethod
    def null() -> "Value":
        return Value(NULL)

    @staticmethod
    def bool_(b: bool) -> "Value":
        return Value(BOOL, bool(b))

    @staticmethod
    def int_(n: int) -> "Value":
        return Value(INT, int(n))

    @staticmethod
    def float_(f: float) -> "Value":
        f = float(f)
        if f != f or f in (float("inf"), float("-inf")):
            raise ViewError("a float on the wire must be finite")
        return Value(FLOAT, f)

    @staticmethod
    def atom(atom_id: int) -> "Value":
        return Value(ATOM, int(atom_id))

    @staticmethod
    def str_(s: str) -> "Value":
        return Value(STR, str(s))

    @staticmethod
    def asset(digest: bytes) -> "Value":
        if len(digest) != limits.HASH_BYTES:
            raise ViewError("an asset is 32 bytes")
        return Value(ASSET, bytes(digest))

    @staticmethod
    def color(ref: ColorRef) -> "Value":
        return Value(COLOR, ref)

    @staticmethod
    def list_(items: list["Value"]) -> "Value":
        return Value(LIST, tuple(items))

    @staticmethod
    def of(obj: object) -> "Value":
        """A plain Python value as the wire would carry it. Strings go
        inline: interning is the encoder's decision, not this one's."""
        if obj is None:
            return Value.null()
        if isinstance(obj, bool):
            return Value.bool_(obj)
        if isinstance(obj, int):
            return Value.int_(obj)
        if isinstance(obj, float):
            return Value.float_(obj)
        if isinstance(obj, str):
            return Value.str_(obj)
        if isinstance(obj, ColorRef):
            return Value.color(obj)
        if isinstance(obj, Value):
            return obj
        if isinstance(obj, (list, tuple)):
            return Value.list_([Value.of(item) for item in obj])
        raise ViewError(f"a prop cannot carry {type(obj).__name__}")

    @staticmethod
    def decode(r: Reader, depth: int = 1) -> "Value":
        if depth > limits.MAX_VALUE_DEPTH:
            raise DecodeError("value nesting")
        tag = r.u8()
        if tag == NULL:
            return Value.null()
        if tag == BOOL:
            b = r.u8()
            if b > 1:
                raise DecodeError("bool must be 0 or 1")
            return Value.bool_(bool(b))
        if tag == INT:
            return Value.int_(r.svarint())
        if tag == FLOAT:
            return Value.float_(r.f64())
        if tag == ATOM:
            return Value.atom(r.varint32())
        if tag == STR:
            return Value.str_(r.str_(limits.MAX_INLINE_STR, "inline string"))
        if tag == ASSET:
            return Value.asset(r.take(limits.HASH_BYTES))
        if tag == COLOR:
            return Value.color(ColorRef(r.u16()))
        if tag == LIST:
            count = r.varint32_max(limits.MAX_VALUE_LIST, "value list length")
            return Value.list_([Value.decode(r, depth + 1) for _ in range(count)])
        raise DecodeError(f"unknown Value tag {tag}")

    def encode(self, w: Writer) -> None:
        tag = self.tag
        if tag == NULL:
            w.u8(NULL)
        elif tag == BOOL:
            w.u8(BOOL).u8(1 if self.value else 0)
        elif tag == INT:
            w.u8(INT).svarint(self.value)  # type: ignore[arg-type]
        elif tag == FLOAT:
            w.u8(FLOAT).f64(self.value)  # type: ignore[arg-type]
        elif tag == ATOM:
            w.u8(ATOM).varint(self.value)  # type: ignore[arg-type]
        elif tag == STR:
            w.u8(STR).str_(self.value)  # type: ignore[arg-type]
        elif tag == ASSET:
            w.u8(ASSET).raw(self.value)  # type: ignore[arg-type]
        elif tag == COLOR:
            w.u8(COLOR).u16(self.value.bits)  # type: ignore[union-attr]
        elif tag == LIST:
            w.u8(LIST).varint(len(self.value))  # type: ignore[arg-type]
            for item in self.value:  # type: ignore[union-attr]
                item.encode(w)

    def to_python(self) -> object:
        """What a handler sees: plain Python, with atoms left as their ids
        for the session to resolve against its own table."""
        if self.tag == NULL:
            return None
        if self.tag == LIST:
            return [item.to_python() for item in self.value]  # type: ignore[union-attr]
        return self.value


SERVER, LOCAL, BOTH = 0x00, 0x01, 0x02


@dataclass(frozen=True, slots=True)
class Handler:
    """What an event does when it happens."""

    kind: int
    chunk: int | None = None
    name: int | None = None

    @staticmethod
    def server(atom_id: int) -> "Handler":
        return Handler(SERVER, None, int(atom_id))

    @staticmethod
    def local(chunk: int) -> "Handler":
        return Handler(LOCAL, int(chunk), None)

    @staticmethod
    def local_then_server(chunk: int, atom_id: int) -> "Handler":
        return Handler(BOTH, int(chunk), int(atom_id))

    @staticmethod
    def decode(r: Reader) -> "Handler":
        tag = r.u8()
        if tag == SERVER:
            return Handler.server(r.varint32())
        if tag == LOCAL:
            return Handler.local(r.varint32())
        if tag == BOTH:
            return Handler.local_then_server(r.varint32(), r.varint32())
        raise DecodeError(f"unknown Handler tag {tag}")

    def encode(self, w: Writer) -> None:
        if self.kind == SERVER:
            w.u8(SERVER).varint(self.name)  # type: ignore[arg-type]
        elif self.kind == LOCAL:
            w.u8(LOCAL).varint(self.chunk)  # type: ignore[arg-type]
        else:
            w.u8(BOTH).varint(self.chunk).varint(self.name)  # type: ignore[arg-type]


@dataclass(slots=True)
class FlatNode:
    """One node, with its props and handlers held as ranges into the owning
    subtree's side arrays."""

    kind: int
    id: int
    style: int
    key: int = 0
    text: TextRef | None = None
    props: tuple[int, int] = (0, 0)
    handlers: tuple[int, int] = (0, 0)
    child_count: int = 0


@dataclass(slots=True)
class Subtree:
    """A subtree, stored pre-order.

    Reconstructing the shape needs nothing but ``child_count``: a node's
    first child is the next entry. Decoding is iterative, so a hostile
    10 000-deep tree costs a bounds check rather than the stack.
    """

    nodes: list[FlatNode] = field(default_factory=list)
    props: list[tuple[int, Value]] = field(default_factory=list)
    handlers: list[tuple[int, Handler]] = field(default_factory=list)

    @property
    def root(self) -> FlatNode | None:
        return self.nodes[0] if self.nodes else None

    def props_of(self, node: FlatNode) -> list[tuple[int, Value]]:
        start, length = node.props
        return self.props[start : start + length]

    def handlers_of(self, node: FlatNode) -> list[tuple[int, Handler]]:
        start, length = node.handlers
        return self.handlers[start : start + length]

    def push(self, *, kind: int, id: int, style: int, key: int = 0,
             text: TextRef | None = None,
             props: list[tuple[int, Value]] | None = None,
             handlers: list[tuple[int, Handler]] | None = None,
             child_count: int = 0) -> "Subtree":
        props = props or []
        handlers = handlers or []
        prop_start = len(self.props)
        self.props.extend(props)
        handler_start = len(self.handlers)
        self.handlers.extend(handlers)
        self.nodes.append(FlatNode(
            kind=kind, id=id, style=style, key=key, text=text,
            props=(prop_start, len(props)), handlers=(handler_start, len(handlers)),
            child_count=child_count))
        return self

    @staticmethod
    def decode(r: Reader) -> "Subtree":
        out = Subtree()
        pending: list[int] = []
        while True:
            if len(out.nodes) >= limits.MAX_NODES:
                raise DecodeError("node count")
            child_count = out._decode_one(r)
            if child_count:
                if len(pending) >= limits.MAX_TREE_DEPTH:
                    raise DecodeError("tree depth")
                pending.append(child_count)
            else:
                while pending:
                    pending[-1] -= 1
                    if pending[-1]:
                        break
                    pending.pop()
            if not pending:
                return out

    def _decode_one(self, r: Reader) -> int:
        kind = r.u8()
        kind_name(kind)
        flags = r.u8()
        if flags & 0xF0:
            raise DecodeError("reserved node flags set")
        node_id = r.varint32()
        if node_id == 0:
            raise DecodeError("node id must be non-zero")
        style = r.varint32()
        key = r.varint32() if flags & 0x01 else 0
        text = TextRef.decode(r) if flags & 0x02 else None

        props: list[tuple[int, Value]] = []
        if flags & 0x04:
            count = r.varint32_max(limits.MAX_PROPS, "props per node")
            for _ in range(count):
                props.append((r.varint32(), Value.decode(r)))

        handlers: list[tuple[int, Handler]] = []
        if flags & 0x08:
            count = r.varint32_max(limits.MAX_HANDLERS, "handlers per node")
            for _ in range(count):
                event = r.u8()
                event_name(event)  # refuse one this revision does not define
                handlers.append((event, Handler.decode(r)))

        if kind in INERT_KINDS and (text or props or handlers):
            raise DecodeError("inert node kind carries content")

        child_count = r.varint32_max(limits.MAX_CHILDREN, "children per node")
        if kind in LEAF_KINDS and child_count:
            raise DecodeError("a leaf kind carries children")

        self.push(kind=kind, id=node_id, style=style, key=key, text=text,
                  props=props, handlers=handlers, child_count=child_count)
        return child_count

    def encode(self, w: Writer) -> None:
        for node in self.nodes:
            props = self.props_of(node)
            handlers = self.handlers_of(node)

            flags = 0
            if node.key:
                flags |= 0x01
            if node.text is not None:
                flags |= 0x02
            if props:
                flags |= 0x04
            if handlers:
                flags |= 0x08

            w.u8(node.kind).u8(flags).varint(node.id).varint(node.style)
            if node.key:
                w.varint(node.key)
            if node.text is not None:
                node.text.encode(w)
            if props:
                w.varint(len(props))
                for atom, value in props:
                    w.varint(atom)
                    value.encode(w)
            if handlers:
                w.varint(len(handlers))
                for event, handler in handlers:
                    w.u8(event)
                    handler.encode(w)
            w.varint(node.child_count)
