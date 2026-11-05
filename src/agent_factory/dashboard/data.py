"""JSON projections of the factory state for the dashboard (read-only views over SQLite)."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .. import budgets
from ..controller import HALTED, TERMINAL as _TERMINAL
from ..state import Store

# States where the job waits for the operator (the dashboard puts them first), and final states.
WAITING = tuple(sorted(str(s) for s in HALTED))
TERMINAL = tuple(sorted(str(s) for s in _TERMINAL))
_ATTEMPT_CAP = re.compile(r"feature used all (\d+) build attempts")
_REASON_MAX = 2000  # the action's text limit (actions.MAX_TEXT)
RETRY_ATTEMPTS = 2  # what the dashboard proposes for a failed task (the CLI allows 1..3)
BUILD_SHARE = 0.75  # the build stops at this share of the budget; the rest is kept for verification


def _reopenable(state: str, blocked_reason: str | None) -> bool:
    """FAILED by an older controller only for the feature build-attempt cap (`factory reopen`)."""
    return state == "FAILED" and (blocked_reason or "").startswith("feature used all")


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
                    "waiting": r["state"] in WAITING, "terminal": r["state"] in TERMINAL,
                    "needs_human": r["state"] in WAITING or _reopenable(r["state"], r["blocked_reason"]),
                    "failed_tasks": tasks.get("FAILED", 0)})
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


_DECISION_EVENTS = {"task.retry": "retry", "budget.raised": "budget", "build_attempts.raised": "attempt cap", "job.reopened": "reopen"}


def _decisions_made(store: Store, job_id: str) -> list[dict[str, Any]]:
    """The operator's earlier decisions on this job, oldest first, with their notes."""
    rows = store.all(f"SELECT at, kind, payload_json FROM events WHERE job_id = ? AND kind IN ({','.join('?' * len(_DECISION_EVENTS))})"
                     " ORDER BY seq DESC LIMIT 20", (job_id, *_DECISION_EVENTS))
    out = []
    for r in reversed(rows):
        p = json.loads(r["payload_json"])
        if r["kind"] == "task.retry":
            what = f"{p.get('task_id')}: {p.get('attempts')} more attempt(s)"
        elif r["kind"] == "job.reopened":
            what = "job reopened"
        else:
            what = f"{_DECISION_EVENTS[r['kind']]} {p.get('from')} → {p.get('to')}"
        out.append({"at": r["at"], "action": _DECISION_EVENTS[r["kind"]], "what": what, "note": p.get("reason", ""),
                    "actor": p.get("actor")})
    return out


def _retry_note(task_id: str, blocking: list[dict]) -> str:
    """A first draft of the operator's note for the builder: what the blocking findings expect."""
    lines = [f"Fix the blocking review findings of {task_id} and stay within the task's file scope:"]
    lines += [f"- {f['finding_id']} ({f['kind']}): {f['expected_fix'] or f['problem']}"
              for f in sorted(blocking, key=lambda f: (f["kind"], f["finding_id"]))]
    note = "\n".join(lines)
    return note if len(note) <= _REASON_MAX else note[: _REASON_MAX - 1] + "…"


