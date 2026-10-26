---
name: factory-recover
description: Use after a controller restart, lease timeout, lost worker or compaction/handoff, to reconcile the recorded state with the real database, Git, processes, outbox and GitHub, and propose non-duplicating recovery actions for the controller to execute.
---

# factory-recover

Follow ../_shared/contract.md

## Purpose

Rebuild a trustworthy picture of a job after an interruption and propose the
smallest safe set of actions that resumes it without duplicated side effects. A
status in a file is a claim; you check the real worktree, processes and remote
state. You **propose**; the controller executes actions through its deterministic
`lease`, `outbox` and `cleanup` code. Uncertain destructive actions are escalated,
never retried blind.

## Required inputs

- Job/task/attempt IDs and the controller's DB view: state, version, last events,
  active lease `(task_id, owner_id, lease_epoch, expires_at)`.
- Last checkpoint/handoff (goal, last confirmed SHA, spec revision, open findings,
  active process handles, next step, remaining budget).
- Read-only tools: `git status`/`git worktree list`/`git log` in the job worktree,
  process/session liveness query via the runtime adapter (`poll`), outbox rows,
  read-only GitHub queries (PR, comments, refs, checks) when the adapter is configured.
- Budget ledger for the feature.

## Procedure

Follow the order in `references/restart-protocol.md`; do not plan work before step 6.

1. **DB:** read state, version and the last events; note the lease epoch and expiry.
2. **Artifacts:** confirm that artifacts referenced by the last events exist in the
   evidence store with matching hashes; missing ones are listed, not recreated.
3. **Git:** real worktree path, `head_sha`, dirty files, branch; compare with the
   last confirmed SHA from the checkpoint. Uncommitted changes are never discarded.
4. **Processes:** poll each recorded handle. Lack of new logs does not prove death;
   `alive: null` when the adapter cannot tell. A live old worker must be cancelled
   and fenced before any new worker is started on the same worktree.
5. **Outbox/GitHub:** for each `pending/inflight/unknown` operation read remote state
   (PR exists? comment with the idempotency marker exists? ref moved?). Already
   effective => `reconcile_outbox` to `confirmed`; absent => safe to re-execute
   (at-least-once with dedupe key); unclear => `mark_unknown` + `escalate`.
6. **Budget:** compare ledger with usage; never propose resuming beyond the limit.
7. Propose actions with `target`, `reason` and `destructive`. Any action that
   deletes, force-updates or re-sends an external effect with unclear state must be
   `escalate`, not executed.
8. Propose the next state (e.g. resume `BUILD_TASK` with a new lease epoch, or
   `BLOCKED_ENV`, `NEEDS_HUMAN`).

## Output

Envelope per contract, `kind: "recover"`. Payload:

```json
{
  "observed": {
    "db_state": "REVIEW v17",
    "lease": {"epoch": 4, "expired": true},
    "git": {"worktree": "/data/worktrees/job-01-task-02", "head_sha": "c71f2b9a...", "dirty": false},
    "processes": [{"handle": "run-42", "alive": false}],
    "outbox": [{"operation_id": "ob-7", "kind": "pr_comment", "state": "inflight", "remote_observed": "comment with marker af:job-01:rev3 exists"}]
  },
  "proposed_actions": [
    {"action": "reconcile_outbox", "target": "ob-7", "reason": "comment already posted", "destructive": false}
  ],
  "duplicates_detected": [],
  "proposed_next_state": "REVIEW"
}
```

- `PASS`: state fully reconciled, all proposed actions non-destructive and safe.
- `CHANGES`: reconciliation possible but needs controller actions that change state
  (cancel stale worker, re-run invalidated reviews); each listed as a finding.
- `BLOCKED`: required source unreadable (DB locked/corrupt, worktree missing, GitHub
  unreachable) or a destructive decision needs an operator.
- `UNKNOWN`: liveness or remote effect cannot be determined.

## Limits

- Never execute recovery actions yourself; never start a second builder on a
  worktree, never `git reset --hard`, `worktree remove --force`, `branch -D` or force-push.
- Results carrying an old lease epoch are rejected, even if they arrived late.
- Do not promise exactly-once; propose at-least-once + dedupe + reconciliation.
- Do not reset attempt, analysis-round or budget counters; do not raise limits.
- Do not rewrite the provenance of old reports to make them look current.
