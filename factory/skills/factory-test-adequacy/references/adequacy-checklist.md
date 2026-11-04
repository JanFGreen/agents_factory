# Test adequacy checklist

Source: blueprint §7.8, §10.2 (`factory-test-adequacy`), §11.3.

## Execution

- [ ] Runner evidence is for the current `head_sha` and from the trusted runner.
- [ ] Required suites executed; `tests_executed > 0`; no collection errors.
- [ ] No required suite skipped by marker, config or path filter.
- [ ] Flaky result (pass after rerun) reported as finding, not as PASS.

## Fault probes (pick those relevant to the task)

| Defect class | Probe example |
|---|---|
| Authorization | drop owner/tenant filter |
| Pagination | off-by-one at page boundary; duplicate row across pages |
| Filtering | filter ignored after first page |
| Validation | accept malformed input instead of 400 |
| Error handling | swallow dependency error, return partial data |
| Persistence | write not committed; re-read returns stale value |
| UI state | state lost on reload |
| Atomicity (EP-16) | replace the transaction with a plain call (`return work()`), or move the permission read before it |
| Concurrency | two writers interleaved between read and write; another connection changes the grant between them |
| Stale UI state (EP-17) | the server value changes (expiry passes, a sync succeeds, focus returns) while the screen stays mounted |
| Invalid config or data (EP-18) | a limit of NaN or -1, an expiry stored as text: the decision must deny |

## Guarantees need a test that fails without them

For every guarantee the task or its comments claim (a transaction, a lock, idempotency, a rate limit,
an expiry, a refresh), name the test that fails when the mechanism is removed, and run that probe.
If no test fails, the guarantee is untested: a blocking finding when the requirement names it.

- JavaScript runs synchronous code to completion: `Promise.all` over handlers whose
  read-decide-write is synchronous never interleaves them, so it proves nothing about a
  transaction. A concurrency test needs separate workers or processes, or a controlled order
  (an injected hook or second connection that acts between the read and the write).
- A time-dependent UI test changes the value while the component stays mounted (advance the
  injected clock, resolve a sync, fire focus). A different response at mount is not that test.
- A test that inspects source text (a regex over a file) is a guardrail, not behaviour evidence.

A probe is `detected` only if a specific test would fail. "Some test might fail" is `not_run`.

## Weakening signals

- removed or loosened assertions (`==` to `in`, exact to truthy)
- new `skip`, `xfail`, `only`, `pytest.mark.flaky`, raised timeouts
- deleted test files or cases; narrowed parametrization
- changed test config (`testpaths`, coverage `omit`, CI filters)
- fixture changes that make assertions trivially true

Weakening is acceptable only when tied to an authorized requirement change, and
never approved by the test's own author/builder.

## Fixtures

- isolation fixtures contain at least two principals
- pagination fixtures exceed page size
- only synthetic data; no secrets, no production endpoints
