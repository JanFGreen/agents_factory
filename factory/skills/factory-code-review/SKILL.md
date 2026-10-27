---
name: factory-code-review
description: Use in controller state REVIEW after the trusted checks for the current HEAD passed, to find concrete correctness, spec-conformance and maintainability problems in the task diff without modifying code.
---

# factory-code-review

Follow ../_shared/contract.md

## Purpose

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

## Procedure

1. Confirm the diff you read is for the given `head_sha` and the gate PASS refers to
   it. Different SHA => `UNKNOWN`; reviews of old snapshots are not evidence.
2. Read the task's requirements and scenarios first, then the diff, then the
   surrounding code needed to understand each change (callers, contracts).
3. For each requirement: trace the code path for happy, error and boundary cases;
   record the requirement in `requirements_checked` only if you traced it.
4. Apply `references/review-checklist.md`. Each finding needs `location`, the concrete
   failing input or scenario in `problem`, and an `expected_fix` (direction, not a patch).
5. Scope: changes outside `file_scope`, unrequested features, lockfile/API changes not
   in the task => `scope_creep` entries and findings.
6. Re-check earlier finding IDs: if fixed with evidence, report them with status
   `fixed`; if not, keep the same ID with status `open`.
7. Reclassification signal: if the diff reveals new interfaces, data migration,
   cross-module coupling or a new trust boundary not in the plan, set
   `reclassification_signal` (suggest a higher class; never lower).
8. Severity reflects impact; `blocking: true` for anything that violates a
   requirement, a contract, security minimum or would break other tasks. Style-only
   remarks are `low`, non-blocking, and few.

## Output

Envelope per contract, `kind: "code-review"`. Payload:

```json
{
  "reviewed_head_sha": "3f9c2a1e7b4d",
  "diff_stats": {"files": 3, "insertions": 142, "deletions": 18},
  "requirements_checked": ["REQ-2", "REQ-3"],
  "scope_creep": [],
  "reclassification_signal": {"suggested_complexity": null, "reason": ""}
}
```

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
