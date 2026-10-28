"""Cost estimate of an accepted plan, for the operator to approve before any build starts.

Numbers come from this factory's own history (average settled cost per role and average
build attempts per accepted task) once there are enough samples, otherwise from defaults
measured on the first live projects. It is an estimate, shown with its basis; the hard
budget limit still stops the job.
"""

from __future__ import annotations

import math
import re

from . import budgets
from .state import Store

# Average settled cost per agent call (USD) on LearnEnglish F1-F3 (Opus/Sonnet bindings).
DEFAULT_ROLE_COST = {
    "builder": 0.31, "code_reviewer": 0.46, "security_reviewer": 0.52, "product_evaluator": 0.24,
    "test_reviewer": 0.26, "ui_reviewer": 0.23, "integration_evaluator": 0.49,
}
DEFAULT_ROUNDS_PER_TASK = 1.55
MIN_SAMPLES = 5
TASK_REVIEWERS = ["code_reviewer", "security_reviewer", "product_evaluator", "test_reviewer"]
_UI = re.compile(r"\.(tsx|jsx|vue|svelte|html|css|scss)$|/(ui|components|templates)/")


def _role_costs(store: Store) -> tuple[dict[str, float], int]:
    rows = store.all(
        "SELECT a.role AS role, AVG(u.cost_usd) AS avg, COUNT(*) AS n FROM usage u JOIN attempts a ON a.attempt_id = u.attempt_id"
        " WHERE u.cost_known = 1 AND u.cost_usd IS NOT NULL GROUP BY a.role"
    )
    costs, samples = dict(DEFAULT_ROLE_COST), 0
    for row in rows:
        if row["role"] in costs and row["n"] >= MIN_SAMPLES:
            costs[row["role"]] = float(row["avg"])
            samples += row["n"]
    return costs, samples


def _rounds(store: Store) -> float:
    row = store.one("SELECT AVG(attempts) AS avg, COUNT(*) AS n FROM tasks WHERE state = 'ACCEPTED' AND attempts > 0")
    return float(row["avg"]) if row and row["n"] >= MIN_SAMPLES else DEFAULT_ROUNDS_PER_TASK


def estimate_plan(store: Store, job_id: str, tasks: list[dict], reserve_fraction: float) -> dict:
    costs, samples = _role_costs(store)
    rounds = _rounds(store)
    per_task = []
    for task in tasks:
        roles = ["builder", *TASK_REVIEWERS]
        if any(_UI.search(path) for path in task.get("file_scope", [])):
            roles.append("ui_reviewer")
        per_task.append({"task_id": task["task_id"], "usd": round(rounds * sum(costs[r] for r in roles), 2)})
    build = sum(t["usd"] for t in per_task)
    # acceptance, integration and at most one full security review of the feature; one retry in five
    verification = 1.2 * (costs["product_evaluator"] + costs["integration_evaluator"] + costs["security_reviewer"])
    try:
        spent = budgets.status(store, job_id).spent
    except KeyError:  # an estimate for a plan outside a job (backtests)
        spent = 0.0
    remaining = build + verification
    return {
        "tasks": len(tasks),
        "per_task": per_task,
        "rounds_per_task": round(rounds, 2),
        "build_usd": round(build, 2),
        "verification_usd": round(verification, 2),
        "spent_usd": round(spent, 2),
        "estimate_usd": round(spent + remaining, 2),
        # the limit keeps `reserve_fraction` free for verification and 25% headroom for rework
        "suggested_limit_usd": float(math.ceil((spent + 1.25 * remaining) / (1 - reserve_fraction))),
        "basis": f"history ({samples} calls)" if samples else "defaults (no history yet)",
    }
