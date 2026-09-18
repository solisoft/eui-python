"""The whole thing, over a real socket: handshake, Hello, Welcome, Mount, an
event, and the patch it costs."""

import queue
import unittest
import urllib.request

from support import *  # noqa: F401,F403 - puts src/ on the path
from support import RunningApp, TestClient

from eui import App, Component, on
from eui.dsl import button, column, text
from eui.errors import ViewError
from eui.proto import (ACK, BATCH, ERROR, EVENT, PONG, PROTOCOL_VERSION,
                       EventFrame, Frame, Value, Viewport, event_code)
from eui.proto import op as opcodes


class Counter(Component):
    def mount(self, params):
        super().mount(params)
        self.count = 0

    @on("increment")
    def increment(self, params):
        self.count += 1

    def render(self):
        return column([text(str(self.count), size="2xl"), button("+", "increment")], gap=4)


class Fragile(Component):
    def mount(self, params):
        super().mount(params)
        self.state = "ok"

    @on("boom")
    def boom(self, params):
        raise RuntimeError("the handler fell over")

    @on("break_the_view")
    def break_the_view(self, params):
        self.state = "broken"

    @on("count")
    def count(self, params):
        self.state = "counted"

    def render(self):
        if self.state == "broken":
            raise ViewError("a role nobody defined")
        # Two handlers on the root, so a test can aim at one without the
        # button underneath answering first.
        return column([text(self.state), button("go", "count")],
                      on={"click": "boom", "double_click": "break_the_view"})


class Ticker(Component):
    def mount(self, params):
        super().mount(params)
        self.ticks = 0
        SEEN.put(self)

    def tick(self):
        self.ticks += 1
        self.refresh()

    def render(self):
        return column([text(f"ticks {self.ticks}")])


SEEN: queue.Queue = queue.Queue()


def counter_app():
    app = App(name="Counter", app_id="counter.test")
    app.mount("counter", Counter)
    return app


