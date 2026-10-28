# Acceptance report — Agent Factory, variant A

Date: 2026-10-27 · Blueprint: `AGENT_FACTORY_AUTONOMOUS.md` v3.1-A · Host: macOS (arm64), single host, `mode: local`.

Statuses: **PASS** — test run and passed; **NOT_RUN** — not run or not implemented (never counted as PASS).
Levels: unit / integration / fault / live / eval.

## 1. What was built and how to run it

| Component | Where |
|---|---|
| Controller (state machine, guards, orchestration) | `src/agent_factory/controller.py`, `factory.py` |
| State (SQLite WAL, migrations, CAS) | `src/agent_factory/state.py` |
| Input identity and invalidation matrix | `src/agent_factory/identities.py` |
| Signed evidence (HMAC, content-addressed artifacts) | `src/agent_factory/evidence.py` |
| Sandbox (`sandbox-exec`, fail closed) and trusted runner | `src/agent_factory/sandbox.py`, `runner.py` |
| Diff policy, risk routing, side-effect broker | `src/agent_factory/policy.py` |
| Shared 3-round counter, option scoring, DAG, leases | `analysis.py`, `decisions.py`, `scheduler.py` |
| Budget, outbox, recovery, worktree | `budgets.py`, `outbox.py`, `recovery.py`, `workspaces.py` |
| Claude adapter (real), GitHub (stub-tested), fake (tests only) | `src/agent_factory/adapters/` |
| `factory` CLI, read-only dashboard, eval | `cli.py`, `dashboard/`, `evals.py` |
| 22 skills, shared contract, role prompts | `factory/skills/`, `factory/prompts/` |
| OpenSpec schema `factory-autonomous` | `openspec/schemas/factory-autonomous/` |

```bash
uv sync
uv run pytest                       # offline tests
uv run factory doctor
uv run factory init --repo <path> --mode local
uv run factory submit --text "..."  # or --file feature.yaml
uv run factory run --job <job-id>
uv run factory --json status --job <job-id>
AGENT_FACTORY_LIVE=1 uv run pytest tests/live   # live smoke (uses the Claude account quota)
```

Operational details: `docs/factory/operations.md`.

## 2. Versions

| Component | Version / source |
|---|---|
| Python | 3.12.12 (uv), Pydantic 2.14.1, PyYAML 6.0.3, pytest 8.4.2, hypothesis 6 — pinned in `uv.lock` |
| Claude Code CLI | 2.1.74, mode `claude -p --output-format stream-json`; authentication: claude.ai login (no API key in the environment) |
| OpenSpec | `@fission-ai/openspec` 1.15.0, pinned in `tools/openspec` and `factory.lock.json`; Node v20.20.0 |
| Models | the default role configuration in `factory/config.yaml` (opus/sonnet/haiku 5.5) is an operator choice without a role benchmark; **live smoke and eval were run with `claude-haiku-5-5` for all roles** |
| Sandbox | `macos-sandbox-exec` (probe: write outside the path and secret read both denied) |
| Policy | `factory/config.yaml` + `workflow.yaml` + `selection.yaml` → `policy_digest`; check manifest and skills → `checks_digest` (computed live) |

## 3. Matrix AF-01–AF-60

Last full offline run: **163 passed, 2 skipped** (the two skipped are live tests run separately, see §4).

