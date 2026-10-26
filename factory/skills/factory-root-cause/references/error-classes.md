# Error classes and retry rules

Source: blueprint §7.8, §8, §12.3.

The trusted gate reports `FAIL_CODE`, `FAIL_POLICY`, `ERROR_ENV`, `TIMEOUT`,
`CANCELLED`, `UNKNOWN`. Root-cause adds `FLAKY` as a diagnosis. `CANCELLED` is an
operator/controller action and is never analyzed by this skill (the controller
does not trigger root-cause for it).

| Class | Typical evidence | Owner / route | Retry allowed? |
|---|---|---|---|
| `FAIL_CODE` | assertion failure, exception in product code | builder → `BUILD` | yes, with a changed hypothesis |
| `FAIL_POLICY` | forbidden path touched, secret detected, lockfile change not allowed | builder → `BUILD`, or analysis if the plan requires it | only after the violating change is removed or authorized |
| `ERROR_ENV` | missing tool, DB not reachable, port in use, OOM | harness → `BLOCKED_ENV` | after environment repair; product code untouched |
| `TIMEOUT` | deadline hit; distinguish hang vs. slow | depends: hang → `BUILD`; slow env → `BLOCKED_ENV` | not with a raised timeout as the only change |
| `FLAKY` | same inputs, different outcomes | test owner → `BUILD` (fix test or race) | never "rerun until green" |
| `UNKNOWN` | evidence insufficient | re-evaluate / `NEEDS_HUMAN` | no advance |

## Trigger and retry rules

- Trigger: the same failure fingerprint on two consecutive attempts of one task.
- A third attempt with an identical prompt and no new evidence is forbidden.
- The next attempt must change at least one of: hypothesis, plan, environment.
- Root-cause does not add attempts; per-task 3 and per-feature 30 limits remain.
- Requirement defects found after implementation route to `OPTIONS_DRAFT`
  (re-selection, re-evaluation, no human approval); wrong intent routes to `INTAKE`.

## Fingerprint

`<check id>:<test id or step>:<error type>[:<normalized message>]`, with paths,
timestamps, ports and random IDs normalized away.
