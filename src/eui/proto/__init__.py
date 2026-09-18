"""The wire format: `spec/02-wire-format.md` and the frames of `spec/01`."""

from . import limits  # noqa: F401
from .frame import (  # noqa: F401
    ACK, BATCH, BLOB, CAPS, CAPS_ALL, DENSITIES, ERROR, EVENT, HELLO, PING,
    PONG, RESYNC, THEME_MODES, UPLOAD, VIEWPORT, WELCOME, EventFrame, Frame,
    Hello, Resume, Transfer, Viewport, Welcome, cap_bit, cap_mask, cap_names,
)
from .node import (  # noqa: F401
    EVENT_BY_CODE, EVENT_BY_NAME, KIND_BY_CODE, KIND_BY_NAME, FlatNode,
    Handler, Subtree, TextRef, Value, event_code, event_name, event_since,
    kind_code, kind_name,
)
from .op import Batch, Op  # noqa: F401
from .reader import Reader  # noqa: F401
from .style import ColorRef, Dim, StyleRecord  # noqa: F401
from .writer import Writer  # noqa: F401

#: The version this library speaks. Four: ``DefFont`` and the font roles it
#: binds took the protocol there. A session speaks ``min(client, server)``,
#: negotiated in the Welcome — the number a client names in its Hello is not
#: a field id and not a capability set, it is this.
PROTOCOL_VERSION = 4