| ID | Scenario (short) | Test | Level | Result |
|---|---|---|---|---|
| AF-01 | install on a fresh repo | `test_install_manifest.py::test_af01_manifest_files_exist_and_parse`, `test_fresh_install_config_loads_and_doctor_runs` | integration | PASS |
| AF-02 | baseline does not pass | `test_pipeline.py::test_af02_failing_baseline_blocks_env` | integration | PASS |
| AF-03 | spec loses a requirement | `test_af03_spec_eval_finds_missing_requirement_before_build` | integration | PASS |
| AF-04 | no feasible option | `test_af04_no_feasible_option_spikes_once_then_fails`, `test_decisions.py::test_af04_no_feasible_option` | integration, unit | PASS |
| AF-05 | wrong choice / two choices | `test_af05_wrong_ai_choice_is_rejected_and_repaired_without_human`, `test_af05_lower_score_choice_rejected`, `test_af05_duplicate_option_ids_rejected` | integration, unit | PASS |
| AF-06 | cycle / missing dependency | `test_af06_af52_cycle_is_fixed_automatically_within_the_limit`, `test_dag.py::test_af06_cycle_and_missing_dependency` | integration, unit | PASS |
| AF-07 | empty/fake review file | `test_af07_empty_or_fake_review_file_gives_no_rights`, `test_af07_placeholder_identity_rejected` | unit | PASS |
| AF-08 | forged PASS | `test_af08_forged_pass_row_is_rejected`, `test_af08_untrusted_producer_cannot_record`, `test_payload_tampering_breaks_signature` | unit, integration | PASS |
| AF-09 | HEAD changes after review | `test_af09_head_change_after_review_invalidates_old_pass` | integration | PASS |
| AF-10 | spec changes after build | `test_af10_spec_change_after_build_requires_new_evaluation` | integration | PASS |
| AF-11 | "UI", but the diff adds an auth endpoint | `test_af11_ui_intake_but_auth_endpoint_raises_risk_and_security`, `test_policy.py::test_af11_*` | integration, unit | PASS |
| AF-12 | zero/skipped tests | `test_runner_sandbox.py::test_af12_zero_or_skipped_tests_never_pass` | integration | PASS |
| AF-13 | weakened tests/config | `test_af13_weakened_tests_and_config_fail_policy`, `test_policy.py::test_af13_*` (8 variants) | integration, unit | PASS |
| AF-14 | known defective code | `test_af14_known_defective_code_is_caught_and_reworked` | integration | PASS |
| AF-15 | UI "success", no change in data | `test_af15_af59_ui_success_without_db_effect_is_caught_by_feature_acceptance` | integration | PASS |
| AF-16 | other users' records | `test_af16_cross_user_leak_is_rejected` | integration | PASS |
| AF-17 | malicious issue/log | `test_af17_malicious_issue_text_grants_nothing` | integration | PASS |
| AF-18 | reading a secret/holdout/another job | `test_af18_builder_cannot_read_keys_holdout_or_other_job`, `test_af18_cannot_write_outside_worktree_or_into_controller`, `test_af18_no_outbound_network_and_no_secrets_in_env` — real attempts in `sandbox-exec` | integration | PASS |
| AF-19 | two jobs concurrently | `test_workspaces.py::test_af19_two_jobs_get_separate_worktrees_branches_and_ports` | integration | PASS |
| AF-20 | two controllers claim a task | `test_af20_only_one_lease_holder`, `test_live_pid_keeps_expired_lease` | unit | PASS |
| AF-21 | stale worker after takeover | `test_af21_stale_epoch_result_rejected` | unit | PASS |
| AF-22 | controller killed during build | `test_af22_controller_killed_mid_build_adopts_live_builder`, `test_af22_dead_attempt_marked_lost_and_budget_released` | fault | PASS |
| AF-23 | kill after PR creation | `test_af23_lost_response_after_pr_created_is_reconciled_not_duplicated` (GitHub stub) | fault | PASS |
| AF-24 | external operation timeout | same test: `unknown` state and lookup before retry | fault | PASS |
| AF-25 | hanging gate/dev server | `test_af25_hanging_process_group_is_killed_at_deadline`, `test_orphaned_children_die_when_check_exits` | integration | PASS |
| AF-26 | 429/5xx, missing price | `test_af26_transient_provider_failures_retry_without_corrupting_state`, `test_af26_persistent_provider_failure_blocks_env`, `test_af26_unknown_cost_charges_full_reservation` | fault, unit | PASS |
| AF-27 | three identical failures | `test_af27_repeated_identical_failure_triggers_root_cause_then_stops` | integration | PASS |
| AF-28 | budget runs out mid-run | `test_af28_reservations_stop_new_parallel_starts`, `test_af28_budget_exhaustion_keeps_checkpoint` | unit, fault | PASS |
| AF-29 | one reviewer times out | `test_af29_one_reviewer_unknown_blocks_acceptance` | integration | PASS |
| AF-30 | parallel semantic changes | `test_af30_base_moved_after_candidate_rebuilds_candidate` | integration | PASS |
| AF-31 | base moves during tests | `test_af31_base_moves_during_merge_is_rejected_atomically`, `test_workspaces.py::test_af31_push_rejected_when_base_moved` | integration | PASS |
| AF-32 | CI pending/failing; approval | `test_af32_pending_or_failing_ci_blocks_merge` (stub), `test_af45_*`, `test_af48_af42_*` | fault, integration | PASS |
| AF-33 | no sandbox | `test_af33_no_sandbox_fails_closed`, `test_af33_non_enforcing_sandbox_fails_probe` | integration | PASS |
| AF-34 | cleanup on uncommitted files | `test_af34_cleanup_preserves_uncommitted_work`, `test_cleanup_dry_run_and_never_force` | integration | PASS |
| AF-35 | merge OK, cleanup failed | `test_af35_merge_ok_cleanup_fails_stays_merged` | integration | PASS |
| AF-36 | spec archiving changes the tree | — the OpenSpec archiving step is not implemented | — | **NOT_RUN** |
| AF-37 | restart restores handoff | `test_af37_restart_preserves_identity_and_budget` | fault | PASS |
| AF-38 | malicious Markdown | `test_af38_markdown_and_html_are_not_executed`, `test_af38_dashboard_routes_are_whitelisted_and_read_only` | unit | PASS |
| AF-39 | real runtime, small task | `tests/live/test_live_claude.py` (§4) | live | PASS |
| AF-40 | comment/deploy in local mode | `test_af40_local_mode_broker_denies_comments_and_deploy`, `test_af40_local_mode_comment_denied_and_audited` | unit, fault | PASS |
| AF-41 | standard job without manual spec approval | `test_af41_standard_path_builds_after_ai_evaluation_without_human` | integration | PASS |
| AF-42 | merge enabled + `off` | `test_af48_af42_trivial_fast_path_merges_automatically` | integration | PASS |
| AF-43 | `before_merge` | `test_af43_before_merge_single_human_stage`, `test_af43_rework_and_reject` | integration | PASS |
| AF-44 | `on_risk` | `test_af44_on_risk_low_merges_high_waits`, `test_af44_on_risk_high_waits` | integration | PASS |
| AF-45 | restart while waiting | `test_af45_restart_while_waiting_never_implies_approval` | integration | PASS |
| AF-46 | tie / uncertain fact | `test_af46_tie_break_is_deterministic` | unit | PASS |
| AF-47 | feature from file/CLI/issue | `test_af47_feature_normalisation_keeps_original`, `test_cli.py::test_cli_flow`; issue: only `test_issue_intake_takes_only_data_fields` (stub) | unit, integration | PASS (file/CLI); issue live — NOT_RUN |
| AF-48 | very simple fix | `test_af48_af42_*`, `test_af48_label_fix_is_trivial` | integration, unit | PASS |
| AF-49 | technically simple auth change | `test_af49_one_line_auth_change_is_not_fast_path` | unit | PASS |
| AF-50 | complex feature | `test_af50_complex_feature_gets_contracts_and_reviewed_decomposition` | integration | PASS |
| AF-51 | plan skips / duplicates a requirement | `test_af51_plan_missing_requirement_is_caught`, `test_dag.py::test_af51_*` | integration, unit | PASS |
| AF-52 | leaf too large / cycle | `test_af06_af52_*`, `test_dag.py::test_af52_oversized_leaf_and_depth` | integration, unit | PASS |
| AF-53 | hypothetical future need | `test_af53_hypothetical_need_is_recorded_not_built` | integration | PASS |
| AF-54 | spec PASS in round 1 | `test_af41_*` (assertion: 1 round) | integration | PASS |
| AF-55 | third round still CHANGES | `test_af55_third_failed_round_stops_without_build_or_fourth_round` | integration | PASS |
| AF-56 | spec and decomposition alternate | `test_af56_spec_and_decomposition_share_one_counter` | integration | PASS |
| AF-57 | restart / model switch / subtask | `test_af57_restart_and_model_switch_do_not_reset_rounds`, `test_round_counter_is_shared_and_bounded` | integration, unit | PASS |
| AF-58 | hidden complexity in the diff | `test_af58_hidden_complexity_reclassifies_fast_path_keeping_counters` | integration | PASS |
| AF-59 | tasks green, feature does not work | `test_af15_af59_*` | integration | PASS |
| AF-60 | cap/budget exceeded | `test_af60_task_cap_blocks_without_scope_reduction`, `test_af28_budget_exhaustion_keeps_checkpoint` | integration, fault | PASS |

