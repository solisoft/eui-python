"""The wire format, against the numbers ``spec/02-wire-format.md`` pins.

A second implementation written from that document alone has to check itself
against the same bytes, which is the whole reason they are written down there
rather than only in the reference crate.
"""

import unittest

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui.errors import DecodeError
from eui.proto import (Batch, ColorRef, EventFrame, Frame, Handler, Hello, Op,
                       PROTOCOL_VERSION, Reader, StyleRecord, Subtree, TextRef,
                       Value, Viewport, Welcome, Writer, cap_mask, event_code,
                       kind_code)
from eui.proto import style as st


def encode(op: Op) -> bytes:
    w = Writer()
    op.encode(w)
    return w.to_bytes()


class ProtoTest(unittest.TestCase):
    def test_varint_is_minimally_encoded(self):
        w = Writer()
        for n in (0, 1, 127, 128, 300):
            w.varint(n)
        self.assertEqual("00017f8001ac02", w.to_bytes().hex())

        r = Reader(w.to_bytes())
        self.assertEqual([0, 1, 127, 128, 300], [r.varint() for _ in range(5)])
        self.assertTrue(r.eof())

    def test_a_non_minimal_varint_is_refused(self):
        # 0x80 0x00 is a two-byte spelling of zero. Normalising it is how one
        # implementation's signature check becomes another's bypass.
        with self.assertRaises(DecodeError):
            Reader(bytes([0x80, 0x00])).varint32()

    def test_svarint_zigzags(self):
        values = [0, -1, 1, -2, 2, 2**40, -(2**40)]
        w = Writer()
        for n in values:
            w.svarint(n)
        r = Reader(w.to_bytes())
        self.assertEqual(values, [r.svarint() for _ in values])

    def test_default_style_record_bytes(self):
        raw = StyleRecord().to_bytes()
        self.assertEqual(64, len(raw))
        expected = ([0x00, 0x00, 0x00, 0x03, 0x05, 0x00, 0x01, 0x00]
                    + [0] * 21          # the seven Dims
                    + [0] * 4 + [0] * 4  # padding, margin
                    + [0] * 6            # bg, fg, border_color
                    + [0] * 4            # border_width
                    + [0x00, 0x00, 0xFF]  # radius, shadow, opacity
                    + [0x00, 0x02, 0x00, 0x00]  # font_family, size base, weight, align
                    + [0x00] * 6         # clamp, decoration, overflow, position, z, cursor
                    + [0x00] * 4)        # transition, animation, blur, motion
        self.assertEqual(expected, list(raw))

    def test_the_worked_example_is_150_bytes(self):
        """`spec/02-wire-format.md` §8, byte for byte: a column with "Hi"."""
        tree = Subtree()
        tree.push(kind=kind_code("box"), id=1, style=1, child_count=1)
        tree.push(kind=kind_code("text"), id=2, style=2, text=TextRef.of_atom(1))

        column = StyleRecord()
        column.display = st.DISPLAY["column"]
        column.padding = (4, 4, 4, 4)
        column.bg = ColorRef.role(1)

        label = StyleRecord()
        label.font_size = 3
        label.fg = ColorRef.role(8)

        body = b"".join(encode(op) for op in [
            Op.def_atom(1, "Hi"), Op.def_style(1, column),
            Op.def_style(2, label), Op.mount(tree)])

        self.assertEqual(150, len(body), "spec §8 body size")
        self.assertEqual("1001024869", body[0:5].hex(), "DefAtom")
        self.assertEqual("1101", body[5:7].hex(), "DefStyle 1 header")
        self.assertEqual("1102", body[71:73].hex(), "DefStyle 2 header")
        self.assertEqual("20010001010102020202000100", body[137:150].hex(), "Mount")

    def test_a_subtree_round_trips(self):
        tree = Subtree()
        tree.push(kind=kind_code("box"), id=1, style=3, key=7,
                  props=[(2, Value.int_(42)),
                         (3, Value.list_([Value.bool_(True), Value.str_("x")]))],
                  handlers=[(event_code("click"), Handler.server(9))], child_count=1)
        tree.push(kind=kind_code("text"), id=2, style=0, text=TextRef.of_inline("Hi"))

        w = Writer()
        tree.encode(w)
        back = Subtree.decode(Reader(w.to_bytes()))

        self.assertEqual(2, len(back.nodes))
        self.assertEqual(7, back.nodes[0].key)
        self.assertEqual([(2, Value.int_(42)),
                          (3, Value.list_([Value.bool_(True), Value.str_("x")]))],
                         back.props_of(back.nodes[0]))
        self.assertEqual([(event_code("click"), Handler.server(9))],
                         back.handlers_of(back.nodes[0]))
        self.assertEqual(TextRef.of_inline("Hi"), back.nodes[1].text)

    def test_a_leaf_with_children_is_refused(self):
        tree = Subtree()
        tree.push(kind=kind_code("text"), id=1, style=0, child_count=1)
        tree.push(kind=kind_code("text"), id=2, style=0)
        w = Writer()
        tree.encode(w)
        with self.assertRaises(DecodeError):
            Subtree.decode(Reader(w.to_bytes()))

    def test_frames_round_trip(self):
        frames = [
            Frame.hello(Hello(4, Viewport(1280, 900, 200, 1, 0, 125),
                              cap_mask(["fs.pick", "net.open"]), None)),
            Frame.welcome(Welcome(4, b"x" * 16, True)),
            Frame.ack(9),
            Frame.ping(b"12345678"),
            Frame.pong(b"12345678"),
            Frame.error(400, "no"),
            Frame.resync(),
            Frame.viewport(Viewport()),
            Frame.event(EventFrame(3, event_code("change"), 5, Value.str_("typed"))),
        ]
        for frame in frames:
            raw = frame.encode()
            back = Frame.decode(raw)
            self.assertEqual(frame.kind, back.kind)
            self.assertEqual(raw.hex(), back.encode().hex(), f"frame kind {frame.kind}")

    def test_trailing_bytes_are_an_error(self):
        with self.assertRaises(DecodeError):
            Frame.decode(Frame.ack(1).encode() + b"x")

    def test_an_unknown_capability_bit_is_refused(self):
        with self.assertRaises(DecodeError):
            Frame.decode(Frame.hello(Hello(4, Viewport(), 1 << 20, None)).encode())

    def test_a_batch_carries_at_most_four_notifications(self):
        raw = Frame.batch(Batch(1, [Op.notify("hi") for _ in range(5)])).encode()
        with self.assertRaises(DecodeError):
            Frame.decode(raw)

    def test_a_style_record_rejects_a_motion_with_nothing_to_direct(self):
        record = StyleRecord()
        record.motion = st.MOTION["top"]
        with self.assertRaises(DecodeError):
            record.validate()

    def test_the_protocol_version_is_the_one_the_client_negotiates(self):
        self.assertEqual(4, PROTOCOL_VERSION)


if __name__ == "__main__":
    unittest.main()
