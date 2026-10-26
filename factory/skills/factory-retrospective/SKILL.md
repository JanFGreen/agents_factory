---
name: factory-retrospective
description: Use after a job ends FAILED or with an incident (escaped defect, false PASS, harness failure, budget blow-up) or at the end of a factory eval cycle, to locate the failing layer from traces and propose one improvement with a regression test as a separate PR.
---

# factory-retrospective

Follow ../_shared/contract.md

## Purpose

Turn a failure into a measured improvement of the factory, not into a silent
change of a running job. You determine from traces which layer failed — the
product, the evaluation, the harness, the policy, the model, or the environment
— and propose a concrete change (skill/prompt version, check, policy, controller
code) together with a regression test that would have caught the incident. The
proposal is delivered as a separate PR with operator permissions and normal
tests/review; it never modifies the policy or skills of a job in progress.

## Required inputs

- Incident reference: job/feature IDs, final state, and what went wrong (who saw it, when).
- Event log and state transitions with timestamps (UTC), attempts and analysis rounds used.
- Reports and evidence manifests of the job (verdicts, findings, runner records).
- Budget/usage ledger.
- Current versions: prompt and skill digests, policy digest, model IDs.
- Factory eval results (for eval cycles): suite, profile, pass rates, previous baseline.

## Procedure

1. Build a short `timeline` of decisive events only (state change, verdict, first
   wrong decision, detection). Times in ISO-8601 UTC from the event log.
2. Find the first point where the factory accepted something wrong or failed to
   progress. Classify `failure_layer` with `references/incident-layers.md`. Evidence
   for the classification is mandatory (event IDs, report IDs, runner records).
3. State `root_cause` as a falsifiable sentence (e.g. "spec-evaluate accepted a
   criterion that only checks that a function exists; counterexample step was skipped").
   If two hypotheses remain, say so and name the evidence that would separate them.
4. Propose at most 3 changes, ordered by expected impact. Each: `change`,
   `target_files`, a `regression_test` that fails today and passes after the change,
   `separate_pr: true`. Prefer the smallest change at the failing layer; adding more
   "think harder" text is not a fix.
5. Record `metrics` that let the operator verify the improvement (e.g. escaped
   defects per 10 features, false-PASS rate, rounds used, cost per feature).
6. Prompt/skill changes are new versions (`<role>.v2.md`), never in-place edits;
   they become active only after their eval passes.

## Output

Envelope per contract, `kind: "retrospective"`. Payload:

```json
{
  "incident_ref": "job-07 FEAT-042 escaped defect INC-3",
  "failure_layer": "evaluation",
  "timeline": [{"at": "2026-10-26T18:02:11Z", "event": "spec-evaluate PASS round 1"}],
  "root_cause": "Acceptance criterion AC-1 did not cover filtered queries; counterexample omitted the filter path.",
  "proposals": [
    {"id": "P-1", "change": "Require counterexamples per query path", "target_files": ["factory/skills/factory-spec-evaluate/SKILL.md"], "regression_test": "tests/evals/spec_evaluate/test_filtered_isolation.py", "separate_pr": true}
  ],
  "metrics": [{"name": "escaped_defects_per_10_features", "value": "1"}]
}
```

- `PASS`: layer identified with evidence, root cause stated, every proposal has a
  regression test; no proposal touches the running job.
- `CHANGES`: the analysis is usable but incomplete (e.g. a proposal lacks a
  regression test, timeline gap); findings say what to add.
- `BLOCKED`: traces/evidence needed for the analysis are missing or retention-expired.
- `UNKNOWN`: evidence is contradictory and no layer can be identified.

## Limits

- Never edit policy, gates, skills, prompts, budgets or evidence of a running or
  finished job; proposals go to a separate PR reviewed like any factory change.
- Do not blame "the model" without evidence ruling out evaluation and harness layers.
- Do not propose relaxing criteria, removing checks or raising limits as the fix for
  a false PASS or a failure; limit changes are operator decisions with a reason.
- Do not include secrets or private holdout cases in the report.
- Based on traces, short: no full conversation transcripts.