Additional §17 requirements: property-based test of the state machine (`test_random_walks_never_reach_merged_without_merging`, hypothesis), schema migration (`test_schema_migration_from_v1`), database backup/restore (`test_backup_and_restore_roundtrip`) — PASS. Fault injection at the boundaries of every outbox operation — partial: create_pr, comment, merge (stub), local.merge; no full matrix for every operation.

Offline tests use a scripted `FakeRuntime` for model responses. Real components: git, a local bare remote, the sandbox, the runner, SQLite and files. These tests prove controller behavior, not model quality.

## 4. Live smoke (gate B4, AF-39)

Run on the final code, 2026-10-27, all roles `claude-haiku-5-5`, fixture `tests/fixtures/repos/greeter`, local bare remote, `mode: local`, `merge.enabled: false`.

| Test | Result | Evidence |
|---|---|---|
| `test_live_adapter_structured_call_and_cancel` | PASS | structured output matches the contract; usage 12 865 in / 305 out; estimated cost $0.016; cancel stopped the process group (`process group terminated`, process no longer exists) |
| `test_live_af39_small_task_end_to_end` | PASS | `READY_TO_MERGE` in 288 s; 16 agent runs, each with `runtime_run_id` and `session_id`; gate from the trusted runner in the sandbox; estimated cost $3.33 |

