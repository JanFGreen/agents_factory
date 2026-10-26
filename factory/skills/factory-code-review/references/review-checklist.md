# Code review checklist

Source: blueprint §7.8, §10.1 (Code reviewer), §11.1, §11.4.

## Snapshot

- [ ] diff and gate PASS refer to the same `head_sha`
- [ ] no reviewer edits; builder paused on this snapshot

## Correctness against spec

- [ ] each requirement's happy, error and boundary path traced in code
- [ ] contracts from `design.md` honored (names, types, status codes, error codes)
- [ ] pagination/ordering deterministic; no off-by-one; stable tie-breakers
- [ ] errors not swallowed; dependency failures produce defined responses
- [ ] concurrency: shared state, caches keyed per user/tenant, idempotent writes
- [ ] data: transactions where needed; migrations reversible

## Scope and integration

- [ ] changes stay in task `file_scope`
- [ ] no unrequested features, flags or "future" hooks beyond recorded constraints
- [ ] no unplanned lockfile, public API or schema changes
- [ ] consumers of changed contracts updated or untouched by design

## Maintainability

- [ ] follows existing module patterns (repo-map); no parallel mechanism
- [ ] names reflect domain; dead code removed
- [ ] tests live next to the change and implement plan scenarios

## Reclassification triggers (signal upward only)

new public interface · data migration/backfill · cross-module coupling ·
new trust boundary · external system · irreversible side effect

## Finding quality

- concrete input or scenario that fails, plus file:line
- stable ID reused across rounds; status `open`/`fixed` set by the reviewer
- blocking only when a requirement, contract or safety rule is violated
