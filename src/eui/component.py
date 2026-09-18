"""One component: state, handlers, and a view that is a function of the state.

    class Counter(Component):
        def mount(self, params):
            super().mount(params)     # the window, as it is right now
            self.count = 0

        @on("increment")
        def increment(self, params):
            self.count += 1

        def render(self):
            return column([text(str(self.count), size="4xl"),
                           button("+", "increment")], gap=4, pad=8)

A handler changes state and returns; it never touches the tree. What reaches
the client is the *difference* the change made, which is the one thing this
protocol is for.
"""

from __future__ import annotations

from .errors import EUIError


def on(name: str):
    """Name an event this component answers. The name is the one the view
    put on a node, not the event kind: ``{"on": {"wake": "tick"}}`` arrives
    here as ``tick``."""
    def decorate(method):
        existing = getattr(method, "_eui_events", ())
        method._eui_events = (*existing, str(name))
        return method
    return decorate


class Component:
    def __init__(self, session=None) -> None:
        self.session = session
        self.viewport: dict = {}

    # -- lifecycle ----------------------------------------------------------
    def mount(self, params: dict) -> None:
        """Called once, when the socket has said Hello. ``params["viewport"]``
        is the window as it is right now; a ``viewport`` event follows every
        resize, so nothing has to ask."""
        self.viewport = params.get("viewport") or {}

    def unmount(self) -> None:
        """Called when the session ends, for whatever reason."""

    def render(self) -> dict:
        """The view: a dict, and a pure function of the state. It is called
        after every handler, so it must be cheap and must not have effects."""
        raise NotImplementedError(f"{type(self).__name__} has no render")

    # -- dispatch -----------------------------------------------------------
    @classmethod
    def handlers(cls) -> dict[str, str]:
        found: dict[str, str] = {}
        for klass in reversed(cls.__mro__):
            for attribute, value in vars(klass).items():
                for event in getattr(value, "_eui_events", ()):
                    found[event] = attribute
        return found

    def handle(self, name: str, params: dict):
        """A method decorated with ``@on`` wins; otherwise a public method of
        the same name; otherwise the event is dropped with a line in the log,
        because a view naming a handler nobody wrote is a typo and not a
        reason to end somebody's session."""
        if name == "viewport" and params.get("viewport"):
            self.viewport = params["viewport"]

        attribute = type(self).handlers().get(name)
        if attribute is not None:
            return getattr(self, attribute)(params)

        method = getattr(self, name, None)
        if callable(method):
            try:
                return method(params)
            except TypeError:
                return method()
        if name == "viewport":
            return None
        raise EUIError(f"no handler for '{name}'")

    # -- what a component may ask of its session ----------------------------
    def refresh(self) -> None:
        """Render again although nothing arrived: a timer, a message from
        another session, anything this process knows and the client does not."""
        if self.session:
            self.session.refresh()

    def notify(self, title: str, body: str = "", tag: str = "") -> None:
        if self.session:
            self.session.notify(title, body=body, tag=tag)

    def close(self, reason: str = "the application closed the session") -> None:
        if self.session:
            self.session.close(reason)

    def granted(self, capability: str) -> bool:
        """Whether the person granted this application something it asked
        for. Being granted is a separate act from asking, and this is the
        only place the answer shows up."""
        return bool(self.session and self.session.granted(capability))

    # -- the window ---------------------------------------------------------
    @property
    def width(self) -> int:
        return int(self.viewport.get("width", 0))

    @property
    def height(self) -> int:
        return int(self.viewport.get("height", 0))

    @property
    def dark(self) -> bool:
        return self.viewport.get("mode") == "dark"
