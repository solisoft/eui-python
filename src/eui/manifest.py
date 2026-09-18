"""The application manifest served at ``/.well-known/eui`` (01 §2.1): an
``EUIM`` record, signed by the publisher, that a client reads before it
opens a session.

The signature is what makes trust-on-first-use mean anything: the client
pins ``publisher_key`` against ``app_id`` on first run and refuses a
different one later. So the key belongs to the *application*, not to a
deployment — keep the file, and keep it out of the repository.
"""

from __future__ import annotations

import os

from . import ed25519
from .assets import Assets
from .errors import EUIError
from .proto import PROTOCOL_VERSION, Value, Writer, cap_mask, cap_names

MAGIC = b"EUIM"
RECORD_VERSION = 1
MAX_STR = 256

KEY_APP_ID = 0
KEY_NAME = 1
KEY_VERSION = 2
KEY_PROTOCOL_MIN = 3
KEY_PROTOCOL_MAX = 4
KEY_PUBLISHER_KEY = 5
KEY_CAPABILITIES = 6
KEY_THEME = 7
KEY_ENTRY = 8
KEY_ROTATION = 9
KEY_SIGNATURE = 10


def publisher_key(path: str) -> bytes:
    """An Ed25519 secret kept on disk, generated on first use. Never
    committed: whoever holds it can publish as this application."""
    if os.path.exists(path):
        with open(path, "rb") as handle:
            raw = handle.read().strip()
        if len(raw) == 64:  # written as hex by an older run, or by hand
            return bytes.fromhex(raw.decode("ascii"))
        if len(raw) != 32:
            raise EUIError(f"{path} is not a 32-byte Ed25519 secret")
        return raw
    secret = ed25519.generate_secret()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "wb") as out:
        out.write(secret)
    return secret


class Manifest:
    def __init__(self, *, app_id: str, name: str, secret: bytes, version: str = "0.1.0",
                 entry: str = "/_eui/session", capabilities=0, theme: bytes | None = None,
                 protocol_min: int = 1, protocol_max: int = PROTOCOL_VERSION) -> None:
        self.app_id = app_id
        self.name = name
        self.version = version
        self._secret = secret
        self.entry = entry
        self.capabilities = cap_mask(capabilities)
        self.theme = theme
        self.protocol_min = protocol_min
        self.protocol_max = protocol_max

    @property
    def public_key_hex(self) -> str:
        return ed25519.public_key(self._secret).hex()

    def signed_bytes(self) -> bytes:
        """The bytes the publisher signs: every field but the signature. A
        decoder rebuilds them exactly, because the order is fixed."""
        return self._write(None)

    def encode(self) -> bytes:
        return self._write(ed25519.sign(self._secret, self.signed_bytes()))

    def capability_names(self) -> list[str]:
        """What this application asks for. Being granted them is a separate
        act, and one this server never hears the answer to unless the client
        reports it in its Hello."""
        return cap_names(self.capabilities)

    def _write(self, signature: bytes | None) -> bytes:
        fields = [
            (KEY_APP_ID, Value.str_(self._checked(self.app_id, "app_id"))),
            (KEY_NAME, Value.str_(self._checked(self.name, "name"))),
            (KEY_VERSION, Value.str_(self._checked(self.version, "version"))),
            (KEY_PROTOCOL_MIN, Value.int_(self.protocol_min)),
            (KEY_PROTOCOL_MAX, Value.int_(self.protocol_max)),
            (KEY_PUBLISHER_KEY, Value.str_(self.public_key_hex)),
            (KEY_CAPABILITIES, Value.int_(self.capabilities)),
            (KEY_THEME, Value.str_(Assets.hex(self.theme)) if self.theme else Value.null()),
            (KEY_ENTRY, Value.str_(self._checked(self.entry, "entry"))),
            # No rotation: this key has never been anything else. When one
            # is needed it is the previous key and its signature over the new
            # one, and the client's pin moves rather than the session failing.
            (KEY_ROTATION, Value.null()),
        ]
        if signature is not None:
            fields.append((KEY_SIGNATURE, Value.str_(signature.hex())))

        w = Writer()
        w.raw(MAGIC).u8(RECORD_VERSION).varint(len(fields))
        for key, value in fields:
            w.varint(key)
            value.encode(w)
        return w.to_bytes()

    @staticmethod
    def _checked(value, what: str) -> str:
        string = str(value)
        if len(string.encode("utf-8")) > MAX_STR:
            raise EUIError(f"manifest {what} is at most {MAX_STR} bytes")
        return string