def human(state: str, blocked_reason: str | None, ctx: dict, tasks: list[dict], findings: list[dict],
          budget: dict[str, float], spent_by_task: dict[str, float], waiting: bool) -> dict[str, Any] | None:
    """What the operator is asked to decide, in plain words, with proposals the dashboard can run.

    A reading of the state only: each proposal names a whitelisted action (``actions.py``) with
    prefilled arguments, and the controller still decides whether the action is allowed.
    """
    reason = blocked_reason or ""
    reopenable = _reopenable(state, reason)
    if not waiting and not reopenable:
        return None
    pending = [d["id"] for d in ctx.get("decisions", []) if d.get("answer") is None]
    failed = [t for t in tasks if t["state"] == "FAILED"]
    accepted = sum(1 for t in tasks if t["state"] == "ACCEPTED")
    remaining = len(tasks) - accepted
    blocked_by = {t["task_id"]: [x["task_id"] for x in tasks if t["task_id"] in x["depends_on"] and x["state"] != "ACCEPTED"]
                  for t in failed}
    cap = _ATTEMPT_CAP.search(reason)
    asks: list[str] = []
    proposals: list[dict[str, Any]] = []
    if pending:
        kind = "decisions"
        asks.append(f"Answer {len(pending)} product decision(s) ({', '.join(pending)}); the job goes on with your answers.")
    elif state == "NEEDS_HUMAN" and ctx.get("plan_estimate") and not ctx.get("plan_approval"):
        kind = "plan"
        asks.append("Approve the plan and its budget so that the build can start.")
    elif state == "AWAITING_HUMAN_MERGE":
        kind = "merge"
        asks.append("Approve or reject the merge of the accepted feature.")
    elif reopenable:
        kind = "reopen"
        asks.append("The job stopped at the feature build-attempt cap. Reopen it, then raise the cap to go on.")
        proposals.append({"action": "reopen", "label": "Reopen the job", "body": {}, "needs_reason": True, "then_resume": False,
                          "detail": "the job becomes halted again; accepted tasks stay on the job branch"})
    elif failed:
        kind = "task_failed"
        for t in failed:
            blocking = [f for f in findings if f["task_id"] == t["task_id"] and f["status"] == "open" and f["blocking"]]
            later = blocked_by[t["task_id"]]
            asks.append(f"{t['task_id']} used all its attempts (${spent_by_task.get(t['task_id'], 0.0):.2f} so far) with "
                        f"{len(blocking)} blocking finding(s) open" + (f"; it holds up {', '.join(later)}." if later else "."))
            proposals.append({"action": "task-retry", "label": f"Retry {t['task_id']} with {RETRY_ATTEMPTS} more attempts",
                              "body": {"task_id": t["task_id"], "attempts": RETRY_ATTEMPTS}, "needs_reason": True,
                              "reason": _retry_note(t["task_id"], blocking), "then_resume": True, "task_id": t["task_id"],
                              "detail": "your note goes to the builder as the operator's instruction for the retry"})
    elif cap:
        kind = "attempt_cap"
        used = int(cap.group(1))
        to = used + max(10, 2 * remaining)
        asks.append(f"The feature used all {used} build attempts with {remaining} task(s) left. Raise the cap to go on.")
        proposals.append({"action": "attempts-raise", "label": f"Raise the attempt cap to {to}", "body": {"to": to},
                          "needs_reason": True, "then_resume": True, "detail": f"{used} used; about two more per remaining task"})
    elif state == "BUDGET_EXHAUSTED":
        kind = "budget"
        asks.append(f"The budget is used up (${budget['spent']:.2f} of ${budget['limit']:.2f}) with {remaining} task(s) left.")
    elif state == "BLOCKED_ENV":
        kind = "environment"
        asks.append("Fix the environment problem named below on this machine, then resume.")
    else:
        kind = "other"
        asks.append("Read why the job stopped below, then resume or cancel it.")
    limit, spent = budget["limit"], budget["spent"]
    per_task = spent / (accepted or max(1, len(tasks)))
    short_of_budget = limit * BUILD_SHARE - spent < per_task * remaining  # at the cost so far, the rest does not fit
    if kind in ("budget", "task_failed", "attempt_cap") and limit and (kind == "budget" or short_of_budget):
        to = max(math.ceil(limit * 1.2), math.ceil((spent + per_task * remaining) / BUILD_SHARE))
        if kind != "budget":
            asks.append(f"At ${per_task:.2f} per task so far, the {remaining} task(s) left do not fit in the budget: "
                        "raise it first, or the build stops again soon.")
        proposals.append({"action": "raise-budget", "label": f"Raise the budget to ${to}", "body": {"to": to}, "needs_reason": True,
                          "then_resume": kind == "budget",
                          "detail": f"${spent:.2f} spent for {accepted} accepted task(s), about ${per_task:.2f} each, {remaining} left; "
                                    f"the build may use {BUILD_SHARE:.0%} of the budget"})
    if kind not in ("decisions", "plan", "merge", "reopen"):
        proposals.append({"action": "resume", "label": "Resume as it is", "body": {}, "needs_reason": False, "then_resume": False,
                          "detail": "helps only if the cause was fixed outside the factory"})
    proposals.append({"action": "cancel", "label": "Cancel the job", "body": {}, "needs_reason": False, "then_resume": False,
                      "danger": True, "detail": "cannot be undone; the job branch stays for inspection"})
    return {"kind": kind, "asks": asks, "reason": reason, "pending_decisions": pending,
            "failed_tasks": [t["task_id"] for t in failed], "blocked_by": blocked_by, "proposals": proposals}


def job(store: Store, job_id: str, *, next_step: str, criteria_digest: str | None) -> dict[str, Any] | None:
    row = store.one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    if row is None:
        return None
    ctx = json.loads(row["context_json"])
    estimate = ctx.get("plan_estimate")
    events = store.all("SELECT seq, at, kind, from_state, to_state, payload_json FROM events WHERE job_id = ? ORDER BY seq DESC LIMIT 200",
                       (job_id,))
    findings = [dict(f) for f in store.all("SELECT finding_id, task_id, kind, severity, blocking, status, problem, expected_fix"
                                           " FROM findings WHERE job_id = ? ORDER BY created_at DESC LIMIT 300", (job_id,))]
    tasks, budget, costs = _tasks(store, job_id, ctx), _budget(store, job_id), _costs(store, job_id)
    waiting = row["state"] in WAITING
    asked = human(row["state"], row["blocked_reason"], ctx, tasks, findings, budget, costs["by_task"], waiting)
    if asked is not None:
        asked["history"] = _decisions_made(store, job_id)
    return {
        "job_id": job_id, "feature_id": row["feature_id"], "repo_id": row["repo_id"], "state": row["state"],
        "path": row["path"], "risk": row["risk"], "blocked_reason": row["blocked_reason"], "head_sha": row["head_sha"],
        "base_sha": row["base_sha"], "branch": row["branch"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        "goal": (ctx.get("intake") or {}).get("goal", ""), "spec_mode": ctx.get("spec_mode"),
        "waiting": waiting, "terminal": row["state"] in TERMINAL,
        "human": asked,
        "budget": budget, "next_step": next_step, "criteria_digest": criteria_digest,
        "plan_estimate": {k: estimate.get(k) for k in ("tasks", "estimate_usd", "suggested_limit_usd", "basis")} if estimate else None,
        "plan_approved": bool(ctx.get("plan_approval")),
        "decisions": [{k: d.get(k) for k in ("id", "question", "why", "options", "proposed", "answer", "source", "critical")}
                      for d in ctx.get("decisions", [])],
        "tasks": tasks, "reviews": _reviews(store, job_id), "costs": costs,
        "findings": findings, "screenshots": _screenshots(store, job_id),
        "events": [{**{k: e[k] for k in ("seq", "at", "kind", "from_state", "to_state")}, "payload": e["payload_json"][:600]} for e in events],
    }
