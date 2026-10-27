"""State machine: legal transitions and a guard for each (blueprint §4.3).

Guards read only signed results from the evidence store and recompute the current
input identity; text in an agent reply or a file in the repo can never satisfy one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from .config import LoadedConfig
from .evidence import EvidenceStore
from .identities import InputIdentity, ResultKind, digest
from .state import Store
from .workspaces import WorkspaceManager


class S(StrEnum):
    FEATURE_RECEIVED = "FEATURE_RECEIVED"
    INTAKE = "INTAKE"
    COMPLEXITY_ASSESSMENT = "COMPLEXITY_ASSESSMENT"
    MICRO_SPEC = "MICRO_SPEC"
    ANALYSIS_ROUND = "ANALYSIS_ROUND"
    OPTIONS_DRAFT = "OPTIONS_DRAFT"
    AI_OPTION_REVIEW = "AI_OPTION_REVIEW"
    OPTION_SELECTED = "OPTION_SELECTED"
    AI_SPEC_EVALUATION = "AI_SPEC_EVALUATION"
    SINGLE_TASK_READY = "SINGLE_TASK_READY"
    DECOMPOSE = "DECOMPOSE"
    AI_DECOMPOSITION_REVIEW = "AI_DECOMPOSITION_REVIEW"
    TASK_GRAPH_READY = "TASK_GRAPH_READY"
    BUILD_TASK = "BUILD_TASK"
    CHECKS = "CHECKS"
    REVIEW = "REVIEW"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    FEATURE_ACCEPTANCE = "FEATURE_ACCEPTANCE"
    INTEGRATION = "INTEGRATION"
    READY_TO_MERGE = "READY_TO_MERGE"
    AWAITING_HUMAN_MERGE = "AWAITING_HUMAN_MERGE"
    MERGING = "MERGING"
    MERGED = "MERGED"
    # side states
    NEEDS_INFO = "NEEDS_INFO"
    BLOCKED_ENV = "BLOCKED_ENV"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    FAILED_NO_FEASIBLE_OPTION = "FAILED_NO_FEASIBLE_OPTION"
    ANALYSIS_LIMIT_REACHED = "ANALYSIS_LIMIT_REACHED"
    PAUSED = "PAUSED"


TERMINAL = {S.MERGED, S.CANCELLED, S.REJECTED, S.FAILED, S.FAILED_NO_FEASIBLE_OPTION}
# Stop states: the job waits for an operator or a configuration change.
HALTED = {S.ANALYSIS_LIMIT_REACHED, S.BUDGET_EXHAUSTED, S.NEEDS_INFO, S.BLOCKED_ENV, S.NEEDS_HUMAN, S.PAUSED, S.AWAITING_HUMAN_MERGE}
SIDE = {
    S.NEEDS_INFO, S.BLOCKED_ENV, S.NEEDS_HUMAN, S.BUDGET_EXHAUSTED, S.CANCELLING, S.FAILED,
    S.ANALYSIS_LIMIT_REACHED, S.PAUSED, S.FAILED_NO_FEASIBLE_OPTION,
}
ANALYSIS_STATES = {S.MICRO_SPEC, S.ANALYSIS_ROUND, S.OPTIONS_DRAFT, S.AI_OPTION_REVIEW, S.OPTION_SELECTED,
                   S.AI_SPEC_EVALUATION, S.DECOMPOSE, S.AI_DECOMPOSITION_REVIEW}

_MAIN: set[tuple[S, S]] = {
    (S.FEATURE_RECEIVED, S.INTAKE),
    (S.INTAKE, S.COMPLEXITY_ASSESSMENT),
    (S.COMPLEXITY_ASSESSMENT, S.MICRO_SPEC),
    (S.COMPLEXITY_ASSESSMENT, S.ANALYSIS_ROUND),
    (S.MICRO_SPEC, S.AI_SPEC_EVALUATION),
    (S.AI_SPEC_EVALUATION, S.SINGLE_TASK_READY),
    (S.AI_SPEC_EVALUATION, S.MICRO_SPEC),  # next round, trivial
    (S.ANALYSIS_ROUND, S.OPTIONS_DRAFT),
    (S.OPTIONS_DRAFT, S.AI_OPTION_REVIEW),
    (S.AI_OPTION_REVIEW, S.OPTIONS_DRAFT),  # bounded spike inside the same round
    (S.AI_OPTION_REVIEW, S.OPTION_SELECTED),
    (S.AI_OPTION_REVIEW, S.ANALYSIS_ROUND),
    (S.OPTION_SELECTED, S.AI_SPEC_EVALUATION),
    (S.AI_SPEC_EVALUATION, S.DECOMPOSE),
    (S.AI_SPEC_EVALUATION, S.ANALYSIS_ROUND),
    (S.AI_SPEC_EVALUATION, S.OPTION_SELECTED),  # next round revises the spec only; the decision stands
    (S.DECOMPOSE, S.AI_DECOMPOSITION_REVIEW),
    (S.AI_DECOMPOSITION_REVIEW, S.TASK_GRAPH_READY),
    (S.AI_DECOMPOSITION_REVIEW, S.ANALYSIS_ROUND),
    (S.AI_DECOMPOSITION_REVIEW, S.DECOMPOSE),  # next round re-plans; spec and decision stand
    (S.SINGLE_TASK_READY, S.BUILD_TASK),
    (S.TASK_GRAPH_READY, S.BUILD_TASK),
    (S.BUILD_TASK, S.CHECKS),
    (S.CHECKS, S.BUILD_TASK),
    (S.CHECKS, S.REVIEW),
    (S.CHECKS, S.ANALYSIS_ROUND),  # reclassification on the actual diff
    (S.REVIEW, S.BUILD_TASK),
    (S.REVIEW, S.TASK_ACCEPTED),
    (S.REVIEW, S.ANALYSIS_ROUND),
    (S.REVIEW, S.OPTIONS_DRAFT),
    (S.REVIEW, S.MICRO_SPEC),
    (S.REVIEW, S.CHECKS),  # gate evidence went stale (checks/policy changed): re-run, don't rebuild
    (S.FEATURE_ACCEPTANCE, S.MICRO_SPEC),
    (S.TASK_ACCEPTED, S.BUILD_TASK),
    (S.TASK_ACCEPTED, S.FEATURE_ACCEPTANCE),
    (S.FEATURE_ACCEPTANCE, S.INTEGRATION),
    (S.FEATURE_ACCEPTANCE, S.BUILD_TASK),
    (S.FEATURE_ACCEPTANCE, S.ANALYSIS_ROUND),
    (S.FEATURE_ACCEPTANCE, S.OPTIONS_DRAFT),
    (S.INTEGRATION, S.READY_TO_MERGE),
    (S.INTEGRATION, S.BUILD_TASK),
    (S.READY_TO_MERGE, S.MERGING),
    (S.READY_TO_MERGE, S.AWAITING_HUMAN_MERGE),
    (S.READY_TO_MERGE, S.INTEGRATION),
    (S.AWAITING_HUMAN_MERGE, S.MERGING),
    (S.AWAITING_HUMAN_MERGE, S.BUILD_TASK),
    (S.AWAITING_HUMAN_MERGE, S.REJECTED),
    (S.AWAITING_HUMAN_MERGE, S.INTEGRATION),
    (S.MERGING, S.MERGED),
    (S.MERGING, S.INTEGRATION),
    (S.CANCELLING, S.CANCELLED),
    # operator recovery out of halted states
    (S.ANALYSIS_LIMIT_REACHED, S.ANALYSIS_ROUND),
    (S.ANALYSIS_LIMIT_REACHED, S.MICRO_SPEC),
    (S.BLOCKED_ENV, S.BUILD_TASK),
    (S.NEEDS_INFO, S.INTAKE),
}


def legal(src: str, dst: str) -> bool:
    src_s, dst_s = S(src), S(dst)
    if (src_s, dst_s) in _MAIN:
        return True
    if src_s in TERMINAL or src_s is S.CANCELLING:
        return False
    if dst_s in SIDE:
        return True
    if src_s in (S.BUDGET_EXHAUSTED, S.PAUSED, S.NEEDS_HUMAN, S.BLOCKED_ENV, S.NEEDS_INFO):
        return dst_s not in TERMINAL  # resume to the recorded state; guards still apply
    return False


class TransitionRefused(RuntimeError):
    pass


@dataclass
class GuardResult:
    ok: bool
    reason: str


class Controller:
    def __init__(self, cfg: LoadedConfig, store: Store, evidence: EvidenceStore):
        self.cfg = cfg
        self.store = store
        self.evidence = evidence
        self.guards: dict[S, Callable[[str], GuardResult]] = {
            S.SINGLE_TASK_READY: self._g_spec_pass,
            S.DECOMPOSE: self._g_spec_pass,
            S.TASK_GRAPH_READY: self._g_decomposition_pass,
            S.BUILD_TASK: self._g_build_allowed,
            S.REVIEW: self._g_gate_pass,
            S.TASK_ACCEPTED: self._g_reviews_pass,
            S.FEATURE_ACCEPTANCE: self._g_all_tasks_accepted,
            S.INTEGRATION: self._g_acceptance_pass,
            S.READY_TO_MERGE: self._g_integration_pass,
            S.MERGING: self._g_merge_allowed,
            S.MERGED: self._g_merge_confirmed,
        }

    # --- identity ---------------------------------------------------------------------

    def identity(self, job_id: str, task_id: str | None = None) -> InputIdentity:
        job = self.store.job(job_id)
        ctx = json.loads(job["context_json"])
        head = base = tree = None
        if job["worktree"] and Path(job["worktree"]).exists():
            head = WorkspaceManager.head(Path(job["worktree"]))
            base = job["base_sha"]
            if ctx.get("candidate") and ctx["candidate"]["sha"] == head:
                tree = WorkspaceManager.tree(Path(job["worktree"]))
                base = ctx["candidate"]["base_sha"]
        return InputIdentity(
            repo_id=job["repo_id"],
            job_id=job_id,
            task_id=task_id,
            base_sha=base,
            head_sha=head,
            candidate_tree_sha=tree,
            feature_revision=job["scope_revision"],
            complexity_digest=ctx.get("complexity_digest"),
            scope_digest=ctx.get("scope_digest"),
            spec_digest=ctx.get("spec_digest"),
            decision_digest=ctx.get("decision_digest"),
            plan_digest=ctx.get("plan_digest"),
            policy_digest=self.cfg.policy_digest,
            checks_digest=self.checks_digest(),
            environment_digest=ctx.get("environment_digest"),
            acceptance_suite_digest=ctx.get("acceptance_suite_digest"),
        )

    def checks_digest(self) -> str:
        """Live digest of the trusted check manifest and installed skills: editing either invalidates results."""
        from .identities import directory_manifest

        return digest({"checks": directory_manifest(self.cfg.factory_dir / "checks"),
                       "skills": directory_manifest(self.cfg.factory_dir / "skills")})

    def criteria_digest(self, job_id: str) -> str:
        """What a human approval is pinned to: candidate, base, spec and policy."""
        ident = self.identity(job_id)
        return digest({"head": ident.head_sha, "base": ident.base_sha, "spec": ident.spec_digest, "policy": ident.policy_digest})

    # --- transitions ------------------------------------------------------------------

    def transition(self, job_id: str, to: S, reason: str = "", **fields) -> None:
        job = self.store.job(job_id)
        src = job["state"]
        if not legal(src, to):
            raise TransitionRefused(f"illegal transition {src} -> {to}")
        guard = self.guards.get(to)
        if guard is not None and to not in SIDE:
            result = guard(job_id)
            if not result.ok:
                raise TransitionRefused(f"guard for {to} refused: {result.reason}")
        with self.store.tx() as conn:
            version = self.store.cas_update_job(conn, job_id, job["version"], state=str(to), **fields)
            self.store.event(
                conn, job_id, "state.transition", {"reason": reason, **({"blocked": fields["blocked_reason"]} if fields.get("blocked_reason") else {})},
                from_state=src, to_state=str(to), from_version=job["version"], to_version=version,
            )

    # --- guards -----------------------------------------------------------------------

    def _ctx(self, job_id: str) -> dict:
        return self.store.job_context(job_id)

    def _g_spec_pass(self, job_id: str) -> GuardResult:
        ok, why = self.evidence.current_pass(job_id, ResultKind.SPEC_EVALUATION, self.identity(job_id))
        return GuardResult(ok, why)

    def _g_decomposition_pass(self, job_id: str) -> GuardResult:
        ok, why = self.evidence.current_pass(job_id, ResultKind.DECOMPOSITION_REVIEW, self.identity(job_id))
        if not ok:
            return GuardResult(False, why)
        return self._g_spec_pass(job_id)

    def _g_build_allowed(self, job_id: str) -> GuardResult:
        job = self.store.job(job_id)
        spec = self._g_spec_pass(job_id)
        if not spec.ok:
            return spec
        if job["path"] != "trivial":
            dec = self._g_decomposition_pass(job_id)
            if not dec.ok:
                return dec
        if self.store.one("SELECT COUNT(*) FROM tasks WHERE job_id = ?", (job_id,))[0] == 0:
            return GuardResult(False, "no tasks")
        return GuardResult(True, "ok")

    def _g_gate_pass(self, job_id: str) -> GuardResult:
        task_id = self._ctx(job_id).get("current_task")
        ok, why = self.evidence.current_pass(job_id, ResultKind.GATE, self.identity(job_id), task_id)
        return GuardResult(ok, why)

    def _g_reviews_pass(self, job_id: str) -> GuardResult:
        ctx = self._ctx(job_id)
        task_id = ctx.get("current_task")
        required = ctx.get("required_reviews", {}).get(task_id or "", [])
        if not required:
            return GuardResult(False, "no required reviews routed")
        ident = self.identity(job_id)
        for kind in [ResultKind.GATE, *map(ResultKind, required)]:
            ok, why = self.evidence.current_pass(job_id, kind, ident, task_id)
            if not ok:
                return GuardResult(False, why)
        blockers = self.evidence.open_blocking_findings(job_id, task_id)
        if blockers:
            return GuardResult(False, f"open blocking findings: {[b['finding_id'] for b in blockers]}")
        return GuardResult(True, "ok")

    def _g_all_tasks_accepted(self, job_id: str) -> GuardResult:
        rows = self.store.all("SELECT task_id, state FROM tasks WHERE job_id = ?", (job_id,))
        pending = [r["task_id"] for r in rows if r["state"] != "ACCEPTED"]
        return GuardResult(not pending and bool(rows), f"tasks not accepted: {pending}" if pending else "ok")

    def _g_analysis_current(self, job_id: str) -> GuardResult:
        """Spec (and, off the fast path, decomposition) evaluations must still match current inputs."""
        if self.store.job(job_id)["path"] == "trivial":
            return self._g_spec_pass(job_id)
        return self._g_decomposition_pass(job_id)

    def _g_acceptance_pass(self, job_id: str) -> GuardResult:
        analysis_ok = self._g_analysis_current(job_id)
        if not analysis_ok.ok:
            return analysis_ok
        ok, why = self.evidence.current_pass(job_id, ResultKind.FEATURE_ACCEPTANCE, self.identity(job_id))
        return GuardResult(ok, why)

    def _g_integration_pass(self, job_id: str) -> GuardResult:
        analysis_ok = self._g_analysis_current(job_id)
        if not analysis_ok.ok:
            return analysis_ok
        ok, why = self.evidence.current_pass(job_id, ResultKind.INTEGRATION, self.identity(job_id))
        return GuardResult(ok, why)

    def human_required(self, job_id: str) -> bool:
        merge = self.cfg.factory.merge
        risk = self.store.job(job_id)["risk"] or "high"
        if merge.human_interaction == "before_merge":
            return True
        if merge.human_interaction == "on_risk":
            return risk in merge.human_review_risk_levels
        return False

    def valid_approval(self, job_id: str) -> bool:
        row = self.store.one(
            "SELECT * FROM approvals WHERE job_id = ? AND decision = 'approve' AND valid = 1 ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        )
        return row is not None and row["criteria_digest"] == self.criteria_digest(job_id)

    def _g_merge_allowed(self, job_id: str) -> GuardResult:
        if not self.cfg.factory.merge.enabled:
            return GuardResult(False, "merge.enabled is false (local result only)")
        integ = self._g_integration_pass(job_id)
        if not integ.ok:
            return integ
        if self.human_required(job_id) and not self.valid_approval(job_id):
            return GuardResult(False, "human approval for the current candidate is required")
        return GuardResult(True, "ok")

    def _g_merge_confirmed(self, job_id: str) -> GuardResult:
        merged = self._ctx(job_id).get("merge_result")
        return GuardResult(bool(merged and merged.get("merged")), "merge not confirmed by the remote")
