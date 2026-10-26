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
