"""From the view's dict to nodes, with the session's tables.

The view returns plain data::

    {"k": "box", "s": {"display": "column", "gap": 4, "bg": "surface.base"},
     "key": "row-12", "t": "text content", "on": {"click": "increment"},
     "p": {"item_height": 20}, "c": [ ...children... ]}

Style values are the spec's own vocabulary — role names, scale indices, px —
so a view author never sees a ``StyleRecord``. Every distinct style is
interned once; the tables only ever grow, exactly as the wire format
requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ViewError
from ..proto import PROTOCOL_VERSION, limits
from ..proto.node import (Handler, Subtree, TextRef, Value, event_code,
                          event_since, kind_code, kind_name)
from ..proto.op import Op
from ..proto.style import StyleRecord
from .style import Compiler

_INERT = {"spacer", "divider"}
_LEAF = {"text", "icon", "spacer", "divider", "audio", "video", "scene"}


@dataclass(slots=True)
class TNode:
    """A node on its way to the wire: the view's dict with every string
    interned, every style resolved to a table id, and an id of its own once
    the diff has settled one."""

    id: int
    kind: int
    style: int
    key: str | None = None
    key_atom: int = 0
    text: TextRef | None = None
    props: list[tuple[int, Value]] = field(default_factory=list)
    handlers: list[tuple[int, Handler]] = field(default_factory=list)
    children: list["TNode"] = field(default_factory=list)
    scroll_to: tuple[int, int] | None = None
    focus_to: bool = False

    @property
    def size(self) -> int:
        return 1 + sum(child.size for child in self.children)


class Encoder:
    """The session's tables and the tree it last sent.

    Tables are append-only and session-scoped, exactly as the wire format
    requires: an atom interned for the first page is still there on the
    tenth, which is why a second page costs no second ``DefAtom``.
    """

    def __init__(self, protocol: int = PROTOCOL_VERSION, assets=None) -> None:
        self.protocol = protocol
        self._assets = assets
        self._atoms: dict[str, int] = {}
        self._atoms_by_id: list[str] = [""]
        self._styles: dict[bytes, int] = {}
        self._style_cache: dict[str, int] = {}
        self._colors: dict[int, int] = {}
        self._fonts: dict[str, int] = {}
        self._pending: list[Op] = []
        self.previous: TNode | None = None
        self._next_id = 1
        self._nodes_by_id: dict[int, TNode] = {}
        self._compiler = Compiler(colors=self.color_literal, fonts=self.font_role)

    # -- tables -------------------------------------------------------------
    def atom(self, string) -> int:
        string = str(string)
        found = self._atoms.get(string)
        if found is not None:
            return found
        atom_id = len(self._atoms) + 1
        if atom_id > limits.MAX_ATOMS:
            raise ViewError(f"more than {limits.MAX_ATOMS} atoms in one session")
        self._atoms[string] = atom_id
        self._atoms_by_id.append(string)
        self._pending.append(Op.def_atom(atom_id, string))
        return atom_id

    def atom_value(self, atom_id: int) -> str | None:
        if 0 <= atom_id < len(self._atoms_by_id):
            return self._atoms_by_id[atom_id]
        return None

    def style(self, record: StyleRecord) -> int:
        """Style id 0 is the default record, which every session already has."""
        raw = record.to_bytes()
        if raw == StyleRecord().to_bytes():
            return 0
        found = self._styles.get(raw)
        if found is not None:
            return found
        style_id = len(self._styles) + 1
        if style_id > limits.MAX_STYLES:
            raise ViewError(f"more than {limits.MAX_STYLES} styles in one session")
        self._styles[raw] = style_id
        self._pending.append(Op.def_style(style_id, record))
        return style_id

    def color_literal(self, rgba: int) -> int:
        found = self._colors.get(rgba)
        if found is not None:
            return found
        color_id = len(self._colors) + 1
        if color_id > limits.MAX_COLORS:
            raise ViewError(f"more than {limits.MAX_COLORS} literal colours")
        self._colors[rgba] = color_id
        self._pending.append(Op.def_color(color_id, rgba))
        return color_id

    def font(self, family: str, hashes: list[bytes]) -> int:
        """Bind a font role to its faces. Roles 0 and 1 are the client's own
        sans and mono; binding one replaces it for this session only."""
        found = self._fonts.get(family)
        if found is not None:
            return found
        role = len(self._fonts) + 2
        if role > limits.MAX_FONT_ROLE:
            raise ViewError(
                f"a session binds at most {limits.MAX_FONT_ROLE - 1} font families")
        self._fonts[family] = role
        self._pending.append(Op.def_font(role, hashes))
        return role

    def font_role(self, family: str) -> int:
        """The role a view means when it names a family. A family nobody
        bound is an error here rather than a silent fall back to ``sans``."""
        try:
            return self._fonts[family]
        except KeyError:
            raise ViewError(
                f"no font bound for '{family}'; call app.font({family!r}, [paths]) at boot"
            ) from None

    def next_id(self) -> int:
        node_id = self._next_id
        self._next_id += 1
        return node_id

    # -- rendering ----------------------------------------------------------
    def render(self, view: dict, full: bool = False) -> list[Op]:
        """The ops this view costs: the definitions it needed, then the patch
        that takes the client's tree to it. Definitions come first because
        the wire format requires it — a decoder rejects a forward reference.
        """
        from .diff import Diff

        tree = self.build(view)
        if full or self.previous is None:
            self.assign_ids(tree)
            ops = [Op.mount(self.subtree_of(tree))]
        else:
            ops = Diff(self).ops(self.previous, tree)
        self.previous = tree
        self._index(tree)
        return self.flush() + ops

    def flush(self) -> list[Op]:
        """What the session owes the client before it can read anything else."""
        pending, self._pending = self._pending, []
        return pending

    def event_target(self, node_id: int, event: int):
        """The node the client named, and what the server last rendered on
        it: the handler's own event name, and the node's props.

        An event on a node that carries no handler for it *now* is dropped.
        Usually that is a race rather than an attack — a handler a render
        removed is still in the client's tree for the one round trip it
        takes the new one to arrive.
        """
        node = self._nodes_by_id.get(node_id)
        if node is None:
            return None
        for kind, handler in node.handlers:
            if kind == event:
                if handler.name is None:
                    return None
                return self.atom_value(handler.name), self.props_of(node)
        return None

    def node(self, node_id: int) -> TNode | None:
        return self._nodes_by_id.get(node_id)

    def forget_tree(self) -> None:
        """Everything a session forgets when its client asks for a resync."""
        self.previous = None
        self._nodes_by_id = {}

    def props_of(self, node: TNode) -> dict:
        """What a handler is handed: the node's props, by name, as plain data."""
        return {self.atom_value(atom): value.to_python() for atom, value in node.props}

    # -- build --------------------------------------------------------------
    def build(self, view, depth: int = 1) -> TNode:
        if not isinstance(view, dict):
            raise ViewError("a view is a hash")
        if depth > limits.MAX_TREE_DEPTH:
            raise ViewError(
                f"the tree is nested more than {limits.MAX_TREE_DEPTH} deep")

        name = view.get("k", "box")
        kind = kind_code(name)
        style_id = self._style_for(view.get("s"))

        key = view.get("key")
        key = str(key) if key is not None else None
        text = self._build_text(view, kind)
        props, scroll_to, focus_to = self._build_props(view.get("p"), kind)
        handlers = self._build_handlers(view.get("on"))

        if kind_name(kind) in _INERT and (text or props or handlers):
            raise ViewError(f"a {kind_name(kind)} carries nothing: no text, no props, no handlers")

        children = [self.build(child, depth + 1) for child in (view.get("c") or []) if child is not None]
        if kind_name(kind) in _LEAF and children:
            raise ViewError(f"a {kind_name(kind)} is a leaf and cannot have children")
        if len(children) > limits.MAX_CHILDREN:
            raise ViewError(f"more than {limits.MAX_CHILDREN} children on one node")

        return TNode(id=0, kind=kind, style=style_id, key=key,
                     key_atom=self.atom(key) if key is not None else 0,
                     text=text, props=props, handlers=handlers, children=children,
                     scroll_to=scroll_to, focus_to=focus_to)

    def subtree_of(self, node: TNode) -> Subtree:
        """A subtree as the wire carries it, pre-order."""
        out = Subtree()
        stack = [node]
        while stack:
            current = stack.pop()
            out.push(kind=current.kind, id=current.id, style=current.style,
                     key=current.key_atom, text=current.text, props=current.props,
                     handlers=current.handlers, child_count=len(current.children))
            stack.extend(reversed(current.children))
        return out

    def assign_ids(self, node: TNode) -> TNode:
        # Pre-order, like the subtree the wire carries: the ids a batch
        # names then read in the order a person reads the tree.
        stack = [node]
        while stack:
            current = stack.pop()
            if current.id == 0:
                current.id = self.next_id()
            stack.extend(reversed(current.children))
        return node

    # -- the pieces of a node ----------------------------------------------
    def _style_for(self, style) -> int:
        """Two style dicts with the same contents are the same style, and a
        table of ten thousand rows has three of them. One lookup instead of
        compiling and encoding a 64-byte record per node — which is most of
        what a render of fifty thousand nodes would otherwise cost."""
        if not style:
            return 0
        cache_key = repr(sorted(style.items(), key=lambda kv: str(kv[0])))
        found = self._style_cache.get(cache_key)
        if found is not None:
            return found
        style_id = self.style(self._compiler.record(style))
        self._style_cache[cache_key] = style_id
        return style_id

    def _build_text(self, view: dict, kind: int) -> TextRef | None:
        raw = view.get("t")
        if raw is None:
            return None
        string = str(raw)
        if len(string.encode("utf-8")) > limits.MAX_INLINE_STR:
            raise ViewError(
                f"a text of {len(string.encode('utf-8'))} bytes; the client takes at most "
                f"{limits.MAX_INLINE_STR} — split it into nodes")
        if kind_name(kind) in _INERT:
            raise ViewError(f"a {kind_name(kind)} carries no text")
        # Interning is for what repeats. A unique cell value would be a
        # permanent entry in a table that is never cleared.
        if view.get("intern") and len(string.encode("utf-8")) <= 24:
            return TextRef.of_atom(self.atom(string))
        return TextRef.of_inline(string)

    def _build_props(self, props, kind: int):
        """``scroll_to`` and ``focus_to`` never reach the client as props:
        they are instructions, done to a node once, and the diff turns a
        change of one into its own op."""
        if props is None:
            return [], None, False
        if not isinstance(props, dict):
            raise ViewError("a node's props are a hash")

        scroll_to = None
        focus_to = False
        out: list[tuple[int, Value]] = []
        for name, value in props.items():
            name = str(name)
            if name == "scroll_to":
                if kind_name(kind) not in ("scroll", "list"):
                    raise ViewError(
                        f"scroll_to is for a scroll or a list, not a {kind_name(kind)}")
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ViewError("a scroll_to is [x, y] in pixels")
                scroll_to = (round(value[0]), round(value[1]))
            elif name == "focus_to":
                focus_to = value is True
            else:
                out.append((self.atom(name), self._prop_value(name, value, kind)))
        if len(out) > limits.MAX_PROPS:
            raise ViewError(f"more than {limits.MAX_PROPS} props on one node")
        return out, scroll_to, focus_to

    def _prop_value(self, name: str, value, kind: int) -> Value:
        """An image's ``src`` is a file in the application: it goes on the
        wire as the hash of its bytes, served from ``/_eui/asset``."""
        name_of_kind = kind_name(kind)
        is_asset = ((name_of_kind in ("image", "audio", "video") and name == "src")
                    or (name_of_kind == "scene" and name in ("shader", "mesh")))
        if is_asset:
            return Value.asset(self._asset_hash(name, value))
        return Value.of(value)

    def _asset_hash(self, name: str, value) -> bytes:
        if isinstance(value, str):
            if self._assets is None:
                raise ViewError(f"no asset store to resolve {name} '{value}'")
            return self._assets.add_file(value)
        if isinstance(value, dict):
            hex_digest = value.get("asset")
            if not hex_digest:
                raise ViewError(f'a {name} is a path or {{"asset": "<hash>"}}')
            if len(hex_digest) != 64 or any(c not in "0123456789abcdef" for c in hex_digest):
                raise ViewError(f"an asset is 64 hex characters, got '{hex_digest}'")
            return bytes.fromhex(hex_digest)
        raise ViewError(f'a {name} is a path or {{"asset": "<hash>"}}')

    def _build_handlers(self, on) -> list[tuple[int, Handler]]:
        if on is None:
            return []
        if not isinstance(on, dict):
            raise ViewError("a node's handlers are a hash")
        out: list[tuple[int, Handler]] = []
        for event, target in on.items():
            code = event_code(event)
            # An event the other end cannot decode is left out rather than
            # sent: the view still renders, the widget just never hears from
            # it. `level` arrived in version 3.
            if event_since(code) > self.protocol:
                continue
            if not isinstance(target, str):
                raise ViewError(
                    "a handler is a server event name; local handlers are not compiled yet")
            out.append((code, Handler.server(self.atom(target))))
        if len(out) > limits.MAX_HANDLERS:
            raise ViewError(f"more than {limits.MAX_HANDLERS} handlers on one node")
        return out

    def _index(self, tree: TNode) -> None:
        self._nodes_by_id = {}
        stack = [tree]
        while stack:
            node = stack.pop()
            self._nodes_by_id[node.id] = node
            stack.extend(node.children)
