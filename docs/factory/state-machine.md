# State machine

Source: `src/agent_factory/controller.py` (states, transitions, guards) and `src/agent_factory/factory.py`
(who triggers a transition and when). Every transition goes through `Controller.transition`: it checks
`legal(src, dst)`, runs the guard of the target state (unless the target is a side state), does a CAS on the
job version and records a `state.transition` event with `from_state/to_state/from_version/to_version`.
A refusal is a `TransitionRefused` exception (CLI: exit 3).

## States (`class S`)

| Group | States |
|---|---|
| Main path | `FEATURE_RECEIVED`, `INTAKE`, `COMPLEXITY_ASSESSMENT`, `MICRO_SPEC`, `ANALYSIS_ROUND`, `OPTIONS_DRAFT`, `AI_OPTION_REVIEW`, `OPTION_SELECTED`, `AI_SPEC_EVALUATION`, `SINGLE_TASK_READY`, `DECOMPOSE`, `AI_DECOMPOSITION_REVIEW`, `TASK_GRAPH_READY`, `BUILD_TASK`, `CHECKS`, `REVIEW`, `TASK_ACCEPTED`, `FEATURE_ACCEPTANCE`, `INTEGRATION`, `READY_TO_MERGE`, `AWAITING_HUMAN_MERGE`, `MERGING`, `MERGED` |
| Side (`SIDE`) | `NEEDS_INFO`, `BLOCKED_ENV`, `NEEDS_HUMAN`, `BUDGET_EXHAUSTED`, `CANCELLING`, `FAILED`, `ANALYSIS_LIMIT_REACHED`, `PAUSED`, `FAILED_NO_FEASIBLE_OPTION` |
| Terminal (`TERMINAL`) | `MERGED`, `CANCELLED`, `REJECTED`, `FAILED`, `FAILED_NO_FEASIBLE_OPTION` |
| Halted (`HALTED`) — waiting for the operator | `ANALYSIS_LIMIT_REACHED`, `BUDGET_EXHAUSTED`, `NEEDS_INFO`, `BLOCKED_ENV`, `NEEDS_HUMAN`, `PAUSED`, `AWAITING_HUMAN_MERGE` |

`ANALYSIS_STATES` (`MICRO_SPEC` … `AI_DECOMPOSITION_REVIEW`) is defined but unused in the logic.
`PAUSED` has no CLI command that would set it.

## Main transitions (`_MAIN`)

| From | To |
|---|---|
| `FEATURE_RECEIVED` | `INTAKE` |
| `INTAKE` | `COMPLEXITY_ASSESSMENT` |
| `COMPLEXITY_ASSESSMENT` | `MICRO_SPEC`, `ANALYSIS_ROUND` |
| `MICRO_SPEC` | `AI_SPEC_EVALUATION` |
| `ANALYSIS_ROUND` | `OPTIONS_DRAFT` |
| `OPTIONS_DRAFT` | `AI_OPTION_REVIEW` |
| `AI_OPTION_REVIEW` | `OPTIONS_DRAFT` (spike), `OPTION_SELECTED`, `ANALYSIS_ROUND` |
| `OPTION_SELECTED` | `AI_SPEC_EVALUATION` |
| `AI_SPEC_EVALUATION` | `SINGLE_TASK_READY`, `MICRO_SPEC`, `DECOMPOSE`, `ANALYSIS_ROUND` |
| `DECOMPOSE` | `AI_DECOMPOSITION_REVIEW` |
| `AI_DECOMPOSITION_REVIEW` | `TASK_GRAPH_READY`, `ANALYSIS_ROUND` |
| `SINGLE_TASK_READY`, `TASK_GRAPH_READY` | `BUILD_TASK` |
| `BUILD_TASK` | `CHECKS`, `FEATURE_ACCEPTANCE` (a lane wave accepted the last tasks) |
| `CHECKS` | `BUILD_TASK`, `REVIEW`, `ANALYSIS_ROUND` (reclassification) |
| `REVIEW` | `BUILD_TASK`, `TASK_ACCEPTED`, `ANALYSIS_ROUND`, `OPTIONS_DRAFT`, `MICRO_SPEC` (trivial), `CHECKS` (stale gate) |
| `TASK_ACCEPTED` | `BUILD_TASK`, `FEATURE_ACCEPTANCE` |
| `FEATURE_ACCEPTANCE` | `INTEGRATION`, `BUILD_TASK`, `ANALYSIS_ROUND`, `OPTIONS_DRAFT`, `MICRO_SPEC` (trivial) |
| `INTEGRATION` | `READY_TO_MERGE`, `BUILD_TASK` |
| `READY_TO_MERGE` | `MERGING`, `AWAITING_HUMAN_MERGE`, `INTEGRATION` |
| `AWAITING_HUMAN_MERGE` | `MERGING`, `BUILD_TASK`, `REJECTED`, `INTEGRATION` |
| `MERGING` | `MERGED`, `INTEGRATION` |
| `CANCELLING` | `CANCELLED` |
| `ANALYSIS_LIMIT_REACHED` | `ANALYSIS_ROUND`, `MICRO_SPEC` |
| `BLOCKED_ENV` | `BUILD_TASK` |
| `NEEDS_INFO` | `INTAKE` |

