---
name: factory-ui-review
description: Use in controller state REVIEW when the task or feature changes anything user-visible, to check visual quality, usability heuristics, conformance to the design direction, responsiveness, accessibility and the complete set of UI states on the running candidate.
---

# factory-ui-review

Follow ../_shared/contract.md
Follow ../_shared/design-direction.md

## Purpose

Independent UX/UI perspective on a user-visible change. You inspect the running
candidate in the isolated environment (not only the diff) and report concrete,
evidenced problems with consistency, responsive layout, accessibility and
missing states, and you judge from the screenshots whether the screens are usable
(Nielsen's heuristics) and look the way the product's design direction says. You
complement `factory-product-verify` (which proves the behavior); you do not re-verify
business logic.

## Required inputs

- Candidate identity (`head_sha`, `candidate_tree_sha`) and the isolated environment URL.
- Diff of the task, spec requirements and acceptance criteria touching UI.
- Existing design conventions found by `factory-repo-map` (component library,
  tokens, layout patterns) with their paths.
- Trusted runner browser tool for screenshots and accessibility snapshots
  (each call returns an evidence ID).
- Viewport set from policy (default desktop 1280 px, mobile 375 px).
- `design`: the design direction document (`text` when it exists) and reference images.
- `controller_note` (re-run only): what the controller could not find in your previous review.

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
7. Usability and look per `references/ux-heuristics.md`: assess `H1`..`H10` and score the six
   visual aspects from the screenshots you took; compare with the design direction and every
   reference image (`design_direction`).
8. Each problem becomes a finding with the component path/line, the viewport/state
   and the evidence ID in `problem`. Severity by user impact: a flow that cannot be
   completed by keyboard or on mobile is blocking.

## Served web build (`environment.kind: "served"`)

The controller serves the gate-built web candidate at `environment.url` and gives you a browser
CLI (`environment.browser.binary`, Playwright CLI) through Bash. This is the rendering
environment: judge the running app, not only the diff.

1. `<cli> open <url>`, then `<cli> snapshot`: the accessibility tree with element refs (`e12`).
   Always run `snapshot` after open, resize or navigation and before a screenshot; it waits for
   the render (a screenshot taken right after `resize` can be blank).
2. For each viewport in `environment.browser.viewports` (e.g. `390x844`, `1280x800`):
   `<cli> resize <w> <h>`, `<cli> snapshot`, `<cli> screenshot --filename <evidence_dir>/<screen>-<w>.png`.
   Use the file names as `evidence_ids`. Look at every screenshot you take (Read the PNG): overflow,
   truncation, alignment, spacing, visual hierarchy, contrast. Each viewport needs at least one
   saved screenshot cited in `viewports[].evidence_ids`; the controller checks the names against
   the files it collected, and a review without them is inconclusive and re-runs.
3. Drive the touched flows with refs from the snapshot: `<cli> click <ref>`, `<cli> fill <ref> <text>`,
   `<cli> press Tab` / `Enter` / `Escape` for the keyboard path, `<cli> set-color-scheme dark` for the
   dark theme. A state the app cannot reach without its backend (no server runs) is
   `NOT_APPLICABLE` with the reason, or judged from code as in `local-checkout`.
4. Score `heuristics` (all ten) and `visual` (all six aspects, with scores) from these
   screenshots; open the reference images from `design.references` and compare.
5. `<cli> console` after the flows: errors in the console are findings (`medium`, blocking when a
   flow breaks).
6. Finish with `<cli> close`. Write files only into `evidence_dir`; the repository is read-only.

Render-only checks are real here: they are `PASS` or `FAIL`, not `NOT_RUN`.

## Environment without a browser (`environment.kind: "local-checkout"`)

The controller states the environment in `<inputs>`. When it is `local-checkout`, no browser,
screenshot tool or served app exists for you; that is the environment, not a defect of the
candidate, so it is never by itself a reason for `BLOCKED` or `UNKNOWN`.

- Judge from the code and the component tests (rendered with Testing Library) in the checkout:
  tokens vs. ad-hoc styles, roles and accessible labels, touch-target sizes in styles, every UI
  state reachable in code (loading/empty/error/success), keyboard handlers, consistency with
  existing components.
- Entries that need a real render (viewport screenshots, measured contrast, visible focus,
  layout shift, `visual` scores) are `NOT_RUN` with the reason "local-checkout: not rendered"
  (`visual` entries get `score: null`). They do not block. Heuristics you can judge from code
  (e.g. H3 no cancel path, H9 raw error text) are assessed as usual.
- `design_direction` is still checked from code when the document exists: tokens vs. literal
  values, shared components vs. ad-hoc styles.
- A defect you can show from code or tests is a finding as usual (blocking when a flow cannot be
  completed, e.g. no keyboard path or a control without a label).
- Risks you could only confirm by rendering are non-blocking findings (`severity: low|medium`),
  so the builder still sees them.
- Verdict: `PASS` when there is no `FAIL` from code/test evidence and no open blocking finding;
  `CHANGES` when there is. Say in `summary` that the review was static.

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
  "consistency_notes": ["Pagination reuses components/Pager.tsx"],
  "heuristics": [
    {"id": "H1", "status": "PASS", "evidence_ids": ["history-1280.png"], "note": "Spinner while the page loads"},
    {"id": "H9", "status": "FAIL", "evidence_ids": ["history-error-390.png"], "note": "Raw 'Error 500' text, no retry"}
  ],
  "visual": [
    {"aspect": "hierarchy", "score": 4, "evidence_ids": ["history-1280.png"], "note": "Title and filter lead"},
    {"aspect": "layout", "score": 3, "evidence_ids": ["history-390.png"], "note": "Uneven gaps between rows on mobile"}
  ],
  "design_direction": {"status": "PASS", "references": ["docs/design/history.png"], "deviations": []}
}
```

- `heuristics` lists all ten (`H1`..`H10`) and `visual` all six aspects when the build is served
  (two entries of each shown above for brevity).
- Values in `states`, `a11y`, heuristic and design statuses: `PASS | FAIL | NOT_APPLICABLE | NOT_RUN`.
- `PASS`: no visual score below `design.min_visual_score`, no `FAIL` and no `NOT_RUN` in required entries (except render-only entries in a
  `local-checkout` environment, see above); no open blocking finding.
- `CHANGES`: at least one `FAIL` with a finding and evidence.
- `BLOCKED`: the environment the controller named (with a browser) is unavailable to the reviewer.
- `UNKNOWN`: screenshots cannot be tied to the reviewed candidate.

## Limits

- Review only; never edit code, styles, snapshots or baseline images.
- Do not judge by the diff alone when a rendering environment is available. Without one
  (`local-checkout`), judge the code and tests and mark render-only checks `NOT_RUN`.
- Taste is not a finding: tie every visual finding to an aspect score, a heuristic, the design
  direction or a reference image, with the screenshot.
- Do not request redesigns beyond the scope of the change; pre-existing problems
  outside the touched screens are `low`, non-blocking notes.
- Do not change the viewport set or a11y requirements; they come from policy.
- Synthetic data only; never use real accounts.
- Counts against the task's 3 build-and-review attempts like any other review.
