"""Preview server for UI review: serves the static build, SPA fallback, nothing outside it."""

from __future__ import annotations

import socket
import urllib.error
import urllib.request

import pytest

from agent_factory.preview import PreviewServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, ""


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("INDEX")
    (root / "settings.html").write_text("SETTINGS")
    (root / "assets" / "app.js").write_text("JS")
    (tmp_path / "secret.txt").write_text("SECRET")
    return root


def test_serves_files_static_routes_and_falls_back_to_index(site):
    server = PreviewServer(site, _free_port())
    try:
        assert _get(server.url) == (200, "INDEX")
        assert _get(server.url + "assets/app.js") == (200, "JS")
        assert _get(server.url + "settings") == (200, "SETTINGS")  # static export: /settings -> settings.html
        assert _get(server.url + "learn/session") == (200, "INDEX")  # client-side route
        assert _get(server.url + "../secret.txt")[1] != "SECRET"
        assert _get(server.url + "%2e%2e/secret.txt")[1] != "SECRET"
    finally:
        server.stop()


def test_without_spa_fallback_unknown_paths_are_404(site):
    server = PreviewServer(site, _free_port(), spa_fallback=False)
    try:
        assert _get(server.url + "learn/session")[0] == 404
    finally:
        server.stop()


def test_stop_releases_the_port(site):
    port = _free_port()
    PreviewServer(site, port).stop()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_a_rewrite_replaces_the_api_url_in_text_files_only(site):
    """The build calls the API URL baked into it; with a preview backend it must call that server instead."""
    (site / "assets" / "app.js").write_text('fetch("http://127.0.0.1:3000/health")')
    (site / "logo.png").write_bytes(b"\x89PNG http://127.0.0.1:3000")
    server = PreviewServer(site, _free_port(), rewrite={"http://127.0.0.1:3000": "http://127.0.0.1:24001"})
    try:
        status, body = _get(server.url + "assets/app.js")
        assert status == 200 and body == 'fetch("http://127.0.0.1:24001/health")'
        with urllib.request.urlopen(server.url + "logo.png", timeout=5) as r:
            assert r.read() == b"\x89PNG http://127.0.0.1:3000"
        assert _get(server.url + "learn/session") == (200, "INDEX")  # the SPA fallback still works
        (site / "index.html").write_text('<script>api="http://127.0.0.1:3000"</script>')
        assert _get(server.url) == (200, '<script>api="http://127.0.0.1:24001"</script>')  # / serves index.html rewritten
    finally:
        server.stop()
