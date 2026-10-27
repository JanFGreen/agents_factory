---
name: factory-plan
description: Use in controller state DECOMPOSE, after the selected option and a current spec-evaluation PASS, to produce the design, shared contracts and task DAG for a standard or complex feature.
---

# factory-plan

Follow ../_shared/contract.md

## Purpose

Turn the AI-selected option and the evaluated spec into an executable plan:
`design.md`, shared contracts, and a DAG of small verifiable leaf tasks plus a
final feature-level acceptance task. Split by outcomes and dependencies, not by
files. The plan is reviewed by `factory-decomposition-review`; you never approve
your own plan. Trivial features do not use this skill (a single task comes from
the micro-spec).

## Required inputs

- Frozen feature snapshot (original request, goal, constraints, acceptance criteria, future directions).
- `decision.json` (selected option) and the current `spec-evaluation.json` PASS with matching `spec_digest`.
- OpenSpec change: `proposal.md`, `specs/**/spec.md`, `intent-map.yaml`, `test-plan.md` if already present.
- Repository map from `factory-repo-map` (modules, commands, trust boundaries).
- Decomposition policy: `max_depth` 3, `max_leaf_tasks_per_feature` 20, `target_task_minutes` 45.
- `analysis_round_id` and, in rounds 2-3, the open findings of the previous decomposition review.

## Procedure

1. Verify inputs are current: `inputs.spec_evaluation.spec_digest` equals the snapshot
   `spec_digest`. Mismatch => `UNKNOWN`, do not plan on stale input. The decision and the
   spec-evaluation PASS arrive in `<inputs>` (`decision`, `spec_evaluation`), not as files.
   If the spec itself looks wrong (e.g. a requirement that makes the tests impossible),
   still plan the closest faithful DAG and add a non-blocking finding; do not stop.
2. Design for the selected option only. If it proves unworkable, return `CHANGES`
   with a finding routed to option selection; never switch option silently.
3. Identify shared contracts (API shape, schema, events, config keys) and make each
   a task that precedes all its consumers.
4. Build vertical increments early: connect API, data and UI incrementally rather
   than finishing isolated layers. Each leaf task has: one or more `requirement_ids`
   (or a necessary dependency it serves), `inputs`, `outputs`, disjoint `file_scope`,
   `risk`, `estimated_minutes`, an observable `acceptance` and named `verification`.
5. Include, within feature scope: migrations with rollback, seed/test data, error
   paths, observability and docs. Do not add speculative features.
6. Use the fewest tasks that keep each leaf within `target_task_minutes`: every task
   pays for a build and a full review round. A feature that one executor can finish
   within the target is exactly one task. Each task owns the tests for its own
   behavior (test files are always in scope for the builder); never plan test-only
   tasks or tasks with an empty `file_scope`. End-to-end acceptance of the whole
   feature against the original request is the controller's FEATURE_ACCEPTANCE stage;
   do not add a separate end-to-end task for it. Green leaf tasks never mark the parent done.
7. Mark `parallel_safe_with` only when file scopes and contracts are disjoint and no
   lockfile/API change is shared.
8. Classify every future direction as `required_now`, `committed_next` or
   `hypothetical`. Build only `required_now`; record `committed_next` as
   `future_constraints` on affected tasks; `hypothetical` is a note only.
9. Check limits: depth <= 3, leaves <= 20. If the feature cannot fit, return
   `BLOCKED` with a staging proposal (epics) in findings; never drop requirements.
10. Self-check before output: acyclic `depends_on`, every requirement covered, every
    referenced task ID exists (see `references/task-schema.md`).

## Output

Envelope per contract, `kind: "plan"`. Payload:

```json
{
  "tasks": [
    {
      "task_id": "TASK-01",
      "requirement_ids": ["REQ-1"],
      "depends_on": [],
      "inputs": ["spec:history-api"],
      "outputs": ["history-api-contract-v1"],
      "file_scope": ["src/history/contract.py"],
      "risk": "low",
      "estimated_minutes": 30,
      "acceptance": "Contract schema validates sample responses",
      "verification": ["history-contract-test"],
      "parallel_safe_with": [],
      "future_constraints": []
    }
  ],
  "future_needs": [{"description": "History export", "status": "hypothetical"}]
}
```

- `risk`: `low | medium | high`, never lower than the feature risk floor for that area.
- `PASS`: complete plan, self-check clean. `CHANGES`: the plan cannot be made valid
  from current inputs (e.g. selected option conflicts with the spec) and a finding
  says what upstream artifact must change. `BLOCKED`: limits exceeded or a required
  credential/decision is missing.

## Limits

- Shares the feature budget of max 3 analysis rounds; replanning or adding subtasks
  does not reset the counter or multiply the budget.
- No implementation code; no edits to policy, gates, budgets or evidence.
- No implicit scope change: neither reduce scope to fit limits nor add unrequested
  functionality. No human approval of the plan is requested.
- A 45-minute estimate is a heuristic, not proof of atomicity.