State path: `INTAKE → COMPLEXITY_ASSESSMENT → MICRO_SPEC → AI_SPEC_EVALUATION → SINGLE_TASK_READY → BUILD_TASK → CHECKS → REVIEW → BUILD_TASK → CHECKS → REVIEW → TASK_ACCEPTED → FEATURE_ACCEPTANCE → INTEGRATION → READY_TO_MERGE`.

Costs are `total_cost_usd` as reported by the CLI (an estimate at API prices). With a claude.ai login, calls consume the plan quota.

Earlier live runs that did not pass uncovered real factory bugs. All were fixed, and each has a regression test:
1. Skill input contract: skills expected files, while the controller passes data in `<inputs>`. Added "How inputs/outputs are delivered" sections in `_shared/contract.md`.
2. The reviewer could write to the worktree when `data_dir` was under `/tmp`. Added `denied_write` for read-only roles and a clean-worktree requirement after review.
3. The controller committed `__pycache__/*.pyc` from builder runs. This caused false reclassification and "out of scope" rejections. Generated files are now excluded (`GENERATED_EXCLUDES`).
4. Findings upsert did not update `blocking` when a reviewer lowered a finding's severity. The job got stuck despite all PASS.

One live observation is substantive. In one run the AI spec evaluator found a counterexample: an implementation hard-coding `'Hello, Ada!'` passed the criteria. It forced a criterion with a second name before coding.

## 5. Eval and ablation (gate B8)

Suite: `tests/evals/dev.yaml` (20 tasks, 8 categories). Golden tests are hidden from the builder: the controller adds them to a copy of the final tree, and the trusted runner runs them in the sandbox. Pilot: tasks E05-notes-delete (crud) and E11-api-isolation (auth), all roles `claude-haiku-5-5`, 1 attempt per task and profile, 2026-10-27. The pilot was repeated 5 times; between runs the factory was fixed based on traces from the previous run.

| Run | single-agent (golden) | full: golden | full: accepted by the factory | full: false acceptances | full: cost (est.) | full: time / task |
|---|---|---|---|---|---|---|
| 1 | 2/2, $0.52 | 0/2 | 0/2 | 0 | $9.00 | 413–495 s |
| 2 | 2/2, $0.63 | 1/2 | 0/2 | 0 | $12.23 (limit $8/job) | 40–53 min* |
| 3 | 2/2, $0.61 | 0/2 | 0/2 | 0 | $6.82 (limit $8/job) | 3–21 min* |
| 4 | 2/2, $0.51 | **2/2** | 0/2 | 0 | $28.65 | 44–173 min* |
| 5 | 2/2, $0.50 | **2/2** | 0/2 | 0 | $13.45 | **8–11 min** |

\* Times marked with an asterisk are inflated by the Mac sleeping overnight (`pmset` log): the frozen CLI process lost its connection and only ended after the timeout. Run 5 was started under `caffeinate -s` and had no hangs.

What was fixed between runs (each fix has a regression test in `tests/integration/test_regressions.py` or `tests/unit/test_claude_adapter_stall.py`):

