"""What a server is allowed to say about colour and size (spec/05-theme.md).

The server never sends a colour. It sends a *role* and a *scale index*, and
the client resolves both against the active theme and the viewer's own mode,
density and font scale. Dark mode costs zero bytes, and this process never
learns which one somebody is in.
"""

from __future__ import annotations

from .errors import ViewError

#: The 33 colour roles, numbered once and for good. Ids 34.. are reserved
#: and a client rejects them.
ROLES = {
    "surface.base": 1, "surface.raised": 2, "surface.sunken": 3, "surface.overlay": 4,
    "text.default": 5, "text.muted": 6, "text.inverted": 7, "text.disabled": 8,
    "accent.base": 9, "accent.hover": 10, "accent.active": 11, "accent.on": 12,
    "success.base": 13, "success.subtle": 14, "success.on": 15,
    "warning.base": 16, "warning.subtle": 17, "warning.on": 18,
    "danger.base": 19, "danger.subtle": 20, "danger.on": 21,
    "info.base": 22, "info.subtle": 23, "info.on": 24,
    "border.subtle": 25, "border.default": 26, "border.strong": 27,
    "focus.ring": 28,
    "series.1": 29, "series.2": 30, "series.3": 31, "series.4": 32, "series.5": 33,
}

#: ``space``, in device-independent pixels at cozy density.
SPACE = (0, 2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96)

#: ``text``. The scale stops at index 7, 38 px, and the theme cannot move
#: it: there is no hero type in this protocol.
TEXT = {"xs": 0, "sm": 1, "base": 2, "lg": 3, "xl": 4, "2xl": 5, "3xl": 6, "4xl": 7}
TEXT_PX = ((11, 16), (13, 18), (15, 22), (17, 24), (20, 28), (24, 32), (30, 38), (38, 46))

RADIUS = {"none": 0, "sm": 1, "md": 2, "lg": 3, "full": 4}
SHADOW = {"none": 0, "sm": 1, "md": 2, "lg": 3}

#: ``motion``, in milliseconds. ``transition`` carries the index + 1, which
#: is why ``none`` is a name here rather than a hole in the scale.
MOTION_MS = {"fast": 100, "base": 180, "slow": 320, "slower": 560, "slowest": 1000}

#: Control heights, for the widgets composed on top of the primitives.
CONTROL = {"sm": 28, "md": 36, "lg": 44}


def role(name: str) -> int:
    try:
        return ROLES[str(name)]
    except KeyError:
        raise ViewError(f"unknown colour role '{name}'") from None


def is_role(name: str) -> bool:
    return str(name) in ROLES


def space(index: int) -> int:
    """The pixel value of a ``space`` index, for a view doing its own
    arithmetic — a measure derived from the viewport, say."""
    try:
        return SPACE[index]
    except IndexError:
        raise ViewError(f"space index {index} is past the end of the scale") from None


def text_size(name_or_index) -> int:
    if isinstance(name_or_index, int):
        index = name_or_index
    else:
        try:
            index = TEXT[str(name_or_index)]
        except KeyError:
            raise ViewError(f"unknown text scale '{name_or_index}'") from None
    if index > 7:
        raise ViewError(f"text index {index} is past the end of the scale")
    return index
