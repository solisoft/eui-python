"""The view layer: the style vocabulary, the tables, and what a view that
cannot be encoded does about it."""

import unittest

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui import blake3, theme
from eui.dsl import box, text
from eui.errors import ViewError
from eui.proto import Dim, Handler, Op, Value, event_code
from eui.proto import style as st
from eui.view import Compiler, Encoder


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.encoder = Encoder()

    @staticmethod
    def compile(style):
        return Compiler().record(style)

    def test_the_style_vocabulary(self):
        r = self.compile({
            "display": "column", "justify": "between", "align": "center",
            "gap": 4, "pad": [2, 3], "bg": "surface.raised", "fg": "text.muted",
            "size": "lg", "weight": "bold", "radius": "md", "shadow": "sm",
            "width": "50%", "height": 240, "max_width": "1fr", "basis": "sp:4",
            "underline": True, "cursor": "pointer", "transition": "fast",
        })
        self.assertEqual(st.DISPLAY["column"], r.display)
        self.assertEqual(st.JUSTIFY["between"], r.justify)
        self.assertEqual((2, 3, 2, 3), r.padding)
        self.assertEqual(theme.role("surface.raised"), r.bg.index)
        self.assertEqual(3, r.font_size, "lg is index 3 on the text scale")
        self.assertEqual(Dim.percent(5000), r.width)
        self.assertEqual(Dim.px(240), r.height)
        self.assertEqual(Dim.fr(100), r.max_width)
        self.assertEqual(Dim.space(4), r.basis)
        self.assertEqual(1, r.text_decoration)
        self.assertEqual(1, r.transition, "fast is motion index 0, carried as index + 1")

    def test_an_unknown_style_key_is_an_error(self):
        # Not a key that does nothing: a style silently dropped is a page
        # that is wrong for a day.
        with self.assertRaises(ViewError) as caught:
            self.compile({"padding": 4})
        self.assertIn("unknown style key 'padding'", str(caught.exception))

    def test_an_unknown_colour_role_is_an_error(self):
        with self.assertRaises(ViewError):
            self.compile({"bg": "surface.fancy"})

    def test_a_literal_colour_needs_a_table(self):
        with self.assertRaises(ViewError):
            self.compile({"bg": "#ff8800"})
        seen = []
        compiler = Compiler(colors=lambda rgba: seen.append(rgba) or 1)
        ref = compiler.record({"bg": "#ff8800"}).bg
        self.assertEqual([0xFF8800FF], seen)
        self.assertTrue(ref.is_literal)

    def test_tables_are_append_only_and_deduplicate(self):
        self.assertEqual(1, self.encoder.atom("click"))
        self.assertEqual(1, self.encoder.atom("click"), "the same string is the same atom")
        self.assertEqual(2, self.encoder.atom("value"))

        first = self.encoder.style(self.compile({"gap": 2}))
        self.assertEqual(first, self.encoder.style(self.compile({"gap": 2})),
                         "equal records share an id")
        self.assertEqual(0, self.encoder.style(self.compile({})), "the default record is id 0")

    def test_the_definitions_come_before_what_uses_them(self):
        ops = self.encoder.render(text("Hi", size="lg"))
        self.assertEqual(Op.mount(None).opcode, ops[-1].opcode)
        self.assertTrue(all(op.opcode < 0x20 for op in ops[:-1]),
                        "every definition precedes the Mount")

    def test_props_and_handlers_reach_the_wire(self):
        ops = self.encoder.render(box(props={"id": 7}, on={"click": "pick"}))
        tree = ops[-1]["subtree"]
        node = tree.nodes[0]
        self.assertEqual([(self.encoder.atom("id"), Value.int_(7))], tree.props_of(node))
        self.assertEqual([(event_code("click"), Handler.server(self.encoder.atom("pick")))],
                         tree.handlers_of(node))

    def test_an_event_arrives_by_the_name_the_view_gave_it(self):
        self.encoder.render(box(props={"id": 7}, on={"wake": "tick"}))
        node = self.encoder.previous.id
        name, props = self.encoder.event_target(node, event_code("wake"))
        self.assertEqual("tick", name, "the handler is named by the view, not by the event kind")
        self.assertEqual({"id": 7}, props)
        self.assertIsNone(self.encoder.event_target(node, event_code("click")))

    def test_a_leaf_cannot_have_children(self):
        with self.assertRaises(ViewError):
            self.encoder.render({"k": "text", "t": "x", "c": [box()]})

    def test_an_inert_node_carries_nothing(self):
        with self.assertRaises(ViewError):
            self.encoder.render({"k": "divider", "on": {"click": "x"}})

    def test_a_tree_deeper_than_the_client_accepts_is_refused(self):
        deep = box()
        for _ in range(300):
            deep = box([deep])
        with self.assertRaises(ViewError):
            self.encoder.render(deep)

    def test_a_font_nobody_bound_is_an_error(self):
        with self.assertRaises(ViewError) as caught:
            self.encoder.render(text("x", font="Space Grotesk"))
        self.assertIn("no font bound", str(caught.exception))

    def test_a_bound_font_becomes_a_role(self):
        self.encoder.font("Space Grotesk", [blake3.digest(b"face")])
        ops = self.encoder.render(text("x", font="Space Grotesk"))
        self.assertEqual(0x15, ops[0].opcode)
        self.assertEqual(2, ops[0]["role"], "roles 0 and 1 are the client's own sans and mono")


if __name__ == "__main__":
    unittest.main()
