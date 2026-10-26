# Incident layers and typical fixes

| Layer | Signal in traces | Typical fix (separate PR) | Regression test |
|---|---|---|---|
| `product` | correct evaluation flagged it, builder failed 3 attempts | better task split or spec clarity | fixture repo reproducing the task |
| `evaluation` | a reviewer/evaluator returned PASS on wrong behavior (false PASS) | tighten skill rubric, add counterexample/probe, add holdout category | eval case that must yield CHANGES |
| `harness` | runner reported success with 0 tests, wrong cwd, missing suite, flaky infra | runner validation, test count assertion, environment pinning | runner unit test with the broken input |
| `policy` | policy allowed a path it should not (risk not raised, wrong gate set) | policy rule or risk routing change | policy test for the triggering diff |
| `model` | same prompt/skill passes on other models, fails on this one consistently | routing change based on benchmark | benchmark suite entry |
| `environment` | credentials, ports, network, disk; not product-related | doctor check, resource allocation | `factory doctor` test |

## Order of suspicion

Check `harness` and `evaluation` before `model`: a model failure is only
established when the same inputs succeed elsewhere under the same harness.

## Proposal rules

- One change per proposal, smallest change at the failing layer.
- Each proposal has a regression test that fails before and passes after.
- Skill/prompt change = new version file + eval; never activated inside a running job.
- Never "fix" by relaxing criteria, removing a check or raising limits.

## Metrics worth tracking

- false-PASS rate per evaluator skill
- escaped defects per 10 merged features
- analysis rounds used per feature (distribution, not mean only)
- build attempts per task, cost and minutes per feature
- share of ANALYSIS_LIMIT_REACHED / BUDGET_EXHAUSTED outcomes
