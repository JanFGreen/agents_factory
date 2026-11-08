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
- [ ] EP-16 what a write depends on (permission, plan, quota, version) is read inside the write's transaction, at one
      instant; a transaction API cannot take an async callback (`(work: () => T)` accepts a Promise: commit before `await`)
- [ ] EP-17 fetched UI state: what refreshes it after a sync, a session change, focus or a known expiry, and is a
      failed read distinguishable from "nothing" (loading / failed / ready) with a retry
- [ ] EP-18 config and stored values that grant access are validated at startup or at the repository read; trace
      NaN / Infinity / negative limits and an unparsable date: each must deny, never allow or "never expire"
- [ ] EP-19 no caller reconstructs a value a lower function dropped (comparing ids to guess an expiry)

## Scope and integration

- [ ] changes stay in task `file_scope`
- [ ] no unrequested features, flags or "future" hooks beyond recorded constraints
- [ ] no unplanned lockfile, public API or schema changes
- [ ] consumers of changed contracts updated or untouched by design

## Maintainability (engineering principles, review rules only)

- [ ] follows existing module patterns (repo-map); no parallel mechanism
- [ ] EP-2 each touched module still has one responsibility; exports only what callers use
- [ ] EP-4 no business rule, constant or schema now implemented twice (also when worded differently)
- [ ] EP-5 lean pass: nothing to delete, reuse (name the path), replace with the standard library, drop as
      yagni or merge with a near-copy; `Skipped:` in the handoff names what was left out on purpose
- [ ] EP-20 a changed function's callers all still work; a shared bug is fixed in the shared code; moved code
      kept its validation and error handling
- [ ] EP-21 a `shortcut:` limit is not already crossed by the expected load, input or a requirement
- [ ] EP-6 no new hidden global/mutable state; time, random and I/O injected
- [ ] EP-7/EP-8 input parsed at the boundary; errors typed, none swallowed
- [ ] EP-10 a new variant did not require editing the same switch in several places
- [ ] EP-11 implementations honour the whole port contract; callers depend on the port
- [ ] EP-15 state and its rules live in a class; a functional design outside EP-15's exceptions has a reason in the handoff
- [ ] EP-13 no N+1, unbounded query/list or repeated expensive work on a user path
- [ ] EP-19 compatibility adapters are named for what they project, not for the concept they replace
- [ ] names reflect domain (EP-9); dead code removed
- [ ] tests live next to the change and implement plan scenarios

Gate-enforced (do not re-report): complexity, function/file size, parameters, nesting,
copied blocks, layer imports, deprecated APIs, formatting.

## Production and legal

- [ ] `production-and-legal.md` applied: no debug leftovers or hard-coded environment; new
      dependencies, assets and personal data checked for licence, consent and minimisation

## Reclassification triggers (signal upward only)

new public interface · data migration/backfill · cross-module coupling ·
new trust boundary · external system · irreversible side effect

## Finding quality

- concrete input or scenario that fails, plus file:line
- stable ID reused across rounds; status `open`/`fixed` set by the reviewer
- blocking only when a requirement, contract or safety rule is violated
