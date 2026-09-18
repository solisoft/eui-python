# eui-python

EUI applications, written in Python.

[EUI](https://github.com/solisoft/eui) delivers an application interface over
HTTPS without HTML, CSS or JavaScript. The server sends a tree that is
**already resolved**, in a compact binary encoding; a native client applies
it, lays it out and draws it on the GPU. There is no tolerant parse at the
other end, no cascade to resolve, no script to run.

This package is the server half: the wire format, the view encoder and its
diff, the session, the content-addressed asset store, the signed manifest,
and a component model in which a view is a dict and a handler changes state.
**No runtime dependencies** — BLAKE3 and Ed25519 are in here, because the
standard library has neither and an asset is named by the hash of its content.

```python
from eui import App, Component, on
from eui.dsl import button, column, text


class Counter(Component):
    def mount(self, params):
        super().mount(params)      # the window, as it is right now
        self.count = 0

    @on("increment")
    def increment(self, params):
        self.count += 1

    def render(self):
        return column([
            text(str(self.count), size="4xl", weight="bold"),
            button("Increment", "increment"),
        ], gap=5, align="center", justify="center",
           width="100%", height="100%", bg="surface.base")


app = App(name="Counter", app_id="counter.example")
app.mount("counter", Counter)
app.run(port=5098)
```

```
EUI_ALLOW_INSECURE_LOOPBACK=1 eui ws://127.0.0.1:5098/_eui/session/counter
```

Pressing the button sends **one** `SetText`. Not a page, not a diffed DOM,
not a frame of JSON: the style records went once, at mount, and every later
render names them by id.

## What a view is

A dict, and a pure function of the state. `eui.dsl` builds them, but there is
nothing behind the helpers: print one and you have the whole document.

```python
{"k": "box", "s": {"display": "column", "gap": 4},
 "c": [{"k": "text", "t": "Hi", "s": {"size": "lg"}}]}
```

- **`k`** one of the seventeen primitive kinds. Everything a person would
  call a widget — button, dialog, table, date picker — is composed from these
  on the server, which is why the catalogue grows without shipping a new
  client.
- **`s`** a flat style dict in the spec's own vocabulary. An unknown key is
  an error, not a key that does nothing.
- **`c`** children, **`t`** text, **`p`** props, **`on`** handlers, **`key`**
  identity for reconciliation.

**[eui.solisoft.net/components](https://eui.solisoft.net/components) is the
reference for all of it** — every node key, all seventeen kinds, every style
key and all thirty-three colour roles, the event names, and the catalogue of
composed widgets, each shown with the hash it returns. It is written against
Soli, and the vocabulary is the protocol's, so a style hash on that page is
the same style dict here. Also worth the visit:
[the running demo](https://eui.solisoft.net/demo),
[the controls](https://eui.solisoft.net/controls) every widget is built from,
and [what is not there yet](https://eui.solisoft.net/gaps).

Colour is a **role** — `surface.raised`, `text.muted`, `danger.base` — never
a literal. The client resolves it against the viewer's theme, so the page is
right in dark mode *without this server ever learning which mode they are
in*. Sizes are scale indices, by index or by name: `size="lg"`, `gap=4`,
`radius="md"`.

This package ships the primitives plus a few composed widgets — `button`,
`card`, `field`, `divider`, `spacer`. Anything else in the catalogue is a
function that returns a dict, so it ports by writing the same dict.

## What a handler is

A method, named by the **view** rather than by the event kind —
`{"on": {"wake": "tick"}}` arrives as `tick`. It changes state and returns;
it never touches the tree.

```python
@on("pick")
def pick(self, params):
    self.selected = params["props"]["id"]
```

`params` carries `node`, `kind`, `payload` and `props` — the node's props as
the server last rendered them. That last one is what lets one handler serve
ten thousand rows: put the identifying value on the node, not in the
handler's name.

A handler that raises leaves the state unchanged and the screen right; it is
a line in the log, not the end of somebody's session. A **view** that cannot
be encoded is the other way round: it fails identically on every later
render, so the session ends with `Error 400` and the reason.

## Running it

| | |
|---|---|
| `app.run(port=5098)` | plain `ws://` on loopback, for development |
| `app.run(host="0.0.0.0", port=443, tls={"cert": ..., "key": ...})` | TLS 1.3, the protocol's floor |

A release client refuses `ws://` outright; a debug one takes
`EUI_ALLOW_INSECURE_LOOPBACK=1`. `EUI_TRACE=1` on the server prints every
frame and every event a session sees, which is the first thing to reach for
when a click does nothing.

Three endpoints and nothing else: `/.well-known/eui` (the signed manifest),
`/_eui/asset/<blake3-hex>` (content-addressed, immutable, served to anyone),
and the session itself.

```python
app = App(name="Books", app_id="books.example",
          key_path="config/eui_publisher.key",
          capabilities=["net.open"])
app.font("Space Grotesk", ["public/fonts/space-grotesk-400.ttf",
                           "public/fonts/space-grotesk-700.ttf"])
```

The publisher key is a PKCS#8 PEM — the same file the Ruby, PHP and Node
libraries read, so an application that changes language keeps its identity
and nobody's pin breaks. It is generated on first use and kept: a client pins
it against `app_id` on first run and refuses a different one later.

## What is here, and what is not

Implemented: the whole wire format of `spec/02` (checked against the byte
vectors that document pins, the §8 example at exactly 150 bytes included),
the session frames of `spec/01`, the view encoder with its append-only
tables, a keyed diff that reorders through a Fenwick tree, assets, fonts,
notifications, `scroll_to` and `focus_to`, and the manifest.

Not yet:

- **Local handlers** (`spec/07`). A handler is a server round trip; the
  bytecode a client runs for itself — the hover that answers without a
  packet — is the next milestone.
- **File transfers** (`spec/01` §6): `Upload` and `Blob` are decoded and
  dropped.
- **Session resume** (`spec/01` §4.1): a socket that breaks gets a fresh
  session and a fresh `Mount`, which is a conforming answer and a worse one.
- The windowed `list`'s `window` event, and `scene` uniforms.

## Against the other six

The same application, written seven times — in Soli, Ruby, Python, PHP,
JavaScript, Go and Rust, node for node. At 500 rows, one number changed:
**9 bytes** out of every one of them, between 2.5 and 37 ms, and a memory
column that ranks what else is in the process rather than the language.
[`bench/README.md`](https://github.com/solisoft/eui-ruby/blob/main/bench/README.md)
has the tables, the method and the caveats; the applications and the one
driver that measures all seven live in `clients/eui-ruby/bench` of the
[EUI repository](https://github.com/solisoft/eui).

## Tests

```
python3 -m unittest discover -s tests -t tests
```

103 of them, and the ones worth reading are `tests/test_proto.py` — the
spec's own §8 example, 150 bytes, byte for byte — `tests/test_apply.py`,
which is a client in forty lines that applies the server's ops and compares
the tree it ends up holding with the server's own, over every permutation of
five keyed rows and four hundred and eighty random edits, and
`tests/test_session.py`, which runs a real application on a real socket and
counts the ops a click costs.

## Licence

MIT.