The edges `REVIEW → OPTIONS_DRAFT` and `FEATURE_ACCEPTANCE → OPTIONS_DRAFT` are legal, but
`factory.py` does not use them (a requirements error goes back to `ANALYSIS_ROUND`, not to `OPTIONS_DRAFT` as in §4.3).

## Side state rules (`legal()`)

Order of checks:

1. Pair in `_MAIN` → legal.
2. Terminal source or `CANCELLING` → illegal (except `CANCELLING → CANCELLED` from item 1).
3. Target in `SIDE` → legal from any non-terminal state (hence every state can move to
   `NEEDS_HUMAN`, `BLOCKED_ENV`, `FAILED`, `CANCELLING` etc.).
4. Source `BUDGET_EXHAUSTED`, `PAUSED`, `NEEDS_HUMAN` → any non-terminal target is legal
   ("resume to the saved state").
5. Otherwise illegal. E.g. `REJECTED` is reachable only from `AWAITING_HUMAN_MERGE`,
   and `CANCELLED` only via `CANCELLING`.

Guards are not run for targets in `SIDE`.

## Guards and required evidence

`current_pass(kind)` = the latest result of the given kind (for a task: with that `task_id`) has a valid HMAC
signature, a trusted producer, verdict `PASS`, and no field from `RESULT_DEPENDENCIES[kind]` differs
from the current identity (`Controller.identity`). See `evidence-contract.md`.

| Target state | Guard | Required evidence |
|---|---|---|
| `COMPLEXITY_ASSESSMENT` | `_g_decisions_answered` | no unanswered product decisions (`guided` mode) |
| `SINGLE_TASK_READY`, `DECOMPOSE` | `_g_spec_pass` | current `spec-evaluation` PASS |
| `TASK_GRAPH_READY` | `_g_decomposition_pass` | current `decomposition-review` PASS + spec PASS |
| `BUILD_TASK` | `_g_build_allowed` | spec PASS; for a path other than `trivial` also decomposition PASS; at least one row in `tasks` |
| `REVIEW` | `_g_gate_pass` | current `gate` PASS for `current_task` (also depends on `base_sha` and `environment_digest`) |
| `TASK_ACCEPTED` | `_g_reviews_pass` | non-empty list `required_reviews[current_task]`; current PASS of `gate` and of every required review; no open blocking findings for the task |
| `FEATURE_ACCEPTANCE` | `_g_all_tasks_accepted` | all tasks in state `ACCEPTED` (and at least one) |
| `INTEGRATION` | `_g_acceptance_pass` | current `feature-acceptance` PASS |
| `READY_TO_MERGE` | `_g_integration_pass` | current `integration` PASS (depends on `candidate_tree_sha`) |
| `MERGING` | `_g_merge_allowed` | `merge.enabled: true`; integration PASS; when `human_required` — a valid approval with `criteria_digest` equal to the current one |
| `MERGED` | `_g_merge_confirmed` | `context.merge_result.merged == true` (from the outbox) |

`human_required`: `before_merge` → always; `on_risk` → when `jobs.risk` (missing = `high`) is in
`human_review_risk_levels`; `off` → never. `criteria_digest` = digest of `{head, base, spec, policy}`.

