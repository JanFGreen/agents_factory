---
name: factory-test-adequacy
description: Use in controller state REVIEW for every task whose diff adds or changes tests, to judge whether the tests would actually detect wrong implementations, whether fixtures are sane, and whether any test was weakened.
---

# factory-test-adequacy

Follow ../_shared/contract.md

## Purpose

Decide whether the tests for the current snapshot are capable of catching the
defects that matter for this task's requirements. Passing tests prove little if
they cannot fail. This skill checks detection power (fault probes), coverage of
the test plan, fixture sanity, and weakening of existing tests. It reviews; it
does not fix tests and does not trust the builder's description of them.

## Required inputs

- Current `head_sha` and the task diff against its base (test and source files).
- `test-plan.md` scenarios for the task's `requirement_ids` (from `factory-test-design`).
- Trusted runner evidence for this snapshot: argv, exit code, number of tests
  executed, per-test results, and (if configured) mutation or coverage reports.
- The builder's handoff (`factory-implement` payload) — as claims to check, not evidence.

## Procedure

1. Confirm runner evidence references the same `head_sha`. Different SHA or missing
   evidence => `UNKNOWN` (do not judge tests that were not run on this snapshot).
2. Executed-tests check: required suites ran and `tests_executed > 0`. Zero tests,
   a skipped suite, or a collection error where tests were required is a blocking
   finding, never PASS.
3. Plan coverage: map every public scenario of the task to a concrete test ID in
   `requirement_coverage`. A missing scenario is a finding against the requirement.
4. Fault probes: for each requirement, describe the simplest plausible defects
   (drop the owner filter, off-by-one on page boundary, ignore filter on page 2,
   swallow the error). Decide from the test code (or a mutation run if available)
   whether some test fails. Record `detected | missed | not_run`. A `missed` probe on
   a requirement assertion is blocking.
5. Weakening: inspect diffs of existing tests and configs for removed assertions,
   loosened comparisons, new `skip`/`xfail`, raised timeouts, deleted tests,
   narrowed fixtures, or excluded paths in test config. Each needs a justification
   tied to a requirement change; unjustified weakening is blocking.
6. Fixture sanity: fixtures must exercise the case (two users for isolation, more
   rows than page size for pagination). A fixture that makes the assertion trivially
   true is a finding. No real personal data or credentials in fixtures.
7. Apply `references/adequacy-checklist.md`; report all checked items.

## Output

Envelope per contract, `kind: "test-adequacy"`. Payload:

```json
{
  "requirement_coverage": {"REQ-3": ["tests/history/test_api.py::test_only_own_operations"]},
  "fault_probes": [
    {"probe": "remove owner_id filter in repository query", "detected_by": ["tests/history/test_api.py::test_only_own_operations"], "result": "detected"}
  ],
  "weakened_tests": [{"path": "tests/history/test_api.py", "change": "assert len(items) == 10 -> assert items", "justified": false}],
  "fixture_sanity": [{"fixture": "user_a_25_operations", "issue": "none"}],
  "tests_executed": 42
}
```

- `PASS`: required tests ran (> 0), every public scenario mapped, no missed probe on a
  requirement assertion, no unjustified weakening.
- `CHANGES`: fixable gaps — missed probe, unmapped scenario, weakened test, bad fixture.
- `BLOCKED`: the trusted runner cannot execute the suite (environment, missing tool).

## Limits

- Do not modify tests or code; findings only. The builder fixes, an evaluator closes.
- A builder (or the author of the tests) can never approve weakening of its own
  tests; only an authorized requirement change justifies it.
- Do not treat coverage percentage alone as adequacy.
- Do not rerun tests repeatedly to obtain a green result; flaky results are a finding.
- Counts as one review of the task attempt (max 3 attempts per task).
