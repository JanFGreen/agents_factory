"""Local preview of a built web candidate, so UI reviewers can look at it in a real browser.

The controller serves the static build that the trusted gate produced in the worktree (e.g.
``dist/`` from ``expo export``) on 127.0.0.1. Unknown paths fall back to ``index.html`` for
single-page apps. Serving is read-only; the build itself is never run by this module.
"""

from __future__ import annotations

import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _Handler(SimpleHTTPRequestHandler):
    spa_fallback = True

    def translate_path(self, path: str) -> str:
        resolved = super().translate_path(path)  # stays inside `directory` (no traversal)
        if self.spa_fallback and not os.path.exists(resolved) and not os.path.exists(resolved + ".html"):
            return os.path.join(self.directory, "index.html")
        if not os.path.exists(resolved) and os.path.exists(resolved + ".html"):
            return resolved + ".html"  # static exports write /settings as settings.html
        return resolved

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (http.server API)
        pass


class PreviewServer:
    def __init__(self, directory: Path, port: int, spa_fallback: bool = True):
        handler = functools.partial(type("Handler", (_Handler,), {"spa_fallback": spa_fallback}), directory=str(directory))
        self.directory = directory
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name=f"preview-{port}", daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
