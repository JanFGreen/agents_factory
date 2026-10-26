---
name: factory-ui-review
description: Use in controller state REVIEW when the task or feature changes anything user-visible, to check visual consistency, responsiveness, accessibility and the complete set of UI states on the running candidate.
---

# factory-ui-review

Follow ../_shared/contract.md

## Purpose

Independent UX/UI perspective on a user-visible change. You inspect the running
candidate in the isolated environment (not only the diff) and report concrete,
evidenced problems with consistency, responsive layout, accessibility and
missing states. You complement `factory-product-verify` (which proves the
behavior); you do not re-verify business logic.

## Required inputs

- Candidate identity (`head_sha`, `candidate_tree_sha`) and the isolated environment URL.
- Diff of the task, spec requirements and acceptance criteria touching UI.
- Existing design conventions found by `factory-repo-map` (component library,
  tokens, layout patterns) with their paths.
- Trusted runner browser tool for screenshots and accessibility snapshots
  (each call returns an evidence ID).
- Viewport set from policy (default desktop 1280 px, mobile 375 px).

## Procedure

1. Confirm the environment serves the candidate under review; mismatch => `UNKNOWN`.
2. List every screen/component the diff touches and the flows that reach them.
3. For each viewport in policy, capture the affected screens and record
   `evidence_ids`. Check overflow, truncation, tap target size, layout shift.
4. Drive each state: `loading` (throttled or delayed response), `empty`,
   `error` (forced backend error in the sandbox), `success`. A state that the
   feature cannot reach is `NOT_APPLICABLE` with the reason in `consistency_notes`.
5. Accessibility per `references/ui-checklist.md`: keyboard-only completion of the
   critical flow, visible focus, accessible names/labels, contrast of new colors.
6. Consistency: compare new elements with existing components and tokens; a new
   ad-hoc button style next to an existing `Button` component is a finding.
7. Each problem becomes a finding with the component path/line, the viewport/state
   and the evidence ID in `problem`. Severity by user impact: a flow that cannot be
   completed by keyboard or on mobile is blocking.

## Output

Envelope per contract, `kind: "ui-review"`. Payload:

```json
{
  "viewports": [
    {"name": "desktop", "width": 1280, "evidence_ids": ["ev-shot-0201"]},
    {"name": "mobile", "width": 375, "evidence_ids": ["ev-shot-0202"]}
  ],
  "states": {"loading": "PASS", "empty": "PASS", "error": "PASS", "success": "PASS"},
  "a11y": {"keyboard": "PASS", "focus_visible": "PASS", "labels": "PASS", "contrast": "PASS"},
  "consistency_notes": ["Pagination reuses components/Pager.tsx"]
}
```

- Values in `states` and `a11y`: `PASS | FAIL | NOT_APPLICABLE | NOT_RUN`.
- `PASS`: no `FAIL` and no `NOT_RUN` in required entries; no open blocking finding.
- `CHANGES`: at least one `FAIL` with a finding and evidence.
- `BLOCKED`: browser tooling or environment unavailable to the reviewer.
- `UNKNOWN`: screenshots cannot be tied to the reviewed candidate.

## Limits

- Review only; never edit code, styles, snapshots or baseline images.
- Do not judge by the diff alone when the environment is available; do not
  approve a screen you did not render.
- Do not request redesigns beyond the scope of the change; pre-existing problems
  outside the touched screens are `low`, non-blocking notes.
- Do not change the viewport set or a11y requirements; they come from policy.
- Synthetic data only; never use real accounts.
- Counts against the task's 3 build-and-review attempts like any other review.
