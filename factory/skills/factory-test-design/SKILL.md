---
name: factory-test-design
description: Use in controller state DECOMPOSE (test-plan artifact) once the spec has a current evaluation PASS, to derive scenarios, assertions and the RED/GREEN strategy for every requirement within the feature's risk level.
---

# factory-test-design

Follow ../_shared/contract.md

## Purpose

Produce the test plan that turns each requirement and acceptance criterion into
concrete, executable scenarios with assertions on observable results (API
response, persisted data, rendered state). The plan is the contract the builder
codes against and evaluators verify against. It feeds `test-plan.md` in the
OpenSpec change and is later checked by `factory-test-adequacy`.

## Required inputs

- Frozen feature snapshot and the OpenSpec change (`specs/**/spec.md`,
  `acceptance-map.yaml`, `design.md` when present) with a current spec-evaluation PASS.
- Classification (`complexity_path`, `risk`) and security minimum from policy.
- Repository map: test framework, test commands, fixture conventions, E2E tooling.
- Holdout policy: which scenario categories are reserved for the evaluator's private pool.

## Procedure

1. Confirm the spec digest matches the evaluated spec. Stale => `UNKNOWN`.
2. For each requirement list scenarios `SC-<req>-<n>`: happy path, each error path
   stated or implied, boundaries (empty, first/last page, max size), authorization
   (other user/role), and data effect (written, then re-read). Skip a category only
   with a reason recorded in `limitations`.
3. Choose the cheapest level that observes the behavior: unit for pure logic,
   integration for persistence/contract, e2e for user flows and cross-task effects.
   Every feature has at least one feature-level e2e scenario owned by the parent task.
4. Write assertions that a wrong implementation would fail. "Endpoint exists",
   "component renders" or "returns 200" alone is not an assertion of behavior.
5. Mark `red_expected: true` when the test must fail before the change; a scenario
   that already passes on the base commit must say why (regression guard).
6. Name fixtures and synthetic data. No real customer data, credentials or production endpoints.
7. Visibility: `public` scenarios are visible to the builder. For holdout, record
   only the category (`visibility: "holdout_category"`, generic `then`), never the
   private case itself. Requirements are never hidden as surprise tests.
8. Apply `references/red-green.md`; list any requirement without a scenario in
   `uncovered_requirements` and return `CHANGES`.

## Output

Envelope per contract, `kind: "test-design"`. Payload:

```json
{
  "strategy": "Contract + integration tests for API, component tests for UI, one e2e flow",
  "scenarios": [
    {
      "scenario_id": "SC-3-1",
      "requirement_ids": ["REQ-3"],
      "level": "integration",
      "given": "users A and B each have 3 operations",
      "when": "A requests GET /api/history",
      "then": "response contains exactly A's 3 operations",
      "assertions": ["len(items) == 3", "all(item.owner_id == A.id)"],
      "red_expected": true,
      "fixtures": ["two_users_with_operations"],
      "visibility": "public"
    }
  ],
  "uncovered_requirements": []
}
```

- `PASS`: every requirement covered, risk categories addressed, assertions observable.
- `CHANGES`: a requirement cannot be tested as written (vague criterion) or is uncovered;
  the finding names the requirement and the missing observable.
- `BLOCKED`: required test tooling or environment is unavailable (e.g. no browser
  runner for a UI requirement) and cannot be provided within the job's permissions.

## Limits

- Do not write production code. You may describe test code; the builder implements it.
- Do not weaken or drop scenarios to fit budget; do not reduce scope.
- You may not approve later weakening of these tests; `factory-test-adequacy` judges that.
- Do not copy holdout cases into the plan or any builder-visible location.
- Runs inside the shared 3-round analysis budget when invoked before build.