Required reviews (`Factory._route_reviews`): always `code-review`, `security-review`,
`product-verify`, `test-adequacy`; `ui-review` when the diff touches the `ui` surface. Security depth:
`full` at `high` risk or with the `security` surface, otherwise `baseline` (passed to the agent as data).

## Error routing (as implemented in `factory.py`)

Exceptions in `Factory.run`: `StageBlocked(state)` → `_halt` to the indicated side state;
`budgets.BudgetExhausted` → `BUDGET_EXHAUSTED`; `analysis.AnalysisLimitReached` →
`ANALYSIS_LIMIT_REACHED`. `_halt` records `resume_state`, `halted_reason`, `blocked_reason` and a checkpoint.
`TransitionRefused` (the guard refused the transition, e.g. evidence became stale after a policy
or check change) → `NEEDS_HUMAN` with `blocked_reason = "guard refused: …"`; the controller never forces
a transition past a guard.

| Situation | Where | Goes to |
|---|---|---|
| Intake `BLOCKED` | `_step_intake` | `NEEDS_INFO` |
| `guided` mode: unanswered product decisions | `_step_intake` | `NEEDS_INFO` (`factory decide` records the answers and resumes to `INTAKE` without re-running intake) |
| Missing classification / options / spec / plan payload | `_step_*` | `NEEDS_INFO` |
| Missing option choice payload | `_step_ai_option_review` | `NEEDS_HUMAN` |
| AI decision contradicts the deterministic choice | `_step_ai_option_review` | one repair in the same round; then the round ends with `CHANGES` → `ANALYSIS_ROUND` (or the limit) |
| No feasible option / near-tie with a spike request | `_step_ai_option_review` | spike → `OPTIONS_DRAFT` (limit `max_feasibility_spikes_per_feature`); without a spike → `FAILED_NO_FEASIBLE_OPTION` |
| Spec/decomposition `CHANGES` (AI findings or controller problems) | `_close_round_and_continue` | `MICRO_SPEC` (trivial) / `ANALYSIS_ROUND`; when the limit is exhausted or at `max_consecutive_rounds_without_progress` → `ANALYSIS_LIMIT_REACHED` |
| Spec/decomposition `BLOCKED`/`UNKNOWN` without concrete problems | same as above | `NEEDS_HUMAN` |
| Baseline does not pass on the untouched base | `_prepare_build` | `BLOCKED_ENV` |
| Task attempt limit | `_step_build_task` / `_parallel_wave` | task `FAILED`, its attempts reverted from the job branch (patch as an artifact); independent tasks keep building; when nothing is left to build → `NEEDS_HUMAN` with a list of blocked ones; operator: `factory task retry <job> <task> --attempts N --reason …` + `factory resume` |
| Feature attempt limit (`total_feature_build_attempts`) | `_step_build_task` | `FAILED` |
| No ready task (dependency deadlock) | `_step_build_task` | `NEEDS_HUMAN` |
| Builder rewrote history / policy violation | `_step_checks` | result `gate=FAIL_POLICY`, patch rejected as an artifact, `git reset --hard` to `build_start_sha` → `BUILD_TASK` |
| The whole feature diff against the base is empty (outside `openspec/`) | `_step_checks` | result `gate=FAIL_CODE` → `BUILD_TASK`. An attempt without changes while the feature diff is non-empty is not a failure (earlier work may already satisfy the spec) — it goes to the runner |
| Tests FAIL, `TIMEOUT` | `_step_checks` | `BUILD_TASK` (error fingerprint; two identical in a row → `factory-root-cause` before the next build) |
| Gate `ERROR_ENV` / `CANCELLED` / `UNKNOWN` | `_step_checks` | `BLOCKED_ENV` |
| Hidden complexity in the trivial path diff (risk ≠ low or > 5 files) | `_needs_reclassification` | path `standard`, `ANALYSIS_ROUND` (or the limit) |
| Task `gate` result stale on entering `REVIEW` (e.g. change of checks/skills/policy) | `_step_review` | `CHECKS` — re-run checks instead of a rebuild |
| Review `CHANGES` | `_step_review` | `BUILD_TASK` |
| Review `UNKNOWN`/`BLOCKED` without `CHANGES` | `_step_review` | stays in `REVIEW` (retries only stale reviews); after the 3rd attempt `NEEDS_HUMAN` |
| `payload.error_class` ∈ {`spec`, `decomposition`} in review or acceptance | `_back_to_analysis` | `ANALYSIS_ROUND` (trivial: `MICRO_SPEC`); no rounds left → `ANALYSIS_LIMIT_REACHED` |
| HEAD changed or the worktree is not clean (`git status --porcelain`) after a reviewer call | `_step_review` | `NEEDS_HUMAN` |
| Feature acceptance FAIL | `_step_feature_acceptance` | task `owner_task_id` (or the last one) → `REWORK`, `BUILD_TASK` |
| Integration conflict or FAIL | `_integration_rebuild` | last task → `REWORK`, `BUILD_TASK`; after `integration_rebuilds` → `NEEDS_HUMAN` |
| Base moved before/during merge | `_step_merging` | `INTEGRATION` |
| Merge rejected by an external policy | `_step_merging` | `NEEDS_HUMAN` (`EXTERNAL_POLICY_REQUIRES_REVIEW`) |
| Merge result `unknown` | `_step_merging` | `NEEDS_HUMAN` ("run `factory recover`") |
| Agent: JSON does not match the contract | `call_agent` | one repair (`structured_output_repair`), then `NEEDS_HUMAN` |
| Agent: run `failed`/`unknown` | `call_agent` | 2 retries with jitter, then `BLOCKED_ENV` |
| Lease lost / lease taken | `_run_agent_once` | `NEEDS_HUMAN` |
| Guard refused the transition (`TransitionRefused`) | `Factory.run` | `NEEDS_HUMAN` (`guard refused: …`) |
| Missing skill / role binding | `_skill_text`, `_binding` | `BLOCKED_ENV` |

