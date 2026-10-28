"""JSON projections of the factory state for the dashboard (read-only views over SQLite)."""

from __future__ import annotations

import json
from typing import Any

from .. import budgets
from ..controller import HALTED, TERMINAL as _TERMINAL
from ..state import Store

# States where the job waits for the operator (the dashboard puts them first), and final states.
WAITING = tuple(sorted(str(s) for s in HALTED))
TERMINAL = tuple(sorted(str(s) for s in _TERMINAL))


def _budget(store: Store, job_id: str) -> dict[str, float]:
    try:
        b = budgets.status(store, job_id)
    except KeyError:
        return {"limit": 0.0, "committed": 0.0, "spent": 0.0}
    return {"limit": round(b.limit, 2), "committed": round(b.committed, 4), "spent": round(b.spent, 4)}


def jobs(store: Store) -> list[dict[str, Any]]:
    rows = store.all("SELECT job_id, feature_id, repo_id, state, path, risk, blocked_reason, created_at, updated_at"
                     " FROM jobs ORDER BY updated_at DESC LIMIT 200")
    counts = {(r["job_id"], r["state"]): r["n"] for r in store.all("SELECT job_id, state, COUNT(*) AS n FROM tasks GROUP BY job_id, state")}
    out = []
    for r in rows:
        tasks = {state: n for (job, state), n in counts.items() if job == r["job_id"]}
        out.append({**dict(r), "budget": _budget(store, r["job_id"]),
                    "tasks": {"total": sum(tasks.values()), "accepted": tasks.get("ACCEPTED", 0),
                              "building": tasks.get("BUILDING", 0) + tasks.get("REVIEW", 0)},
                    "waiting": r["state"] in WAITING, "terminal": r["state"] in TERMINAL})
    return out


def _costs(store: Store, job_id: str) -> dict[str, Any]:
    rows = store.all("SELECT a.role AS role, a.task_id AS task_id, COUNT(*) AS calls, SUM(u.cost_usd) AS usd,"
                     " SUM(CASE WHEN u.cost_known = 0 THEN 1 ELSE 0 END) AS unknown"
                     " FROM usage u JOIN attempts a ON a.attempt_id = u.attempt_id WHERE u.job_id = ? GROUP BY a.role, a.task_id",
                     (job_id,))
    by_role: dict[str, dict] = {}
    by_task: dict[str, float] = {}
    for r in rows:
        role = by_role.setdefault(r["role"], {"role": r["role"], "calls": 0, "usd": 0.0, "unknown": 0})
        role["calls"] += r["calls"]
        role["usd"] = round(role["usd"] + (r["usd"] or 0.0), 4)
        role["unknown"] += r["unknown"]
        if r["task_id"]:
            by_task[r["task_id"]] = round(by_task.get(r["task_id"], 0.0) + (r["usd"] or 0.0), 4)
    return {"by_role": sorted(by_role.values(), key=lambda x: -x["usd"]), "by_task": by_task}


def _reviews(store: Store, job_id: str) -> dict[str, dict[str, str]]:
    """Latest verdict per task and result kind (``-`` = the job itself)."""
    latest: dict[str, dict[str, str]] = {}
    for r in store.all("SELECT task_id, kind, verdict FROM results WHERE job_id = ? ORDER BY rowid", (job_id,)):
        latest.setdefault(r["task_id"] or "-", {})[r["kind"]] = r["verdict"]
    return latest


def _screenshots(store: Store, job_id: str) -> list[dict[str, Any]]:
    rows = store.all("SELECT artifact_id, producer_json, created_at FROM artifacts WHERE job_id = ? AND kind = 'screenshot'"
                     " ORDER BY created_at DESC LIMIT 60", (job_id,))
    return [{"artifact_id": r["artifact_id"], "role": json.loads(r["producer_json"]).get("role"), "at": r["created_at"]} for r in rows]


def _tasks(store: Store, job_id: str, ctx: dict) -> list[dict[str, Any]]:
    difficulty = (ctx.get("task_difficulty") or {}).get("tasks", {})
    out = []
    for r in store.all("SELECT task_id, state, attempts, spec_json FROM tasks WHERE job_id = ? ORDER BY order_idx", (job_id,)):
        spec = json.loads(r["spec_json"])
        out.append({
            "task_id": r["task_id"], "state": r["state"], "attempts": r["attempts"],
            "depends_on": spec.get("depends_on", []), "file_scope": spec.get("file_scope", []),
            "acceptance": spec.get("acceptance", ""), "requirement_ids": spec.get("requirement_ids", []),
            "difficulty": (difficulty.get(r["task_id"]) or {}).get("difficulty") or spec.get("difficulty"),
        })
    return out


def job(store: Store, job_id: str, *, next_step: str, criteria_digest: str | None) -> dict[str, Any] | None:
    row = store.one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    if row is None:
        return None
    ctx = json.loads(row["context_json"])
    estimate = ctx.get("plan_estimate")
    events = store.all("SELECT seq, at, kind, from_state, to_state, payload_json FROM events WHERE job_id = ? ORDER BY seq DESC LIMIT 200",
                       (job_id,))
    findings = store.all("SELECT finding_id, task_id, kind, severity, blocking, status, problem, expected_fix FROM findings"
                         " WHERE job_id = ? ORDER BY created_at DESC LIMIT 300", (job_id,))
    return {
        "job_id": job_id, "feature_id": row["feature_id"], "repo_id": row["repo_id"], "state": row["state"],
        "path": row["path"], "risk": row["risk"], "blocked_reason": row["blocked_reason"], "head_sha": row["head_sha"],
        "base_sha": row["base_sha"], "branch": row["branch"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        "goal": (ctx.get("intake") or {}).get("goal", ""), "spec_mode": ctx.get("spec_mode"),
        "waiting": row["state"] in WAITING, "terminal": row["state"] in TERMINAL,
        "budget": _budget(store, job_id), "next_step": next_step, "criteria_digest": criteria_digest,
        "plan_estimate": {k: estimate.get(k) for k in ("tasks", "estimate_usd", "suggested_limit_usd", "basis")} if estimate else None,
        "plan_approved": bool(ctx.get("plan_approval")),
        "decisions": [{k: d.get(k) for k in ("id", "question", "why", "options", "proposed", "answer", "source")}
                      for d in ctx.get("decisions", [])],
        "tasks": _tasks(store, job_id, ctx), "reviews": _reviews(store, job_id), "costs": _costs(store, job_id),
        "findings": [dict(f) for f in findings], "screenshots": _screenshots(store, job_id),
        "events": [{**{k: e[k] for k in ("seq", "at", "kind", "from_state", "to_state")}, "payload": e["payload_json"][:600]} for e in events],
    }
