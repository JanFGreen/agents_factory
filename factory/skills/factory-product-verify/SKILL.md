---
name: factory-product-verify
description: Use in controller states REVIEW (per task, L4) and FEATURE_ACCEPTANCE (whole feature) to verify each acceptance criterion against the real observable result of the running product, from the PM or QA perspective.
---

# factory-product-verify

Follow ../_shared/contract.md

## Purpose

Prove, with executed evidence, that the product does what the original request
asked for. You are the product evaluator: you do not trust the builder's summary
or claimed checks. You drive the flow from the user/API side in an isolated test
environment and check the effect in data. Run once per perspective:

- `pm` — intent and completeness: does the delivered behavior cover every intent
  of the original request, with no hidden scope reduction?
- `qa` — execution: run the scenarios, including errors and boundaries, and
  check persisted effects.

At `FEATURE_ACCEPTANCE` the scope is the whole feature against the original
request; green leaf tasks never make the parent DONE automatically.

## Required inputs

- Frozen feature snapshot (original request, goal, constraints, acceptance criteria) and revision.
- Current spec: requirements, `acceptance-map.yaml`, `test-plan.md` scenario IDs.
- Candidate identity (`head_sha`, `candidate_tree_sha`) and the isolated environment
  handle (URL/ports, synthetic accounts, seeded data) provided by the workspace manager.
- Trusted runner access for executing flows (browser/API client) and the evidence store IDs it returns.
- Perspective (`pm` or `qa`) and, for `qa`, access to the evaluator-only holdout pool.
- `mode: feature-acceptance` only: `open_findings`, the open findings the task reviewers left
  non-blocking (`finding_id`, `task_id`, `kind`, `severity`, `location`, `problem`).

## Second look at non-blocking findings (feature acceptance)

Task reviewers sometimes rate a real defect `low`. Read every entry of `open_findings` against
the contract's "Severity and blocking" table and the whole feature as it now runs. When one
describes a user-facing defect that is still present (lost or duplicated data, a stuck user, a
requirement not met), confirm it in the candidate (code path, test or browser) and list it in
`payload.escalate` as `{"finding_id", "task_id", "reason"}`, with the reason naming the evidence.
The controller makes escalated findings blocking and reopens their task. Leave style and
maintainability notes alone; do not escalate what you could not confirm.

## Procedure

1. Confirm the environment runs the candidate under review (version endpoint,
   build tree hash, or deployed SHA). Mismatch => `UNKNOWN`. When `inputs.environment.kind` is
   `served`, the controller serves the gate-built web candidate at `environment.url` and gives a
   browser CLI (`environment.browser.binary`) through Bash: perform the user flows of the
   acceptance criteria in it (`open`, `snapshot` for element refs, `click`/`fill`/`press`,
   `snapshot` again to observe the result, `console` for errors, `screenshot --filename
   <evidence_dir>/<flow>.png` at the key steps) and cite the file names as evidence; finish with
   `close`. Behaviour that needs the backend server (not running) is verified as in
   `local-checkout`. When `inputs.environment.kind`
   is `local-checkout` (libraries, CLIs, modules without a service) the working directory
   at `head_sha` is the environment: perform each action by running the code read-only
   (e.g. a python snippet importing it against temp files) and record the command and
   its output as the evidence. A missing URL, account or runner evidence ID is then not
   a reason for `BLOCKED` or `NOT_RUN`.
2. Build the criteria list: every acceptance criterion `AC-n` with its
   `requirement_ids` and `scenario_ids`. For `pm`, also map every intent of the
   original request; an intent without a criterion is an `intent_gaps` entry and a
   blocking finding.
3. For each criterion perform the real action (submit, filter, paginate, reload,
   call the API as another user) — viewing a page without performing the critical
   action is not evidence. Record `action`, what you `observed`, and the runner
   `evidence_ids` (screenshots, HTTP transcripts, DB query results).
4. Check effects in data: persisted state, re-read after reload, idempotency of
   repeated submits, and isolation (user A cannot see user B) within the risk level.
5. Apply the UI or backend checklist from `references/verification-checklist.md`
   according to what the change touches.
6. `qa` only: run holdout variants. Report failures with a sanitized description
   (category + observed behavior), never the private case contents.
7. Result per criterion: `PASS` (observed matches), `FAIL` (finding with
   evidence), `NOT_RUN` (with reason in `limitations`). Any `NOT_RUN` on a required
   criterion prevents `PASS`.

## Output

Envelope per contract, `kind: "product-verify"`. Payload:

```json
{
  "perspective": "qa",
  "environment": "local-sandbox job-01 http://127.0.0.1:18042 seed=history-v2",
  "criteria": [
    {
      "acceptance_id": "AC-2",
      "requirement_ids": ["REQ-2"],
      "scenario_ids": ["SC-2-1"],
      "action": "Filter by type=transfer, open page 2, reload browser",
      "observed": "Page 2 shows 10 transfer rows owned by user-a; filter and page kept after reload",
      "evidence_ids": ["ev-e2e-0142"],
      "result": "PASS"
    }
  ],
  "intent_gaps": [],
  "escalate": [
    {"finding_id": "F-2", "task_id": "TASK-08", "reason": "Confirmed in the served build: after opening the leaderboard and coming back, the typed nickname is gone and Retry saves under the old one (back-nickname.png)."}
  ]
}
```

- `escalate` (feature acceptance only): open non-blocking findings confirmed as user-facing defects;
  empty when there are none. Escalating any finding makes the acceptance `CHANGES`.

- `PASS`: every required criterion `PASS` with evidence IDs; no intent gaps; no open blocking finding.
- `CHANGES`: at least one criterion `FAIL` or an intent gap; findings cite the evidence
  and the owning requirement so the controller routes to BUILD or OPTIONS_DRAFT.
- `BLOCKED`: the request needs a service, synthetic account or credential that is not provided
  and cannot be replaced by running the code locally (never for `local-checkout` code you can run).
- `UNKNOWN`: candidate identity cannot be confirmed or the runner produced no usable evidence.

## Limits

- Never guess success; a builder claim, a passing unit test or a rendered page is
  not proof that an action works.
- Test only in the isolated environment with synthetic data. Never log in as real
  users, send real messages/webhooks or perform real payments.
- Do not modify code, tests, fixtures, spec, policy or evidence; do not fix what you find.
- Do not lower or reinterpret acceptance criteria; a narrower interpretation of the
  original request is an intent gap, not a pass.
- Do not leak holdout cases to builder-visible outputs.
- Each CHANGES result counts against the task's 3 build-and-review attempts.