| After run | Symptom in the trace | Fix |
|---|---|---|
| 1 | spec-author BLOCKED ("I can't run the validator"); planner received two different `selected_option_id` | spec-author verdict rules; one consistent decision passed downstream |
| 1 | ~1/3 of analysis calls repeated because `payload` had extra/mistyped fields | the CLI gets the exact `payload` schema of the given skill (`envelope_json_schema(kind)`); zero format repairs in runs 2–5 |
| 1 | every spec CHANGES round re-drafted and re-selected options from scratch; the evaluator kept adding new edge cases, 3 rounds without a build | CHANGES with a feasible approach fixes only the spec (`AI_SPEC_EVALUATION → OPTION_SELECTED`); only gaps against the explicit request or risk-required controls block, the rest goes to the builder as `spec_advisories`; from run 2 the spec passes in round 1 |
| 2 | CHANGES from decomposition review went back to options and the spec; calls hung until the 900 s limit | new plan without touching the spec (`AI_DECOMPOSITION_REVIEW → DECOMPOSE`); stream idle watchdog (`agent_stall_seconds`) and recovery of a result already committed by the CLI |
| 3 | planner NEEDS_INFO because it did not receive the spec evaluation result | `spec_evaluation` in the planner inputs |
| 4 | E11 split into 7 tasks (test-only tasks, an empty E2E task); the builder skipped tests because `file_scope` did not include `tests/` | fewest tasks within the time limit; each task has its own tests; an empty `file_scope` is a plan problem; E2E is done by the FEATURE_ACCEPTANCE stage |
| 5 | integration and acceptance returned BLOCKED "missing data" (acceptance map, `candidate_tree_sha`, check list, "environment"), and the controller turned this into rebuilds until attempts ran out; security-review blocked on a trust model given by the request itself | shared `_verification_context` (criteria, required checks, tree, environment `local-checkout`); evaluator BLOCKED/UNKNOWN: one retry, then NEEDS_HUMAN, no rebuild; security-review routes a trust-model dispute to analysis (`error_class: spec`) |

**Fixes after run 5 were not verified live.** Run 6 was not executed.

Conclusions:
- In runs 4 and 5 the code produced by the factory passes the hidden golden tests in both tasks (2/2), same as the single agent. The factory still does not accept it on its own: 2 false rejections, 0 false acceptances across all runs.
- The factory is 25–30× more expensive and ~25× slower than a single agent on these tasks (run 5: $6–7.5 and 8–11 min per task vs $0.25 and 25 s).
- The remaining rejections in run 5 stemmed from gaps in the evaluators' input contract, not from code quality. They were fixed but not measured.
- n=2, one attempt each. This is a pilot, not statistics. These two tasks are small and sequential, and in such tasks multi-role coordination need not pay off (S9). A fair comparison requires tasks where decomposition and independent evaluation matter, and several attempts per task.

Single-stage ablation was not performed. Status: **NOT_RUN**. Concurrency: the measured profile is 1 job. 2 and 3 jobs were not measured (**NOT_RUN**).

## 6a. First real project: LearnEnglish (Node/Expo), F1

Repo `LearnEnglish` (Expo + Fastify, TypeScript), manifest `node`, default models (Opus/Sonnet).
F1 "domain core": complex path, 3/3 analysis rounds (spec 2× CHANGES — justified: the limit was not
enforced by the queue), plan of 7 tasks, 5 accepted (TASK-05 after 2 justified fixes),
cost $20.13, time ~1 h 25 min (including ~30 min lost to the old CLI).
Job `FAILED` on TASK-06 (scheduler): the reviewer rightly pointed out that the card type from TASK-03 had no
relearning state after a mistake — a contract gap, not a builder one. The operator merged the accepted
tasks 01–05 and submitted the rest as F1b with a clarified requirement.

Factory bugs found and fixed (with regression tests):
- per-repo check manifest + `node_modules` linking (previously pytest only);
- the policy did not recognize `*.test.tsx` as tests (scope violations, no protection against weakening);
- a rejected API request (CLI 2.1.74 vs Opus 5.5) was retried after 10 min — now fails fast;
- a budget refusal before starting the builder consumed a task attempt — now refunded.

### The whole LearnEnglish app (F1–F3)

