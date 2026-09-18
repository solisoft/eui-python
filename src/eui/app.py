"""An application: the components it serves, the assets they name, and the
manifest that says who published it.

    app = App(name="Counter", app_id="counter.example")
    app.mount("counter", Counter)
    app.run(port=5012)

The first component mounted is the one a bare origin opens, because the
protocol has one entry and a server here has many: ``wss://host`` has to mean
something, and the first one in the file is what a person reading it top to
bottom would say.
"""

from __future__ import annotations

import os

from .assets import Assets
from .manifest import Manifest, publisher_key
from .proto import PROTOCOL_VERSION
from .server import Server


class App:
    def __init__(self, *, name: str, app_id: str | None = None, version: str = "0.1.0",
                 root: str | None = None, key_path: str | None = None,
                 capabilities=(), logger=None) -> None:
        self.name = name
        self.app_id = app_id or "".join(
            c if c.isalnum() else "-" for c in name.lower()).strip("-")
        self.version = version
        self.root = root or os.getcwd()
        self.assets = Assets(root=self.root)
        self.components: dict[str, type] = {}
        self.capabilities = capabilities
        self.key_path = key_path
        self.fonts: dict[str, list[bytes]] = {}
        self.logger = logger
        self._default: str | None = None
        self._manifest: Manifest | None = None

    def mount(self, path: str, component_class: type) -> "App":
        name = str(path).lstrip("/")
        self.components[name] = component_class
        if self._default is None:
            self._default = name
        return self

    def component_for(self, name: str | None) -> type | None:
        if name is None:
            return self.components.get(self._default or "")
        return self.components.get(name)

    def font(self, family: str, paths) -> str:
        """The face an application draws in, bound to a font role and sent as
        content-addressed assets — so the window talks to no font service and
        opens no connection the session did not.

        Declaring one at boot is what takes the manifest's floor to EUI 4;
        calling it later falls back to ``sans`` for the sessions already open
        rather than ending them.
        """
        self.fonts[family] = [self.assets.add_file(path) for path in paths]
        return family

    @property
    def manifest(self) -> Manifest | None:
        """The signed record at ``/.well-known/eui``. Without a key path
        there is no manifest, which a debug client on loopback accepts and a
        release client does not."""
        if self.key_path is None:
            return None
        if self._manifest is None:
            self._manifest = Manifest(
                app_id=self.app_id, name=self.name, version=self.version,
                secret=publisher_key(self.key_path),
                entry=f"/_eui/session/{self._default}",
                capabilities=self.capabilities,
                protocol_min=1, protocol_max=PROTOCOL_VERSION)
        return self._manifest

    def run(self, host: str = "127.0.0.1", port: int = 5012, tls: dict | None = None) -> None:
        Server(self, host=host, port=port, tls=tls, logger=self.logger).start()
