# Evidence contract

Sources: `src/agent_factory/schemas/__init__.py`, `identities.py`, `evidence.py`, `runner.py`.

## 1. Agent envelope (`AgentEnvelope`)

Every skill invocation must return a single JSON envelope. The Claude adapter passes
`AgentEnvelope.model_json_schema()` as `--json-schema`; when the CLI returns `structured_output`, it is
used, otherwise `extract_json_object` takes the last ```` ```json ```` block (or the whole text,
if it is a bare object). All models have `extra="forbid"`.

| Field | Type / constraint |
|---|---|
| `schema_version` | `"1"` |
| `kind` | ≤ 60 characters; must equal the skill name without the `factory-` prefix (otherwise `ContractError`) |
| `verdict` | `PASS` \| `CHANGES` \| `BLOCKED` \| `UNKNOWN` |
| `summary` | ≤ 8000 characters |
| `findings` | list of `Finding` |
| `checked_criteria`, `limitations` | lists of strings |
| `payload` | object; validated by the model from `PAYLOADS` only for `PASS`/`CHANGES` |

Rule: `PASS` with an open blocking finding violates the contract (model validator).
Contract error → one repair attempt with the validator message, then `NEEDS_HUMAN`.

`Finding`: `id` (`^[A-Za-z0-9_.-]{1,40}$`), `severity` (`low|medium|high|critical`), `blocking`,
`requirement_ids`, `location {path (relative, no ..), line ≥ 1}`, `problem` (1–4000), `evidence_ids`,
`expected_fix`, `status` (`open|fixed|disputed|accepted-risk`, default `open`).

### Payloads (`PAYLOADS`)

| `kind` | Model | Key fields |
|---|---|---|
| `intake` | `IntakePayload` | `goal`, `acceptance_criteria`, `constraints`, `open_questions`, `future_directions` |
| `classify-feature` | `ClassificationPayload` | `dimensions{name: {score 0–2 \| null, evidence}}`, `risk`, `single_atomic_task`, `known_pattern`, `new_trust_boundary`, `forced_complex_reasons` |
| `options-review`, `select-option` | `OptionsPayload` | `options[{option_id, feasible, infeasible_reasons, ratings{goal_fit, correctness, maintainability, delivery_time, cost: 0–5}, estimated_cost_usd, estimated_minutes}]`, `selected_option_id`, `spike_requested` |
| `spec-author` | `SpecPayload` | `requirements[{id REQ-*, text, source}]`, `intent_map[{intent, requirement_ids}]`, `acceptance_criteria[{id, requirement_ids, observable}]`, `micro_spec`, `files{rel_path: content}` |
| `spec-evaluate` | `SpecEvaluationPayload` | `dimensions{name: PASS\|CHANGES}`, `counterexample`, `uncovered_requirements` |
| `plan` | `PlanPayload` | `tasks[TaskSpec]`, `future_needs[{description, status: required_now\|committed_next\|hypothetical}]` |
| `decomposition-review` | `DecompositionPayload` | `requirements_map{REQ: [TASK]}`, `per_task`, `dag_ok` |
| `implement` | `ImplementPayload` | `changed_files`, `tests_added`, `handoff`, `claimed_checks` (claims, not evidence) |

`TaskSpec`: `task_id` (`TASK-*`), `requirement_ids`, `depends_on`, `inputs`, `outputs`, `file_scope`,
`risk`, `estimated_minutes`, `acceptance`, `verification`, `parallel_safe_with`,
`future_constraints`, `parent`.

Review skills (`code-review`, `security-review`, `ui-review`, `product-verify`, `test-adequacy`,
`integration-review`, `root-cause`) have no payload model; the controller reads from `payload` only
`error_class` (`spec`/`decomposition` → back to analysis) and `owner_task_id` (feature acceptance).

The controller adds deterministic checks that can change `PASS` to `CHANGES`:
`spec_structure_problems` (tracing REQ ↔ intent ↔ criteria), required spec dimensions
(`workflow.spec_evaluation.mandatory_dimensions`), a non-empty `counterexample`, `validate_dag` and the
decomposition reviewer's requirements map.

## 2. Input identity (`InputIdentity`)

Frozen model, `extra="forbid"`; non-applicable fields are explicit `None`.

| Field | How `Controller.identity` fills it |
|---|---|
| `repo_id`, `job_id`, `task_id` | from the job / argument |
| `attempt_id` | not set |
| `base_sha` | `jobs.base_sha`; for an integration candidate (`candidate.sha == HEAD`) — `candidate.base_sha` |
| `head_sha` | `git rev-parse HEAD` of the worktree |
| `candidate_tree_sha` | `HEAD^{tree}` only when HEAD is an integration candidate |
| `feature_revision` | `jobs.scope_revision` |
| `complexity_digest`, `scope_digest`, `spec_digest`, `decision_digest`, `plan_digest` | from `context_json` (content digest) |
| `decomposition_digest`, `analysis_round_id` | not set (the round goes to `results.analysis_round`) |
| `policy_digest` | `LoadedConfig.policy_digest` (digest of `config.yaml` + `workflow.yaml` + `selection.yaml`) |
| `checks_digest` | live: `Controller.checks_digest()` = digest of the manifests of the `factory/checks/` and `factory/skills/` directories (`directory_manifest`) |
| `environment_digest` | from the context, recorded at `INTAKE` (Python version + sandbox name) |
| `acceptance_suite_digest` | digest of `[acceptance_criteria spec, ChecksManifest.digest()]` recorded in the context at spec authoring |
| `runtime_version`, `model_id`, `prompt_digest`, `skill_digests`, `grader_version` | not set |

`digest()` = SHA-256 of canonical JSON (`sort_keys`, no spaces). `tasks.md` has a plan digest with
checkboxes zeroed (`tasks_plan_digest`), so ticking off progress does not invalidate the evaluation.
`validate_real()` rejects values that are not a 40-character Git SHA / 64-character SHA-256.

## 3. Invalidation matrix (`RESULT_DEPENDENCIES`)

A result is current when every field in its row equals the current identity (`stale_fields`).
Abbreviations: ANALYSIS = `feature_revision, scope_digest, policy_digest`;
CODE = `head_sha, spec_digest, plan_digest, policy_digest, checks_digest`.

| `ResultKind` | Depends on |
|---|---|
| `classification` | ANALYSIS |
| `option-selection` | ANALYSIS + `complexity_digest` |
| `spec-evaluation` | ANALYSIS + `spec_digest`, `decision_digest` |
| `decomposition-review` | ANALYSIS + `spec_digest`, `decision_digest`, `plan_digest` |
| `gate` | CODE + `environment_digest`, `base_sha` |
| `code-review` | CODE |
| `security-review` | CODE |
| `ui-review` | CODE |
| `product-verify` | CODE + `acceptance_suite_digest` |
| `test-adequacy` | CODE |
| `feature-acceptance` | CODE + `scope_digest`, `acceptance_suite_digest` |
| `integration` | CODE + `base_sha`, `candidate_tree_sha`, `acceptance_suite_digest` |
| `human-approval` | `head_sha, base_sha, spec_digest, policy_digest` |

`invalidated_kinds(ChangeKind)` gives the "what a change invalidates" view: `scope`, `plan`, `code`, `base`,
`policy`, `formatting-only` (by default invalidates like a spec change; there is no equivalence event).
`human-approval` is not recorded as a result — human approval lives in the `approvals` table and
is compared via `criteria_digest` (`{head, base, spec, policy}`).

## 4. Signing and verification (`EvidenceStore`)

Write (`record_result`):

1. The producer must be in `TRUSTED_PRODUCERS` = `trusted-runner`, `agent-adapter`, `controller`, `operator`.
2. `identity.validate_real()` must be empty.
3. Payload → artifact `result:<kind>` (SHA-256 of the content, path `artifacts/aa/bb/<sha>`).
4. Body `{result_id, job_id, task_id, kind, verdict, identity, producer, payload_artifact_id,
   payload_sha256}` (`payload_sha256` = the payload's `artifacts.sha256`) → HMAC-SHA256 with the key `data_dir/keys/evidence.key` (32 bytes from `os.urandom`, file `0600`).
5. In the same transaction: the `results` row, findings update, the `result.recorded` event.

Read (`_row_to_result`): HMAC recomputed (with `payload_sha256` read from `artifacts`) and
`hmac.compare_digest`; producer trusted; payload read through `EvidenceStore.read`, which compares the
file's SHA-256 with `artifacts.sha256`. Replacing the payload file or the `artifacts` row therefore invalidates
the result.
`current_pass` takes the **newest** row of a given `kind` (and `task_id`) — an older PASS does not rescue
a newer FAIL. `factory verify <id>` and `recover` verify all results of the job.

Producers in practice: `trusted-runner/gate` (checks), `agent-adapter/<role>` with `runtime_run_id` and
`model_id` (agent evaluations), `controller/controller` (e.g. `FAIL_POLICY` from the policy gate, classification
PASS, decomposition "not applicable" on the trivial path).

## 5. Runner evidence (`CheckEvidence`)

One record per command from the manifest (`factory/checks/default.yaml`, `ChecksManifest`):

| Field | Meaning |
|---|---|
| `name`, `argv` | check name and argv after substituting `{python}`, `{report}`, `{worktree}` |
| `cwd`, `env_keys`, `sandbox` | worktree, environment variable names (no values), sandbox name |
| `started_at`, `ended_at` | epoch times |
| `exit_code`, `signal`, `timed_out` | process result; a timeout kills the process group (SIGTERM, SIGKILL after 3 s) |
| `stdout_artifact`, `stderr_artifact`, `stdout_sha256`, `stderr_sha256` | full logs (up to 2 MB) as artifacts and their digest |
| `excerpt` | tail of stdout+stderr (~4 KB) for agent context |
| `tests` | `{collected, failed, errors, skipped}` from junit (for `report: junit`) |
| `verdict`, `reason` | `GateVerdict` and reason |
| `head_sha`, `tree_sha` | snapshot it ran on |

Classification (`_classify`): timeout → `TIMEOUT`; signal → `CANCELLED` (TERM/INT) or `UNKNOWN`; exit 127
/ "command not found" / "No module named" → `ERROR_ENV`; junit: no report → `ERROR_ENV`, 0 or fewer
than `min_tests` executed → `FAIL_POLICY`, failures/errors/exit ≠ 0 → `FAIL_CODE`; without a report exit ≠ 0
→ `FAIL_CODE`. Tool missing from `PATH` before start → `ERROR_ENV`.

Gate (`run_gate`): missing required checks → `FAIL_POLICY`; otherwise the first verdict present
in the order `ERROR_ENV > TIMEOUT > FAIL_POLICY > FAIL_CODE > CANCELLED > UNKNOWN`, otherwise
`PASS`. Stages: `task` (`task` checks), `acceptance` (`task`+`acceptance`), `integration` (all).
The current manifest has one check, `unit` (pytest, junit, `min_tests: 1`, network `localhost`, 600 s).

## 6. Findings lifecycle

- Key `finding_key = <job_id>:<task_id or '-'>:<kind>:<finding.id>`; written in the same transaction
  as the result. The same identifiers in different tasks do not collide.
- A new result of a given `kind` for a given `task_id` marks as `fixed` all open findings of that
  `kind`/`task_id` that the evaluator did not report again (when the new result has no findings —
  all of them). Closure is therefore confirmed by a new evaluator run, never by the builder.
- Findings reported again are upserted (status, `result_id`, `updated_at`).
- The `TASK_ACCEPTED` guard requires no open blocking findings for the task; open findings of the task
  (and those without a task) go into the builder's input.
- The `disputed`/`accepted-risk` statuses can be set only by the evaluator in the envelope; there is no operator command
  and no dispute assessor (the prompt `factory/prompts/decision-assessor.v1.md` is not used by the code).
