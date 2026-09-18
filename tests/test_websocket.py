"""RFC 6455, with only what a session needs."""

import base64
import hashlib
import socket
import unittest

from support import *  # noqa: F401,F403 - puts src/ on the path

from eui.websocket import GUID, ProtocolError, WebSocket, accept_key


def masked(payload: bytes, opcode: int = 0x2, fin: bool = True) -> bytes:
    mask = bytes([1, 2, 3, 4])
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    head = bytes([(0x80 if fin else 0x00) | opcode, 0x80 | len(payload)])
    return head + mask + body


class WebSocketTest(unittest.TestCase):
    def pair(self):
        server, client = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        return WebSocket(server, "/x", {}), client

    def test_the_accept_key_is_the_rfc_example(self):
        # The one number in this file worth pinning: an accept key that is
        # wrong by a character is a handshake every client refuses, and the
        # error it prints points at the server's key rather than at the GUID.
        self.assertEqual("s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", accept_key("dGhlIHNhbXBsZSBub25jZQ=="))
        self.assertEqual(
            base64.b64encode(hashlib.sha1(("k" + GUID).encode()).digest()).decode(),
            accept_key("k"))

    def test_a_masked_binary_message_arrives_whole(self):
        ws, client = self.pair()
        client.sendall(masked(b"hello"))
        self.assertEqual(("binary", b"hello"), ws.recv())

    def test_a_fragmented_message_is_reassembled(self):
        ws, client = self.pair()
        client.sendall(masked(b"he", fin=False))
        client.sendall(masked(b"llo", opcode=0x0))
        self.assertEqual(("binary", b"hello"), ws.recv())

    def test_a_ping_is_answered_without_surfacing(self):
        ws, client = self.pair()
        client.sendall(masked(b"ab", opcode=0x9))
        client.sendall(masked(b"done"))
        self.assertEqual(("binary", b"done"), ws.recv())
        self.assertEqual(0x8A, client.recv(16)[0], "pong")

    def test_an_unmasked_client_frame_is_refused(self):
        ws, client = self.pair()
        client.sendall(bytes([0x82, 0x01]) + b"x")
        with self.assertRaises(ProtocolError):
            ws.recv()

    def test_a_close_ends_the_stream(self):
        ws, client = self.pair()
        client.sendall(masked(b"", opcode=0x8))
        self.assertIsNone(ws.recv())

    def test_what_the_server_writes_is_not_masked(self):
        ws, client = self.pair()
        ws.send_binary(b"hi")
        frame = client.recv(16)
        self.assertEqual(0x82, frame[0])
        self.assertEqual(2, frame[1], "no mask bit, length 2")
        self.assertEqual(b"hi", frame[2:4])

    def test_a_long_message_uses_the_extended_length(self):
        ws, client = self.pair()
        ws.send_binary(b"x" * 200)
        frame = client.recv(4)
        self.assertEqual(126, frame[1])
        self.assertEqual(200, int.from_bytes(frame[2:4], "big"))


if __name__ == "__main__":
    unittest.main()
