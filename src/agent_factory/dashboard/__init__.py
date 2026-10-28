"""Dashboard: a live view of the factory and the operator's actions, on localhost only.

A projection of SQLite, never a second state store. The page is static (no external scripts)
and renders every value as text, never as HTML. Reading needs no token; every action needs the
per-server token, a localhost Host and a same-origin Origin, and runs as the same CLI command an
operator would type (``actions.py``), so the controller's guards apply unchanged.
`/job/<id>` keeps the plain server-rendered page (no JavaScript).
"""

from __future__ import annotations

import html
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..evidence import EvidenceError, EvidenceStore
from ..state import Store
from . import data
from .actions import ActionError, ActionRunner

_JOB_ROUTE = re.compile(r"^/job/(job-[0-9a-f]{12})$")
_API_JOB = re.compile(r"^/api/job/(job-[0-9a-f]{12})$")
_API_ACTION = re.compile(r"^/api/job/(job-[0-9a-f]{12})/([a-z-]{3,20})$")
_API_ARTIFACT = re.compile(r"^/api/artifact/(art-[0-9a-f]{12})$")
_CSP = "default-src 'none'; style-src 'unsafe-inline'"
_APP_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_STATIC = Path(__file__).parent / "static"
_ASSETS = {"/static/app.js": "application/javascript; charset=utf-8", "/static/app.css": "text/css; charset=utf-8"}
_MAX_BODY = 16_384
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_text(text: str) -> str:
    """Markdown/agent output is displayed as inert, escaped preformatted text."""
    return f"<pre>{_e(text)}</pre>"


def page(title: str, body: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8><title>" + _e(title) + "</title>"
        "<style>body{font:14px system-ui;margin:24px;max-width:1100px}td,th{padding:4px 8px;border-bottom:1px solid #ddd;"
        "text-align:left;vertical-align:top}pre{white-space:pre-wrap;background:#f6f6f6;padding:8px}</style>"
        f"<h1>{_e(title)}</h1>{body}"
    )


def jobs_page(store: Store) -> str:
    rows = store.all("SELECT job_id, feature_id, state, path, risk, blocked_reason, updated_at FROM jobs ORDER BY updated_at DESC LIMIT 200")
    body = "<table><tr><th>job</th><th>feature</th><th>state</th><th>path</th><th>risk</th><th>blocked</th><th>updated</th></tr>"
    for r in rows:
        body += (f"<tr><td><a href='/job/{_e(r['job_id'])}'>{_e(r['job_id'])}</a></td><td>{_e(r['feature_id'])}</td>"
                 f"<td>{_e(r['state'])}</td><td>{_e(r['path'])}</td><td>{_e(r['risk'])}</td><td>{_e(r['blocked_reason'])}</td>"
                 f"<td>{_e(r['updated_at'])}</td></tr>")
    return page("Agent Factory — jobs", body + "</table>")


def job_page(store: Store, job_id: str) -> str | None:
    job = store.one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    if job is None:
        return None
    ctx = json.loads(job["context_json"])
    tasks = store.all("SELECT task_id, state, attempts FROM tasks WHERE job_id = ? ORDER BY order_idx", (job_id,))
    findings = store.all("SELECT finding_id, kind, severity, blocking, status, problem FROM findings WHERE job_id = ? ORDER BY created_at", (job_id,))
    events = store.all("SELECT seq, kind, from_state, to_state, payload_json, at FROM events WHERE job_id = ? ORDER BY seq DESC LIMIT 100", (job_id,))
    body = (f"<p>state <b>{_e(job['state'])}</b> · path {_e(job['path'])} · risk {_e(job['risk'])} · head {_e(job['head_sha'])}</p>"
            f"<h2>Why blocked / next step</h2>{render_text(job['blocked_reason'] or 'not blocked')}")
    body += "<h2>Tasks</h2><table>" + "".join(f"<tr><td>{_e(t['task_id'])}</td><td>{_e(t['state'])}</td><td>{_e(t['attempts'])}</td></tr>" for t in tasks) + "</table>"
    body += "<h2>Findings</h2><table>" + "".join(
        f"<tr><td>{_e(f['finding_id'])}</td><td>{_e(f['kind'])}</td><td>{_e(f['severity'])}</td><td>{'blocking' if f['blocking'] else ''}</td>"
        f"<td>{_e(f['status'])}</td><td>{render_text(f['problem'])}</td></tr>" for f in findings) + "</table>"
    body += f"<h2>Original request</h2>{render_text(ctx.get('intake', {}).get('goal', ''))}"
    body += "<h2>Events</h2><table>" + "".join(
        f"<tr><td>{_e(e['seq'])}</td><td>{_e(e['at'])}</td><td>{_e(e['kind'])}</td><td>{_e(e['from_state'])} → {_e(e['to_state'])}</td>"
        f"<td>{render_text(e['payload_json'])}</td></tr>" for e in events) + "</table>"
    return page(f"Job {job_id}", body)


