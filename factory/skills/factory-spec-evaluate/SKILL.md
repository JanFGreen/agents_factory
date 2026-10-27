---
name: factory-spec-evaluate
description: Use in controller state AI_SPEC_EVALUATION to independently evaluate the current micro-spec or OpenSpec package against the original request on five mandatory dimensions and a counterexample; its PASS is the automatic pre-build quality gate, with no human approval.
---

# factory-spec-evaluate

Follow ../_shared/contract.md

## Purpose

Judge whether the current spec package is a correct, complete and testable
contract for the original request. You run in a fresh context, independent of the
spec author. Only a current PASS lets the builder start; there is no human spec
approval. OpenSpec validation checks format; you check semantics. You must also
construct a counterexample: the simplest wrong implementation that would pass the
current acceptance criteria. If it reveals a material gap, the verdict is CHANGES.

## Required inputs

- Original request (verbatim) and normalized feature revision, constraints, criteria.
- Path (`trivial | standard | complex`) and risk from the controller.
- Spec package: micro-spec, or `proposal.md`, `specs/**/spec.md`, `intent-map.yaml`,
  `acceptance-map.yaml`, `future-needs.md`; plus `decision.json` (standard/complex).
- Accepted baseline specs under `openspec/specs/` touched by the change.
- `factory-repo-map` facts (modules, boundaries, commands), policy minimums, budget.
- `spec_digest`, intent digest, `analysis_round_id`, previous findings (rounds 2-3).

## Procedure

1. Confirm digests. Evaluating a spec other than the one named in the request => `UNKNOWN`.
2. Re-derive intents from the original request yourself before reading the intent map;
   then compare. Do not trust the author's mapping.
3. Score each dimension `PASS` or `CHANGES` using `references/rubric.md`:
   `intent_coverage`, `consistency`, `testability`, `feasibility`, `risk_controls`.
   Every `CHANGES` dimension has at least one finding with location and evidence.
4. Write the counterexample: one concrete wrong implementation (e.g. "filter applied
   client-side to the first page only") and state whether current criteria reject it.
   If they do not and it violates an explicit intent or constraint of the original
   request (or a control the risk level mandates), add a blocking finding. A gap that
   only concerns behavior the request does not ask for is non-blocking (see "What blocks").
5. List `uncovered_requirements`: intents or requirements without an observable criterion.
6. Check hidden scope reduction: an intent moved to `future-needs.md` as
   `hypothetical`/`committed_next`, or a criterion weaker than the request, is a blocking finding.
7. Rounds 2-3: verify each previous finding is resolved (set `status: "fixed"` with
   evidence) or still `open`. A reworded but unchanged problem is not progress. A new
   blocking finding in a later round is allowed only for a regression the revision
   introduced or an explicit intent that is still missing; new edge cases are non-blocking.
8. Trivial path: short mode (rubric section "Trivial"), still all five dimensions.

## What blocks

The spec is a contract for *this request*, not a catalogue of every possible hardening.

- Blocking (`blocking: true`, dimension `CHANGES`): an explicit intent, constraint or
  user criterion of the original request is missing, contradicted, weakened or not
  observable; the approach is infeasible in this repo; or the risk level mandates a
  control that is absent (e.g. per-user data without an isolation criterion).
- Non-blocking (`blocking: false`, dimension stays `PASS`): edge cases and hardening the
  request does not mention (legacy/corrupt data, concurrency, extra validation, nicer
  errors), style and wording. The controller forwards them to the builder and the
  reviewers as advisories, so they are not lost; they just do not stop the build.
- A dimension is `CHANGES` only when it has at least one blocking finding.

## Output

Envelope per contract, `kind: "spec-evaluate"`. Payload:

```json
{
  "dimensions": {
    "intent_coverage": "PASS",
    "consistency": "PASS",
    "testability": "CHANGES",
    "feasibility": "PASS",
    "risk_controls": "PASS"
  },
  "counterexample": "Simplest wrong implementation that passes current criteria, and whether it is rejected",
  "uncovered_requirements": ["REQ-3"]
}
```

- `PASS`: all five dimensions `PASS`, counterexample rejected by current criteria,
  `uncovered_requirements` empty, no open blocking finding.
- `CHANGES`: at least one dimension `CHANGES`; findings name the artifact and line.
- `BLOCKED`: a needed business decision or permission is missing (feasibility cannot be
  established without it); never used for ordinary fixable gaps.
- `UNKNOWN`: inputs stale or unreadable.

## Limits

- One evaluation per analysis round; all rounds share the feature limit of 3.
- Do not edit the spec, pick options, or write the plan. Do not ask a human to approve.
- Do not demand perfect prose or a "10/10"; judge correctness, completeness, testability.
- Do not accept a narrower goal than the original request, and do not invent new scope
  beyond the request (that is a finding against the spec, not something you add).
