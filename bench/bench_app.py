"""The Python half of the comparison: the same application as the Ruby gem's
``bench/bench_app.rb`` and the Soli app beside it, node for node.

    ROWS=10000 PORT=5103 python3 bench/bench_app.py

Raw dicts rather than the DSL, so the files can be read side by side and the
trees compared line by line. What is being measured is what it costs each
server to turn this into bytes — not four different views.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from eui import App, Component, on  # noqa: E402

ROWS = int(os.environ.get("ROWS", "10000"))
WIDTHS = (90, 160, 90, 90)


class Bench(Component):
    def mount(self, params):
        super().mount(params)
        self.order = "asc"
        self.ticks = 0

    @on("sort")
    def sort(self, params):
        self.order = "desc" if self.order == "asc" else "asc"

    @on("tick")
    def tick(self, params):
        self.ticks += 1

    @staticmethod
    def cells(i):
        return (f"FA-{i}", f"Client {i % 37} SARL",
                "Paid" if i % 3 == 0 else "Open", f"{100 + i * 37} EUR")

    def row_node(self, i):
        values = self.cells(i)
        return {
            "k": "box",
            "key": f"r{i}",
            "s": {"display": "row", "gap": 4, "pad": [1, 3, 1, 3],
                  "border": [0, 0, 1, 0], "border_color": "border.subtle"},
            "c": [{"k": "text", "t": values[c],
                   "s": {"width": WIDTHS[c], "size": 1, "fg": "text.default"}}
                  for c in range(4)],
        }

    def render(self):
        ids = range(ROWS) if self.order == "asc" else range(ROWS - 1, -1, -1)
        rows = [self.row_node(i) for i in ids]
        return {
            "k": "box",
            "s": {"display": "column", "pad": 6, "gap": 3, "bg": "surface.base",
                  "width": "100%", "height": "100%"},
            "c": [
                {
                    "k": "box",
                    "s": {"display": "row", "gap": 3, "align": "center"},
                    "c": [
                        {"k": "text", "t": "Invoices", "s": {"size": 5, "weight": "bold"}},
                        {"k": "text", "t": str(self.ticks),
                         "s": {"size": 2, "fg": "text.muted", "font": "mono"}},
                        {"k": "spacer", "s": {"grow": 1}},
                        {
                            "k": "box",
                            "s": {"bg": "surface.raised", "radius": 2, "pad": [2, 4],
                                  "cursor": "pointer"},
                            "on": {"click": "sort"},
                            "c": [{"k": "text",
                                   "t": "Sort down" if self.order == "asc" else "Sort up",
                                   "s": {"weight": "medium"}}],
                        },
                        {
                            "k": "box",
                            "s": {"bg": "accent.base", "radius": 2, "pad": [2, 4],
                                  "cursor": "pointer"},
                            "on": {"click": "tick"},
                            "c": [{"k": "text", "t": "Tick",
                                   "s": {"fg": "accent.on", "weight": "medium"}}],
                        },
                    ],
                },
                {"k": "scroll", "s": {"grow": 1, "width": "100%"},
                 "c": [{"k": "box", "s": {"display": "column"}, "c": rows}]},
            ],
        }


if __name__ == "__main__":
    app = App(name="Bench", app_id="bench.eui-python")
    app.mount("bench", Bench)
    app.run(port=int(os.environ.get("PORT", "5103")))
