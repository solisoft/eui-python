"""EUI in Python: an application interface delivered over HTTPS without HTML,
CSS or JavaScript.

The server sends an interface tree that is **already resolved**, in a compact
binary encoding; a native client applies it, lays it out and draws it on the
GPU. There is no tolerant parse, no cascade to resolve and no script to run
at the other end — which is why a view here is a dict and a style is a flat,
closed vocabulary rather than a language.

The protocol is specified in ``spec/`` of the EUI repository; every module in
this package names the section it implements.
"""

from . import blake3, dsl, ed25519, proto, theme  # noqa: F401
from .app import App  # noqa: F401
from .assets import Assets  # noqa: F401
from .component import Component, on  # noqa: F401
from .dsl import (box, button, canvas, card, column, divider, field, icon,  # noqa: F401
                  image, input_, keyed, list_, node, overlay, row, scroll,
                  sizer, spacer, stack, text, textarea)
from .errors import DecodeError, EUIError, ViewError  # noqa: F401
from .manifest import Manifest  # noqa: F401
from .proto import PROTOCOL_VERSION  # noqa: F401
from .server import Server  # noqa: F401
from .session import Session  # noqa: F401
from .view import Encoder  # noqa: F401
from .websocket import WebSocket  # noqa: F401

__version__ = "0.1.0"
