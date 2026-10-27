# Spec evaluation rubric

Blueprint §7.5. All five dimensions are mandatory (`spec_evaluation.mandatory_dimensions`).

| Dimension | PASS when | CHANGES when (examples) |
|---|---|---|
| `intent_coverage` | every intent and material scenario of the original request maps to a requirement with an observable criterion; no hidden scope limit | an intent is missing; an intent is moved to future-needs; "filter" covered but "together with pagination" not |
| `consistency` | requirements do not contradict each other, the constraints, or accepted baseline specs (unless an explicit MODIFIED delta) | REQ-3 uses page numbers while baseline mandates cursors; two criteria demand different sort orders |
| `testability` | criteria describe observable results (API body, persisted rows, rendered state) for happy path, errors and key boundaries | "history page exists"; "filtering works"; no error or empty case; no way to observe the result |
| `feasibility` | approach fits the real repo, permissions, environment and budget; unknowns resolved or explicitly deferrable without changing the spec | needs a credential not in policy; depends on a module that does not exist; estimate exceeds budget |
| `risk_controls` | data, auth, security, accessibility and performance criteria exist in proportion to the risk | per-user data without an isolation criterion; new endpoint without auth criterion; UI change without keyboard/a11y criterion |

## Counterexample (mandatory)

Write the **simplest wrong implementation that would pass the current criteria**.

- Good: "Return all rows and filter by user in the browser — AC-1 only checks the UI
  list, so it passes while the API leaks data." → blocking `risk_controls` + `testability`.
- Good: "Apply the filter only to the first page" → rejected by AC-3 (page 2 checked) → OK.
- Not acceptable: "None found" without trying; a counterexample that violates an
  explicit criterion (it would not pass, so it proves nothing).

If the counterexample passes the criteria and violates an explicit intent or constraint
of the request (or a risk-mandated control), verdict is CHANGES. If it only exploits
behavior the request does not specify, record a non-blocking finding and say in
`counterexample` that the remaining gap is outside the request.

## Verdict rules

- PASS = 5 × PASS, counterexample rejected (or only outside the request), no uncovered
  requirements, no open blocking finding.
- Style issues are non-blocking (`severity: low`, `blocking: false`).
- Unrequested edge cases and hardening are non-blocking (`severity: low|medium`); they
  reach the builder as advisories. A dimension is CHANGES only with a blocking finding.
- Later rounds: new blocking findings only for regressions introduced by the revision or
  explicit intents still missing. Do not move the goalposts between rounds.
- Findings keep IDs across rounds; resolved ones get `status: "fixed"` with the evidence in `problem`.
- Two rounds without new evidence or closed blocking findings is a no-progress signal
  for the controller; say so in `summary`.

## Trivial (short mode)

For a micro-spec check, briefly but explicitly: the change matches the request exactly,
1-3 criteria are observable, the single task's verification proves the criteria, and
there is no risk signal (auth, data, new boundary). Any risk signal → CHANGES with a
finding asking the controller to re-classify (the feature leaves the fast path).
