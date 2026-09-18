"""Batch operations (``spec/02-wire-format.md`` §5).

Structural only. Whether an op is *coherent* — that the node it names
exists, that the atom it references was defined — is session state and
belongs to the session, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import DecodeError, EUIError
from . import limits
from .node import Handler, Subtree, TextRef, Value, event_name
from .reader import Reader
from .style import StyleRecord
from .writer import Writer

DEF_ATOM = 0x10
DEF_STYLE = 0x11
DEF_COLOR = 0x12
DEF_CHUNK = 0x13
DEF_CHUNK_BYTES = 0x14
DEF_FONT = 0x15
MOUNT = 0x20
REPLACE = 0x21
SET_STYLE = 0x22
SET_TEXT = 0x23
SET_PROP = 0x24
INSERT_CHILD = 0x25
REMOVE_CHILD = 0x26
MOVE_CHILD = 0x27
SET_HANDLER = 0x28
CLEAR_HANDLER = 0x29
FOCUS = 0x2A
SCROLL_TO = 0x2B
NOTIFY = 0x2C


def _nonzero(value: int, what: str) -> int:
    if value == 0:
        raise DecodeError(f"{what} must be non-zero")
    return value


@dataclass(slots=True)
class Op:
    """One operation in a batch."""

    opcode: int
    fields: dict = field(default_factory=dict)

    def __getitem__(self, name: str):
        return self.fields[name]

    # -- constructors, named as the specification names the ops ------------
    @staticmethod
    def def_atom(atom_id: int, value: str) -> "Op":
        return Op(DEF_ATOM, {"id": atom_id, "value": value})

    @staticmethod
    def def_style(style_id: int, record: StyleRecord) -> "Op":
        return Op(DEF_STYLE, {"id": style_id, "record": record})

    @staticmethod
    def def_color(color_id: int, rgba: int) -> "Op":
        return Op(DEF_COLOR, {"id": color_id, "rgba": rgba})

    @staticmethod
    def def_chunk(chunk_id: int, digest: bytes) -> "Op":
        return Op(DEF_CHUNK, {"id": chunk_id, "hash": digest})

    @staticmethod
    def def_chunk_bytes(chunk_id: int, raw: bytes) -> "Op":
        return Op(DEF_CHUNK_BYTES, {"id": chunk_id, "bytes": raw})

    @staticmethod
    def def_font(role: int, faces: list[bytes]) -> "Op":
        return Op(DEF_FONT, {"role": role, "faces": faces})

    @staticmethod
    def mount(subtree: Subtree) -> "Op":
        return Op(MOUNT, {"subtree": subtree})

    @staticmethod
    def replace(node: int, subtree: Subtree) -> "Op":
        return Op(REPLACE, {"node": node, "subtree": subtree})

    @staticmethod
    def set_style(node: int, style: int) -> "Op":
        return Op(SET_STYLE, {"node": node, "style": style})

    @staticmethod
    def set_text(node: int, text: TextRef) -> "Op":
        return Op(SET_TEXT, {"node": node, "text": text})

    @staticmethod
    def set_prop(node: int, prop: int, value: Value) -> "Op":
        return Op(SET_PROP, {"node": node, "prop": prop, "value": value})

    @staticmethod
    def insert_child(parent: int, index: int, subtree: Subtree) -> "Op":
        return Op(INSERT_CHILD, {"parent": parent, "index": index, "subtree": subtree})

    @staticmethod
    def remove_child(parent: int, index: int, count: int) -> "Op":
        return Op(REMOVE_CHILD, {"parent": parent, "index": index, "count": count})

    @staticmethod
    def move_child(parent: int, from_: int, to: int) -> "Op":
        return Op(MOVE_CHILD, {"parent": parent, "from": from_, "to": to})

    @staticmethod
    def set_handler(node: int, event: int, handler: Handler) -> "Op":
        return Op(SET_HANDLER, {"node": node, "event": event, "handler": handler})

    @staticmethod
    def clear_handler(node: int, event: int) -> "Op":
        return Op(CLEAR_HANDLER, {"node": node, "event": event})

    @staticmethod
    def focus(node: int) -> "Op":
        return Op(FOCUS, {"node": node})

    @staticmethod
    def scroll_to(node: int, x: int, y: int) -> "Op":
        return Op(SCROLL_TO, {"node": node, "x": x, "y": y})

    @staticmethod
    def notify(title: str, body: str = "", tag: str = "") -> "Op":
        return Op(NOTIFY, {"title": title, "body": body, "tag": tag})

    # -- the wire ----------------------------------------------------------
    @staticmethod
    def decode(r: Reader) -> "Op":
        opcode = r.u8()
        if opcode == DEF_ATOM:
            return Op.def_atom(_nonzero(r.varint32(), "atom id"),
                               r.str_(limits.MAX_ATOM_BYTES, "atom value"))
        if opcode == DEF_STYLE:
            return Op.def_style(_nonzero(r.varint32(), "style id"), StyleRecord.decode(r))
        if opcode == DEF_COLOR:
            return Op.def_color(_nonzero(r.varint32(), "color id"), r.u32())
        if opcode == DEF_CHUNK:
            return Op.def_chunk(_nonzero(r.varint32(), "chunk id"), r.take(limits.HASH_BYTES))
        if opcode == DEF_CHUNK_BYTES:
            return Op.def_chunk_bytes(_nonzero(r.varint32(), "chunk id"),
                                      r.bytes_(limits.MAX_CHUNK_BYTES, "chunk bytes"))
        if opcode == DEF_FONT:
            role = r.u8()
            if role > limits.MAX_FONT_ROLE:
                raise DecodeError("font role")
            count = r.varint32()
            if count == 0:
                raise DecodeError("a font role with no face")
            if count > limits.MAX_FACES_PER_ROLE:
                raise DecodeError("font faces")
            return Op.def_font(role, [r.take(limits.HASH_BYTES) for _ in range(count)])
        if opcode == MOUNT:
            return Op.mount(Subtree.decode(r))
        if opcode == REPLACE:
            return Op.replace(_nonzero(r.varint32(), "node id"), Subtree.decode(r))
        if opcode == SET_STYLE:
            return Op.set_style(_nonzero(r.varint32(), "node id"), r.varint32())
        if opcode == SET_TEXT:
            return Op.set_text(_nonzero(r.varint32(), "node id"), TextRef.decode(r))
        if opcode == SET_PROP:
            return Op.set_prop(_nonzero(r.varint32(), "node id"), r.varint32(), Value.decode(r))
        if opcode == INSERT_CHILD:
            return Op.insert_child(_nonzero(r.varint32(), "node id"), r.varint32(), Subtree.decode(r))
        if opcode == REMOVE_CHILD:
            return Op.remove_child(_nonzero(r.varint32(), "node id"), r.varint32(), r.varint32())
        if opcode == MOVE_CHILD:
            return Op.move_child(_nonzero(r.varint32(), "node id"), r.varint32(), r.varint32())
        if opcode == SET_HANDLER:
            node = _nonzero(r.varint32(), "node id")
            event = r.u8()
            event_name(event)
            return Op.set_handler(node, event, Handler.decode(r))
        if opcode == CLEAR_HANDLER:
            node = _nonzero(r.varint32(), "node id")
            event = r.u8()
            event_name(event)
            return Op.clear_handler(node, event)
        if opcode == FOCUS:
            return Op.focus(_nonzero(r.varint32(), "node id"))
        if opcode == SCROLL_TO:
            return Op.scroll_to(_nonzero(r.varint32(), "node id"), r.svarint(), r.svarint())
        if opcode == NOTIFY:
            return Op.notify(r.str_(limits.MAX_NOTIFY_TITLE, "notification title"),
                             r.str_(limits.MAX_NOTIFY_BODY, "notification body"),
                             r.str_(limits.MAX_NOTIFY_TAG, "notification tag"))
        raise DecodeError(f"unknown opcode {opcode}")

    def encode(self, w: Writer) -> None:
        f = self.fields
        w.u8(self.opcode)
        code = self.opcode
        if code == DEF_ATOM:
            w.varint(f["id"]).str_(f["value"])
        elif code == DEF_STYLE:
            w.varint(f["id"])
            f["record"].encode(w)
        elif code == DEF_COLOR:
            w.varint(f["id"]).u32(f["rgba"])
        elif code == DEF_CHUNK:
            w.varint(f["id"]).raw(f["hash"])
        elif code == DEF_CHUNK_BYTES:
            w.varint(f["id"]).bytes_(f["bytes"])
        elif code == DEF_FONT:
            w.u8(f["role"]).varint(len(f["faces"]))
            for face in f["faces"]:
                w.raw(face)
        elif code == MOUNT:
            f["subtree"].encode(w)
        elif code == REPLACE:
            w.varint(f["node"])
            f["subtree"].encode(w)
        elif code == SET_STYLE:
            w.varint(f["node"]).varint(f["style"])
        elif code == SET_TEXT:
            w.varint(f["node"])
            f["text"].encode(w)
        elif code == SET_PROP:
            w.varint(f["node"]).varint(f["prop"])
            f["value"].encode(w)
        elif code == INSERT_CHILD:
            w.varint(f["parent"]).varint(f["index"])
            f["subtree"].encode(w)
        elif code == REMOVE_CHILD:
            w.varint(f["parent"]).varint(f["index"]).varint(f["count"])
        elif code == MOVE_CHILD:
            w.varint(f["parent"]).varint(f["from"]).varint(f["to"])
        elif code == SET_HANDLER:
            w.varint(f["node"]).u8(f["event"])
            f["handler"].encode(w)
        elif code == CLEAR_HANDLER:
            w.varint(f["node"]).u8(f["event"])
        elif code == FOCUS:
            w.varint(f["node"])
        elif code == SCROLL_TO:
            w.varint(f["node"]).svarint(f["x"]).svarint(f["y"])
        elif code == NOTIFY:
            w.str_(f["title"]).str_(f["body"]).str_(f["tag"])
        else:
            raise EUIError(f"cannot encode opcode {code}")


@dataclass(slots=True)
class Batch:
    """An ordered run of ops carrying a sequence number. The client acks the
    last one it applied, and applies a batch all or nothing."""

    seq: int
    ops: list[Op] = field(default_factory=list)

    @staticmethod
    def decode(r: Reader) -> "Batch":
        seq = r.varint()
        count = r.varint32_max(limits.MAX_OPS_PER_BATCH, "ops per batch")
        ops = []
        notifications = 0
        for _ in range(count):
            op = Op.decode(r)
            if op.opcode == NOTIFY:
                notifications += 1
                if notifications > limits.MAX_NOTIFY_PER_BATCH:
                    raise DecodeError("notifications per batch")
            ops.append(op)
        return Batch(seq, ops)

    def encode(self, w: Writer) -> None:
        w.varint(self.seq).varint(len(self.ops))
        for op in self.ops:
            op.encode(w)
