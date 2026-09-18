"""What the decoder refuses.

Every case here is bytes a hostile peer can send for free, and every one of
them has a tempting repair: clamp the enum, ignore the trailing bytes,
normalise the varint, truncate the string. The repair is the bug — it is how
two implementations come to disagree about what a frame said, which is the
whole of the attack surface on a protocol like this.
"""

import unittest

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui.errors import DecodeError, ViewError
from eui.proto import Frame, StyleRecord, Writer, kind_code, limits
from eui.proto import node as node_mod
from eui.proto import op as opcodes
from eui.view import Encoder


def batch_frame(op_bytes: bytes) -> bytes:
    """A batch of one op, wrapped as the frame it would really arrive in."""
    body = Writer().varint(1).varint(1).raw(op_bytes).to_bytes()
    return Writer().u8(0x03).varint(len(body)).raw(body).to_bytes()


def style_bytes(offset: int | None = None, value: int = 0) -> bytes:
    raw = bytearray(StyleRecord().to_bytes())
    if offset is not None:
        raw[offset] = value
    return bytes(raw)


class RejectTest(unittest.TestCase):
    def refuse(self, raw: bytes, what: str):
        with self.assertRaises(DecodeError, msg=what):
            Frame.decode(raw)

    def test_a_truncated_frame(self):
        whole = Frame.ack(7).encode()
        self.refuse(whole[:-1], "a frame shorter than it declared")

    def test_trailing_bytes_after_the_payload(self):
        self.refuse(Frame.ack(7).encode() + b"\x00", "trailing bytes are an error, not padding")

    def test_a_frame_kind_this_revision_does_not_define(self):
        self.refuse(bytes([0x7F, 0x00]), "unknown kinds are not reserved for forward compatibility")

    def test_a_frame_longer_than_the_ceiling(self):
        raw = Writer().u8(0x03).varint(limits.MAX_FRAME_BYTES + 1).to_bytes()
        self.refuse(raw, "a declared length past MAX_FRAME_BYTES")

    def test_a_non_minimal_varint_inside_a_frame(self):
        raw = Writer().u8(0x05).varint(2).raw(bytes([0x80, 0x00])).to_bytes()
        self.refuse(raw, "two bytes spelling zero")

    def test_a_string_that_is_not_utf8(self):
        body = Writer().varint(400).varint(2).raw(b"\xc3\x28").to_bytes()
        self.refuse(Writer().u8(0x08).varint(len(body)).raw(body).to_bytes(),
                    "a lone continuation byte")

    def test_an_inline_string_past_its_ceiling(self):
        too_long = limits.MAX_INLINE_STR + 1
        op = (Writer().u8(opcodes.SET_TEXT).varint(1).u8(0x01)
              .varint(too_long).raw(b"x" * too_long).to_bytes())
        self.refuse(batch_frame(op), "an inline string of more than 4 KiB")

    def test_a_node_id_of_zero(self):
        op = Writer().u8(opcodes.SET_STYLE).varint(0).varint(1).to_bytes()
        self.refuse(batch_frame(op), 'id 0 is always "none"')

    def test_reserved_node_flags(self):
        op = (Writer().u8(opcodes.MOUNT).u8(kind_code("box")).u8(0x10)
              .varint(1).varint(0).varint(0).to_bytes())
        self.refuse(batch_frame(op), "a flag bit this revision does not define")

    def test_a_node_kind_and_an_event_kind_nobody_defines(self):
        op = Writer().u8(opcodes.MOUNT).u8(0x7E).u8(0x00).varint(1).varint(0).varint(0).to_bytes()
        self.refuse(batch_frame(op), "kind 0x7E")

        op = Writer().u8(opcodes.CLEAR_HANDLER).varint(1).u8(0x7E).to_bytes()
        self.refuse(batch_frame(op), "event 0x7E")

    def test_a_leaf_kind_given_children(self):
        w = Writer().u8(opcodes.MOUNT)
        w.u8(kind_code("text")).u8(0x00).varint(1).varint(0).varint(1)
        w.u8(kind_code("text")).u8(0x00).varint(2).varint(0).varint(0)
        self.refuse(batch_frame(w.to_bytes()), "a text node with a child")

    def test_an_inert_kind_carrying_content(self):
        w = Writer().u8(opcodes.MOUNT)
        w.u8(kind_code("divider")).u8(0x02).varint(1).varint(0)
        w.u8(0x01).str_("no")
        w.varint(0)
        self.refuse(batch_frame(w.to_bytes()), "a divider with text")

    def test_more_props_than_a_node_may_carry(self):
        w = Writer().u8(opcodes.MOUNT).u8(kind_code("box")).u8(0x04).varint(1).varint(0)
        w.varint(limits.MAX_PROPS + 1)
        self.refuse(batch_frame(w.to_bytes()), f"more than {limits.MAX_PROPS} props")

    def test_more_handlers_than_a_node_may_carry(self):
        w = Writer().u8(opcodes.MOUNT).u8(kind_code("box")).u8(0x08).varint(1).varint(0)
        w.varint(limits.MAX_HANDLERS + 1)
        self.refuse(batch_frame(w.to_bytes()), f"more than {limits.MAX_HANDLERS} handlers")

    def test_a_value_nested_past_its_depth(self):
        w = Writer().u8(opcodes.SET_PROP).varint(1).varint(1)
        for _ in range(5):
            w.u8(node_mod.LIST).varint(1)
        w.u8(node_mod.NULL)
        self.refuse(batch_frame(w.to_bytes()), "a list five deep")

    def test_a_bool_that_is_neither(self):
        op = (Writer().u8(opcodes.SET_PROP).varint(1).varint(1)
              .u8(node_mod.BOOL).u8(2).to_bytes())
        self.refuse(batch_frame(op), "a bool of 2")

    def test_a_float_that_is_not_finite(self):
        for value in (float("nan"), float("inf")):
            op = (Writer().u8(opcodes.SET_PROP).varint(1).varint(1)
                  .u8(node_mod.FLOAT).f64(value).to_bytes())
            self.refuse(batch_frame(op), f"a float of {value}")

    def test_a_style_record_with_an_enum_outside_its_range(self):
        op = Writer().u8(opcodes.DEF_STYLE).varint(1).raw(style_bytes(0, 9)).to_bytes()
        self.refuse(batch_frame(op), "display 9 is clamped by nobody")

    def test_a_style_record_whose_auto_carries_a_value(self):
        op = Writer().u8(opcodes.DEF_STYLE).varint(1).raw(style_bytes(9, 5)).to_bytes()
        self.refuse(batch_frame(op), "Dim.auto with a value")

    def test_a_style_record_with_bits_this_revision_does_not_define(self):
        for offset, value in ((60, 9), (61, 0x40), (55, 0x04)):
            op = Writer().u8(opcodes.DEF_STYLE).varint(1).raw(style_bytes(offset, value)).to_bytes()
            self.refuse(batch_frame(op), f"style byte {offset} = {value}")

    def test_a_font_role_with_no_face_and_a_role_past_the_table(self):
        op = Writer().u8(opcodes.DEF_FONT).u8(2).varint(0).to_bytes()
        self.refuse(batch_frame(op), "a role bound to nothing")

        op = (Writer().u8(opcodes.DEF_FONT).u8(limits.MAX_FONT_ROLE + 1)
              .varint(1).raw(b"x" * 32).to_bytes())
        self.refuse(batch_frame(op), "role 10")

    def test_an_opcode_nobody_defines(self):
        self.refuse(batch_frame(bytes([0x7F])), "opcode 0x7F")

    def test_a_resume_offer_that_is_neither_yes_nor_no(self):
        from eui.proto import Viewport
        body = Writer().varint(4)
        Viewport().encode(body)
        body.varint(0).u8(2)
        raw = body.to_bytes()
        self.refuse(Writer().u8(0x01).varint(len(raw)).raw(raw).to_bytes(), "resume tag 2")

    def test_a_transfer_chunk_past_its_ceiling(self):
        body = (Writer().varint(1).varint(0).u8(0)
                .varint(limits.MAX_TRANSFER_CHUNK_BYTES + 1).to_bytes())
        self.refuse(Writer().u8(0x0B).varint(len(body)).raw(body).to_bytes(),
                    "a chunk of more than 256 KiB")

    def test_a_view_the_protocol_cannot_carry_fails_at_encode(self):
        encoder = Encoder()
        # Not a decode error: these never reach the wire at all, which is the
        # point — the author finds out while writing the view.
        for view in ({"k": "box", "s": {"size": 300}},
                     {"k": "nope"},
                     {"k": "box", "on": {"clicked": "x"}},
                     {"k": "box", "p": {"a": object()}},
                     {"k": "text", "t": "x" * 5000},
                     {"k": "box", "p": {"scroll_to": [0, 1]}}):
            with self.assertRaises(ViewError):
                encoder.render(view)


if __name__ == "__main__":
    unittest.main()
