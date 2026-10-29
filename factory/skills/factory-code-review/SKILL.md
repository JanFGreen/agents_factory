---
name: factory-code-review
description: Use in controller state REVIEW after the trusted checks for the current HEAD passed, to find concrete correctness, spec-conformance and maintainability problems in the task diff without modifying code.
---

# factory-code-review

Follow ../_shared/contract.md
Follow ../_shared/engineering-principles.md

## Purpose

You are the lead engineer the builders report to. Keep them writing good production code
and never let a change expose the operator to legal cost (`references/production-and-legal.md`).

Independent review of one task's diff at one exact `head_sha`. Find defects that
the checks did not catch: wrong behavior against the spec, missed requirement
paths, broken contracts, unsafe error handling, scope creep and maintainability
problems that will cost the next task. Report findings with locations and
evidence; never fix them. Also signal when the actual diff shows more
complexity than the classification assumed.

## Required inputs

- `head_sha`, `base_sha` and the task diff; trusted gate result PASS for this `head_sha`.
- Task record (`requirement_ids`, `file_scope`, `acceptance`, `verification`) and the
  spec scenarios for those requirements; `design.md` contracts when present.
- Classification of the feature (`complexity_path`, `risk`) and repo-map conventions.
- Builder handoff (claims to check) and open finding IDs from earlier attempts.
- `spec_advisories`: non-blocking spec findings. Not handling one is never blocking by itself.
- `effect_sites`: files of the diff the controller found to call the network, handle requests,
  write persistent data, use browser storage or timers (`{path, kinds}`), and `probe_workspace`:
  a writable copy of the candidate with the repo's `test_command`.

## Procedure

1. Confirm the diff you read is for the given `head_sha` and the gate PASS refers to
   it. Different SHA => `UNKNOWN`; reviews of old snapshots are not evidence.
2. Read the task's requirements and scenarios first, then the diff, then the
   surrounding code needed to understand each change (callers, contracts).
3. For each requirement: trace the code path for happy, error and boundary cases;
   record the requirement in `requirements_checked` only if you traced it.
4. Apply `references/review-checklist.md` and the *review* rules of the engineering
   principles. Each finding needs `location`, the concrete failing input or scenario in
   `problem` (for a principle: its ID and the cost it causes), and an `expected_fix`
   (direction, not a patch). Do not report what the trusted gate enforces (complexity,
   sizes, copied blocks, layer imports, deprecated APIs, formatting): it already passed.
5. Failure modes (every file in `effect_sites`, see `references/failure-modes.md`): for each
   effectful operation fill one `failure_modes` entry and answer all seven scenarios
   (`timeout`, `cancellation`, `retry_idempotent`, `lost_response`, `double_submit`,
   `unmount_or_navigation`, `partial_failure`) with `PASS`, `FAIL` or `NOT_APPLICABLE`; the
   `evidence` names the code line or test for each answer and the reason for each
   `NOT_APPLICABLE`. Think as the network and the user would misbehave: the response never
   arrives, arrives after the screen changed, arrives twice; the request succeeded but its
   answer was lost and the client retries. A file left out makes the review inconclusive.
   Each `FAIL` has a finding (`finding_id`). When you can, prove it: write a probe test in
   `probe_workspace.dir`, run it with `test_command`, and name it in `probe`. The controller
   re-runs the probe in the trusted runner; a probe that fails there makes the finding blocking.
   A probe must fail because of the defect (assert the correct behavior), not because of a typo.
6. Production and legal (`references/production-and-legal.md`): check the diff against the
   production-readiness list and the legal-exposure table. A new dependency, asset, personal-data
   field or user-visible text always gets this check.
7. Scope: changes outside `file_scope`, unrequested features, lockfile/API changes not
   in the task => `scope_creep` entries and findings.
8. Re-check earlier finding IDs: if fixed with evidence, report them with status
   `fixed`; if not, keep the same ID with status `open`.
9. Reclassification signal: if the diff reveals new interfaces, data migration,
   cross-module coupling or a new trust boundary not in the plan, set
   `reclassification_signal` (suggest a higher class; never lower).
10. Severity follows the contract's "Severity and blocking" table: lost, duplicated or wrong
   user data and a stuck user are blocking even when the fix is small. Style-only remarks
   are `low`, non-blocking, and few.

## Output

Envelope per contract, `kind: "code-review"`. Payload:

```json
{
  "reviewed_head_sha": "3f9c2a1e7b4d",
  "diff_stats": {"files": 3, "insertions": 142, "deletions": 18},
  "requirements_checked": ["REQ-2", "REQ-3"],
  "scope_creep": [],
  "reclassification_signal": {"suggested_complexity": null, "reason": ""},
  "failure_modes": [
    {"path": "src/ui/leaderboard-api.ts", "operation": "saveResult(): POST /api/results",
     "scenarios": {"timeout": "FAIL", "cancellation": "NOT_APPLICABLE", "retry_idempotent": "FAIL", "lost_response": "FAIL",
                   "double_submit": "PASS", "unmount_or_navigation": "PASS", "partial_failure": "NOT_APPLICABLE"},
     "evidence": "timeout: fetch has no signal/deadline (leaderboard-api.ts:24), a hung request keeps the form in 'saving'. retry/lost_response: no request id, a retry after a lost 201 inserts a second row (results-route.ts:47). double_submit: Save is disabled while saving (SaveResultForm.tsx:88).",
     "probe": "src/ui/leaderboard-api.hang.test.ts", "finding_id": "F-4"}
  ]
}
```

- `failure_modes` covers every file in `effect_sites` (empty when there are none).

- `PASS`: every task requirement traced, no open blocking finding.
- `CHANGES`: at least one open finding the builder must address (same IDs across rounds).
- `BLOCKED`: the diff or required context cannot be read (missing artifact, permission).

## Limits

- Never modify code, tests, or the worktree; read-only.
- Review only the current HEAD; a new commit invalidates this review.
- Do not judge security depth (that is `factory-security-review`) beyond obvious
  defects, and do not replace product verification with reading code.
- Do not approve on the builder's claims; do not demand perfection or style rewrites.
- Do not lower the feature's complexity or risk; signals only go up.
