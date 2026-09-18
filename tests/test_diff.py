"""What a change costs.

The protocol's whole claim is in these numbers: a click that changes one
number is one op, and a thousand-row table reordered is *n* moves rather
than a rebuild.
"""

import unittest

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui.dsl import box, column, keyed, row, text
from eui.proto import Op, Value
from eui.proto import op as opcodes
from eui.view import Encoder


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.encoder = Encoder()

    def render(self, view):
        return self.encoder.render(view)

    @staticmethod
    def opcodes(ops):
        return [op.opcode for op in ops]

    def test_the_first_render_is_a_mount(self):
        ops = self.render(column([text("0")]))
        self.assertEqual(opcodes.MOUNT, ops[-1].opcode)

    def test_one_changed_word_is_one_op(self):
        self.render(column([text("0"), text("steady")]))
        ops = self.render(column([text("1"), text("steady")]))
        self.assertEqual([opcodes.SET_TEXT], self.opcodes(ops))
        self.assertEqual("1", ops[0]["text"].inline)

    def test_an_unchanged_view_costs_nothing(self):
        self.render(column([text("0")]))
        self.assertEqual([], self.render(column([text("0")])))

    def test_a_changed_style_is_one_set_style(self):
        self.render(text("x", fg="text.default"))
        ops = self.render(text("x", fg="danger.base"))
        # The record is new to the session, so it is defined before it is named.
        self.assertEqual([opcodes.DEF_STYLE, opcodes.SET_STYLE], self.opcodes(ops))

    def test_a_changed_kind_is_a_replace(self):
        self.render(column([text("x")]))
        ops = self.render(column([box()]))
        self.assertEqual([opcodes.REPLACE], self.opcodes(ops))

    def test_props_that_go_away_are_set_to_null(self):
        self.render(box(props={"a": 1, "b": 2}))
        ops = self.render(box(props={"a": 3}))
        self.assertEqual([opcodes.SET_PROP, opcodes.SET_PROP], self.opcodes(ops))
        self.assertEqual(Value.int_(3), ops[0]["value"])
        self.assertEqual(Value.null(), ops[1]["value"])

    def test_a_handler_removed_is_cleared(self):
        self.render(box(on={"click": "a"}))
        ops = self.render(box())
        self.assertEqual([opcodes.CLEAR_HANDLER], self.opcodes(ops))

    def test_children_appended_and_removed_positionally(self):
        self.render(column([text("a")]))
        ops = self.render(column([text("a"), text("b")]))
        self.assertEqual([opcodes.INSERT_CHILD], self.opcodes(ops))

        ops = self.render(column([text("a")]))
        self.assertEqual([opcodes.REMOVE_CHILD], self.opcodes(ops))
        self.assertEqual(1, ops[0]["index"])
        self.assertEqual(1, ops[0]["count"])

    @staticmethod
    def rows(keys):
        return column([keyed(k, text(str(k))) for k in keys])

    def test_a_reordered_keyed_list_is_moves(self):
        self.render(self.rows(["a", "b", "c"]))
        ops = self.render(self.rows(["c", "a", "b"]))
        self.assertEqual([opcodes.MOVE_CHILD], self.opcodes(ops))
        self.assertEqual(2, ops[0]["from"])
        self.assertEqual(0, ops[0]["to"])

    def test_a_keyed_row_keeps_its_node_id_when_it_moves(self):
        self.render(self.rows(["a", "b", "c"]))
        before = [child.id for child in self.encoder.previous.children]
        self.render(self.rows(["c", "b", "a"]))
        after = [child.id for child in self.encoder.previous.children]
        self.assertEqual(list(reversed(before)), after, "identity is the key, not the position")

    def test_a_run_of_removed_rows_is_one_op(self):
        self.render(self.rows(["a", "b", "c", "d", "e"]))
        ops = self.render(self.rows(["a", "e"]))
        self.assertEqual([opcodes.REMOVE_CHILD], self.opcodes(ops))
        self.assertEqual(1, ops[0]["index"])
        self.assertEqual(3, ops[0]["count"])

    def test_a_new_keyed_row_is_inserted_where_it_belongs(self):
        self.render(self.rows(["a", "c"]))
        ops = self.render(self.rows(["a", "b", "c"]))
        # The new key is a string this session had not interned yet, so its
        # definition rides ahead of the insertion that names it.
        self.assertEqual([opcodes.DEF_ATOM, opcodes.INSERT_CHILD], self.opcodes(ops))
        self.assertEqual(1, ops[-1]["index"])

    def test_a_resync_sends_the_tree_again_but_not_the_tables(self):
        self.render(self.rows(["a", "b"]))
        keys = [self.encoder.atom(k) for k in ("a", "b")]
        self.encoder.forget_tree()
        ops = self.render(self.rows(["a", "b"]))
        self.assertEqual([opcodes.MOUNT], self.opcodes(ops), "tables are never cleared")
        self.assertEqual(keys, [self.encoder.atom(k) for k in ("a", "b")],
                         "the atoms the session holds are still its own")

    def test_scroll_and_focus_are_instructions_not_props(self):
        self.render({"k": "scroll", "p": {"scroll_to": [0, 0]}})
        ops = self.render({"k": "scroll", "p": {"scroll_to": [0, 640]}})
        self.assertEqual([opcodes.SCROLL_TO], self.opcodes(ops))
        self.assertEqual(640, ops[0]["y"])

        self.render(box(props={"focus_to": False}))
        ops = self.render(box(props={"focus_to": True}))
        self.assertEqual([opcodes.FOCUS], self.opcodes(ops))


if __name__ == "__main__":
    unittest.main()
