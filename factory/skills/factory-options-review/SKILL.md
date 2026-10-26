---
name: factory-options-review
description: Use in controller state AI_OPTION_REVIEW (after options.md is drafted for a standard/complex feature) to critique each implementation option for feasibility, hard constraints and rating evidence, and recommend one without waiting for a human.
---

# factory-options-review

Follow ../_shared/contract.md

## Purpose

Give an independent critique of the real alternatives in `options.md` before
`factory-select-option` decides. You check that options are genuinely different
and feasible, that hard constraints are verified with evidence, that ratings and
estimates are justified, and that no option silently reduces scope. You may
recommend an option; the binding selection is the controller's weighted argmax
computed via `factory-select-option`. Spec semantics are judged separately by
`factory-spec-evaluate`.

## Required inputs

- Original request, normalized feature, constraints and current spec draft (`proposal.md`, `specs/**`).
- `openspec/changes/<change-id>/options.md` for this round.
- Classification (path, risk, dimension evidence) and `factory-repo-map` facts.
- Selection policy (weights, tie-break, minimum options: 2 for standard/complex, 1 for trivial).
- Previous options-review findings (rounds 2-3) and `analysis_round_id`.

## Draft-and-critique mode

When `<inputs>` has `mode: "draft-and-critique"` and no `options` field (controller state
`OPTIONS_DRAFT`), this run is also the planner: draft `minimum_options`–3 genuinely
different, realistic implementation options from the request and the repository
facts, then critique and rate each one exactly as below. When `spike_question` is
set, answer it with the facts you can verify read-only and re-rate the options.

## Procedure

1. Confirm digests of options, spec draft and policy.
2. Check the option count against the policy minimum. Fewer real options than required is
   a blocking finding unless the path is trivial with a written justification.
3. For each option apply `references/critique-checklist.md`:
   hard constraints, feasibility in this repo, permissions and environment, risks,
   evidence behind each rating, estimates, and hidden scope reduction.
4. Detect straw men (an option designed to lose) and near-duplicates (same approach
   with cosmetic differences); both are findings because they distort the choice.
5. Mark an option `feasible: false` only with concrete `infeasible_reasons` citing
   evidence; never call an impossible option feasible to keep two options.
6. Provide your own ratings 0-5 per criterion (higher is better; cheaper/faster =>
   higher `cost`/`delivery_time`). Where you disagree with `options.md`, add a finding.
7. Set `selected_option_id` to your recommendation computed with the policy weights
   and tie-break, or `null` when the package needs changes before any choice is sound.
8. If a decisive fact is unknown and the top options are within 0.03, recommend the
   single feature spike in a finding (bounded experiment, expected result per outcome).

## Output

Envelope per contract, `kind: "options-review"`. Payload (same shape as select-option):

```json
{
  "options": [
    {"option_id": "OPT-A", "feasible": true, "infeasible_reasons": [],
     "ratings": {"goal_fit": 5, "correctness": 4, "maintainability": 4, "delivery_time": 3, "cost": 3},
     "estimated_cost_usd": 4.5, "estimated_minutes": 90}
  ],
  "selected_option_id": "OPT-A"
}
```

- `PASS`: enough real options, each critiqued, feasibility and ratings evidenced; a recommendation exists.
- `CHANGES`: options package needs fixes (missing constraint check, straw man, unsupported rating,
  too few options); `selected_option_id` may be `null`.
- `BLOCKED`: no option can be feasible without a missing credential, permission or business decision.

## Limits

- Part of the shared analysis budget (max 3 rounds per feature, first counts).
- Do not ask a human to choose; do not request spec approval.
- Do not write or rewrite options yourself beyond findings; do not edit the spec.
- Do not change weights, constraints or the minimum option count; do not recommend an
  option that drops an intent of the original request.
