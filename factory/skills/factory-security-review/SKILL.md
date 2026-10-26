---
name: factory-security-review
description: Use in controller state REVIEW for every task (baseline depth) and at full depth whenever the risk is medium/high or the diff touches a trust boundary, to analyze threats and verify boundary controls with evidence.
---

# factory-security-review

Follow ../_shared/contract.md

## Purpose

Determine whether the change at the current `head_sha` keeps every trust boundary
it touches safe: authentication, authorization and data isolation, input handling,
secrets, outbound calls, dependencies. A baseline review runs for every task; a
full threat analysis runs on any risk signal. You may raise the observed risk;
you may never lower the policy minimum. Findings require evidence: code location,
a concrete attack input, or a test/run result.

## Required inputs

- `head_sha`, diff, trusted gate result for the same SHA (including secret scan and
  dependency audit outputs, if configured).
- Feature `risk` from classification, the policy security minimum for touched paths,
  and trust boundaries from `factory-repo-map`.
- Spec requirements with security relevance (isolation, auth, privacy) and test plan.
- Allowed commands: read-only analysis and tests in the sandbox. No production access.

## Procedure

1. Confirm diff and gate evidence are for the given `head_sha`; otherwise `UNKNOWN`.
2. Decide `review_depth`: `full` if feature risk is medium/high, policy demands it, or
   any trigger in `references/threat-checklist.md` appears in the actual diff (new
   data endpoint, credential, webhook, SQL/raw query, file upload, outbound URL,
   permission change, new dependency). Otherwise `baseline`.
3. Map every touched boundary: who calls it, what identity is trusted, where it is
   verified, what data crosses. Each boundary gets controls and evidence IDs.
4. Enumerate threats per boundary (authn, authz/IDOR, injection, secrets, data
   exposure, SSRF, dependency/supply chain). For each, state whether it is mitigated,
   citing the control (file:line) and the test that proves it. "Probably safe" is
   not mitigated.
5. Where cheap and safe, confirm with a sandbox test using synthetic data (e.g. user A
   requests user B's ID). Record the evidence ID.
6. Secrets: check diff, fixtures and logs for credentials; any hit sets
   `secrets_detected: true` and a critical finding (never copy the secret into output).
7. Set `observed_risk` = max(feature risk, policy minimum, what the diff shows). If it
   exceeds the classification, set `risk_raise_required: true`; the controller reroutes
   reviews and merge policy.
8. Anything requiring a production action, real credentials or real recipients is out
   of scope: record it in `limitations` and, if decisive, return `BLOCKED`.

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

- `PASS`: all threats in scope mitigated with evidence; no open blocking finding.
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
