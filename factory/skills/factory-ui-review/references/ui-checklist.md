# UI review checklist

## Viewports (policy default)

| Name | Width | Must check |
|---|---|---|
| desktop | 1280 px | layout, alignment, density |
| mobile | 375 px | no horizontal scroll, tap targets >= 44 px, wrapping of long values |

## States — every data-driven view

| State | How to reach in the sandbox | Expected |
|---|---|---|
| loading | delay/throttle the API response | skeleton or spinner, no layout jump, no stale data shown as current |
| empty | user/filter with no rows | explanatory empty state, not an error and not a blank area |
| error | force 5xx or network error | readable message, retry path, no raw stack trace |
| success | normal data | correct content, formatting of dates/amounts per locale |

## Accessibility

- **keyboard:** critical flow completable with Tab/Shift+Tab/Enter/Space/Escape; no trap.
- **focus_visible:** focus indicator visible on every interactive element, including custom ones.
- **labels:** inputs, icon buttons and selects have accessible names; errors are associated with fields.
- **contrast:** text >= 4.5:1, large text and UI components >= 3:1 for any newly introduced colors.

## Consistency

- Reuse existing components and design tokens found by repo-map.
- Copy and terminology match the rest of the product.
- Filter/pagination state reflected in the URL when the existing app does so.

## Severity guide

| Situation | Severity | Blocking |
|---|---|---|
| Flow cannot be completed (keyboard, mobile, error state dead end) | high | yes |
| Missing required state (no empty/error handling) | medium | yes |
| Inconsistent but usable styling | low | no |
| Pre-existing issue outside touched screens | low | no |
