"""Local preview of a built web candidate, so UI reviewers can look at it in a real browser.

The controller serves the static build that the trusted gate produced in the worktree (e.g.
``dist/`` from ``expo export``) on 127.0.0.1. Unknown paths fall back to ``index.html`` for
single-page apps. Serving is read-only; the build itself is never run by this module.

``rewrite`` replaces text in served html/js/css/json files: the API base URL baked into a build
(e.g. ``http://127.0.0.1:3000``) becomes the URL of the preview backend on a free port, so the
build calls the server the walkthrough started and never another app on the machine.
"""

from __future__ import annotations

import functools
import io
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _Handler(SimpleHTTPRequestHandler):
    spa_fallback = True
    rewrite: dict[bytes, bytes] = {}
    TEXT_SUFFIXES = (".html", ".js", ".mjs", ".css", ".json", ".map")

    def translate_path(self, path: str) -> str:
        resolved = super().translate_path(path)  # stays inside `directory` (no traversal)
        if self.spa_fallback and not os.path.exists(resolved) and not os.path.exists(resolved + ".html"):
            return os.path.join(self.directory, "index.html")
        if not os.path.exists(resolved) and os.path.exists(resolved + ".html"):
            return resolved + ".html"  # static exports write /settings as settings.html
        return resolved

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path) and self.path.split("?", 1)[0].endswith("/"):
            path = os.path.join(path, "index.html")
        if not self.rewrite or os.path.isdir(path) or not path.endswith(self.TEXT_SUFFIXES):
            return super().send_head()
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return super().send_head()
        for old, new in self.rewrite.items():
            body = body.replace(old, new)
        self.send_response(200)
        self.send_header("Content-type", self.guess_type(path))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        return io.BytesIO(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (http.server API)
        pass


class PreviewServer:
    def __init__(self, directory: Path, port: int, spa_fallback: bool = True, rewrite: dict[str, str] | None = None):
        replace = {old.encode(): new.encode() for old, new in (rewrite or {}).items()}
        handler = functools.partial(type("Handler", (_Handler,), {"spa_fallback": spa_fallback, "rewrite": replace}),
                                    directory=str(directory))
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
