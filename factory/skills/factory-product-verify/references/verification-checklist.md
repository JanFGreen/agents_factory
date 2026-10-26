# Product verification checklist (blueprint §7.8, §7.9, §4)

## Perspectives

| | PM | QA |
|---|---|---|
| Question | Does it do what was asked, completely? | Does it actually work, including failures? |
| Baseline | original request + intent map | test-plan scenarios + holdout |
| Typical finding | intent missing, scope silently narrowed | wrong data, broken boundary, no persistence |

Both run on the same candidate snapshot. Both are required where the risk policy says so.

## UI changes

- Desktop and mobile viewport.
- Keyboard-only path through the critical flow.
- States: loading, empty, error, success.
- Roles: each role in scope, plus an unauthorized role.
- Write, then reload and re-read (state survives refresh).

## Backend changes

- API contract: status codes, schema, error bodies.
- Persistence: re-read from storage, not from the response cache.
- Idempotency: repeated request does not duplicate effects.
- Dependency errors: downstream failure produces the specified error, not a 500 or silent success.
- Isolation: user/tenant A cannot read or modify B.

## What is NOT evidence

- Builder handoff or `claimed_checks`.
- A page that renders without the critical action being performed.
- Process exit code 0 with zero executed test cases.
- A screenshot without the identity of the candidate it came from.

## Environment rules

- Isolated environment with synthetic data only. Login, forms, webhooks and
  payments are exercised against sandboxes, never real recipients.
- Confirm the environment serves the candidate under review before testing.

## Holdout (§7.9)

- Public criteria and representative tests are builder-visible; holdout adds
  data variants, boundaries and regression scenarios, never hidden requirements.
- Report holdout failures as sanitized category + observed behavior.
- Any revealed case becomes known; the controller refreshes the pool.

## Whole-feature acceptance

At `FEATURE_ACCEPTANCE` re-check the original request end to end, including
interaction between tasks (e.g. filter + pagination + reload together). All
green tasks do not imply the parent is DONE.
