# Builder handoff format and forbidden actions

Source: blueprint §7.7, §10.1 (Builder), §11.4, §12.4.

## Handoff (the `handoff` string)

Plain text, at most ~15 lines, these labels in this order:

```
Goal: <task acceptance in one line>
Base/Head: <base_sha> -> <worktree HEAD after change>
Done: <what changed, by requirement ID>
Findings addressed: F-3 <how>, F-5 <how>   (or "none")
Tests: <tests added/changed and the scenario IDs they implement>
Claimed checks: <local commands run and results; unverified>
Open: <known gaps, risks, anything out of file_scope that was needed>
Next step: <what the gate/reviewer should look at first>
```

No conversation history, no hidden reasoning, no self-assessment like "high quality".

## Claims are not evidence

`claimed_checks` and the handoff are inputs to reviewers. Only the trusted runner's
evidence for the reviewed `head_sha` counts. A builder writing "all tests pass"
changes nothing in the gate.

## Counters

| Counter | Limit | After exhaustion |
|---|---|---|
| attempts per leaf task (build + review fixes) | 3 | task FAILED; parent cannot be accepted |
| build attempts per feature | 30 | feature stopped |
| identical retry after same failure fingerprint twice | not allowed | `factory-root-cause` first |

## Forbidden

- merge, push to protected branches, force push, admin bypass
- approve, close or re-status findings; edit review exports
- write gate results, evidence, policy, checks manifest, budgets, limits, skills, prompts
- lower complexity/risk classification; reduce scope; build hypothetical features
- weaken/skip/delete tests; read or write holdout resources
- use credentials or network beyond the sandbox grant
