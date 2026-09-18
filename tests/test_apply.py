"""The test the diff is actually worth: a client, in forty lines, that
applies what the server sends — and then the two trees are compared node by
node.

Everything else about a patch stream can look right and still be wrong. A
``MoveChild`` with an index off by one, a ``RemoveChild`` that counts from
the list before the removal rather than after, an id quietly reused: each
produces ops that encode, decode and apply without complaint, and leaves the
window showing a tree nobody wrote. The only check that catches those is to
*be* the client.
"""

import random
import unittest
from dataclasses import dataclass, field
from itertools import permutations

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui.dsl import box, column, keyed, text
from eui.proto import Value
from eui.proto import op as opcodes
from eui.view import Encoder


@dataclass
class Node:
    """What a client holds: the same fields the wire carries, and nothing else."""

    id: int
    kind: int
    style: int
    key: int
    text: object
    props: dict = field(default_factory=dict)
    handlers: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def shape(self):
        return (self.id, self.kind, self.style, self.key, self.text,
                tuple(sorted(self.props.items())), tuple(sorted(self.handlers.items())),
                tuple(child.shape() for child in self.children))


class Client:
    def __init__(self):
        self.root: Node | None = None

    def apply(self, ops):
        for op in ops:
            self._apply_one(op)
        return self

    def find(self, node_id):
        stack = [self.root] if self.root else []
        while stack:
            node = stack.pop()
            if node.id == node_id:
                return node
            stack.extend(node.children)
        return None

    def _apply_one(self, op):
        f = op.fields
        code = op.opcode
        if code == opcodes.MOUNT:
            self.root = self._build(f["subtree"])
        elif code == opcodes.REPLACE:
            self._replace(f["node"], self._build(f["subtree"]))
        elif code == opcodes.SET_STYLE:
            self._node(f["node"]).style = f["style"]
        elif code == opcodes.SET_TEXT:
            self._node(f["node"]).text = f["text"]
        elif code == opcodes.SET_PROP:
            props = self._node(f["node"]).props
            if f["value"] == Value.null():
                props.pop(f["prop"], None)
            else:
                props[f["prop"]] = f["value"]
        elif code == opcodes.SET_HANDLER:
            self._node(f["node"]).handlers[f["event"]] = f["handler"]
        elif code == opcodes.CLEAR_HANDLER:
            self._node(f["node"]).handlers.pop(f["event"], None)
        elif code == opcodes.INSERT_CHILD:
            self._node(f["parent"]).children.insert(f["index"], self._build(f["subtree"]))
        elif code == opcodes.REMOVE_CHILD:
            children = self._node(f["parent"]).children
            del children[f["index"]:f["index"] + f["count"]]
        elif code == opcodes.MOVE_CHILD:
            children = self._node(f["parent"]).children
            children.insert(f["to"], children.pop(f["from"]))
        elif code in (opcodes.DEF_ATOM, opcodes.DEF_STYLE, opcodes.DEF_COLOR,
                      opcodes.DEF_FONT, opcodes.FOCUS, opcodes.SCROLL_TO, opcodes.NOTIFY):
            pass
        else:
            raise AssertionError(f"the client has no op {code:#x}")

    def _node(self, node_id):
        node = self.find(node_id)
        if node is None:
            raise AssertionError(f"op names node {node_id}, which this client does not have")
        return node

    def _replace(self, node_id, fresh):
        if self.root.id == node_id:
            self.root = fresh
            return
        stack = [self.root]
        while stack:
            node = stack.pop()
            for index, child in enumerate(node.children):
                if child.id == node_id:
                    node.children[index] = fresh
                    return
            stack.extend(node.children)
        raise AssertionError(f"replace names node {node_id}, which this client does not have")

    def _build(self, subtree):
        """A subtree arrives pre-order, its shape carried by ``child_count``."""
        position = 0

        def take():
            nonlocal position
            flat = subtree.nodes[position]
            position += 1
            node = Node(flat.id, flat.kind, flat.style, flat.key, flat.text,
                        dict(subtree.props_of(flat)), dict(subtree.handlers_of(flat)), [])
            for _ in range(flat.child_count):
                node.children.append(take())
            return node

        return take()


