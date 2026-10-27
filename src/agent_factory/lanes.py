"""Parallel task lanes: independent ready tasks build and get reviewed at the same time.

A wave takes up to ``execution.max_builders_per_job`` ready tasks whose file scopes do not
overlap. Each task gets its own lane: a detached worktree at the job head, its own builder,
trusted checks and reviewers (results are bound to the lane head). The controller then
integrates the lanes one by one into the job worktree, in plan order: a lane is accepted
only if its signed gate and review results are current, it merges without conflict, and
the trusted checks pass on the combined tree. A lane that conflicts or breaks the combined
checks goes back to REWORK and is rebuilt on the new head; its attempt is not counted.
The job stays in BUILD_TASK during a wave; the job worktree only ever moves forward through
the controller, never through an agent.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import budgets
from .controller import S
from .evidence import Producer
from .identities import ResultKind, digest
from .policy import evaluate_diff, is_test_file, max_risk
from .runner import GateVerdict
from .schemas import TaskSpec
from .scheduler import ready_tasks
from .workspaces import AUTHOR, WorkspaceManager, git

if TYPE_CHECKING:
    from .factory import Factory

_GLOB = "*?["
MAX_PARKS = 2  # inconclusive review rounds a built lane may be parked for before a human looks


def _literal_prefix(pattern: str) -> str:
    cut = min((pattern.index(c) for c in _GLOB if c in pattern), default=len(pattern))
    return pattern[:cut].rstrip("/")


def _code_scope(scope: list[str]) -> list[str]:
    # Test files are in every task's scope (policy); a clash there shows up as a merge conflict.
    return [p for p in scope if not is_test_file(p) and _literal_prefix(p) not in ("tests", "test")]


def scopes_overlap(a: list[str], b: list[str]) -> bool:
    """Conservative: an empty code scope, or one path prefix containing the other, overlaps."""
    a, b = _code_scope(a), _code_scope(b)
    if not a or not b:
        return True
    for x in map(_literal_prefix, a):
        for y in map(_literal_prefix, b):
            if not x or not y or x.startswith(y) or y.startswith(x):
                return True
    return False


def pick_wave(tasks: list[tuple[str, list[str]]], limit: int) -> list[str]:
    """Ready tasks in plan order whose scopes are pairwise disjoint, at most ``limit``."""
    chosen: list[tuple[str, list[str]]] = []
    for task_id, scope in tasks:
        if len(chosen) == limit:
            break
        if all(not scopes_overlap(scope, other) for _, other in chosen):
            chosen.append((task_id, scope))
    return [task_id for task_id, _ in chosen]


@dataclass
class LaneOutcome:
    task_id: str
    kind: str  # accepted | failed | analysis | inconclusive | budget | blocked
    head: str | None = None
    verdict: str = ""
    reason: str = ""
    excerpt: dict[str, Any] = field(default_factory=dict)
    risk: str = "low"
    surfaces: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)
    blocked_state: S | None = None
    built: bool = True  # False when the lane only re-ran the reviews of parked work


class ParallelLanes:
    """Mixed into Factory; methods use its agents, runner, evidence and policy."""

    def _lane_path(self: "Factory", job_id: str, task_id: str) -> Path:
        # A direct child of worktrees/: every other job and lane is a denied read for this lane's agents.
        return self.workspaces.root / f"{job_id}--{task_id.lower()}"

    # --- pool entry ---------------------------------------------------------------------

    def _lane_candidates(self: "Factory", job_id: str) -> list[tuple[str, list[str]]]:
        """Ready tasks in plan order with their scopes; a task out of attempts fails on its own."""
        max_attempts = self.cfg.workflow.implementation.max_attempts_per_leaf_task
        parked = self._ctx(job_id).get("parked_lanes", {})
        candidates = []
        for task_id in ready_tasks(self.store, job_id):
            task = self._task(job_id, task_id)
            if parked.get(task_id, {}).get("count", 0) >= MAX_PARKS:
                continue  # waits for the operator (see _after_wave)
            if task["attempts"] >= max_attempts and task_id not in parked:
                self._fail_task(job_id, task_id, f"used all {max_attempts} attempts")  # independent tasks go on
                continue
            candidates.append((task_id, TaskSpec.model_validate(json.loads(task["spec_json"])).file_scope))
        return candidates

    def _parallel_wave(self: "Factory", job_id: str) -> list[str]:
        """Tasks to start the lane pool with, or [] to build sequentially."""
        limit = self.cfg.factory.execution.max_builders_per_job
        if limit < 2:
            return []
        wave = pick_wave(self._lane_candidates(job_id), limit)
        parked = self._ctx(job_id).get("parked_lanes", {})
        return wave if len(wave) >= 2 or any(t in parked for t in wave) else []

    def _recover_wave(self: "Factory", job_id: str) -> None:
        """A controller that died with lanes running left them behind: stop their agents, drop the lanes."""
        wave = self._ctx(job_id).get("parallel_wave")
        if not wave:
            return
        repo = Path(self._job(job_id)["repo_path"])
        parked = self._ctx(job_id).get("parked_lanes", {})
        for task_id in wave:
            for row in self.store.all(
                "SELECT attempt_id, pid FROM attempts WHERE job_id = ? AND task_id = ? AND state = 'running'", (job_id, task_id)
            ):
                if row["pid"]:
                    try:
                        os.killpg(row["pid"], signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                with self.store.tx() as conn:
                    conn.execute("UPDATE attempts SET state = 'abandoned' WHERE attempt_id = ?", (row["attempt_id"],))
            refund = 0 if task_id in parked else 1  # a parked lane's re-review never took an attempt
            with self.store.tx() as conn:
                conn.execute(
                    "UPDATE tasks SET state = 'REWORK', attempts = MAX(attempts - ?, 0) WHERE job_id = ? AND task_id = ? AND state = 'BUILDING'",
                    (refund, job_id, task_id),
                )
            self._remove_lane(repo, self._lane_path(job_id, task_id))
        with self.store.tx() as conn:
            self.store.event(conn, job_id, "lanes.recovered", {"tasks": wave})
        self._set_ctx(job_id, parallel_wave=None)

    def _run_wave(self: "Factory", job_id: str, first: list[str]) -> None:
        """Lane pool: up to max_builders_per_job lanes; each finished lane is integrated at once and its
        slot goes to the next ready task (scopes of running lanes never overlap)."""
        limit = self.cfg.factory.execution.max_builders_per_job
        job = self._job(job_id)
        repo, wt = Path(job["repo_path"]), self._worktree(job_id)
        links = self._checks(job_id).link_from_checkout
        running: dict[Future, tuple[str, Path, str, list[str]]] = {}
        outcomes: list[LaneOutcome] = []
        stop = False
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="lane") as pool:
            while True:
                if not stop:
                    order = {t: i for i, t in enumerate(first)}
                    candidates = sorted(self._lane_candidates(job_id), key=lambda c: order.get(c[0], len(order)))
                    for task_id, scope in candidates:
                        if len(running) >= limit:
                            break
                        busy = list(running.values())
                        if any(task_id == b[0] or scopes_overlap(scope, b[3]) for b in busy):
                            continue
                        path, start = self._start_lane(job_id, task_id, repo, wt, links)  # git bookkeeping on one thread
                        running[pool.submit(self._lane_run, job_id, task_id, path)] = (task_id, path, start, scope)
                        self._set_ctx(job_id, parallel_wave=[b[0] for b in running.values()])
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    task_id, path, start, _ = running.pop(future)
                    outcome = future.result()
                    outcomes.append(outcome)
                    try:
                        self._integrate_lane(job_id, outcome, path, start)
                    finally:
                        self._remove_lane(repo, path)
                        self._set_ctx(job_id, parallel_wave=[b[0] for b in running.values()] or None)
                    if outcome.kind in ("budget", "blocked", "analysis"):
                        stop = True  # let the running lanes finish, start no new ones
        self._sync_head(job_id)
        self._after_wave(job_id, outcomes)

    def _start_lane(self: "Factory", job_id: str, task_id: str, repo: Path, wt: Path, links: list[str]) -> tuple[Path, str]:
        parked = self._ctx(job_id).get("parked_lanes", {}).get(task_id)
        head = parked["head"] if parked else WorkspaceManager.head(wt)
        with self.store.tx() as conn:
            if parked:  # only the reviews re-run: no new attempt
                conn.execute("UPDATE tasks SET state = 'BUILDING' WHERE job_id = ? AND task_id = ?", (job_id, task_id))
            else:
                conn.execute("UPDATE tasks SET state = 'BUILDING', attempts = attempts + 1 WHERE job_id = ? AND task_id = ?",
                             (job_id, task_id))
                row = conn.execute("SELECT version, build_attempts FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                self.store.cas_update_job(conn, job_id, row["version"], build_attempts=row["build_attempts"] + 1)
            self.store.event(conn, job_id, "lanes.started", {"task": task_id, "start_sha": head, "parked": bool(parked)})
        path = self._lane_path(job_id, task_id)
        self._remove_lane(repo, path)
        git(repo, "worktree", "add", "--quiet", "--detach", str(path), head)
        WorkspaceManager.link_from_checkout(repo, path, links)
        return path, parked["start"] if parked else head

    # --- one lane (runs on a worker thread) ---------------------------------------------

    def _lane_run(self: "Factory", job_id: str, task_id: str, lane: Path) -> LaneOutcome:
        self._lane.worktree = lane
        built = task_id not in self._ctx(job_id).get("parked_lanes", {})
        try:
            outcome = self._lane_body(job_id, task_id, lane, built)
        except budgets.BudgetExhausted as exc:
            outcome = LaneOutcome(task_id, "budget", reason=str(exc))
        except Exception as exc:  # StageBlocked and anything else: stop this lane, never the others
            from .factory import StageBlocked

            if isinstance(exc, StageBlocked):
                outcome = LaneOutcome(task_id, "blocked", reason=exc.reason, blocked_state=exc.state)
            else:
                outcome = LaneOutcome(task_id, "blocked", reason=f"lane crashed: {type(exc).__name__}: {exc}", blocked_state=S.NEEDS_HUMAN)
        finally:
            self._lane.worktree = None
        outcome.built = built
        return outcome

    def _lane_body(self: "Factory", job_id: str, task_id: str, lane: Path, build: bool) -> LaneOutcome:
        job = self._job(job_id)
        task = self._task(job_id, task_id)
        spec = TaskSpec.model_validate(json.loads(task["spec_json"]))
        parked = self._ctx(job_id).get("parked_lanes", {}).get(task_id)
        start = parked["start"] if parked else WorkspaceManager.head(lane)
        if build:
            try:
                self._run_builder(job_id, task_id, task, self._ctx(job_id))
            except budgets.BudgetExhausted as exc:
                return LaneOutcome(task_id, "budget", reason=str(exc))
            WorkspaceManager.commit_all(lane, f"factory: {task_id} attempt {task['attempts']} (lane)")
        head = WorkspaceManager.head(lane)
        if not self._is_ancestor(lane, start, head):
            return self._lane_fail(job_id, task_id, "FAIL_POLICY", "history before the build was rewritten")
        report = evaluate_diff(WorkspaceManager.diff(lane, start), self.cfg.factory.policy, task_file_scope=spec.file_scope or None)
        lane_diff = [d for d in WorkspaceManager.diff(lane, job["base_sha"]) if not d.path.startswith("openspec/")]
        risk_report = evaluate_diff(lane_diff, self.cfg.factory.policy)
        risk = max_risk(job["risk"] or "low", risk_report.risk)
        surfaces = sorted(set(self._ctx(job_id).get("surfaces", [])) | risk_report.surfaces)
        if not report.ok:
            return self._lane_fail(job_id, task_id, "FAIL_POLICY", "; ".join(report.violations))
        if not lane_diff:
            return self._lane_fail(job_id, task_id, "FAIL_CODE", "feature has no change versus base")
        ident = self._identity(job_id, task_id)
        gate_ok, _ = self.evidence.current_pass(job_id, ResultKind.GATE, ident, task_id)
        if not gate_ok:  # a parked lane keeps its gate result while nothing changed
            gate = self._gate(job_id, "task", lane, head_sha=head, tree_sha=WorkspaceManager.tree(lane))
            self._record(job_id, ResultKind.GATE, str(gate.verdict), gate.as_payload(), producer=Producer("trusted-runner", "gate"), task_id=task_id)
            if gate.verdict not in (GateVerdict.PASS, GateVerdict.FAIL_CODE, GateVerdict.FAIL_POLICY, GateVerdict.TIMEOUT):
                return LaneOutcome(task_id, "blocked", reason=f"gate {gate.verdict}: {gate.reason}", blocked_state=S.BLOCKED_ENV)
            if gate.verdict is not GateVerdict.PASS:
                return self._lane_fail(job_id, task_id, str(gate.verdict), gate.reason, {c.name: c.excerpt[-1500:] for c in gate.checks})
        required = self._required_reviews(surfaces)
        for _ in range(3):  # inconclusive reviews re-run, as in the sequential REVIEW state
            verdicts, error_class = self._run_reviews(job_id, task_id, json.loads(task["spec_json"]), lane, ident, required,
                                                      self._security_depth(risk, surfaces), surfaces)
            if not any(v in ("UNKNOWN", "BLOCKED") for v in verdicts.values()) or any(v == "CHANGES" for v in verdicts.values()):
                break
        else:
            return LaneOutcome(task_id, "inconclusive", head=head, reason=f"reviews inconclusive after 3 tries: {verdicts}")
        if all(v == "PASS" for v in verdicts.values()):
            return LaneOutcome(task_id, "accepted", head=head, risk=risk, surfaces=surfaces, required=required)
        if error_class in ("spec", "decomposition"):
            return LaneOutcome(task_id, "analysis", reason=f"review found a {error_class} defect: {verdicts}")
        return self._lane_fail(job_id, task_id, "CHANGES", f"review verdicts {verdicts}")

    def _lane_fail(self: "Factory", job_id: str, task_id: str, verdict: str, reason: str, excerpt: dict | None = None) -> LaneOutcome:
        if verdict == "FAIL_POLICY":
            self._record(job_id, ResultKind.GATE, verdict, {"reason": reason}, task_id=task_id)
        return LaneOutcome(task_id, "failed", verdict=verdict, reason=reason, excerpt=excerpt or {})

    # --- integration (controller thread) --------------------------------------------------

    def _lane_evidence_ok(self: "Factory", job_id: str, outcome: LaneOutcome, lane: Path) -> tuple[bool, str]:
        """Re-verify the signed lane results; an agent's word never accepts a task."""
        ident = self.controller.identity(job_id, outcome.task_id, worktree=lane)
        if ident.head_sha != outcome.head:
            return False, "lane head moved after its reviews"
        for kind in [ResultKind.GATE, *map(ResultKind, outcome.required)]:
            ok, why = self.evidence.current_pass(job_id, kind, ident, outcome.task_id)
            if not ok:
                return False, why
        blockers = self.evidence.open_blocking_findings(job_id, outcome.task_id)
        if blockers:
            return False, f"open blocking findings: {[b['finding_id'] for b in blockers]}"
        return True, "ok"

    def _integrate_lane(self: "Factory", job_id: str, outcome: LaneOutcome, lane: Path, start: str) -> None:
        wt = self._worktree(job_id)
        task_id = outcome.task_id
        if outcome.kind == "inconclusive":
            return self._lane_park(job_id, outcome, lane)
        self._unpark(job_id, task_id)
        if outcome.kind != "accepted":
            return self._lane_settle(job_id, outcome, lane, start)
        ok, why = self._lane_evidence_ok(job_id, outcome, lane)
        if not ok:
            return self._lane_settle(job_id, LaneOutcome(task_id, "failed", verdict="REVIEW", reason=why, built=outcome.built), lane, start)
        before = WorkspaceManager.head(wt)
        merged = subprocess.run(
            ["git", *AUTHOR, "merge", "--no-ff", "--no-edit", "-m", f"factory: {task_id} (parallel lane)", outcome.head],
            cwd=wt, capture_output=True, text=True,
        )
        if merged.returncode != 0:
            subprocess.run(["git", "merge", "--abort"], cwd=wt, capture_output=True)
            return self._lane_requeue(job_id, task_id, "MERGE_CONFLICT",
                                      f"conflicts with a task accepted while this lane ran: {merged.stdout[-500:]}", outcome.built)
        if WorkspaceManager.tree(wt) != git(lane, "rev-parse", f"{outcome.head}^{{tree}}"):
            # Combined with lanes accepted meanwhile: the trusted checks decide on the combined tree.
            gate = self._gate(job_id, "task", wt, head_sha=WorkspaceManager.head(wt), tree_sha=WorkspaceManager.tree(wt))
            self._record(job_id, ResultKind.GATE, str(gate.verdict), gate.as_payload(),
                         ident=self.controller.identity(job_id, task_id), producer=Producer("trusted-runner", "gate"), task_id=task_id)
            if gate.verdict is not GateVerdict.PASS:
                git(wt, "reset", "--quiet", "--hard", before)
                return self._lane_requeue(job_id, task_id, f"COMBINED_{gate.verdict}",
                                          f"checks fail when combined with tasks accepted meanwhile: {gate.reason}", outcome.built)
        head = self._sync_head(job_id)
        with self.store.tx() as conn:
            conn.execute("UPDATE tasks SET state = 'ACCEPTED', accepted_head_sha = ? WHERE job_id = ? AND task_id = ?",
                         (head, job_id, task_id))
            self.store.event(conn, job_id, "task.accepted", {"task_id": task_id, "lane_head": outcome.head, "head": head})
        routed = self._ctx(job_id).get("required_reviews", {})
        routed[task_id] = outcome.required
        self._set_ctx(job_id, required_reviews=routed)

    def _lane_park(self: "Factory", job_id: str, outcome: LaneOutcome, lane: Path) -> None:
        """Inconclusive reviews: keep the built lane and re-run only its reviews later (no rebuild)."""
        parked = self._ctx(job_id).get("parked_lanes", {})
        # "start" is where the build began: the builder's diff (scope, test rules) is checked against it.
        entry = parked.get(outcome.task_id) or {"start": self._lane_start_of(job_id, outcome.task_id), "count": 0}
        entry.update(head=outcome.head, count=entry["count"] + 1)
        git(lane, "update-ref", f"refs/factory/parked/{job_id}/{outcome.task_id}", outcome.head)  # keeps the commits
        parked[outcome.task_id] = entry
        self._set_ctx(job_id, parked_lanes=parked)
        with self.store.tx() as conn:
            conn.execute("UPDATE tasks SET state = 'REWORK' WHERE job_id = ? AND task_id = ?", (job_id, outcome.task_id))
            self.store.event(conn, job_id, "task.parked", {"task_id": outcome.task_id, "head": outcome.head, "reason": outcome.reason[:500]})

    def _lane_start_of(self: "Factory", job_id: str, task_id: str) -> str:
        for event in reversed(self.store.events_for(job_id)):
            if event["kind"] == "lanes.started":
                payload = json.loads(event["payload_json"])
                if payload.get("task") == task_id:
                    return payload["start_sha"]
        raise LookupError(f"no lane start recorded for {task_id}")

    def _unpark(self: "Factory", job_id: str, task_id: str) -> None:
        parked = self._ctx(job_id).get("parked_lanes", {})
        if parked.pop(task_id, None) is not None:
            git(self._worktree(job_id), "update-ref", "-d", f"refs/factory/parked/{job_id}/{task_id}", check=False)
            self._set_ctx(job_id, parked_lanes=parked)

    def _lane_requeue(self: "Factory", job_id: str, task_id: str, verdict: str, reason: str, built: bool) -> None:
        """Not the task's fault (another lane won): rebuild on the new head without using an attempt."""
        last = self._ctx(job_id).get("last_failure", {})
        last[task_id] = {"verdict": verdict, "reason": reason, "excerpt": {}}
        self._set_ctx(job_id, last_failure=last)
        with self.store.tx() as conn:
            conn.execute("UPDATE tasks SET state = 'REWORK', attempts = attempts - ? WHERE job_id = ? AND task_id = ?",
                         (1 if built else 0, job_id, task_id))
            self.store.event(conn, job_id, "task.requeued", {"task_id": task_id, "verdict": verdict, "reason": reason[:500]})

    def _lane_settle(self: "Factory", job_id: str, outcome: LaneOutcome, lane: Path, start: str) -> None:
        task_id = outcome.task_id
        if outcome.kind in ("budget", "blocked"):
            # The lane stopped before a verdict: the attempt is returned, the task waits for resume.
            with self.store.tx() as conn:
                conn.execute("UPDATE tasks SET attempts = attempts - ? WHERE job_id = ? AND task_id = ?",
                             (1 if outcome.built else 0, job_id, task_id))
                conn.execute("UPDATE tasks SET state = CASE WHEN attempts > 0 THEN 'REWORK' ELSE 'PENDING' END"
                             " WHERE job_id = ? AND task_id = ?", (job_id, task_id))
            return
        patch = git(lane, "diff", f"{start}..HEAD", check=False)
        self.evidence.put(patch.encode(), "rejected-attempt-patch", Producer("controller", "lane"), job_id)
        ctx = self._ctx(job_id)
        fps = ctx.get("failure_fingerprints", {})
        fps.setdefault(task_id, []).append(digest([outcome.verdict, outcome.reason])[:16])
        last = ctx.get("last_failure", {})
        last[task_id] = {"verdict": outcome.verdict or outcome.kind.upper(), "reason": outcome.reason, "excerpt": outcome.excerpt}
        self._set_ctx(job_id, failure_fingerprints=fps, last_failure=last)
        with self.store.tx() as conn:
            conn.execute("UPDATE tasks SET state = 'REWORK' WHERE job_id = ? AND task_id = ?", (job_id, task_id))
            self.store.event(conn, job_id, "task.rework", {"task_id": task_id, "kind": outcome.kind, "reason": outcome.reason[:500]})

    def _after_wave(self: "Factory", job_id: str, outcomes: list[LaneOutcome]) -> None:
        job = self._job(job_id)
        accepted = [o for o in outcomes if o.kind == "accepted"
                    and self._task(job_id, o.task_id)["state"] == "ACCEPTED"]
        if accepted:
            risk = max_risk(job["risk"] or "low", *(o.risk for o in accepted))
            surfaces = sorted(set(self._ctx(job_id).get("surfaces", [])).union(*(o.surfaces for o in accepted)))
            if risk != job["risk"]:
                with self.store.tx() as conn:
                    version = conn.execute("SELECT version FROM jobs WHERE job_id = ?", (job_id,)).fetchone()[0]
                    self.store.cas_update_job(conn, job_id, version, risk=risk)
                    self.store.event(conn, job_id, "risk.raised", {"from": job["risk"], "to": risk, "reasons": ["parallel lanes"]})
            self._set_ctx(job_id, surfaces=surfaces)
        self._checkpoint(job_id, f"lane pool: {[(o.task_id, o.kind) for o in outcomes]}")
        parked = self._ctx(job_id).get("parked_lanes", {})
        stuck = [t for t, entry in parked.items() if entry["count"] >= MAX_PARKS]
        for kind in ("blocked", "budget", "analysis"):
            stop = next((o for o in outcomes if o.kind == kind), None)
            if stop is None:
                continue
            if kind == "budget":
                raise budgets.BudgetExhausted(stop.reason)
            if kind == "analysis":
                return self._back_to_analysis(job_id, stop.reason)
            from .factory import StageBlocked

            raise StageBlocked(stop.blocked_state or S.NEEDS_HUMAN, f"{stop.task_id}: {stop.reason}")
        if stuck:
            from .factory import StageBlocked

            raise StageBlocked(S.NEEDS_HUMAN, f"reviews of {stuck} stayed inconclusive after {MAX_PARKS} parked re-reviews; the built "
                                              "work is kept (refs/factory/parked); read the findings, then `factory resume`")
        if not ready_tasks(self.store, job_id) and all(
            r["state"] == "ACCEPTED" for r in self.store.all("SELECT state FROM tasks WHERE job_id = ?", (job_id,))
        ):
            self.controller.transition(job_id, S.FEATURE_ACCEPTANCE, "the lane pool accepted the last tasks")

    @staticmethod
    def _remove_lane(repo: Path, path: Path) -> None:
        if path.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=repo, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
