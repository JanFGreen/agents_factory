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
| `difficulty` | easy/medium/hard | rubric below; picks the builder model |
| `difficulty_reasons` | text | evidence for the label; `indivisible: <why>` keeps a hard leaf unsplit |

## DAG rules

1. No cycles, no dangling references, explicit critical order.
2. Shared contracts precede consumers.
3. Vertical integration early; no big unconnected layers.
4. Fewest tasks within `target_task_minutes`; each task owns its tests. Feature-level end-to-end
   acceptance is the controller's FEATURE_ACCEPTANCE stage, not a planned task.
5. Migrations include rollback; seed/test data and error paths are planned.
6. `max_depth` 3, `max_leaf_tasks_per_feature` 20. Exceeding => staging proposal, analysis ends; scope is never silently reduced.

## Future needs statuses

| Status | Meaning | Allowed effect on the plan |
|---|---|---|
| `required_now` | part of the original request | build it |
| `committed_next` | decided follow-up | record contract/constraint if it affects today's design |
| `hypothetical` | possible later | note/ADR only; no code |

## Difficulty and atomic tasks

| Level | Rubric | Builder |
|---|---|---|
| `easy` | One responsibility, 1-2 production files, follows an existing pattern, no new interface, one obvious test level | small model |
| `medium` | A few production files or a new internal interface, state or error handling to design, tests at more than one level, or medium risk | mid model |
| `hard` | Several modules or layers, a new public or cross-module contract, concurrency, migration or tricky state, unclear behaviour, several requirements at once | large model; split it |

The controller raises a label to its structural floor: folders or globs in `file_scope` or
more than 3 production paths → medium, more than 6 → hard; risk above low, more than 3
requirements or more than 2 output contracts → medium. A configured decision model may raise
it further. Each later attempt of a task runs one model level up.

A task is **atomic** when:

1. it has one responsibility (describable in one sentence without "and");
2. it touches at most about 3 production files, all listed in `file_scope`;
3. its contract is in the record: the signatures or component props it creates in `outputs`,
   the contracts it consumes in `inputs` (outputs of its dependencies);
4. its `verification` names the test cases that prove `acceptance`;
5. it needs nothing a dependency has not already delivered.

A small model given an atomic task with its contract written out does as well as a large
model guessing a vague one; a large vague task is where retries come from.
