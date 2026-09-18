"""BLAKE3 and Ed25519, against the official vectors.

An asset's name *is* its content and a manifest's signature is what makes
trust-on-first-use mean anything, so these are the two pieces of arithmetic
in the library that another implementation will disagree with loudly.
"""

import os
import tempfile
import unittest

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui import blake3, ed25519


class Blake3Test(unittest.TestCase):
    # The input of length n is the bytes 0, 1, 2 … 250 repeating, which is
    # what the BLAKE3 test vectors use.
    VECTORS = {
        0: "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262",
        63: "e9bc37a594daad83be9470df7f7b3798297c3d834ce80ba85d6e207627b7db7b",
        64: "4eed7141ea4a5cd4b788606bd23f46e212af9cacebacdc7d1f4c6dc7f2511b98",
        65: "de1e5fa0be70df6d2be8fffd0e99ceaa8eb6e8c93a63f2d8d1c30ecb6b263dee",
        1023: "10108970eeda3eb932baac1428c7a2163b0e924c9a9e25b35bba72b28f70bd11",
        1024: "42214739f095a406f3fc83deb889744ac00df831c10daa55189b5d121c855af7",
        1025: "d00278ae47eb27b34faecf67b4fe263f82d5412916c1ffd97c8cb7fb814b8444",
        2048: "e776b6028c7cd22a4d0ba182a8bf62205d2ef576467e838ed6f2529b85fba24a",
        2049: "5f4d72f40d7a5f82b15ca2b2e44b1de3c2ef86c426c95c1af0b6879522563030",
        3000: "5fade288bf27444bee55ba2babb98c3c922c1e84c2e445e7d1f6da24756f5060",
        4096: "015094013f57a5277b59d8475c0501042c0b642e531b0a1c8f58d2163229e969",
        10000: "5f81f9e4ab67627b6b036d5d4e3bc40d9d3daa6fcc2b6dd07ab2bbf0a877da54",
        65536: "68d647e619a930e7b1082f74f334b0c65a315725569bdc123f0ee11881717bfe",
    }

    @staticmethod
    def pattern(length: int) -> bytes:
        return bytes(i % 251 for i in range(length))

    def test_the_vectors(self):
        for length, want in self.VECTORS.items():
            self.assertEqual(want, blake3.hexdigest(self.pattern(length)), f"length {length}")

    def test_abc(self):
        self.assertEqual("6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85",
                         blake3.hexdigest(b"abc"))

    def test_streaming_matches_one_shot(self):
        """A chunk is 1024 bytes and a block is 64: fed in sevens, every
        boundary falls somewhere awkward, which is where a streaming hash
        breaks."""
        data = self.pattern(3000)
        hasher = blake3.Hasher()
        for i in range(0, len(data), 7):
            hasher.update(data[i:i + 7])
        self.assertEqual(blake3.hexdigest(data), hasher.hexdigest())

    def test_a_file_hashes_as_its_bytes(self):
        data = self.pattern(5000)
        handle, path = tempfile.mkstemp()
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            self.assertEqual(blake3.digest(data), blake3.digest_file(path))
        finally:
            os.unlink(path)


class Ed25519Test(unittest.TestCase):
    # RFC 8032 §7.1, test 1 and test 2.
    SECRET_1 = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    PUBLIC_1 = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    SIGNATURE_1 = ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e0652249015"
                   "55fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")

    def test_the_rfc_vector(self):
        self.assertEqual(self.PUBLIC_1, ed25519.public_key(self.SECRET_1).hex())
        self.assertEqual(self.SIGNATURE_1, ed25519.sign(self.SECRET_1, b"").hex())

    def test_a_signature_verifies_and_a_changed_message_does_not(self):
        secret = ed25519.generate_secret()
        public = ed25519.public_key(secret)
        signature = ed25519.sign(secret, b"EUI manifest")
        self.assertTrue(ed25519.verify(public, b"EUI manifest", signature))
        self.assertFalse(ed25519.verify(public, b"EUI manifesto", signature))
        self.assertFalse(ed25519.verify(public, b"EUI manifest", bytes(64)))

    def test_the_key_file_is_the_pem_every_implementation_reads(self):
        """A PKCS#8 PEM, so an application that changes language keeps its
        identity and nobody's pin breaks."""
        from eui.manifest import publisher_key, secret_from_pem, secret_to_pem

        secret = ed25519.generate_secret()
        pem = secret_to_pem(secret)
        self.assertTrue(pem.startswith("-----BEGIN PRIVATE KEY-----"))
        self.assertEqual(secret, secret_from_pem(pem))

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "written-elsewhere.pem")
            with open(path, "w") as handle:
                handle.write(pem)
            self.assertEqual(secret, publisher_key(path))

    def test_a_key_is_32_bytes_and_a_signature_64(self):
        secret = ed25519.generate_secret()
        self.assertEqual(32, len(secret))
        self.assertEqual(32, len(ed25519.public_key(secret)))
        self.assertEqual(64, len(ed25519.sign(secret, b"x")))


if __name__ == "__main__":
    unittest.main()
