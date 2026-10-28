# Design direction

The visual contract of a product with a user interface. UI is built against it and reviewed
against it, so "modern, intuitive, beautiful" becomes something a reviewer can check on a
screenshot instead of a matter of taste.

`inputs.design` tells you where it lives in the target repository and whether it exists:
`{"doc": "docs/DESIGN.md", "exists": true|false, "references_dir": "docs/design", "references": [...], "text": "..."}`.
`references` are reference images (screenshots of the accepted look, mock-ups, or products the
operator pointed to) stored in the repository.

## When it is missing

A feature that adds or changes user-visible screens and finds `exists: false`:

- **intake** raises one decision about the visual direction (see factory-intake), unless the
  request already describes the look.
- **spec-author** adds a requirement to write the design direction document from the decided
  direction, with the sections below, before any screen is built.
- **plan** makes that document and the design tokens the first UI task; every UI task depends on it.

## Required sections of `docs/DESIGN.md`

1. **Direction** – 2-4 sentences: audience, personality (e.g. calm and focused, playful), density,
   and 1-3 named references (products or the images in `references_dir`).
2. **Tokens** – where they live in code (one module/theme file) and their values:
   - color: background, surface, text (primary/secondary), primary, accent, success, warning, danger,
     each with its dark-theme value; text contrast >= 4.5:1 on its background;
   - typography: font family, a type scale (e.g. 12/14/16/20/24/32) with weights and line heights;
   - spacing: one scale (e.g. 4/8/12/16/24/32/48); radius; elevation/shadow.
3. **Layout** – grid/max content width, breakpoints, safe areas, minimum touch target (44 px).
4. **Components** – the shared components every screen uses (button, input, card, list item,
   empty/error state), with variants. New screens compose these; no ad-hoc styles.
5. **Motion** – durations (e.g. 150-250 ms), easing, what animates (feedback, transitions) and
   reduced-motion behavior.
6. **Voice** – tone of copy, terminology, how errors and empty states are worded.

## Rules for code

- Styles come from the tokens; a literal color, size or spacing value in a screen is a deviation
  unless the document allows it.
- Changing the direction or a token is a change of the document in the same task, stated in the
  handoff; never drift silently.
- When the document and a reference image disagree, the document wins; record the conflict.
