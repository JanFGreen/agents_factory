"""Restart reconciliation (blueprint §12.4): DB → artifacts → Git → live processes → outbox → budget."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import outbox
from .controller import TERMINAL, S
from .evidence import EvidenceError
from .state import Store, now


@dataclass
class RecoveryReport:
    job_id: str
    actions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_job(factory, job_id: str) -> RecoveryReport:
    store: Store = factory.store
    report = RecoveryReport(job_id)
    job = store.job(job_id)
    if S(job["state"]) in TERMINAL:
        return report
    # 1. artifacts referenced by results must still match their hashes
    for row in store.all("SELECT result_id FROM results WHERE job_id = ?", (job_id,)):
        try:
            factory.evidence._row_to_result(store.one("SELECT * FROM results WHERE result_id = ?", (row["result_id"],)))
        except EvidenceError as exc:
            report.problems.append(str(exc))
    # 2. git: the worktree must exist and be on the job branch
    if job["worktree"]:
        wt = Path(job["worktree"])
        if not wt.exists():
            report.problems.append(f"worktree {wt} is missing")
        else:
            from .workspaces import git

            branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD", check=False)
            if branch != job["branch"]:
                report.problems.append(f"worktree is on {branch}, expected {job['branch']}")
    # 3. live processes: adopt the living, mark the dead as lost (never start a second builder)
    for attempt in store.all("SELECT * FROM attempts WHERE job_id = ? AND state = 'running'", (job_id,)):
        if pid_alive(attempt["pid"]):
            report.actions.append(f"attempt {attempt['attempt_id']} ({attempt['role']}) still running as pid {attempt['pid']}: will be adopted")
            continue
        handle = json.loads(attempt["handle_json"]) if attempt["handle_json"] else None
        with store.tx() as conn:
            conn.execute("UPDATE attempts SET state = 'lost', ended_at = ? WHERE attempt_id = ?", (now(), attempt["attempt_id"]))
            store.event(conn, job_id, "recovery.attempt_lost", {"attempt_id": attempt["attempt_id"], "handle": handle})
        report.actions.append(f"attempt {attempt['attempt_id']} process is gone: marked lost")
    # leases whose holders are dead and expired
    with store.tx() as conn:
        for lease in conn.execute("SELECT * FROM leases WHERE lease_key LIKE ?", (f"{job_id}:%",)).fetchall():
            if lease["expires_at"] < time.time() and not pid_alive(lease["pid"]):
                conn.execute("DELETE FROM leases WHERE lease_key = ?", (lease["lease_key"],))
                report.actions.append(f"expired lease {lease['lease_key']} released")
    # 4. outbox: reconcile unknown/inflight effects by looking them up before any retry
    for op in outbox.pending_ops(store, job_id):
        if factory.merge_handler is None:
            report.problems.append(f"outbox op {op['op_id']} ({op['kind']}) pending but no handler configured")
            continue
        result = outbox.execute(store, op["op_id"], factory.merge_handler)
        report.actions.append(f"outbox {op['kind']} {op['op_id']}: {result.state}")
        if result.state == "confirmed" and op["kind"].endswith("merge") and job["state"] == str(S.MERGING):
            factory.store.update_job_context(job_id, merge_result=result.result)
            factory.controller.transition(job_id, S.MERGED, "recovered: merge confirmed by the remote")
            with store.tx() as conn:
                conn.execute("UPDATE jobs SET active = 0, version = version + 1 WHERE job_id = ?", (job_id,))
            factory._post_merge_cleanup(job_id)
    # 5. budget: open reservations of dead attempts are released
    with store.tx() as conn:
        released = conn.execute(
            "UPDATE budget_reservations SET state = 'released' WHERE job_id = ? AND state = 'open'"
            " AND NOT EXISTS (SELECT 1 FROM attempts WHERE job_id = ? AND state = 'running')",
            (job_id, job_id),
        ).rowcount
        if released:
            report.actions.append(f"{released} orphaned budget reservations released")
        store.event(conn, job_id, "recovery.done", {"actions": report.actions, "problems": report.problems})
    return report


def recover_all(factory) -> list[RecoveryReport]:
    return [recover_job(factory, row["job_id"]) for row in factory.store.all("SELECT job_id FROM jobs WHERE active = 1")]
