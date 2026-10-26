# Restart and reconciliation protocol (blueprint §12.1–§12.4)

## Order (do not reorder)

1. Database (state, version, events, leases)
2. Artifacts (evidence store, hashes)
3. Git state (worktree, head, dirty files, branches)
4. Live processes / agent sessions
5. Outbox and GitHub remote state
6. Budget
7. Only then: plan the next step

## Lease and fencing

- Worker holds `(task_id, owner_id, lease_epoch, expires_at)`; heartbeat renews it.
- Writing a result requires the current epoch. Late results from an old epoch are rejected.
- On timeout first check whether the process/session is alive; missing logs prove nothing.
- Takeover: cancel the old worker and cut it off from tools and writes, then issue a
  new epoch. Never two builders writing one worktree.

## Outbox

| State | Meaning | Recovery |
|---|---|---|
| pending | intent recorded, not sent | execute |
| inflight | sent, no confirmation | read remote state first |
| confirmed | remote effect observed | nothing |
| unknown | cannot tell | escalate if destructive, else re-read later |
| failed | definite failure | retry per retry class |

Idempotency key e.g. `(repo, job, revision, operation_kind)`; a marker in comment
bodies helps detection but is not atomicity. Guarantee is at-least-once + dedupe.

## Retry classes

- 429 / transient 5xx / dropped connection: backoff with jitter, honor `Retry-After` and deadline.
- Invalid JSON from an agent: one format repair; second failure blocks the stage.
- Test FAIL: diagnose (root-cause), never rerun until green.
- Missing credentials/permissions: BLOCKED with the concrete need.
- Merge conflict: rebuild candidate and repeat gates; never force-push user changes.

## Checkpoint/handoff content

Goal, last confirmed SHA, spec revision, completed criteria, open findings, active
processes and handles, next step, remaining budget. Not the conversation history.

## Never

- Trust a status file over the real worktree/process.
- Delete uncommitted work, force-remove worktrees or branches.
- Re-send an external effect whose state is unknown and destructive.