| Feature | Tasks | Result | Cost | Time |
|---|---|---|---|---|
| F1 domain core | 7 (5 accepted) | FAILED on TASK-06 (contract gap), operator merged 01–05 | $20.13 | ~85 min (~30 min old CLI) |
| F1b scheduler + queue | 3 | READY_TO_MERGE | $7.41 | 21 min |
| F2 app (12 tasks, lanes) | 12 (11 accepted) | FAILED on TASK-12 (policy forbade deleting a dead test), operator merged 01–11 | $50.80 | ~80 min |
| F2b navigation | 4 | READY_TO_MERGE | $11.33 | 27 min |
| F3 accounts, sync, permissions (high) | 6 | READY_TO_MERGE | $25.78 | 63 min |
| **Total** | 32 | | **$115.45** | **~4 h 40 min** |

Before each merge the operator ran: `npm run check` (631 tests at the end), for F2b a smoke test in headless
Chrome (onboarding → home → check → learn), for F3 manual API scenarios (user isolation,
forged premium, tokens, argon2id). Cost is the CLI estimate from the claude.ai plan quota.

Additional factory bugs found on this project and fixed: the UI reviewer without a browser
returned UNKNOWN (now static evaluation, render-only = NOT_RUN); the policy forbade deleting a test
together with the code being removed; artifacts with identical content raced on a `.tmp` file across lanes.
Fix rounds cost the most (builder + 5 reviewers on Opus) — the main cost lever.

## 6. Permissions, limits and merge

- Mode `policy.mode: local`. The broker denies comments, PRs, GitHub merge and deploy (`authorize_side_effect`), and the denial is audited (`broker.denied`).
- `merge.enabled: false` (default): the job's result is a locally accepted candidate (`READY_TO_MERGE`). With merge enabled in local mode, the controller pushes the tested candidate to the base branch via `git push --force-with-lease=refs/heads/<base>:<tested_base_sha>`. The server rejects the update if the base has moved, and then a new candidate is created. We do not use a server-side merge queue.
- `merge.human_interaction: "off"`; `before_merge` and `on_risk` are supported, with the decision pinned to the candidate's `criteria_digest`.
- Limits: 3 analysis rounds per feature, 3 attempts per task, 30 build attempts per feature, 2 integration rebuilds, $25 per job, $1.50 reservation per agent call, 25% of the budget reserved for verification.

## 7. Open limitations and what is needed to remove them

| Limitation | What needs to be done |
|---|---|
| AF-36: no OpenSpec archiving step; the controller does not call the OpenSpec CLI (validate/archive) | add a stage after product acceptance: `openspec validate --strict`, `openspec archive`, a new integration candidate and re-run checks |
| `pr`/`automerge` modes not wired: no `github.create_pr` step, `submit --issue` does not work; the GitHub adapter is tested only against a stub | add a PR step to orchestration and a handler in the CLI; live smoke on a chosen test repo with operator authorization |
| The SBPL profile starts from `(allow default)` and cuts off specific paths; Mach services and Unix sockets are not blocked; `agent-logs`/`runs` readable across jobs | deny-by-default profile with a list of required services, or a container/VM; add these directories to the denied reads |
| The agent process (Claude CLI) needs network access to the API, so its Bash tool also has network access | network broker / proxy with an allowlist limited to the provider API |
| `environment_digest` frozen at INTAKE; `prompt_digest`, `model_id` and skill digests outside `InputIdentity` | add these fields to the identity and the invalidation matrix |
| Unused config keys (`max_active_jobs`, `job_deadline_minutes`, `command_timeout_seconds`, `approval_timeout_action` and others), `max_turns` is not passed to the CLI, `run --queue` does not schedule, no PAUSED command, `allocate_port` not called | wire them into the scheduler and adapter, or remove them from the schema |
| Skills not called by the controller: repo-map, test-design, evidence-audit, recover, retrospective, release-verify; `factory/prompts/` not loaded | wire them into the appropriate states (test-design before build — test-adequacy kept asking for `test-plan.md`) |
| Tasks run sequentially; no parallel worktrees per task; concurrency > 1 job not measured | parallel branches per task according to the DAG; measure 1/2/3 jobs |
| Release/deploy (§14.1) does not exist | a separate release entity with policy and rollback |
| Test-weakening detection is heuristic (skip/xfail, `assert True`, deleted tests/assertions) | mutation-based test adequacy checking on the candidate |
| Role models are not chosen by benchmark | role eval on the dev suite, then record the choice in `docs/factory/model-routing.md` |

Without authorization, the following were not run live: GitHub (PR/merge/CI), issue intake and release. Their implementation and stub tests exist. I do not claim they work against real GitHub.
