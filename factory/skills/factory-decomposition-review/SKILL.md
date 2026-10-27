---
name: factory-decomposition-review
description: Use in controller state AI_DECOMPOSITION_REVIEW for standard/complex features to independently check that the task DAG covers every requirement, is acyclic, has atomic leaves with clear contracts, and records future needs without speculative work.
---

# factory-decomposition-review

Follow ../_shared/contract.md

## Purpose

Verify that the plan from `factory-plan` turns the evaluated spec into executable
tasks without losing the goal. You review the whole DAG and every leaf against the
eight points of `references/checklist.md`. A current PASS (together with a current
spec-evaluation PASS) lets the controller mark `TASK_GRAPH_READY`; there is no human
plan approval. Splitting work must never hide a scope reduction. For trivial
features this review is not run; the controller records
`not-applicable: single atomic task` backed by the classification.

## Required inputs

- Original request, spec package (requirements with IDs, acceptance criteria,
  `intent-map.yaml`, `acceptance-map.yaml`) and the current spec-evaluation PASS.
- Plan: `tasks.md`, the `factory-plan` payload (tasks with `depends_on`,
  `file_scope`, `verification`, ...), `design.md`, `test-plan.md`, `future-needs.md`.
- Selected option (`decision.json`) and `factory-repo-map` facts.
- Limits: `decomposition.max_depth` (3), `max_leaf_tasks_per_feature` (20),
  `target_task_minutes` (45), per-task attempt limit, remaining feature budget.
- `plan_digest`, `spec_digest`, `analysis_round_id`, previous findings.

## Procedure

1. Confirm digests: the plan must target the evaluated spec digest. Mismatch => `UNKNOWN`.
2. Build `requirements_map` yourself from task `requirement_ids`. Every REQ needs at
   least one task and a verification; every task needs a REQ or a stated necessary dependency.
3. Check the DAG: no cycles, no missing or dangling `depends_on`, shared contracts
   before consumers, a clear critical path. Feature-level end-to-end acceptance is the
   controller's FEATURE_ACCEPTANCE stage; do not require a planned task for it.
   Over-decomposition (test-only tasks, empty `file_scope`, several tasks a single
   executor would finish within the target) is a blocking finding: merge them.
   Set `dag_ok` from this check only.
4. Per leaf: one executor can finish it within budget; inputs/outputs explicit;
   acceptance observable; `verification` names real checks; estimate plausible
   (45 minutes is a heuristic, not proof of atomicity).
5. Conflicts: overlapping `file_scope` between tasks marked `parallel_safe_with`,
   uncontrolled lockfile/API changes, duplicated work.
6. Vertical integration: API/UI/data joined incrementally, not separate big layers at the end.
7. Completeness: migrations with rollback, seed/test data, error paths,
   observability and docs within feature scope.
8. Future needs: statuses `required_now | committed_next | hypothetical`; only
   `required_now` is built; `committed_next` appears as constraints. A hypothetical
   feature being built is a blocking finding.
9. Over depth 3 or 20 leaves: CHANGES with a staging/epic proposal finding; never
   recommend dropping requirements.

## Output

Envelope per contract, `kind: "decomposition-review"`. Payload:

```json
{
  "requirements_map": {"REQ-1": ["TASK-02", "TASK-05"]},
  "per_task": [{"task_id": "TASK-02", "verdict": "PASS", "notes": "..."}],
  "dag_ok": true
}
```

- `per_task[].verdict` is `PASS` or `CHANGES`; for PASS/CHANGES every task of the plan
  appears exactly once; for BLOCKED at least the tasks cited in findings appear.
- `PASS`: all requirements mapped, `dag_ok: true`, every task PASS, no open blocking finding.
- `CHANGES`: fixable gaps (unmapped REQ, cycle, oversized leaf, file conflict, speculative task).
- `BLOCKED`: the plan cannot be made executable within limits without an operator
  decision (e.g. raise leaf limit or split into epics) or a missing permission.

## Limits

- Shares the feature analysis limit of 3 rounds; a replan or new subtasks do not reset it.
- Do not rewrite the plan; report findings with `expected_fix`.
- Do not accept a decomposition that removes or weakens a requirement, and do not
  suggest building hypothetical features.
- Green leaf tasks never imply the parent feature is done; the controller's FEATURE_ACCEPTANCE
  stage checks the whole feature against the original request.
