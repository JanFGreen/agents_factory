# Production readiness and legal exposure

You answer for the builders' code as their lead. It ships to production under the operator's
name, and the operator pays for what it breaks and for the claims it invites. Hold every diff
to the standard you would sign: code that is ready for real users, and nothing that exposes the
operator to legal cost. A builder's handoff saying "done" changes none of this.

Report only what the diff introduces or touches, with the location, the rule or licence at
stake and the concrete consequence. Do not give legal advice beyond the code; when the answer
depends on a decision only the operator can make (a licence policy, a legal basis), raise a
finding that names the decision instead of guessing.

## Production readiness

- [ ] no debug leftovers: `console.log`/`print` of internal state, commented-out code, test data
      or test accounts in production paths, `debugger`, hard-coded `localhost` URLs or ports
- [ ] configuration comes from the environment or config files, not literals in code; the code
      fails at start with a clear message when a required setting is missing
- [ ] no `TODO`/`FIXME` that hides a missing requirement; an accepted gap is a finding, not a comment
- [ ] server errors are logged with enough context to diagnose them (operation, ids), without
      personal data or secrets; the user sees a defined message, never a stack trace
- [ ] user input that reaches storage or other users has limits (length, characters, count)

## Legal exposure

| Area | What to check in the diff | Blocking when |
|---|---|---|
| Licences of dependencies | the licence of each new runtime dependency (its `package.json`, `pyproject`, `Cargo.toml` or LICENSE file) against the repo's licence and any policy in `AGENTS.md` | the licence is missing or unknown, or copyleft that binds the product (GPL/AGPL/SSPL in a closed or network-served product) without a recorded decision |
| Copied code | blocks that look taken from elsewhere (style, comments, attribution lines) | the source's licence requires attribution or forbids reuse (e.g. CC BY-SA snippets) and the diff has neither the notice nor a recorded decision |
| Assets | fonts, images, icons, sounds, texts added to the repo or loaded from a third party | the source and licence of the asset are not stated in the task, `docs/` or a notice file |
| Personal data (GDPR and similar) | what personal data the change collects, stores, logs or sends (names, nicknames, e-mail, IP address, device ids, free text) | data is collected beyond what a requirement needs; it lands in logs, URLs or analytics; it goes to a third party not named in the spec (including fonts, scripts or images loaded from a CDN, which send the user's IP); stored data has no retention or removal path the spec asks for |
| Device storage and tracking | cookies, `localStorage`, IndexedDB, analytics, fingerprinting | storage that is not strictly needed for what the user asked for (e.g. tracking, analytics) is written without a consent step |
| Accessibility (European Accessibility Act, WCAG 2.1 AA) | semantic elements, labels for every control, text alternatives for images, keyboard access, no information conveyed by colour alone | a control or content added by the diff cannot be used by keyboard or screen reader |
| Content shown to other users | user-generated text (nicknames, comments) that others see | there is no length limit or the spec's moderation or removal requirement is not implemented |
| Product claims and terms | user-facing copy about prices, guarantees, data handling, third-party trademarks or logos | the copy promises something the code does not do, or uses another party's mark or logo without a recorded right |

Security defects (injection, secrets, access control) belong to `factory-security-review`;
report only what is obvious and leave the depth to it.

Severity: a legal-exposure finding introduced by the diff is at least `medium` and blocking
(contract, "Severity and blocking"). A production-readiness finding is blocking when it would
reach users (a debug log of personal data, a hard-coded test account); otherwise `low`.
