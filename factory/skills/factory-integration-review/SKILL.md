---
name: factory-integration-review
description: Use in controller state INTEGRATION whenever a new integration candidate (feature branch synced with the current base) is created, to assess the effects of combining changes and re-check critical scenarios on that exact candidate.
---

# factory-integration-review

Follow ../_shared/contract.md

## Purpose

Judge whether the feature still works after it is combined with the current
base. Passing task reviews were made against an older base; a clean Git merge
does not prove semantic compatibility. You review the difference the base
introduced, the conflict resolutions and the CI/critical scenario results on the
frozen candidate, and say where a problem must go. You never replace CI with an
opinion: missing or pending required checks are never success.

## Required inputs

- Frozen candidate identity: `base_sha`, `head_sha`, `candidate_tree_sha`.
- Diff `base_at_task_review..base_sha` (what changed on the target branch since the
  task reviews) and the feature diff.
- Conflict log with each resolution made by the builder/controller.
- Required checks list from policy and their status on this candidate (trusted runner / CI).
- Critical scenario IDs from `acceptance-map.yaml` and their evidence on this candidate.
- Integration rebuild counter (max 2 per feature).

## Procedure

1. Confirm that every result you read refers to `candidate_tree_sha`; results from
   another tree are stale and listed in `limitations`, never counted.
2. Enumerate base changes that touch the feature's modules, contracts, schemas,
   migrations, dependencies or config. For each, decide whether feature behavior
   or assumptions change; cite path and line.
3. For each conflict record `path`, `resolution` and `semantic_change`. A
   resolution that changes behavior or a requirement => `route_to: OPTIONS_DRAFT`
   (spec/plan must be revisited); a code-only mistake => `route_to: BUILD`.
4. Check required checks: each must be `success` on this candidate. `pending`,
   `missing` or `failure` blocks a PASS.
5. Check critical scenarios (`PASS | FAIL | NOT_RUN`) with evidence IDs. Any
   `NOT_RUN` critical scenario blocks a PASS; request its execution.
6. List `interaction_risks`: e.g. a base migration renamed a column the feature
   queries, two features registering the same route, lockfile drift.
7. Set `route_to`: `MERGE_RECOMMENDATION` on PASS, `BUILD` or `OPTIONS_DRAFT` on
   CHANGES, `null` on BLOCKED/UNKNOWN. If the rebuild counter is exhausted, report
   CHANGES with a finding stating that the merge stays blocked.

## Output

Envelope per contract, `kind: "integration-review"`. Payload:

```json
{
  "base_sha": "3a9e0c41d2b7f6e8a1c5d9b0e4f7a2c6d8b1e3f5",
  "head_sha": "c71f2b9a0e4d8c6b2a1f5e9d3c7b0a4e8f2d6c1b",
  "candidate_tree_sha": "9f1c2e7a5b3d8f0c4e6a2b9d1f7c3e5a8b0d4f6c",
  "conflicts": [{"path": "src/history/api.py", "resolution": "kept both route handlers", "semantic_change": false}],
  "required_checks": [{"name": "unit", "status": "success"}],
  "critical_scenarios": [{"scenario_id": "SC-1-2", "result": "PASS", "evidence_ids": ["ev-e2e-0301"]}],
  "interaction_risks": [],
  "route_to": "MERGE_RECOMMENDATION"
}
```

- `PASS`: all required checks `success`, all critical scenarios `PASS`, no unresolved
  semantic conflict, no open blocking finding.
- `CHANGES`: concrete integration problem with `route_to` BUILD or OPTIONS_DRAFT.
- `BLOCKED`: candidate cannot be built/tested (environment, CI unavailable, permissions).
- `UNKNOWN`: results cannot be tied to the candidate tree.

## Limits

- Do not merge, rebase, force-push or resolve conflicts yourself; you review.
- `pending` is never `success`; absence of conflicts is never proof of compatibility.
- Do not accept results from a previous candidate or base, even if the diff "looks the same".
- Maximum 2 integration rebuilds per feature; do not suggest a third under another name.
- A conflict resolution that silently drops feature behavior is scope reduction: blocking.
- Do not edit policy, required check lists or evidence.
