---
name: factory-select-option
description: Use in controller state OPTION_SELECTED, after factory-options-review, to pick exactly one feasible implementation option with the configured weighted rubric, without asking a human.
---

# factory-select-option

Follow ../_shared/contract.md

## Purpose

Select exactly one best feasible implementation option for the current analysis
round, so that spec evaluation and decomposition can proceed on a single approach.
You rate; the controller recomputes the weighted score and tie-break and rejects
your output if `selected_option_id` differs from its argmax. Selection is not a
quality gate: a higher score never replaces a PASS of `factory-spec-evaluate` or
`factory-decomposition-review`.

## Required inputs

- Original request and frozen feature snapshot (goal, constraints, acceptance criteria).
- Classification (`complexity_path`, `risk`) from `factory-classify-feature`.
- `openspec/changes/<change-id>/options.md` and the latest `factory-options-review` result.
- Repository facts from `factory-repo-map` (commands, modules, boundaries).
- Selection policy: weights and tie-break order (see `references/scoring.md`) and its `policy_digest`.
- `analysis_round_id` (1-3) and the remaining spike allowance (max 1 per feature).

## Procedure

1. Confirm the options and review results carry the current spec/intent digests.
   Stale review => `UNKNOWN` with a limitation naming the stale artifact.
2. Hard-constraint filter: for each option check every feature constraint and
   policy rule (security minimum, allowed paths, no new trust boundary unless
   covered). Any violation => `feasible: false` with concrete `infeasible_reasons`
   citing the constraint and the evidence. Never mark an impossible option feasible.
3. Rate each feasible option 0-5 on `goal_fit`, `correctness`, `maintainability`,
   `delivery_time`, `cost`. Higher is always better: a cheaper or faster option gets
   a higher `cost` / `delivery_time` rating. Base each rating on cited facts or on
   explicit estimates (`estimated_cost_usd`, `estimated_minutes`); carry over or
   explicitly overrule each blocking finding of `factory-options-review`.
4. Compute `score = sum(weight * rating / 5)` yourself and apply the tie-break
   exactly as in `references/scoring.md`, then set `selected_option_id`. The
   controller repeats this computation; a mismatch invalidates the run.
5. If the top two scores differ by less than 0.03 and a decisive fact is unknown,
   request the single feature-level spike (finding with `expected_fix` describing
   the bounded experiment) if it is still available; otherwise decide by the
   tie-break and record the uncertainty in `limitations`.
6. Trivial path: one obvious option is allowed; give a one-sentence justification in
   `summary` why alternatives are not worth exploring.
7. No feasible option => `verdict: BLOCKED`, `selected_option_id: null`, one finding
   per option with the reason (controller state `FAILED_NO_FEASIBLE_OPTION`).

## Output

Envelope per contract, `kind: "select-option"`. Payload:

```json
{
  "options": [
    {
      "option_id": "OPT-A",
      "feasible": true,
      "infeasible_reasons": [],
      "ratings": {"goal_fit": 5, "correctness": 4, "maintainability": 4, "delivery_time": 3, "cost": 3},
      "estimated_cost_usd": 4.5,
      "estimated_minutes": 90
    }
  ],
  "selected_option_id": "OPT-A"
}
```

- Every option from `options.md` appears exactly once, feasible or not.
- `ratings` are integers 0-5; infeasible options still carry ratings but never win.
- `PASS`: exactly one selected feasible option equal to the controller argmax.
- `CHANGES`: options package is fixable (e.g. a rating lacks evidence, an option is
  missing a hard-constraint check) — the selection is not usable yet.
- `BLOCKED`: no feasible option, or a decisive business decision/credential is missing.

## Limits

- Counts against the shared feature budget of max 3 analysis rounds; never request
  a fourth round under another name. At most 1 feasibility spike per feature.
- Do not ask the user or any human to choose a technical option.
- Do not change weights, tie-break order or constraints; do not reduce scope to make
  an option feasible.
- Do not evaluate spec semantics (that is `factory-spec-evaluate`) and do not hand
  tasks to the builder — only the controller does, after current spec evaluation
  and (for standard/complex) decomposition review PASS.
- Selection is saved before downstream evaluation; a later change of requirements
  invalidates it and the controller re-runs this skill in the remaining rounds.
