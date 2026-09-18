"""What it costs to go from the tree the client holds to the one the view
just returned.

The point of the whole exercise: a click that changes one number is one
``SetText``, not a page. Keyed children reconcile by ``MoveChild``, so
reordering a thousand-row table is *n* moves rather than a rebuild.
"""

from __future__ import annotations

from ..proto.node import Value
from ..proto.op import Op
from .tree import TNode


class Fenwick:
    """How many of the rows still waiting sit ahead of this one.

    A plain list of counts would answer it in a scan; this answers it, and
    takes a row out of the running, in log n.
    """

    __slots__ = ("size", "tree")

    def __init__(self, size: int) -> None:
        self.size = size
        self.tree = [0] * (size + 1)
        for i in range(1, size + 1):
            self.tree[i] += 1
            parent = i + (i & -i)
            if parent <= size:
                self.tree[parent] += self.tree[i]

    def count_before(self, slot: int) -> int:
        """Rows still waiting at slots ``0...slot``."""
        total = 0
        i = slot
        while i > 0:
            total += self.tree[i]
            i -= i & -i
        return total

    def place(self, slot: int) -> None:
        i = slot + 1
        while i <= self.size:
            self.tree[i] -= 1
            i += i & -i


class Diff:
    __slots__ = ("_encoder", "_ops")

    def __init__(self, encoder) -> None:
        self._encoder = encoder
        self._ops: list[Op] = []

    def ops(self, old: TNode, new: TNode) -> list[Op]:
        self._ops = []
        if self._replaceable(old, new):
            self._node(old, new)
        else:
            # The root changed kind: there is no parent to patch it in, so
            # the whole document is replaced. Tables are not cleared with it.
            self._encoder.assign_ids(new)
            self._ops.append(Op.mount(self._encoder.subtree_of(new)))
        return self._ops

    @staticmethod
    def _replaceable(old: TNode, new: TNode) -> bool:
        return old.kind == new.kind and old.key == new.key

    def _node(self, old: TNode, new: TNode) -> None:
        new.id = old.id
        if old.style != new.style:
            self._ops.append(Op.set_style(new.id, new.style))
        if old.text != new.text:
            self._ops.append(Op.set_text(new.id, new.text))
        self._props(old, new)
        self._handlers(old, new)
        self._children(old, new)
        # Instructions rather than state: a node is scrolled, or focused,
        # once — so what triggers the op is the view asking again, not the
        # client's own offset, which this server never learns.
        if new.scroll_to and new.scroll_to != old.scroll_to:
            self._ops.append(Op.scroll_to(new.id, new.scroll_to[0], new.scroll_to[1]))
        if new.focus_to and not old.focus_to:
            self._ops.append(Op.focus(new.id))

    def _props(self, old: TNode, new: TNode) -> None:
        before = dict(old.props)
        after = dict(new.props)
        for atom, value in after.items():
            if before.get(atom) != value:
                self._ops.append(Op.set_prop(new.id, atom, value))
        # There is no op that removes a property, and a client that kept one
        # the view stopped sending would answer for a state nothing holds.
        # Null is how a prop goes away.
        for atom in before.keys() - after.keys():
            self._ops.append(Op.set_prop(new.id, atom, Value.null()))

    def _handlers(self, old: TNode, new: TNode) -> None:
        before = dict(old.handlers)
        after = dict(new.handlers)
        for event, handler in after.items():
            if before.get(event) != handler:
                self._ops.append(Op.set_handler(new.id, event, handler))
        for event in before.keys() - after.keys():
            self._ops.append(Op.clear_handler(new.id, event))

    def _children(self, old: TNode, new: TNode) -> None:
        if not old.children and not new.children:
            return
        if self._keyed(old.children) and self._keyed(new.children):
            self._keyed_children(old, new)
        else:
            self._positional_children(old, new)

    @staticmethod
    def _keyed(children: list[TNode]) -> bool:
        return bool(children) and all(child.key is not None for child in children)

    def _positional_children(self, old: TNode, new: TNode) -> None:
        """Position is identity: child *i* on one side is child *i* on the
        other. Right for a view whose shape is fixed, wrong for a list —
        which is what keys are for."""
        shared = min(len(old.children), len(new.children))
        for i in range(shared):
            before, after = old.children[i], new.children[i]
            if self._replaceable(before, after):
                self._node(before, after)
            else:
                self._encoder.assign_ids(after)
                self._ops.append(Op.replace(before.id, self._encoder.subtree_of(after)))

        if len(old.children) > shared:
            self._ops.append(Op.remove_child(new.id, shared, len(old.children) - shared))
        elif len(new.children) > shared:
            for offset, child in enumerate(new.children[shared:]):
                self._encoder.assign_ids(child)
                self._ops.append(
                    Op.insert_child(new.id, shared + offset, self._encoder.subtree_of(child)))

    def _keyed_children(self, old: TNode, new: TNode) -> None:
        """Identity is the key, so a row that moved is a row that moved
        rather than every row below it having changed.

        The obvious way to write this is quadratic — scan the old children
        for each new one — and a ten-thousand-row sort then costs fifty
        million comparisons before a single byte is sent. What makes it
        ``n log n`` instead is the observation that a ``MoveChild`` only ever
        pulls a row *forward*: everything before ``index`` is already final,
        and the rest keep their relative order. So a row's current position
        is ``index`` plus however many rows ahead of it are still waiting,
        and a Fenwick tree answers that in fourteen steps rather than ten
        thousand.
        """
        parent = new.id
        wanted = {child.key for child in new.children}

        cur = list(old.children)
        i = 0
        while i < len(cur):
            if cur[i].key in wanted:
                i += 1
                continue
            run = 1
            while i + run < len(cur) and cur[i + run].key not in wanted:
                run += 1
            self._ops.append(Op.remove_child(parent, i, run))
            del cur[i : i + run]

        at = {child.key: slot for slot, child in enumerate(cur)}
        waiting = Fenwick(len(cur))

        for index, after in enumerate(new.children):
            slot = at.get(after.key)
            if slot is None:
                self._encoder.assign_ids(after)
                self._ops.append(Op.insert_child(parent, index, self._encoder.subtree_of(after)))
                continue

            origin = index + waiting.count_before(slot)
            if origin != index:
                self._ops.append(Op.move_child(parent, origin, index))
            waiting.place(slot)
            self._reconcile_kept(cur[slot], after)

    def _reconcile_kept(self, before: TNode, after: TNode) -> None:
        if before.kind == after.kind:
            self._node(before, after)
        else:
            self._encoder.assign_ids(after)
            self._ops.append(Op.replace(before.id, self._encoder.subtree_of(after)))
