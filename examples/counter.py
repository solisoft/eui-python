"""The counter, as an EUI application in Python.

    python3 examples/counter.py
    EUI_ALLOW_INSECURE_LOOPBACK=1 eui ws://127.0.0.1:5098/_eui/session/counter

What to look at: ``render`` is a pure function of ``self.count``, and
pressing a button sends one ``SetText`` — not a page, not a diffed DOM, not a
frame of JSON. The style records were sent once, at mount, and every later
render references them by id.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from eui import App, Component, on  # noqa: E402
from eui.dsl import button, column, divider, row, text  # noqa: E402


class Counter(Component):
    def mount(self, params):
        super().mount(params)
        self.count = 0

    @on("increment")
    def increment(self, params):
        self.count += 1

    @on("decrement")
    def decrement(self, params):
        self.count -= 1

    @on("reset")
    def reset(self, params):
        self.count = 0

    def render(self):
        return column([
            text("COUNTER", size="sm", weight="semibold", fg="text.muted", font="mono"),
            text(str(self.count), size="4xl", weight="bold", fg=self._tone()),
            row([
                button("−", "decrement", tone="quiet", size="lg"),
                button("Reset", "reset", tone="quiet"),
                button("+", "increment", size="lg"),
            ], gap=4),
            divider(width=320),
            text(self._footnote(), size="xs", fg="text.muted", font="mono"),
        ], display="column", justify="center", align="center", gap=7,
           bg="surface.base", width="100%", height="100%", pad=8)

    # Colour by what the number *is*, so it reads as a legend rather than
    # decoration — and reads right in either theme, because the client
    # resolves the role and this server never learns which one they are in.
    def _tone(self):
        if self.count < 0:
            return "danger.base"
        if self.count == 0:
            return "text.muted"
        return "success.base"

    def _footnote(self):
        return f"{self.width} × {self.height} · {self.viewport.get('mode', 'light')}"


if __name__ == "__main__":
    app = App(name="Counter", app_id="counter.eui-python")
    app.mount("counter", Counter)
    app.run(port=int(os.environ.get("PORT", "5098")))