class SessionTest(unittest.TestCase):
    @staticmethod
    def first_click_node(mount_op):
        tree = mount_op["subtree"]
        for node in tree.nodes:
            for event, _handler in tree.handlers_of(node):
                if event == event_code("click"):
                    return node.id
        return None

    def test_a_session_welcomes_mounts_and_patches(self):
        with RunningApp(counter_app()) as port:
            client = TestClient(port, "/_eui/session/counter")
            client.hello()

            welcome = client.recv()
            self.assertEqual(0x02, welcome.kind)
            self.assertEqual(PROTOCOL_VERSION, welcome.body.version)
            self.assertEqual(16, len(welcome.body.session))
            self.assertFalse(welcome.body.resumed,
                             "a first Hello is answered with a session that starts empty")

            batch = client.recv()
            self.assertEqual(BATCH, batch.kind)
            self.assertEqual(1, batch.body.seq)
            mount = batch.body.ops[-1]
            self.assertEqual(opcodes.MOUNT, mount.opcode)

            node = self.first_click_node(mount)
            self.assertIsNotNone(node, "the button carries a click handler")

            client.click(node)
            patch = client.recv()
            self.assertEqual(BATCH, patch.kind)
            self.assertEqual(2, patch.body.seq)
            self.assertEqual([opcodes.SET_TEXT], [op.opcode for op in patch.body.ops],
                             "one changed number is one op")
            self.assertEqual("1", patch.body.ops[0]["text"].inline)
            client.close()

    def test_an_event_on_a_node_with_no_handler_is_dropped(self):
        with RunningApp(counter_app()) as port:
            client = TestClient(port, "/_eui/session/counter")
            client.hello()
            client.recv()
            client.recv()
            client.click(9999)
            self.assertIsNone(client.recv(timeout=0.4),
                              "nothing is looked up and nothing is answered")
            client.close()

    def test_a_ping_is_answered(self):
        with RunningApp(counter_app()) as port:
            client = TestClient(port, "/_eui/session/counter")
            client.hello()
            client.recv()
            client.recv()
            client.send(Frame.ping(b"12345678"))
            pong = client.recv()
            self.assertEqual(PONG, pong.kind)
            self.assertEqual(b"12345678", pong.body, "the nonce comes back as it went")

            # An ack changes nothing and is answered with nothing.
            client.send(Frame.ack(1))
            self.assertIsNone(client.recv(timeout=0.3))
            client.close()

    def test_a_resync_is_answered_with_a_fresh_mount(self):
        with RunningApp(counter_app()) as port:
            client = TestClient(port, "/_eui/session/counter")
            client.hello()
            client.recv()
            client.recv()
            client.send(Frame.resync())
            batch = client.recv()
            self.assertEqual(opcodes.MOUNT, batch.body.ops[-1].opcode)
            client.close()

    def test_the_asset_endpoint_serves_by_content(self):
        app = counter_app()
        digest = app.assets.add_bytes(b"some bytes", content_type="image/png")
        with RunningApp(app) as port:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/_eui/asset/{digest.hex()}") as response:
                self.assertEqual(200, response.status)
                self.assertEqual(b"some bytes", response.read())
                self.assertEqual("public, max-age=31536000, immutable",
                                 response.headers["cache-control"])
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/_eui/asset/{'0' * 64}")
                self.fail("a hash this server does not hold is a 404, never a redirect")
            except urllib.error.HTTPError as error:
                self.assertEqual(404, error.code)

    def test_a_session_that_does_not_exist_is_a_404(self):
        with RunningApp(counter_app()) as port:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/_eui/session/nobody")
                self.fail("expected a 404")
            except urllib.error.HTTPError as error:
                self.assertEqual(404, error.code)

    # ----------------------------------------------------- what goes wrong
    @staticmethod
    def fragile_app():
        app = App(name="Fragile", app_id="fragile.test")
        app.mount("fragile", Fragile)
        return app

    def connected(self, port, path="/_eui/session/fragile"):
        client = TestClient(port, path)
        client.hello()
        client.recv()          # welcome
        return client, client.recv()   # and the mount

    def test_a_handler_that_raises_does_not_end_the_session(self):
        with RunningApp(self.fragile_app()) as port:
            client, mount = self.connected(port)
            root = mount.body.ops[-1]["subtree"].nodes[0].id

            client.send(Frame.event(EventFrame(root, event_code("click"), 0, Value.null())))
            # The state did not change, so the view did not change, so there
            # is nothing to send. The session is still up, which is the point.
            self.assertIsNone(client.recv(timeout=0.4))

            client.send(Frame.viewport(Viewport(500, 400, 100, 1, 1, 100)))
            self.assertIsNone(client.recv(timeout=0.4),
                              "a viewport this view ignores costs nothing either")
            client.close()

    def test_a_view_that_cannot_be_encoded_ends_the_session_with_a_reason(self):
        with RunningApp(self.fragile_app()) as port:
            client, mount = self.connected(port)
            root = mount.body.ops[-1]["subtree"].nodes[0].id

            client.send(Frame.event(EventFrame(root, event_code("double_click"), 0, Value.null())))
            error = client.recv()
            self.assertEqual(ERROR, error.kind)
            self.assertEqual(400, error.body[0])
            self.assertIn("a role nobody defined", error.body[1])
            client.close()

    def test_a_render_can_be_asked_for_from_outside_the_socket(self):
        app = App(name="Ticker", app_id="ticker.test")
        app.mount("ticker", Ticker)
        with RunningApp(app) as port:
            client = TestClient(port, "/_eui/session/ticker")
            client.hello()
            client.recv()
            client.recv()

            component = SEEN.get(timeout=3)
            component.tick()
            batch = client.recv()
            self.assertEqual([opcodes.SET_TEXT], [op.opcode for op in batch.body.ops])
            self.assertEqual("ticks 1", batch.body.ops[0]["text"].inline)
            client.close()

    def test_a_notification_is_an_op_like_any_other(self):
        app = App(name="Ticker", app_id="notify.test")
        app.mount("ticker", Ticker)
        with RunningApp(app) as port:
            client = TestClient(port, "/_eui/session/ticker")
            client.hello()
            client.recv()
            client.recv()
            SEEN.get(timeout=3).notify("Two replies", body="in this thread", tag="thread-7")
            batch = client.recv()
            op = batch.body.ops[0]
            self.assertEqual(opcodes.NOTIFY, op.opcode)
            self.assertEqual("Two replies", op["title"])
            self.assertEqual("thread-7", op["tag"])
            client.close()

    def test_the_viewport_reaches_the_component(self):
        class Sizer(Component):
            def render(self):
                return column([text(f"{self.width} x {self.height}")])

        app = App(name="Sizer", app_id="sizer.test")
        app.mount("sizer", Sizer)
        with RunningApp(app) as port:
            client = TestClient(port, "/_eui/session/sizer")
            client.hello(width=1280, height=900)
            client.recv()
            mount = client.recv()
            node = next(n for n in mount.body.ops[-1]["subtree"].nodes if n.text)
            self.assertEqual("1280 x 900", node.text.inline, "the Hello carried it")

            client.send(Frame.viewport(Viewport(640, 480, 100, 0, 1, 100)))
            batch = client.recv()
            self.assertEqual("640 x 480", batch.body.ops[0]["text"].inline,
                             "and a resize follows on its own")
            client.close()


if __name__ == "__main__":
    unittest.main()