def server_shape(tnode):
    """The server's own tree, in the same shape, so the two can be compared."""
    return (tnode.id, tnode.kind, tnode.style, tnode.key_atom, tnode.text,
            tuple(sorted(dict(tnode.props).items())),
            tuple(sorted(dict(tnode.handlers).items())),
            tuple(server_shape(child) for child in tnode.children))


class ApplyTest(unittest.TestCase):
    def assert_same(self, encoder, client, what):
        self.assertEqual(server_shape(encoder.previous), client.root.shape(), what)

    def test_a_sequence_of_edits_leaves_the_client_holding_the_same_tree(self):
        encoder, client = Encoder(), Client()
        views = [
            column([text("a"), text("b")]),
            column([text("a"), text("B"), text("c")]),
            column([text("a")]),
            column([text("a"), box(props={"id": 1}, on={"click": "go"})]),
            column([text("a"), box(props={"id": 2})]),
            column([box(), text("a")]),
            column([text("a"), text("b"), text("c")]),
        ]
        for index, view in enumerate(views):
            client.apply(encoder.render(view))
            self.assert_same(encoder, client, f"after view {index}")

    @staticmethod
    def keyed_rows(keys, marked=()):
        return column([keyed(k, text(str(k), fg="danger.base" if k in marked else "text.default"))
                       for k in keys])

    def test_every_permutation_of_five_keyed_rows_applies(self):
        keys = ["a", "b", "c", "d", "e"]
        for wanted in permutations(keys):
            encoder, client = Encoder(), Client()
            client.apply(encoder.render(self.keyed_rows(keys)))
            client.apply(encoder.render(self.keyed_rows(list(wanted))))
            self.assert_same(encoder, client, f"reordered to {''.join(wanted)}")

    def test_rows_added_removed_and_reordered_at_once(self):
        encoder, client = Encoder(), Client()
        client.apply(encoder.render(self.keyed_rows(["a", "b", "c", "d", "e"])))
        client.apply(encoder.render(self.keyed_rows(["e", "x", "b", "z", "a"], {"b"})))
        self.assert_same(encoder, client, "three at once")

    def test_random_edits_applied_over_and_over(self):
        """The one that finds what hand-written cases do not: a thousand
        random edits, each one applied and checked."""
        rng = random.Random(20_260_918)
        keys = [chr(c) for c in range(ord("a"), ord("m"))]

        for round_number in range(40):
            encoder, client = Encoder(), Client()
            present = rng.sample(keys, rng.randint(1, 6))
            client.apply(encoder.render(self.keyed_rows(present)))
            self.assert_same(encoder, client, f"round {round_number} mount")

            for step in range(12):
                choice = rng.randrange(5)
                if choice == 0:
                    rng.shuffle(present)
                elif choice == 1:
                    candidate = rng.choice(keys)
                    if candidate not in present:
                        present.append(candidate)
                elif choice == 2 and len(present) > 1:
                    present.remove(rng.choice(present))
                elif choice == 3:
                    candidate = rng.choice(keys)
                    if candidate not in present:
                        present.insert(rng.randrange(len(present) + 1), candidate)
                marked = {k for k in present if rng.randrange(3) == 0}
                client.apply(encoder.render(self.keyed_rows(present, marked)))
                self.assert_same(encoder, client,
                                 f"round {round_number} step {step}: {','.join(present)}")

    def test_a_nested_keyed_list_inside_a_changing_shell(self):
        encoder, client = Encoder(), Client()

        def shell(title, rows):
            return column([text(title), self.keyed_rows(rows)])

        client.apply(encoder.render(shell("one", ["a", "b", "c"])))
        client.apply(encoder.render(shell("two", ["c", "a"])))
        self.assert_same(encoder, client, "nested")
        client.apply(encoder.render(shell("two", ["c", "a", "b"])))
        self.assert_same(encoder, client, "nested, grown")

    def test_node_ids_are_never_reused_while_a_node_is_alive(self):
        encoder, client = Encoder(), Client()
        for i in range(10):
            rows = (["a", "b", "c", "d"][i % 4:] + ["a", "b", "c", "d"][:i % 4])[:3]
            client.apply(encoder.render(self.keyed_rows(rows)))
            live = set()
            stack = [client.root]
            while stack:
                node = stack.pop()
                self.assertNotIn(node.id, live, f"node {node.id} appears twice in one tree")
                live.add(node.id)
                stack.extend(node.children)


if __name__ == "__main__":
    unittest.main()
