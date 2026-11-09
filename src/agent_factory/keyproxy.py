"""Key-holding proxy: agents call model APIs through the controller and never see the operator's keys.

An agent process gets, instead of a key, a one-time token valid for its own call and the proxy's local address as
the API base URL. The proxy (a thread of the controller on 127.0.0.1) checks the token, swaps it for the real key,
forwards the request to the upstream API and streams the answer back. It counts the tokens each answer reports and
refuses further requests once the call's budget is spent (HTTP 402), or once the call ended (the token is revoked).
"""

from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Headers that carry a key in the APIs the runtimes call (OpenAI, Anthropic, Gemini).
_AUTH_HEADERS = ("authorization", "x-api-key", "x-goog-api-key")
_HOP = {"host", "connection", "keep-alive", "proxy-connection", "transfer-encoding", "upgrade", "content-length",
        "accept-encoding", *_AUTH_HEADERS}
_USAGE = {  # output and input token fields per API, in non-streamed bodies and in stream events
    "in": ("input_tokens", "prompt_tokens", "promptTokenCount"),
    "out": ("output_tokens", "completion_tokens", "candidatesTokenCount"),
}


@dataclass(frozen=True)
class Route:
    """One upstream API and the controller-side variable that holds its key."""

    upstream: str  # e.g. https://api.openai.com/v1
    source_env: str  # operator-side variable with the real key
    header: str = "authorization"  # authorization (Bearer), x-api-key or x-goog-api-key


@dataclass
class Grant:
    route: str
    expires: float
    budget_usd: float | None
    price: dict[str, float] | None  # input_per_mtok, output_per_mtok
    tokens_in: int = 0
    tokens_out: int = 0
    requests: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def spent(self) -> float | None:
        if not self.price:
            return None
        return (self.tokens_in * self.price.get("input_per_mtok", 0) + self.tokens_out * self.price.get("output_per_mtok", 0)) / 1e6


def usage_of(text: str) -> tuple[int, int]:
    """Token counts an answer reports; a stream reports them in its last events, so the largest value counts."""
    found = {"in": 0, "out": 0}
    for side, keys in _USAGE.items():
        for key in keys:
            for value in re.findall(rf'"{key}"\s*:\s*(\d+)', text):
                found[side] = max(found[side], int(value))
    return found["in"], found["out"]


class KeyProxy:
    MAX_REQUESTS = 2000  # per grant: a runaway loop stops even without a price

    def __init__(self, routes: dict[str, Route], environ: dict[str, str] | None = None):
        self.routes = routes
        self.environ = environ if environ is not None else os.environ
        self.grants: dict[str, Grant] = {}
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    # --- lifecycle ---------------------------------------------------------------------

    def base_url(self, route: str) -> str:
        server = self._start()
        return f"http://127.0.0.1:{server.server_address[1]}/{route}"

    def _start(self) -> ThreadingHTTPServer:
        with self._lock:
            if self._server is None:
                proxy = self

                class Handler(_Handler):
                    owner = proxy

                self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                self._server.daemon_threads = True
                threading.Thread(target=self._server.serve_forever, name="key-proxy", daemon=True).start()
            return self._server

    def close(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._server = None

    # --- grants --------------------------------------------------------------------------

    def grant(self, route: str, ttl_seconds: float, budget_usd: float | None = None, price: dict | None = None) -> str:
        if route not in self.routes:
            raise KeyError(f"no key proxy route {route!r}")
        if not self.environ.get(self.routes[route].source_env):
            raise KeyError(f"{self.routes[route].source_env} is not set on the controller side")
        token = "kp-" + secrets.token_urlsafe(24)
        self.grants[token] = Grant(route, time.time() + ttl_seconds, budget_usd, price)
        return token

    def revoke(self, tokens: list[str] | None) -> None:
        for token in tokens or []:
            self.grants.pop(token, None)

    def check(self, route: str, token: str | None) -> tuple[Grant | None, int, str]:
        grant = self.grants.get(token or "")
        if grant is None or grant.route != route:
            return None, 401, "unknown or revoked factory token"
        if time.time() > grant.expires:
            self.grants.pop(token or "", None)
            return None, 401, "factory token expired"
        spent = grant.spent()
        if grant.budget_usd is not None and spent is not None and spent >= grant.budget_usd:
            return None, 402, f"call budget ${grant.budget_usd:.2f} spent (${spent:.2f})"
        if grant.requests >= self.MAX_REQUESTS:
            return None, 429, f"more than {self.MAX_REQUESTS} requests in one call"
        return grant, 200, ""


class _Handler(BaseHTTPRequestHandler):
    owner: KeyProxy
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # the controller logs nothing of the traffic (it carries prompts)
        return

    def _token(self) -> str | None:
        for name in _AUTH_HEADERS:
            value = self.headers.get(name)
            if value:
                return value.removeprefix("Bearer ").strip()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return (query.get("key") or [None])[0]

    def _refuse(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message, "type": "factory_key_proxy"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self) -> None:
        route_name, _, rest = self.path.lstrip("/").partition("/")
        route = self.owner.routes.get(route_name)
        if route is None:
            return self._refuse(404, "unknown route")
        grant, status, why = self.owner.check(route_name, self._token())
        if grant is None:
            return self._refuse(status, why)
        with grant.lock:
            grant.requests += 1
        split = urllib.parse.urlsplit(route.upstream)
        path, _, query = rest.partition("?")
        query = "&".join(p for p in query.split("&") if p and not p.startswith("key="))
        target = f"{split.path.rstrip('/')}/{path}" + (f"?{query}" if query else "")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
        key = self.owner.environ[route.source_env]
        headers[route.header] = f"Bearer {key}" if route.header == "authorization" else key
        conn_cls = http.client.HTTPSConnection if split.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(split.netloc, timeout=600)
        answered = False
        try:
            conn.request(self.command, target, body=body, headers=headers)
            upstream = conn.getresponse()
            answered = True
            self.send_response(upstream.status)
            for name, value in upstream.getheaders():
                if name.lower() not in _HOP:
                    self.send_header(name, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            seen = b""
            while chunk := upstream.read1(65536) if hasattr(upstream, "read1") else upstream.read(65536):
                seen = (seen + chunk)[-200_000:]  # usage arrives at the end of a stream
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            tokens_in, tokens_out = usage_of(seen.decode("utf-8", "replace"))
            with grant.lock:
                grant.tokens_in += tokens_in
                grant.tokens_out += tokens_out
        except OSError as exc:
            if not answered:
                self._refuse(502, f"upstream error: {type(exc).__name__}")
            self.close_connection = True
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _forward
