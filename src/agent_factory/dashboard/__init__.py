"""Read-only dashboard: a projection of SQLite, never a second state store.

Binds to localhost, escapes every value (Markdown and agent text are shown as
escaped text, never rendered as HTML/JS), accepts only whitelisted GET routes and
exposes no state-changing actions (those go through the authenticated CLI).
"""

from __future__ import annotations

import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..state import Store

_JOB_ROUTE = re.compile(r"^/job/(job-[0-9a-f]{12})$")
_CSP = "default-src 'none'; style-src 'unsafe-inline'"


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


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, content: str) -> None:
            data = content.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", _CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, jobs_page(store))
            match = _JOB_ROUTE.match(path)
            if match:
                content = job_page(store, match.group(1))
                return self._send(200, content) if content else self._send(404, page("not found", ""))
            self._send(404, page("not found", ""))

        def do_POST(self) -> None:  # noqa: N802
            self._send(405, page("read-only dashboard", "<p>Use the CLI for actions.</p>"))

        def log_message(self, *args) -> None:
            pass

    return Handler


def serve(store: Store, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the dashboard binds to localhost only")
    ThreadingHTTPServer((host, port), make_handler(store)).serve_forever()
