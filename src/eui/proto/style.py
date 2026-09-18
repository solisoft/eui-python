"""Computed style: the 64-byte record of ``spec/02-wire-format.md`` §3.

Everything in it is already resolved. There is no cascade, no specificity,
no inheritance to walk: a client's whole styling cost is one indexed lookup,
and a thousand table rows share three ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import DecodeError
from . import limits
from .reader import Reader
from .writer import Writer

AUTO, PX, PERCENT, FR, SPACE = 0, 1, 2, 3, 4


@dataclass(frozen=True, slots=True)
class Dim:
    """A length, in one of the five forms the layout algorithm understands
    (02 §3.1). There is no ``calc()``: a server that wants a computed length
    computes it, and the wire carries the answer."""

    tag: int = AUTO
    value: int = 0

    @staticmethod
    def auto() -> "Dim":
        return Dim(AUTO, 0)

    @staticmethod
    def px(value: int) -> "Dim":
        return Dim(PX, int(value))

    @staticmethod
    def percent(hundredths: int) -> "Dim":
        return Dim(PERCENT, int(hundredths))

    @staticmethod
    def fr(hundredths: int) -> "Dim":
        return Dim(FR, int(hundredths))

    @staticmethod
    def space(index: int) -> "Dim":
        return Dim(SPACE, int(index))

    @staticmethod
    def decode(r: Reader) -> "Dim":
        tag = r.u8()
        value = r.u16()
        if tag == AUTO and value != 0:
            raise DecodeError("Dim.auto carries a value")
        if tag > SPACE:
            raise DecodeError(f"unknown Dim tag {tag}")
        if tag == SPACE and value > 255:
            raise DecodeError("space index above 255")
        return Dim(tag, value)

    def encode(self, w: Writer) -> None:
        w.u8(self.tag).u16(self.value)


@dataclass(frozen=True, slots=True)
class ColorRef:
    """A colour: a theme role, a literal from the session's table, or
    nothing. Roles are strongly preferred — only a role follows the viewer's
    mode, contrast and density."""

    bits: int = 0
    LITERAL_BIT = 0x8000

    @staticmethod
    def role(role_id: int) -> "ColorRef":
        return ColorRef(role_id & 0x7FFF)

    @staticmethod
    def literal(index: int) -> "ColorRef":
        return ColorRef((index & 0x7FFF) | ColorRef.LITERAL_BIT)

    @property
    def is_literal(self) -> bool:
        return bool(self.bits & ColorRef.LITERAL_BIT)

    @property
    def is_none(self) -> bool:
        return self.bits == 0

    @property
    def index(self) -> int:
        return self.bits & 0x7FFF


NONE = ColorRef(0)

# The enumerated style fields. Each rejects a value it does not define rather
# than clamping it: clamping is how two implementations quietly disagree
# about a layout for a year.
DISPLAY = {"row": 0, "column": 1, "stack": 2, "grid": 3, "none": 4}
WRAP = {"nowrap": 0, "wrap": 1, "wrap_reverse": 2}
JUSTIFY = {"start": 0, "center": 1, "end": 2, "between": 3, "around": 4, "evenly": 5}
ALIGN_ITEMS = {"start": 0, "center": 1, "end": 2, "stretch": 3, "baseline": 4}
ALIGN_SELF = {**ALIGN_ITEMS, "auto": 5}
FONT_WEIGHT = {"regular": 0, "medium": 1, "semibold": 2, "bold": 3}
TEXT_ALIGN = {"start": 0, "center": 1, "end": 2, "justify": 3}
OVERFLOW = {"visible": 0, "clip": 1, "scroll": 2}
POSITION = {"flow": 0, "absolute": 1, "pointer": 2}
CURSOR = {
    "default": 0, "pointer": 1, "text": 2, "grab": 3, "grabbing": 4,
    "resize_h": 5, "resize_v": 6, "wait": 7, "not_allowed": 8,
}
TRANSITION = {"none": 0, "fast": 1, "base": 2, "slow": 3, "slower": 4, "slowest": 5}
MOTION = {"fade": 0, "leading": 1, "trailing": 2, "top": 3, "bottom": 4, "scale": 5, "paired": 6}
# `animation` is a bit set rather than one name: a node has to say how it
# arrives *and* how it leaves while it is still there to say it.
ANIMATION = {"none": 0, "spin": 1, "enter": 2, "exit": 4}

ANIMATION_SPIN = 1
ANIMATION_ENTER = 2
ANIMATION_EXIT = 4
ANIMATION_MASK = ANIMATION_SPIN | ANIMATION_ENTER | ANIMATION_EXIT


def _checked(table: dict[str, int], value: int, what: str) -> int:
    if value not in table.values():
        raise DecodeError(f"{what} is outside the range this revision defines: {value}")
    return value


@dataclass(slots=True)
class StyleRecord:
    """The neutral record is a transparent row that inherits what it can."""

    display: int = 0
    wrap: int = 0
    justify: int = 0
    align_items: int = 3          # stretch
    align_self: int = 5           # auto
    grow: int = 0
    shrink: int = 1
    gap: int = 0
    basis: Dim = field(default_factory=Dim.auto)
    width: Dim = field(default_factory=Dim.auto)
    height: Dim = field(default_factory=Dim.auto)
    min_width: Dim = field(default_factory=Dim.auto)
    min_height: Dim = field(default_factory=Dim.auto)
    max_width: Dim = field(default_factory=Dim.auto)
    max_height: Dim = field(default_factory=Dim.auto)
    padding: tuple[int, int, int, int] = (0, 0, 0, 0)
    margin: tuple[int, int, int, int] = (0, 0, 0, 0)
    bg: ColorRef = NONE
    fg: ColorRef = NONE
    border_color: ColorRef = NONE
    border_width: tuple[int, int, int, int] = (0, 0, 0, 0)
    radius: int = 0
    shadow: int = 0
    opacity: int = 255
    font_family: int = 0          # sans
    font_size: int = 2            # `base` on the text scale; 0 would be `xs`
    font_weight: int = 0
    text_align: int = 0
    line_clamp: int = 0
    text_decoration: int = 0
    overflow: int = 0
    position: int = 0
    z: int = 0
    cursor: int = 0
    transition: int = 0
    animation: int = 0
    blur: int = 0
    motion: int = 0

    @staticmethod
    def decode(r: Reader) -> "StyleRecord":
        raw = Reader(r.take(limits.STYLE_RECORD_BYTES))
        rec = StyleRecord()
        rec.display = _checked(DISPLAY, raw.u8(), "display")
        rec.wrap = _checked(WRAP, raw.u8(), "wrap")
        rec.justify = _checked(JUSTIFY, raw.u8(), "justify")
        rec.align_items = _checked(ALIGN_ITEMS, raw.u8(), "align_items")
        rec.align_self = _checked(ALIGN_SELF, raw.u8(), "align_self")
        rec.grow = raw.u8()
        rec.shrink = raw.u8()
        rec.gap = raw.u8()
        rec.basis = Dim.decode(raw)
        rec.width = Dim.decode(raw)
        rec.height = Dim.decode(raw)
        rec.min_width = Dim.decode(raw)
        rec.min_height = Dim.decode(raw)
        rec.max_width = Dim.decode(raw)
        rec.max_height = Dim.decode(raw)
        rec.padding = tuple(raw.take(4))
        rec.margin = tuple(raw.take(4))
        rec.bg = ColorRef(raw.u16())
        rec.fg = ColorRef(raw.u16())
        rec.border_color = ColorRef(raw.u16())
        rec.border_width = tuple(raw.take(4))
        rec.radius = raw.u8()
        rec.shadow = raw.u8()
        rec.opacity = raw.u8()
        rec.font_family = raw.u8()
        rec.font_size = raw.u8()
        rec.font_weight = _checked(FONT_WEIGHT, raw.u8(), "font_weight")
        rec.text_align = _checked(TEXT_ALIGN, raw.u8(), "text_align")
        rec.line_clamp = raw.u8()
        rec.text_decoration = raw.u8()
        rec.overflow = _checked(OVERFLOW, raw.u8(), "overflow")
        rec.position = _checked(POSITION, raw.u8(), "position")
        rec.z = raw.u8()
        rec.cursor = _checked(CURSOR, raw.u8(), "cursor")
        rec.transition = raw.u8()
        rec.animation = raw.u8()
        rec.blur = raw.u8()
        rec.motion = _checked(MOTION, raw.u8(), "motion")
        raw.finish()
        rec.validate()
        return rec

    def validate(self) -> "StyleRecord":
        if self.transition > 5:
            raise DecodeError("transition is a motion index + 1, at most 5")
        if self.animation & ~ANIMATION_MASK:
            raise DecodeError("animation is a bit set of 1, 2 and 4")
        if self.text_decoration & ~0b11:
            raise DecodeError("text_decoration has unknown bits")
        # A direction with nothing going that way.
        if self.motion != 0 and not self.animation & (ANIMATION_ENTER | ANIMATION_EXIT):
            raise DecodeError("motion needs an entrance or an exit to belong to")
        return self

    def encode(self, w: Writer) -> None:
        w.u8(self.display).u8(self.wrap).u8(self.justify).u8(self.align_items).u8(self.align_self)
        w.u8(self.grow).u8(self.shrink).u8(self.gap)
        for dim in (self.basis, self.width, self.height, self.min_width,
                    self.min_height, self.max_width, self.max_height):
            dim.encode(w)
        w.raw(bytes(self.padding)).raw(bytes(self.margin))
        w.u16(self.bg.bits).u16(self.fg.bits).u16(self.border_color.bits)
        w.raw(bytes(self.border_width))
        w.u8(self.radius).u8(self.shadow).u8(self.opacity)
        w.u8(self.font_family).u8(self.font_size).u8(self.font_weight).u8(self.text_align)
        w.u8(self.line_clamp).u8(self.text_decoration).u8(self.overflow)
        w.u8(self.position).u8(self.z).u8(self.cursor)
        w.u8(self.transition).u8(self.animation).u8(self.blur).u8(self.motion)

    def to_bytes(self) -> bytes:
        w = Writer()
        self.encode(w)
        return w.to_bytes()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, StyleRecord) and other.to_bytes() == self.to_bytes()

    def __hash__(self) -> int:
        return hash(self.to_bytes())
