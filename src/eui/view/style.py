"""A style hash, in the spec's own vocabulary, compiled into the 64-byte
record the wire carries.

    {"display": "column", "gap": 4, "bg": "surface.base",
     "pad": [4, 6, 4, 6], "size": "lg", "fg": "text.muted"}

An unknown key is an error rather than a key that does nothing: a style that
is silently dropped is a page that is wrong for a day.
"""

from __future__ import annotations

from typing import Callable

from .. import theme
from ..errors import ViewError
from ..proto import style as st
from ..proto.style import ColorRef, Dim, StyleRecord


def rgba_of(hex_colour: str) -> int:
    """``#RRGGBB`` or ``#RRGGBBAA`` as ``0xRRGGBBAA``."""
    digits = hex_colour.lstrip("#")
    try:
        if len(digits) == 6:
            return (int(digits, 16) << 8) | 0xFF
        if len(digits) == 8:
            return int(digits, 16)
    except ValueError:
        raise ViewError(f"bad hex colour '{hex_colour}'") from None
    raise ViewError(f"a hex colour is #RRGGBB or #RRGGBBAA, got '{hex_colour}'")


class Compiler:
    """``colors`` is asked for a literal ``#RRGGBB`` and answers the session
    table index it interned it at; roles never reach it. ``fonts`` is asked
    for a family the application bound and answers its role."""

    __slots__ = ("_colors", "_fonts")

    def __init__(self, colors: Callable[[int], int] | None = None,
                 fonts: Callable[[str], int] | None = None) -> None:
        self._colors = colors
        self._fonts = fonts

    def record(self, style: dict | None) -> StyleRecord:
        rec = StyleRecord()
        if style is None:
            return rec
        if not isinstance(style, dict):
            raise ViewError("a style is a hash")
        for key, value in style.items():
            self._apply(rec, str(key), value)
        rec.validate()
        return rec

    def color(self, value) -> ColorRef:
        """A colour as the wire carries it: a role, a literal the session
        interned, or nothing."""
        if isinstance(value, ColorRef):
            return value
        name = str(value)
        if name == "none":
            return st.NONE
        if name.startswith("#"):
            if self._colors is None:
                raise ViewError(f"no colour table to intern '{name}' into")
            return ColorRef.literal(self._colors(rgba_of(name)))
        return ColorRef.role(theme.role(name))

    # -- one key at a time --------------------------------------------------
    def _apply(self, r: StyleRecord, key: str, v) -> None:
        if key == "display":
            r.display = self._enum(st.DISPLAY, v, key)
        elif key == "wrap":
            r.wrap = self._enum(st.WRAP, v, key)
        elif key == "justify":
            r.justify = self._enum(st.JUSTIFY, v, key)
        elif key == "align":
            r.align_items = self._enum(st.ALIGN_ITEMS, v, key)
        elif key == "self":
            r.align_self = self._enum(st.ALIGN_SELF, v, key)
        elif key == "grow":
            r.grow = self._byte(v, key)
        elif key == "shrink":
            r.shrink = self._byte(v, key)
        elif key == "gap":
            r.gap = self._byte(v, key)
        elif key == "basis":
            r.basis = self._dim(v)
        elif key == "width":
            r.width = self._dim(v)
        elif key == "height":
            r.height = self._dim(v)
        elif key == "min_width":
            r.min_width = self._dim(v)
        elif key == "min_height":
            r.min_height = self._dim(v)
        elif key == "max_width":
            r.max_width = self._dim(v)
        elif key == "max_height":
            r.max_height = self._dim(v)
        elif key == "pad":
            r.padding = self._edges(v)
        elif key == "margin":
            r.margin = self._edges(v)
        elif key == "bg":
            r.bg = self.color(v)
        elif key == "fg":
            r.fg = self.color(v)
        elif key == "border_color":
            r.border_color = self.color(v)
        elif key == "border":
            r.border_width = self._edges(v)
        elif key == "radius":
            r.radius = self._scale(theme.RADIUS, v, key)
        elif key == "shadow":
            r.shadow = self._scale(theme.SHADOW, v, key)
        elif key == "opacity":
            r.opacity = self._byte(v, key)
        elif key == "blur":
            r.blur = self._byte(v, key)
        elif key == "font":
            r.font_family = self._font(v)
        elif key == "size":
            r.font_size = self._scale(theme.TEXT, v, key)
        elif key == "weight":
            r.font_weight = self._enum(st.FONT_WEIGHT, v, key)
        elif key == "text_align":
            r.text_align = self._enum(st.TEXT_ALIGN, v, key)
        elif key == "clamp":
            r.line_clamp = self._byte(v, key)
        elif key == "underline":
            r.text_decoration |= 1 if v else 0
        elif key == "strike":
            r.text_decoration |= 2 if v else 0
        elif key == "overflow":
            r.overflow = self._enum(st.OVERFLOW, v, key)
        elif key == "transition":
            r.transition = self._enum(st.TRANSITION, v, key)
        elif key == "animation":
            r.animation = self._animation(v)
        elif key == "motion":
            r.motion = self._enum(st.MOTION, v, key)
        elif key == "position":
            r.position = self._enum(st.POSITION, v, key)
        elif key == "z":
            r.z = self._byte(v, key)
        elif key == "cursor":
            r.cursor = self._enum(st.CURSOR, v, key)
        else:
            raise ViewError(f"unknown style key '{key}'")

    def _enum(self, table: dict[str, int], value, key: str) -> int:
        try:
            return table[str(value)]
        except KeyError:
            raise ViewError(
                f"unknown {key} '{value}'; it is one of {', '.join(table)}") from None

    def _scale(self, names: dict[str, int], value, key: str) -> int:
        """A scale index, written as the index or as the name the spec gives it."""
        if isinstance(value, int) and not isinstance(value, bool):
            return self._byte(value, key)
        try:
            return names[str(value)]
        except KeyError:
            raise ViewError(
                f"unknown {key} '{value}'; it is an index or one of {', '.join(names)}") from None

    def _byte(self, value, key: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ViewError(f"{key} is a number, got {value!r}")
        n = int(value)
        if not 0 <= n <= 255:
            raise ViewError(f"{key} is 0–255, got {n}")
        return n

    def _dim(self, v) -> Dim:
        """``12`` (px), ``"auto"``, ``"50%"``, ``"1fr"``, ``"sp:4"``."""
        if isinstance(v, bool):
            raise ViewError(f"cannot read a length from {v!r}")
        if isinstance(v, int):
            if not 0 <= v <= 65_535:
                raise ViewError(f"px is 0–65535, got {v}")
            return Dim.px(v)
        if isinstance(v, float):
            return Dim.px(max(0, min(65_535, round(v))))
        if isinstance(v, str):
            try:
                if v == "auto":
                    return Dim.auto()
                if v.endswith("%"):
                    return Dim.percent(max(0, min(65_535, round(float(v[:-1]) * 100))))
                if v.endswith("fr"):
                    return Dim.fr(max(0, min(65_535, round(float(v[:-2]) * 100))))
                if v.startswith("sp:"):
                    return Dim.space(int(v[3:]))
            except ValueError:
                raise ViewError(f"cannot read a length from {v!r}") from None
        raise ViewError(f"cannot read a length from {v!r}")

    def _edges(self, v) -> tuple[int, int, int, int]:
        """One index for every side, ``[y, x]``, or ``[t, r, b, l]``."""
        if isinstance(v, bool):
            raise ViewError("edges are one index, [y, x] or [t, r, b, l]")
        if isinstance(v, (int, float)):
            n = self._byte(v, "edge")
            return (n, n, n, n)
        if isinstance(v, (list, tuple)):
            if len(v) == 4:
                return tuple(self._byte(n, "edge") for n in v)  # type: ignore[return-value]
            if len(v) == 2:
                y = self._byte(v[0], "edge")
                x = self._byte(v[1], "edge")
                return (y, x, y, x)
        raise ViewError(f"edges are one index, [y, x] or [t, r, b, l], got {v!r}")

    def _font(self, v) -> int:
        """``"sans"``, ``"mono"``, or a family the application bound."""
        from ..proto import limits
        if isinstance(v, int) and not isinstance(v, bool):
            if v > limits.MAX_FONT_ROLE:
                raise ViewError(f"font role {v} is above {limits.MAX_FONT_ROLE}")
            return v
        name = str(v)
        if name == "sans":
            return 0
        if name == "mono":
            return 1
        if self._fonts is None:
            raise ViewError(
                f'a font is "sans", "mono" or a family the application bound, got {v!r}')
        return self._fonts(name)

    def _animation(self, v) -> int:
        """One name, or several: a node has to say how it arrives *and* how
        it leaves while it is still there to say it."""
        names = v if isinstance(v, (list, tuple)) else [v]
        mask = 0
        for name in names:
            mask |= self._enum(st.ANIMATION, name, "animation")
        return mask
