---
name: factory-security-review
description: Use in controller state REVIEW for every task (baseline depth, focused on the diff's security triggers) and once per feature at full depth before acceptance, to analyze threats and verify boundary controls with evidence.
---

# factory-security-review

Follow ../_shared/contract.md

## Purpose

Determine whether the change at the current `head_sha` keeps every trust boundary
it touches safe: authentication, authorization and data isolation, input handling,
secrets, outbound calls, dependencies. A baseline review runs for every task; one
full threat analysis runs on the whole feature diff before acceptance when the
feature has a security trigger or high risk. You may raise the observed risk;
you may never lower the policy minimum. Findings require evidence: code location,
a concrete attack input, or a test/run result.

## Required inputs

- `head_sha`, diff, trusted gate result for the same SHA (including secret scan and
  dependency audit outputs, if configured).
- Feature `risk` from classification, the policy security minimum for touched paths,
  and trust boundaries from `factory-repo-map`.
- Spec requirements with security relevance (isolation, auth, privacy) and test plan.
- Allowed commands: read-only analysis and tests in the sandbox. No production access.
- `security_depth` (`baseline | full`), set by the controller, and `mode` (`task`, or
  `feature` for the review of the whole feature before acceptance).
- `security_triggers`: what the controller found in the actual diff, as `{trigger, path}`
  (`auth`, `secrets`, `endpoint`, `sql`, `outbound-request`, `html-sink`, `process-exec`,
  `file-upload`, `webhook`, `crypto`, `dependency`, `sensitive-path`).
- `known_findings`: security findings already reported for this feature by other reviews.
- Deterministic tools already ran: the policy gate blocks secret files and key-shaped
  literals in the diff (a change with one never reaches you), and `gate_evidence` holds the
  outputs of the security checks the repository configures (e.g. `sast`).

## Procedure

1. Confirm diff and gate evidence are for the given `head_sha`; otherwise `UNKNOWN`.
2. Depth is the controller's `security_depth`; report it as `review_depth`.
   - `baseline`: check the code at each `security_triggers` entry against
     `references/threat-checklist.md` (the trigger's threats only) plus the always-on
     items (secrets in logs/errors, input validation at the touched boundary). Do not run
     a whole-system threat model. Something only a full analysis can settle is a
     non-blocking finding with `observed_risk` raised; the feature review covers it.
   - `full` (`mode: feature` or high-risk task): steps 3-5 for every boundary the diff
     touches, including chains across tasks (a value one task stores and another renders).
3. Scope is the diff. Report code outside it only when the diff makes it reachable in a
   new way, and say so; otherwise it is a pre-existing note (`low`, non-blocking). The
   controller makes any finding located outside the changed files non-blocking.
   Do not repeat `known_findings`: cite their IDs in `checked_criteria` instead. A known
   finding the diff makes worse is a new finding that says so.
4. Map every touched boundary: who calls it, what identity is trusted, where it is
   verified, what data crosses. Each boundary gets controls and evidence IDs.
5. Enumerate threats per boundary (authn, authz/IDOR, injection, secrets, data
   exposure, SSRF, dependency/supply chain). For each, state whether it is mitigated,
   citing the control (file:line) and the test that proves it. "Probably safe" is
   not mitigated.
6. Where cheap and safe, confirm with a sandbox test using synthetic data (e.g. user A
   requests user B's ID). Record the evidence ID.
7. Secrets the deterministic scan cannot see (a credential printed into a log line, an
   error message or a response; a secret derived in code): check the diff for them; any hit sets
   `secrets_detected: true` and a critical finding (never copy the secret into output).
8. Set `observed_risk` = max(feature risk, policy minimum, what the diff shows). If it
   exceeds the classification, set `risk_raise_required: true`; the controller reroutes
   reviews and merge policy.
9. Anything requiring a production action, real credentials or real recipients is out
   of scope: record it in `limitations` and, if decisive, return `BLOCKED`.
10. Trust model fixed by the request or the accepted spec (e.g. "the caller-supplied
   `user` key is the identity", no auth layer exists in the repo) is not a defect the
   task can fix inside its `file_scope`. Verify the change enforces that model correctly;
   record the model's residual risk as a non-blocking finding. Only if the spec's model
   contradicts the original request, or the risk level forbids it, add a blocking
   finding with `payload.error_class: "spec"` so the controller routes it to analysis
   instead of rebuilding code that cannot change the outcome.

## Output

Envelope per contract, `kind: "security-review"`. Payload:

```json
{
  "review_depth": "full",
  "trust_boundaries": [
    {"boundary": "HTTP GET /api/history", "change": "new endpoint", "controls": ["session auth", "owner_id from session"], "evidence_ids": ["ev-sec-0031"]}
  ],
  "threats": [
    {"id": "T-1", "category": "authz", "description": "IDOR via owner_id parameter", "mitigated": true, "evidence_ids": ["ev-sec-0031"]}
  ],
  "observed_risk": "medium",
  "risk_raise_required": false,
  "secrets_detected": false
}
```

- `mode: feature`: add `"owner_task_id"` (the task whose change must fix the first blocking
  finding); the controller reopens that task. Give every finding a `location`.

- `PASS`: all threats in scope mitigated with evidence; no open blocking finding. Findings
  outside the diff or already known never make a CHANGES (the controller turns such a
  CHANGES into PASS).
- `CHANGES`: an unmitigated threat, missing control or secret; findings give the attack input.
- `BLOCKED`: required evidence needs access the job does not have (e.g. production
  config, a credential to an auth provider sandbox).

## Limits

- No unauthorized production actions, no real credentials, no scanning of external
  systems, no real users or payment/message recipients.
- An LLM judgment never lowers the policy minimum or the feature risk; only raise.
- Do not modify code or tests. Do not accept-risk on your own; `accepted-risk` needs an
  authorized decision reference.
- Majority opinion of other reviewers never overrides a hard security requirement.
- Review applies to this `head_sha` only; any new commit invalidates it.
