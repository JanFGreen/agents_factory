"""Defects found after a feature was merged: the factory's measure of what its reviews let through.

An operator reports each escaped defect against the job that shipped it and names the review
that should have caught it. The record is the repository's memory: every later review of the
same repository gets the recent escapes (`past_escapes`) and checks the change for the same
class of defect first.
"""

from __future__ import annotations

from collections import Counter

from .state import Store, new_id, now

AREAS = ("correctness", "failure-mode", "ux", "security", "legal", "quality")
MISSED_BY = ("spec", "plan", "code-review", "test-adequacy", "security-review", "ui-review", "product-verify")
# Roles that read past escapes before judging a change of the same repository.
READERS = {"factory-plan", "factory-test-design", "factory-code-review", "factory-test-adequacy",
           "factory-security-review", "factory-ui-review", "factory-product-verify"}
RECENT = 20


class EscapeRefused(ValueError):
    pass


def report(store: Store, job_id: str, *, area: str, location: str, problem: str, missed_by: str, actor: str) -> str:
    if area not in AREAS:
        raise EscapeRefused(f"area must be one of {', '.join(AREAS)}")
    if missed_by not in MISSED_BY:
        raise EscapeRefused(f"missed_by must be one of {', '.join(MISSED_BY)}")
    if not problem.strip() or not location.strip():
        raise EscapeRefused("location and problem are required")
    job = store.one("SELECT repo_path FROM jobs WHERE job_id = ?", (job_id,))
    if job is None:
        raise EscapeRefused(f"unknown job {job_id}")
    escape_id = new_id("esc")
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO escaped_defects (escape_id, job_id, repo_path, area, location, problem, missed_by, reported_by, reported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (escape_id, job_id, job["repo_path"], area, location.strip(), problem.strip(), missed_by, actor, now()))
        store.event(conn, job_id, "defect.escaped", {"escape_id": escape_id, "area": area, "missed_by": missed_by,
                                                     "location": location.strip(), "reported_by": actor})
    return escape_id


def for_repo(store: Store, repo_path: str, limit: int = RECENT) -> list[dict]:
    rows = store.all("SELECT escape_id, job_id, area, location, problem, missed_by, reported_at FROM escaped_defects"
                     " WHERE repo_path = ? ORDER BY reported_at DESC LIMIT ?", (repo_path, limit))
    return [dict(r) for r in rows]


def summary(store: Store, repo_path: str | None = None) -> dict:
    """Escapes per review and per area, and per merged job: the numbers to steer the reviews by."""
    where, params = ("WHERE repo_path = ?", (repo_path,)) if repo_path else ("", ())
    rows = store.all(f"SELECT job_id, area, missed_by FROM escaped_defects {where}", params)
    shipped = store.one(f"SELECT COUNT(*) AS n FROM jobs WHERE state = 'MERGED'{' AND repo_path = ?' if repo_path else ''}", params)["n"]
    return {"escapes": len(rows), "merged_jobs": shipped,
            "escapes_per_merged_job": round(len(rows) / shipped, 2) if shipped else None,
            "by_missed_by": dict(Counter(r["missed_by"] for r in rows).most_common()),
            "by_area": dict(Counter(r["area"] for r in rows).most_common())}
