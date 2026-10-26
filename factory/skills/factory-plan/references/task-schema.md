# Task schema and DAG rules

Source: blueprint §7.6, §7.0 `decomposition`.

## Task fields

| Field | Type | Rule |
|---|---|---|
| `task_id` | `TASK-NN` | stable across rounds; never renumber |
| `requirement_ids` | REQ ids | >= 1, or empty only for a necessary dependency named in `acceptance` |
| `depends_on` | task ids | must exist; graph acyclic |
| `inputs` / `outputs` | contract or artifact names | an input must be an output of a dependency or an existing repo artifact |
| `file_scope` | paths/globs | disjoint from tasks marked `parallel_safe_with` |
| `risk` | low/medium/high | not below the policy floor for touched paths |
| `estimated_minutes` | int | target 45; larger leaves should be split |
| `acceptance` | text | observable result, not "code exists" |
| `verification` | check/test ids | at least one per task |
| `parallel_safe_with` | task ids | symmetric; no shared lockfile/API/migration |
| `future_constraints` | text | from `committed_next` needs only |

## DAG rules

1. No cycles, no dangling references, explicit critical order.
2. Shared contracts precede consumers.
3. Vertical integration early; no big unconnected layers.
4. Last task: feature-level end-to-end acceptance against the original request.
5. Migrations include rollback; seed/test data and error paths are planned.
6. `max_depth` 3, `max_leaf_tasks_per_feature` 20. Exceeding => staging proposal, analysis ends; scope is never silently reduced.

## Future needs statuses

| Status | Meaning | Allowed effect on the plan |
|---|---|---|
| `required_now` | part of the original request | build it |
| `committed_next` | decided follow-up | record contract/constraint if it affects today's design |
| `hypothetical` | possible later | note/ADR only; no code |
