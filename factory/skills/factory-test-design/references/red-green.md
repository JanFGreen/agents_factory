# RED/GREEN and scenario rules

Source: blueprint §7.8, §7.9, §11.3.

## RED/GREEN

1. **RED:** the new test runs against the base commit and fails for the expected
   reason (assertion on behavior, not import error or missing fixture).
2. **GREEN:** after the change the same test passes without editing its assertions.
3. A test that is green on base must be labeled a regression guard with a reason.
4. Zero executed tests where tests were required is never GREEN.

## Scenario checklist per requirement

| Category | Example (FEAT-042 history) | Required when |
|---|---|---|
| Happy path | filter by type returns matching rows | always |
| Error path | invalid cursor returns 400 with error code | input validation exists |
| Boundary | empty history, last page, page size max | lists, limits, pagination |
| Authorization | user B's rows never in A's response | any user-owned data |
| Data effect | create operation, reload, appears in history | writes or persistence |
| Persistence of UI state | filter + page restored after reload | UI state in URL/storage |
| Dependency failure | DB timeout returns 503, no partial page | risk medium/high backend |

## Weak assertions (reject)

- status code only; "element exists"; snapshot of whole page without semantic check;
  mocking the unit under test; asserting on log text instead of result.

## Holdout

Public scenarios are representative and visible to the builder. The evaluator's
private pool holds extra data variants and boundaries per category. The plan
names the category only. A leaked holdout case becomes public and is replaced.