## Resume (`Factory.resume`)

Works only for `HALTED` other than `AWAITING_HUMAN_MERGE` (for other states it does nothing). Target:
`resume_state` recorded by `_halt` (default `INTAKE`); for `ANALYSIS_LIMIT_REACHED` —
`MICRO_SPEC`/`ANALYSIS_ROUND`. Before writing, `resume` checks `legal(state, target)` and, if the transition is not
legal, raises `TransitionRefused` (CLI: exit 3). The state is written directly via
`cas_update_job` (event `state.resumed`), **without the target state's guard**. Evidence is checked
only at the next transition: the target state's step is executed again, and the guard of the
next state works normally.

Consequence of the `legal()` rules: from `BUDGET_EXHAUSTED`, `NEEDS_HUMAN`, `PAUSED` you can resume to
any non-terminal state. From `BLOCKED_ENV` only a return to `BUILD_TASK` is legal, and from
`NEEDS_INFO` only to `INTAKE` (see known problems).

## Known problems (from reading the code)

- **Resume from `BLOCKED_ENV` and `NEEDS_INFO` is refused for most states.** `BLOCKED_ENV`
  arises, among others, in `SINGLE_TASK_READY`/`TASK_GRAPH_READY` (baseline FAIL), `CHECKS` (gate `ERROR_ENV`),
  `FEATURE_ACCEPTANCE`, `INTEGRATION`, `MERGING` (no handler) and in any state with an agent (run
  `failed`, missing skill). `resume_state` is the state in which the halt occurred, and `_MAIN` contains only
  `(BLOCKED_ENV, BUILD_TASK)`, so `factory resume` ends with `cannot resume BLOCKED_ENV -> <state>`.
  Likewise `NEEDS_INFO` from `COMPLEXITY_ASSESSMENT`, `OPTIONS_DRAFT`, `MICRO_SPEC`, `OPTION_SELECTED`,
  `DECOMPOSE`. The `BLOCKED_ENV` branch in `resume` (assignment `target = resume_state`) changes nothing.
- **Stale analysis results after a policy change.** The job now visibly halts in
  `NEEDS_HUMAN` (`guard refused: …`), but there is no path to re-evaluate the spec/decomposition:
  `resume` returns to the state in which the guard refused, so the refusal repeats. Workaround: `cancel` and
  a new `submit`.
- Resuming `NEEDS_INFO` does not create a new revision; a new revision (`submit`) creates a new job, and the old one
  stays active in `NEEDS_INFO`.
