"""The view, written as Python.

Every helper answers a plain dict — ``{"k": ..., "s": ..., "c": ...}`` — so
a view is data all the way down: printable, comparable, testable without a
socket. Style keys are the spec's own vocabulary, passed as keyword
arguments; ``on``, ``props`` and ``key`` are the three that are not style::

    column(gap=4, pad=6, bg="surface.base")[
        text("Hello", size="2xl", weight="bold"),
        button("Increment", "increment"),
    ]

or, without the indexing sugar, ``column([...], gap=4)``.
"""

from __future__ import annotations

from .errors import ViewError

RESERVED = ("on", "props", "key", "intern")


def node(kind: str, children=None, *, text=None, **options) -> dict:
    style = {k: v for k, v in options.items() if k not in RESERVED}
    out: dict = {"k": str(kind)}
    if style:
        out["s"] = style
    if text is not None:
        out["t"] = text
    if options.get("key") is not None:
        out["key"] = str(options["key"])
    if options.get("intern"):
        out["intern"] = True
    if options.get("props"):
        out["p"] = {str(k): v for k, v in options["props"].items()}
    if options.get("on"):
        out["on"] = {str(k): v for k, v in options["on"].items()}
    kids = [child for child in (children or []) if child is not None]
    if kids:
        out["c"] = kids
    return out


# ---------------------------------------------------------------- primitives

def box(children=None, **options) -> dict:
    return node("box", children, **options)


def row(children=None, **options) -> dict:
    return node("box", children, **{**options, "display": "row"})


def column(children=None, **options) -> dict:
    return node("box", children, **{**options, "display": "column"})


def stack(children=None, **options) -> dict:
    """Children overlap, ordered by ``z``: menus, tooltips, a badge on a corner."""
    return node("box", children, **{**options, "display": "stack"})


def text(content, **options) -> dict:
    return node("text", None, text=str(content), **options)


def image(src, **options) -> dict:
    props = {**(options.pop("props", None) or {}), "src": src}
    return node("image", None, props=props, **options)


def icon(name, **options) -> dict:
    props = {**(options.pop("props", None) or {}), "name": name}
    return node("icon", None, props=props, **options)


def input_(value, on_change: str | None = None, **options) -> dict:
    props = {**(options.pop("props", None) or {}), "value": str(value)}
    on = dict(options.pop("on", None) or {})
    if on_change:
        on["change"] = on_change
    return node("input", None, props=props, on=on, **options)


def textarea(value, on_change: str | None = None, **options) -> dict:
    props = {**(options.pop("props", None) or {}), "value": str(value)}
    on = dict(options.pop("on", None) or {})
    if on_change:
        on["change"] = on_change
    return node("textarea", None, props=props, on=on, **options)


def scroll(children=None, **options) -> dict:
    return node("scroll", children, **options)


def list_(children=None, **options) -> dict:
    """A virtualised list: only the window in view is laid out, and the
    client asks for another range with a ``window`` event."""
    return node("list", children, **options)


def canvas(paths, **options) -> dict:
    props = {**(options.pop("props", None) or {}), "paths": paths}
    return node("canvas", None, props=props, **options)


def overlay(children=None, **options) -> dict:
    return node("overlay", children, **options)


def sizer(children=None, **options) -> dict:
    return node("sizer", children, **options)


def spacer(**options) -> dict:
    """Flexible empty space. Inert: no text, no props, no handlers."""
    return node("spacer", None, **{"grow": 1, **options})


def divider(**options) -> dict:
    return node("divider", None, **{"height": 1, "bg": "border.subtle", "width": "100%", **options})


def keyed(key, a_node: dict) -> dict:
    """Identity for reconciliation: a row that moved is a row that moved,
    rather than every row below it having changed."""
    return {**a_node, "key": str(key)}


# ------------------------------------------------------------------- widgets
# Composed from the primitives above and nothing else, which is the whole
# reason the catalogue can grow without shipping a new client.

TONES = {
    "accent": ("accent.base", "accent.on"),
    "danger": ("danger.base", "danger.on"),
    "success": ("success.base", "success.on"),
    "warning": ("warning.base", "warning.on"),
    "info": ("info.base", "info.on"),
    "quiet": ("surface.raised", "text.default"),
}
_BUTTON_PAD = {"sm": [1, 3], "md": [2, 4], "lg": [3, 5]}


def button(label, event: str, tone: str = "accent", size: str = "md", **options) -> dict:
    try:
        bg, fg = TONES[str(tone)]
    except KeyError:
        raise ViewError(f"unknown button tone '{tone}'") from None
    style = {
        "display": "row", "justify": "center", "align": "center", "bg": bg,
        "radius": "md", "pad": _BUTTON_PAD.get(str(size), [2, 4]),
        "cursor": "pointer", "transition": "fast",
        **{k: v for k, v in options.items() if k not in RESERVED},
    }
    on = {**(options.get("on") or {}), "click": event}
    return box([text(label, fg=fg, weight="medium")], on=on, key=options.get("key"), **style)


def card(children=None, **options) -> dict:
    """A surface a thing sits on: raised, padded, with a hairline."""
    style = {
        "display": "column", "bg": "surface.raised", "radius": "md", "pad": 5,
        "gap": 4, "border": 1, "border_color": "border.subtle",
        **{k: v for k, v in options.items() if k not in RESERVED},
    }
    return box(children, on=options.get("on"), key=options.get("key"), **style)


def field(label, value, on_change: str, **options) -> dict:
    """A label above a field, the pair kept together."""
    style = {k: v for k, v in options.items() if k not in RESERVED}
    return column([
        text(label, size="sm", fg="text.muted"),
        input_(value, on_change=on_change, bg="surface.sunken", radius="sm",
               pad=[2, 3], border=1, border_color="border.default"),
    ], gap=2, **style)
