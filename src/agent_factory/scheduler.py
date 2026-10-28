"""DAG validation, ready-task selection, global slots and leases with fencing."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass

from .config import Decomposition
from .schemas import TaskSpec
from .state import Store, new_id, now


@dataclass
class DagReport:
    problems: list[str]
    order: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_dag(tasks: list[TaskSpec], requirement_ids: list[str], cfg: Decomposition) -> DagReport:
    problems: list[str] = []
    ids = [t.task_id for t in tasks]
    by_id = {t.task_id: t for t in tasks}
    if len(set(ids)) != len(ids):
        problems.append(f"duplicate task ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    if not tasks:
        problems.append("plan has no tasks")
    if len(tasks) > cfg.max_leaf_tasks_per_feature:
        problems.append(f"{len(tasks)} leaf tasks exceed the cap of {cfg.max_leaf_tasks_per_feature}")
    known_reqs = set(requirement_ids)
    covered: set[str] = set()
    for task in tasks:
        for dep in task.depends_on:
            if dep not in by_id:
                problems.append(f"{task.task_id} depends on missing task {dep}")
        if not task.requirement_ids:
            problems.append(f"{task.task_id} does not trace to any requirement")
        unknown = set(task.requirement_ids) - known_reqs
        if unknown:
            problems.append(f"{task.task_id} references unknown requirements {sorted(unknown)}")
        covered |= set(task.requirement_ids)
        if task.estimated_minutes > cfg.target_task_minutes * 3:
            problems.append(f"{task.task_id} is too large ({task.estimated_minutes} min, target {cfg.target_task_minutes})")
    if cfg.full_requirement_coverage:
        missing = known_reqs - covered
        if missing:
            problems.append(f"requirements without a task: {sorted(missing)}")
    # parallel-safe tasks must not share file ownership
    for task in tasks:
        for other_id in task.parallel_safe_with:
            other = by_id.get(other_id)
            if other and set(task.file_scope) & set(other.file_scope):
                problems.append(f"{task.task_id} and {other_id} are parallel_safe but share files {sorted(set(task.file_scope) & set(other.file_scope))}")
    # depth via parent chain
    for task in tasks:
        depth, seen, cursor = 1, set(), task.parent
        while cursor and cursor not in seen:
            seen.add(cursor)
            depth += 1
            cursor = by_id[cursor].parent if cursor in by_id else None
        if depth > cfg.max_depth:
            problems.append(f"{task.task_id} exceeds max depth {cfg.max_depth}")
    order = topological_order(tasks)
    if order is None:
        problems.append("dependency cycle detected")
        order = []
    return DagReport(problems, order)


def topological_order(tasks: list[TaskSpec]) -> list[str] | None:
    """Kahn's algorithm with stable (sorted) ordering; None when there is a cycle.

    Dependencies on unknown tasks are reported by validate_dag and ignored here.
    """
    known = {t.task_id for t in tasks}
    pending = {t.task_id: {d for d in t.depends_on if d in known} for t in tasks}
    order: list[str] = []
    while True:
        ready = sorted(t for t, deps in pending.items() if not deps)
        if not ready:
            break
        current = ready[0]
        order.append(current)
        del pending[current]
        for deps in pending.values():
            deps.discard(current)
    return order if not pending else None


def ready_tasks(store: Store, job_id: str) -> list[str]:
    rows = store.all("SELECT task_id, spec_json, state FROM tasks WHERE job_id = ? ORDER BY order_idx", (job_id,))
    done = {r["task_id"] for r in rows if r["state"] == "ACCEPTED"}
    ready = []
    for row in rows:
        if row["state"] not in ("PENDING", "REWORK"):
            continue
        deps = json.loads(row["spec_json"]).get("depends_on", [])
        if all(d in done for d in deps):
            ready.append(row["task_id"])
    return ready


# --- leases & fencing -------------------------------------------------------------------


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    key: str
    owner_id: str
    epoch: int
    expires_at: float


def acquire_lease(store: Store, key: str, owner_id: str, ttl: float, *, pid: int | None = None) -> Lease | None:
    """Claim ``key``; returns None if another live owner holds it. Epoch increases on takeover."""
    with store.tx() as conn:
        row = conn.execute("SELECT * FROM leases WHERE lease_key = ?", (key,)).fetchone()
        t = time.time()
        if row is None:
            epoch = 1
            conn.execute("INSERT INTO leases VALUES (?, ?, ?, ?, ?)", (key, owner_id, epoch, t + ttl, pid))
        elif row["owner_id"] == owner_id and row["expires_at"] > t:
            epoch = row["epoch"]
            conn.execute("UPDATE leases SET expires_at = ? WHERE lease_key = ?", (t + ttl, key))
        elif row["expires_at"] > t or (row["pid"] and _pid_alive(row["pid"])):
            # Expiry alone does not prove the holder is dead: a live process keeps the lease.
            return None
        else:
            epoch = row["epoch"] + 1
            conn.execute(
                "UPDATE leases SET owner_id = ?, epoch = ?, expires_at = ?, pid = ? WHERE lease_key = ?",
                (owner_id, epoch, t + ttl, pid, key),
            )
            store.event(conn, None, "lease.taken_over", {"key": key, "from": row["owner_id"], "to": owner_id, "epoch": epoch})
        return Lease(key, owner_id, epoch, t + ttl)


def heartbeat(store: Store, lease: Lease, ttl: float) -> Lease:
    with store.tx() as conn:
        check_fence(conn, lease)
        expires = time.time() + ttl
        conn.execute("UPDATE leases SET expires_at = ? WHERE lease_key = ?", (expires, lease.key))
    return Lease(lease.key, lease.owner_id, lease.epoch, expires)


def check_fence(conn: sqlite3.Connection, lease: Lease) -> None:
    row = conn.execute("SELECT owner_id, epoch FROM leases WHERE lease_key = ?", (lease.key,)).fetchone()
    if row is None or row["owner_id"] != lease.owner_id or row["epoch"] != lease.epoch:
        raise LeaseLost(f"lease {lease.key} epoch {lease.epoch} is no longer current")


def release_lease(store: Store, lease: Lease) -> None:
    with store.tx() as conn:
        conn.execute("DELETE FROM leases WHERE lease_key = ? AND owner_id = ? AND epoch = ?", (lease.key, lease.owner_id, lease.epoch))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- global slots ------------------------------------------------------------------------


class NoSlot(RuntimeError):
    pass


_CONTROLLER_OWNER = re.compile(r"^controller-(\d+)-[0-9a-f]+$")


def _reclaim_dead_slots(conn: sqlite3.Connection, kind: str) -> list[str]:
    """Release slots whose controller process no longer exists (a crash or kill left them taken)."""
    rows = conn.execute("SELECT alloc_id, owner FROM resource_allocations WHERE kind = ? AND released_at IS NULL", (f"slot:{kind}",)).fetchall()
    dead = [r["alloc_id"] for r in rows if (m := _CONTROLLER_OWNER.match(r["owner"] or "")) and not _pid_alive(int(m.group(1)))]
    for alloc_id in dead:
        conn.execute("UPDATE resource_allocations SET released_at = ?, owner = owner || ':reclaimed' WHERE alloc_id = ?", (now(), alloc_id))
    return dead


def acquire_slot(store: Store, kind: str, limit: int, job_id: str, owner: str) -> str:
    """Global slot from resource_allocations (e.g. agent_process, test_process)."""
    with store.tx() as conn:
        _reclaim_dead_slots(conn, kind)
        for index in range(limit):
            try:
                alloc_id = new_id("slot")
                conn.execute(
                    "INSERT INTO resource_allocations VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (alloc_id, f"slot:{kind}", str(index), job_id, owner, now()),
                )
                return alloc_id
            except sqlite3.IntegrityError:
                continue
    raise NoSlot(f"all {limit} {kind} slots are busy")


def wait_for_slot(store: Store, kind: str, limit: int, job_id: str, owner: str, *, timeout: float, poll: float = 2.0) -> str:
    """Acquire a slot, waiting while other lanes or jobs hold them all; NoSlot after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return acquire_slot(store, kind, limit, job_id, owner)
        except NoSlot:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll)


def release_allocation(store: Store, alloc_id: str) -> None:
    with store.tx() as conn:
        conn.execute("UPDATE resource_allocations SET released_at = ? WHERE alloc_id = ? AND released_at IS NULL", (now(), alloc_id))
