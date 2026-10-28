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

## Maintainability (engineering principles, review rules only)

- [ ] follows existing module patterns (repo-map); no parallel mechanism
- [ ] EP-2 each touched module still has one responsibility; exports only what callers use
- [ ] EP-4 no business rule, constant or schema now implemented twice (also when worded differently)
- [ ] EP-5 no abstraction, option or hook without a current second use
- [ ] EP-6 no new hidden global/mutable state; time, random and I/O injected
- [ ] EP-7/EP-8 input parsed at the boundary; errors typed, none swallowed
- [ ] EP-10 a new variant did not require editing the same switch in several places
- [ ] EP-11 implementations honour the whole port contract; callers depend on the port
- [ ] EP-13 no N+1, unbounded query/list or repeated expensive work on a user path
- [ ] names reflect domain (EP-9); dead code removed
- [ ] tests live next to the change and implement plan scenarios

Gate-enforced (do not re-report): complexity, function/file size, parameters, nesting,
copied blocks, layer imports, deprecated APIs, formatting.

## Reclassification triggers (signal upward only)

new public interface · data migration/backfill · cross-module coupling ·
new trust boundary · external system · irreversible side effect

## Finding quality

- concrete input or scenario that fails, plus file:line
- stable ID reused across rounds; status `open`/`fixed` set by the reviewer
- blocking only when a requirement, contract or safety rule is violated
