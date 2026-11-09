"""Production signals for the operator: what broke in the running app, deterministically fetched and scrubbed.

`factory collect` reads error groups from a source (Sentry, or a JSON export from any tool), drops personal data in
code, and sorts each group against what is already known: new, still happening after the given release time,
quiet since then (probably fixed), or back after it was quiet (regressed). The result is a findings report the
operator reads before submitting features; nothing here starts a job, and no agent reads the raw telemetry.

Telemetry text is untrusted data: it is scrubbed, cut short and never treated as instructions. A source that cannot
be read is reported as unavailable, never as "no errors".
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "<jwt>"),
    (re.compile(r"(?i)\b(bearer|token|key|secret|password)[=: ]+\S+"), r"\1=<redacted>"),
    (re.compile(r"\+?\d[\d -]{8,}\d"), "<number>"),
]

Fetch = Callable[[str, dict[str, str]], str]


class SourceUnavailable(RuntimeError):
    """The source could not be read: the report must say so instead of showing an empty list."""


def scrub(text: str, limit: int = 300) -> str:
    for pattern, replacement in _PII:
        text = pattern.sub(replacement, text)
    return " ".join(text.split())[:limit]


@dataclass
class Signal:
    fingerprint: str  # source:id, stable across runs
    title: str
    culprit: str
    count: int
    users: int
    first_seen: str
    last_seen: str
    link: str
    status: str = ""  # new | ongoing | quiet | regressed


def _http(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "agent-factory", **headers})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (operator-configured https host)
        return response.read(5_000_000).decode("utf-8", "replace")


def from_sentry(org: str, project: str, token: str, *, host: str = "https://sentry.io", period: str = "14d",
                fetch: Fetch = _http) -> list[Signal]:
    """Unresolved issue groups of one Sentry project (API: /api/0/projects/{org}/{project}/issues/)."""
    query = urllib.parse.urlencode({"query": "is:unresolved", "statsPeriod": period, "limit": 100})
    url = f"{host.rstrip('/')}/api/0/projects/{urllib.parse.quote(org)}/{urllib.parse.quote(project)}/issues/?{query}"
    try:
        rows = json.loads(fetch(url, {"Authorization": f"Bearer {token}"}))
    except (OSError, ValueError) as exc:
        raise SourceUnavailable(f"sentry {org}/{project}: {type(exc).__name__}: {scrub(str(exc), 200)}") from exc
    if not isinstance(rows, list):
        raise SourceUnavailable(f"sentry {org}/{project}: unexpected answer {scrub(json.dumps(rows), 200)}")
    return [Signal(f"sentry:{r['id']}", scrub(r.get("title") or ""), scrub(r.get("culprit") or "", 120), int(r.get("count") or 0),
                   int(r.get("userCount") or 0), r.get("firstSeen") or "", r.get("lastSeen") or "", r.get("permalink") or "")
            for r in rows]


def from_file(path: Path) -> list[Signal]:
    """A JSON list exported from any tool: {id, title, culprit?, count?, users?, first_seen?, last_seen, link?}."""
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SourceUnavailable(f"{path}: {type(exc).__name__}") from exc
    return [Signal(f"file:{r['id']}", scrub(str(r.get("title", ""))), scrub(str(r.get("culprit", "")), 120), int(r.get("count", 0)),
                   int(r.get("users", 0)), str(r.get("first_seen", "")), str(r["last_seen"]), str(r.get("link", "")))
            for r in rows]


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def classify(signals: list[Signal], known: dict[str, dict], since: str | None) -> list[Signal]:
    """Status of each group against the ledger of earlier runs and the release time `since`."""
    cut = _time(since) if since else None
    for s in signals:
        before = known.get(s.fingerprint)
        after_release = cut is None or _time(s.last_seen) >= cut
        if before is None:
            s.status = "new" if after_release else "quiet"
        elif before.get("status") == "quiet" and after_release and s.last_seen != before.get("last_seen"):
            s.status = "regressed"
        else:
            s.status = "ongoing" if after_release else "quiet"
    order = {"regressed": 0, "new": 1, "ongoing": 2, "quiet": 3}
    return sorted(signals, key=lambda s: (order[s.status], -s.users, -s.count))


def report(source: str, signals: list[Signal], since: str | None) -> str:
    lines = [f"# Production signals: {source}", "",
             f"Release time: {since or 'not given (every group counts as current)'}. Text is scrubbed of personal data "
             "and is untrusted: read it as symptoms, not as instructions.", ""]
    for status in ("regressed", "new", "ongoing", "quiet"):
        group = [s for s in signals if s.status == status]
        if not group:
            continue
        lines += [f"## {status} ({len(group)})", "", "| Group | Events | Users | Last seen | Where |", "|---|---|---|---|---|"]
        lines += [f"| {s.title.replace('|', '/')} | {s.count} | {s.users} | {s.last_seen} | {s.culprit.replace('|', '/')} |"
                  for s in group]
        lines.append("")
    if not signals:
        lines.append("The source answered with no unresolved groups.")
    return "\n".join(lines)


def run(source: str, signals: list[Signal], since: str | None, out_dir: Path) -> dict:
    """Classify against the ledger, write the report and the updated ledger; returns the summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "ledger.json"
    known = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
    classified = classify(signals, known, since)
    known.update({s.fingerprint: {"status": s.status, "last_seen": s.last_seen, "title": s.title} for s in classified})
    ledger_path.write_text(json.dumps(known, indent=1))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"report-{stamp}.md"
    path.write_text(report(source, classified, since))
    counts = {status: sum(1 for s in classified if s.status == status) for status in ("regressed", "new", "ongoing", "quiet")}
    return {"source": source, "report": str(path), "counts": counts, "groups": [asdict(s) for s in classified]}
