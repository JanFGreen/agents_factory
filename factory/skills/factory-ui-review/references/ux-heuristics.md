# UX heuristics and visual quality from screenshots

Judge what the screenshots and the driven flows show, not what the code intends. Every entry
cites the screenshot file names it is based on (`evidence_ids`).

## Nielsen's ten heuristics (`heuristics`, one entry each, `H1`..`H10`)

| Id | Heuristic | What to look for on the touched screens |
|---|---|---|
| H1 | Visibility of system status | Loading, saving and progress are visible; the current step/tab/selection is obvious; results of an action show at once |
| H2 | Match with the real world | Words of the user, not of the code (no ids, enum names, "null"); familiar order and icons; people named as people ("You", "Computer", a nickname), not only by their internal token ("X", "O", "player 1") |
| H3 | User control and freedom | Back, cancel, undo or close on every step; no dead end; destructive actions confirm or can be undone |
| H4 | Consistency and standards | Same action looks and is named the same everywhere; platform conventions (tabs, back, links) |
| H5 | Error prevention | Disabled/validated inputs before submit, sensible defaults, no easy mis-tap next to a destructive control |
| H6 | Recognition rather than recall | Options visible, labels on icons, context kept between steps, no need to remember values from another screen |
| H7 | Flexibility and efficiency | Primary action reachable in one tap/key; keyboard path (Enter submits, Escape closes); no needless steps |
| H8 | Aesthetic and minimalist design | One clear primary action per screen; no noise, duplicated text or competing emphasis |
| H9 | Recognize, diagnose, recover from errors | Error says what happened and what to do, in plain words, next to the cause, with a way to retry |
| H10 | Help and documentation | First use explains itself (empty states, hints, onboarding) without a manual |

Status: `PASS` (no problem seen), `FAIL` (a finding with evidence), `NOT_APPLICABLE` (the touched
screens have nothing this heuristic covers; say why in `note`), `NOT_RUN` (only in `local-checkout`).

## Visual quality (`visual`, one entry per aspect, score 1-5)

| Aspect | 5 – polished | 3 – acceptable | 1 – broken |
|---|---|---|---|
| hierarchy | The eye lands on the primary content/action first; clear heading levels | Order understandable, emphasis a bit flat or split | No focal point; everything equal or competing |
| layout | Consistent grid and spacing rhythm from the spacing scale; aligned edges; good use of width on both viewports | Minor uneven gaps or alignment | Overlap, overflow, horizontal scroll, cramped or empty areas |
| typography | Type scale from the tokens; readable sizes (body >= 16 px mobile), comfortable line length and height | One or two off-scale sizes or weights | Unreadable, truncated or mixed fonts |
| color | Palette from the tokens, purposeful accent, sufficient contrast, dark theme coherent | Some off-token colors, contrast borderline | Clashing colors, illegible text, meaning only by color |
| consistency | Every element is a shared component; matches the other screens and the design direction | Small deviations from existing components | New ad-hoc styles next to existing components |
| polish | Feels finished: icons aligned, states designed, transitions smooth, nothing placeholder | Works, a few rough edges | Placeholder text, broken images, default browser styling |

- Score each touched screen set as a whole; the note names the worst spot and its screenshot.
- A score below the policy floor (`inputs.design.min_visual_score`, default 3) is a blocking finding: the screen
  does not meet the product's bar even if it works. The controller turns a PASS with such a score
  into CHANGES, so report it as CHANGES yourself with the finding.
- Do not ask for a redesign of screens the change does not touch; score only the touched ones.

## Design direction (`design_direction`)

- `inputs.design.exists: true`: read `inputs.design.text` and open every image in
  `inputs.design.references` (Read the file). Compare the screenshots with them: tokens (colors,
  type, spacing, radius), component usage, density, tone. `status`: `PASS` or `FAIL`; list each
  concrete deviation in `deviations` (what, where, which screenshot) and make blocking ones findings.
  List the compared images in `references`.
- `exists: false`: `NOT_APPLICABLE`, unless this task was meant to create the document (then its
  absence is a `FAIL` finding against the spec).

## Measuring in the browser (Playwright CLI)

- **Element box:** `<cli> eval "() => JSON.stringify(document.querySelector('<selector>').getBoundingClientRect())"`.
  Read it before and after the action. `shift_px` = max(|Δtop|, |Δleft|).
- **Colors:** `<cli> eval "() => { const s = getComputedStyle(document.querySelector('<selector>')); return JSON.stringify([s.color, s.backgroundColor, s.outlineColor, s.boxShadow]); }"`.
  Take the background from the nearest ancestor with a non-transparent one. Contrast =
  (L1 + 0.05) / (L2 + 0.05) with the WCAG relative luminance of each sRGB color.
- **States:** `<cli> hover <ref>` for hover; `<cli> press Tab` until the control has focus for
  focus; click a selectable control, then hover or Tab back to it for the combined states. Take a
  screenshot in each state and cite it.
- **First screen:** `<cli> resize 360 640`, reload, screenshot without scrolling. An element is
  above the fold when its `getBoundingClientRect().bottom <= 640`.
