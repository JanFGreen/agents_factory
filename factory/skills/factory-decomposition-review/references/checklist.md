# Decomposition review checklist

Blueprint §7.6 and `workflow.yaml: decomposition`. All eight points are mandatory.

1. **Coverage both ways.** Every requirement has ≥1 task and a verification;
   every task derives from a requirement or a necessary dependency (stated).
   The goal is not lost by splitting.
2. **DAG integrity.** Acyclic; no missing or dangling `depends_on`; explicit critical
   order; shared contracts (API schema, DB schema, events) are produced before consumers.
3. **Executable leaves.** Each leaf fits one executor's context and budget, has clear
   inputs/outputs and a completion test. `target_task_minutes: 45` is a heuristic,
   not proof of atomicity.
4. **No conflicts.** No duplicated work; no two `parallel_safe_with` tasks sharing a
   `file_scope`; lockfile and public API changes owned by exactly one task; no impossible parallelism.
5. **Early vertical integration.** API, UI and data are connected incrementally;
   avoid building large disconnected layers that meet only at the end.
6. **Completeness of supporting work.** Migrations (with rollback), seed/test data,
   error handling, observability, documentation — within feature scope.
7. **Future needs.** Each has status `required_now`, `committed_next` or `hypothetical`.
   Build only `required_now` plus minimal boundaries needed for correctness. Record
   `committed_next` as a contract/constraint when it affects today's decisions.
   `hypothetical` is a note/ADR, never a task.
8. **Feature-level acceptance.** Separate E2E scenarios, a task-interaction test and a
   parent acceptance task responsible for the full intent. Green leaves do not make the parent DONE.

## Limits

| Limit | Default | On violation |
|---|---|---|
| `max_depth` | 3 | CHANGES; propose staging, do not drop requirements |
| `max_leaf_tasks_per_feature` | 20 | CHANGES/BLOCKED with epic proposal; further work needs an explicit limit raise or an authorized scope split |
| analysis rounds (shared) | 3 per feature | controller enters `ANALYSIS_LIMIT_REACHED` |

## Example of a good shape (FEAT-042)

```
TASK-01 history API contract (REQ-1..3)            depends_on: []
TASK-02 owner-scoped query + index (REQ-1)         depends_on: [TASK-01]
TASK-03 filter + cursor endpoint (REQ-2, REQ-3)    depends_on: [TASK-02]
TASK-04 history view with URL state (REQ-2..4)     depends_on: [TASK-01]   parallel_safe_with: [TASK-02]
TASK-05 feature E2E acceptance (REQ-1..4)          depends_on: [TASK-03, TASK-04]
```

## Trivial path

Not run. The controller records `not-applicable: single atomic task`, backed by the
classification. Spec evaluation still runs.
