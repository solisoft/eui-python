# Changelog

## 0.1.0

The first cut: enough to write an EUI application in Python and have the
reference client draw it.

- `eui.proto` — the wire format of `spec/02`: varints, the 64-byte style
  record, nodes, values, handlers, ops, batches and every session frame,
  checked against the byte vectors the spec pins.
- `eui.view` — a view dict compiled into interned atoms, styles and colours,
  and diffed against the tree the client holds: keyed children reconcile
  through a Fenwick tree, so a ten-thousand-row sort is *n* moves and not a
  scan per row.
- `eui.session` / `eui.server` — the HTTP and WebSocket halves of `spec/01`:
  manifest, content-addressed assets, and a session that welcomes, mounts,
  patches, answers a ping and rebuilds on a resync.
- `eui.Component` — state, `@on` handlers, and a view that is a function of
  the state.
- `eui.blake3` and `eui.ed25519` — both in Python, because an asset is named
  by the hash of its content and a manifest is signed, and the standard
  library has neither. Checked against the official vectors.

Not yet: local handlers (`spec/07` bytecode), file transfers (`spec/01` §6),
session resume, and the windowed `list`'s `window` event.