def _status_extras(store: Store, job_id: str) -> tuple[str, str | None]:
    from ..cli import _criteria, _next_step, _pending_decisions  # the same wording as `factory status`

    job = store.job(job_id)
    pending = _pending_decisions(json.loads(job["context_json"]))
    step = "answer the product decisions" if pending else _next_step(job["state"])
    return step, _criteria(store, job_id) if job["state"] == "AWAITING_HUMAN_MERGE" else None


def make_handler(store: Store, evidence: EvidenceStore | None = None, runner: ActionRunner | None = None, token: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, content: str | bytes, ctype: str = "text/html; charset=utf-8", csp: str = _CSP) -> None:
            body = content.encode("utf-8") if isinstance(content, str) else content
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, value) -> None:
            self._send(code, json.dumps(value, default=str), "application/json; charset=utf-8", _APP_CSP)

        def _local_host(self) -> bool:
            """Host header names this machine: a DNS-rebinding page cannot reach the API."""
            host = self.headers.get("Host") or ""
            name = host[: host.find("]") + 1] if host.startswith("[") else host.split(":")[0]
            return name in _LOCAL_HOSTS

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if not self._local_host():
                return self._send(403, page("forbidden", "<p>localhost only</p>"))
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon", _APP_CSP)
            if path == "/":
                index = (_STATIC / "index.html").read_text().replace("__TOKEN__", html.escape(token, quote=True))
                return self._send(200, index, csp=_APP_CSP)
            if path in _ASSETS:
                return self._send(200, (_STATIC / path.removeprefix("/static/")).read_bytes(), _ASSETS[path], _APP_CSP)
            if path == "/plain":
                return self._send(200, jobs_page(store))
            if path == "/api/jobs":
                return self._json(200, data.jobs(store))
            if match := _API_JOB.match(path):
                if store.one("SELECT 1 FROM jobs WHERE job_id = ?", (match.group(1),)) is None:
                    return self._json(404, {"error": "unknown job"})
                step, criteria = _status_extras(store, match.group(1))
                view = data.job(store, match.group(1), next_step=step, criteria_digest=criteria)
                view["run"] = runner.background(match.group(1)) if runner else None
                return self._json(200, view)
            if (match := _API_ARTIFACT.match(path)) and evidence is not None:
                row = store.one("SELECT kind FROM artifacts WHERE artifact_id = ?", (match.group(1),))
                if row is None or row["kind"] != "screenshot":  # only screenshots: logs and payloads stay in the CLI
                    return self._json(404, {"error": "unknown screenshot"})
                try:
                    content = evidence.read(match.group(1))
                except (EvidenceError, OSError):
                    return self._json(404, {"error": "unreadable screenshot"})
                return self._send(200, content, "image/png", _APP_CSP)
            match = _JOB_ROUTE.match(path)
            if match:
                content = job_page(store, match.group(1))
                return self._send(200, content) if content else self._send(404, page("not found", ""))
            self._send(404, page("not found", ""))

        def do_POST(self) -> None:  # noqa: N802
            match = _API_ACTION.match(self.path.split("?")[0])
            if match is None or runner is None:
                return self._send(405, page("read-only page", "<p>Actions: the dashboard at / or the CLI.</p>"))
            port = self.server.server_address[1]
            origin = self.headers.get("Origin") or ""
            if not self._local_host() or origin not in {f"http://{h}:{port}" for h in _LOCAL_HOSTS}:
                return self._json(403, {"error": "same-origin localhost requests only"})
            if not token or not secrets.compare_digest(self.headers.get("X-Factory-Token") or "", token):
                return self._json(403, {"error": "missing or wrong dashboard token (reload the page)"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY or "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json(400, {"error": "JSON body up to 16 KB expected"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ActionError("JSON object expected")
                result = runner.execute(match.group(2), match.group(1), body)
            except (ActionError, json.JSONDecodeError) as exc:
                return self._json(400, {"error": str(exc)})
            self._json(200 if result.ok else 409, {"ok": result.ok, "exit_code": result.exit_code,
                                                   "output": result.output, "background": result.background})

        def log_message(self, *args) -> None:
            pass

    return Handler


def serve(store: Store, host: str = "127.0.0.1", port: int = 8765, *, evidence: EvidenceStore | None = None,
          runner: ActionRunner | None = None) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the dashboard binds to localhost only")
    token = secrets.token_urlsafe(32)
    ThreadingHTTPServer((host, port), make_handler(store, evidence, runner, token)).serve_forever()
