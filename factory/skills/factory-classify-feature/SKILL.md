---
name: factory-classify-feature
description: Use in controller state COMPLEXITY_ASSESSMENT (and again after the actual diff exists) to score the six complexity dimensions with repository evidence and assess risk separately, so the controller can compute the trivial/standard/complex path.
---

# factory-classify-feature

Follow ../_shared/contract.md

## Purpose

Provide the evidence the controller needs to route a feature. You score six
dimensions 0-2 and assess risk as a separate axis. You do **not** pick the path:
the controller sums the scores and applies the thresholds and fast-path
preconditions from `references/complexity-rubric.md`. Complexity is not risk: a
one-line authorization change is small but high risk and never qualifies for the
fast path. When re-run on the actual diff, hidden complexity raises the class;
neither you nor the builder may lower it, and attempt counters and budget are kept.

## Required inputs

- Normalized feature (`feature.json` from `factory-intake`) and its revision.
- `factory-repo-map` result (modules, trust boundaries, commands) for the current base.
- Policy minimums: security path rules, risk floor per path (e.g. `src/auth/**` >= high).
- For re-classification: the actual diff (`base_sha..head_sha`) and the previous assessment.

## Procedure

1. Confirm feature revision and repo-map base match the request digests.
2. For each dimension (`scope`, `coupling`, `state_and_data`, `interfaces`,
   `uncertainty`, `verification`) choose 0, 1 or 2 using the anchors in the rubric.
   Each score needs `evidence`: concrete paths, symbols, commands or diff hunks,
   plus a one-line reason. File count is a hint, not a decision.
3. If you cannot establish a dimension from evidence, set `score: null` and say
   what is missing. Unknown never qualifies for trivial; do not guess 0.
4. Set the fast-path facts honestly: `single_atomic_task` (one coherent task with
   one verification), `known_pattern` (an existing equivalent in the repo, cite it),
   `new_trust_boundary` (new endpoint, credential, webhook, external call, SQL, file upload...).
5. Fill `forced_complex_reasons` when a forcing rule applies: cross-cutting contract
   change, significant data migration, unknown external system, hard-to-reverse effects.
   A single expensive dimension may force complex.
6. Assess `risk` (`low | medium | high`) independently from the scores: data
   exposure, auth, money, irreversible data changes, security path rules, new
   permissions. Risk is the maximum of intake, path rules, diff and your observations;
   never below the policy floor. List `risk_reasons` with evidence.
7. On re-classification compare with the previous assessment; any raised dimension
   or risk is reported with the diff evidence (a non-blocking finding explaining the change).

## Output

Envelope per contract, `kind: "classify-feature"`. Payload:

```json
{
  "dimensions": {
    "scope": {"score": 1, "evidence": "src/history/ and src/web/history.tsx; one module + one view"},
    "coupling": {"score": 1, "evidence": "..."},
    "state_and_data": {"score": 1, "evidence": "..."},
    "interfaces": {"score": 1, "evidence": "..."},
    "uncertainty": {"score": 0, "evidence": "..."},
    "verification": {"score": 1, "evidence": "..."}
  },
  "risk": "medium",
  "risk_reasons": ["Reads per-user data; tenant isolation required"],
  "single_atomic_task": false,
  "known_pattern": true,
  "new_trust_boundary": true,
  "forced_complex_reasons": []
}
```

- `PASS`: all six dimensions assessed with evidence (null allowed only with a stated
  reason in `limitations`) and risk reasons cited.
- `CHANGES`: the input feature is ambiguous in a way that makes classification
  impossible to evidence (route back to intake with findings).
- `BLOCKED`: repo map missing/stale or repository not readable.

## Limits

- Do not output a path name, a total score or a recommendation to skip analysis;
  the controller computes them.
- Do not lower risk below the policy minimum and do not lower a class set earlier.
- Read-only: no code changes, no commands with side effects.
- A re-classification does not reset analysis rounds, attempts or budget.
