---
name: factory-evidence-audit
description: Use in controller state READY_TO_MERGE, before any merge recommendation, to audit that every requirement has current, trusted, identity-matched evidence and that every required report exists for the exact candidate.
---

# factory-evidence-audit

Follow ../_shared/contract.md

## Purpose

Last semantic check before the controller recommends a merge. You verify the
acceptance matrix end to end: requirement -> scenarios -> evidence, and that each
piece of evidence and each required report (checks, code/security/UI/product
reviews, integration review, spec evaluation, decomposition review) refers to the
current candidate and inputs and was produced by a trusted producer. The
controller checks reference integrity deterministically; you judge whether the
assertions actually prove the requirement. You recommend; the controller decides
and performs the merge.

## Required inputs

- Candidate identity: `head_sha`, `base_sha`, `candidate_tree_sha`, `spec_digest`,
  `plan_digest`, `policy_digest`, `checks_digest`.
- Acceptance matrix (`acceptance-map.yaml`) and intent map.
- Evidence manifest from the evidence store: for each artifact its producer
  (`runner` / authenticated adapter / agent role), input identity and assertion.
- List of required reports for the feature's risk level and complexity path (from policy).
- Findings register with statuses (`open/fixed/disputed/accepted-risk`).

## Procedure

1. For every requirement in the intent map, locate its row in the acceptance matrix;
   a requirement without a row is `missing`.
2. For every evidence item check, per `references/evidence-rules.md`:
   - `identity_match`: the evidence input identity equals the candidate (tree/head,
     spec, policy, checks digests). A different value => `stale`.
   - `producer_trusted`: PASS of checks/tests comes from the trusted runner or an
     authenticated adapter, never from a file written by the builder.
   - the assertion actually exercises the requirement (e.g. "user A cannot export
     B's rows"), not just existence of code; zero executed tests is not evidence.
3. `not_applicable` is accepted only with a `reason` and a reference to an
   authorized decision; otherwise it is `missing`.
4. Check that every required report exists and is current; list absent ones in
   `missing_reports` and outdated ones in `stale_reports`.
5. Check the findings register: any `open` blocking finding, or a `fixed` status
   not confirmed by an evaluator, blocks the recommendation.
6. Set `recommendation`: `merge` (all verified, nothing stale/missing), `rework`
   (fixable gaps: rerun evidence, missing scenario), `block` (untrusted producer,
   tampering signs, policy breach).

## Output

Envelope per contract, `kind: "evidence-audit"`. Payload:

```json
{
  "candidate": {"head_sha": "c71f2b9a...", "base_sha": "3a9e0c41...", "spec_digest": "sha256:...", "policy_digest": "sha256:..."},
  "criteria": [
    {"requirement_id": "REQ-1", "status": "verified", "evidence_ids": ["ev-e2e-0140"], "identity_match": true, "producer_trusted": true, "reason": "E2E isolation assertion on candidate"}
  ],
  "stale_reports": [],
  "missing_reports": [],
  "recommendation": "merge"
}
```

(Hashes are shown shortened here; real output carries the full values.)

- `PASS` only with `recommendation: merge`: every requirement `verified` (or
  authorized `not_applicable`), no stale/missing report, no open blocking finding.
- `CHANGES` with `recommendation: rework`.
- `BLOCKED` with `recommendation: block` when trust or authorization is broken, or
  when the evidence store is not readable.
- `UNKNOWN` when identity of evidence cannot be established.

## Limits

- A checked box in `tasks.md` or a statement in a handoff is never evidence.
- Do not create, edit, re-sign or re-date evidence or reports; do not relabel an old
  report as a new round.
- Do not accept partial reuse of earlier results unless the controller recorded a
  deterministic equivalence event for those inputs.
- Do not merge or approve; `approve` by a human never turns UNKNOWN into PASS.
- Do not override a blocking security finding by majority of other reviews.
