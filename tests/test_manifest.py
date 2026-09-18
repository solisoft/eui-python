"""The manifest: what a client reads before it opens a session, and the
signature that makes trust-on-first-use mean anything."""

import os
import stat
import tempfile
import unittest

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui import ed25519
from eui.errors import EUIError
from eui.manifest import Manifest, publisher_key
from eui.proto import Reader, Value, cap_mask


class ManifestTest(unittest.TestCase):
    SECRET = bytes(range(32))

    def manifest(self, **overrides):
        options = dict(app_id="counter.test", name="Counter", secret=self.SECRET,
                       entry="/_eui/session/counter")
        options.update(overrides)
        return Manifest(**options)

    def test_the_record_is_signed_over_everything_but_the_signature(self):
        m = self.manifest()
        raw = m.encode()
        self.assertEqual(b"EUIM", raw[:4])
        self.assertEqual(1, raw[4], "record version")

        signature = bytes.fromhex(raw[-128:].decode("ascii"))
        public = ed25519.public_key(self.SECRET)
        self.assertTrue(ed25519.verify(public, m.signed_bytes(), signature))
        self.assertFalse(ed25519.verify(public, m.signed_bytes() + b"x", signature))

    def test_the_fields_are_in_key_order(self):
        r = Reader(self.manifest().encode())
        r.take(4)
        r.u8()
        count = r.varint()
        self.assertEqual(11, count, "a manifest has exactly eleven fields")
        for expected in range(count):
            self.assertEqual(expected, r.varint(), "fields are in key order")
            Value.decode(r)
        r.finish()

    def test_capabilities_are_a_bitset_of_names(self):
        m = self.manifest(capabilities=["net.open", "notifications"])
        self.assertEqual(cap_mask(["net.open", "notifications"]), m.capabilities)
        self.assertEqual(["notifications", "net.open"], m.capability_names())

    def test_a_publisher_key_is_made_once_and_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config", "eui_publisher.key")
            first = publisher_key(path)
            self.assertTrue(os.path.exists(path))
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(0o600, mode, "whoever holds it can publish as this application")
            self.assertEqual(first, publisher_key(path))

    def test_a_string_field_is_bounded(self):
        with self.assertRaises(EUIError):
            self.manifest(name="x" * 300).encode()


if __name__ == "__main__":
    unittest.main()
