# Model routing

**The model assignments in `factory/models.yaml` are an operator choice, not a benchmark result.** The blueprint
(§3 item 6, §13.2) requires a small role benchmark. `factory eval` (`evals.py`) compares the whole factory with
a single agent, but does not compare models per role; the role benchmark has not been run. The eval pilot
results are in `acceptance-report.md`. Treat the values below as a starting point.

## The `factory/models.yaml` file

The only place where models are chosen; `config.yaml` does not contain them (a model entry in `config.yaml` →
`ConfigError`). The controller inserts it into the same configuration fields as before, so behavior does not
change. Sections:

| Section | What it sets | Configuration field |
|---|---|---|
| `runtimes` | how the agent is launched: `binary`, `min_cli_version`, `api_key_env` (variable name, not the key), `base_url` | `runtime.runtimes`, `runtime.claude_binary` |
| `roles` | runtime, model, `effort`, tools of each role | `runtime.roles` |
| `routing` | builder model by task difficulty, escalation, trivial path model | `routing` |
| `review_models` | cheaper task reviewers (`for_risk`, `models`); baseline security review model (`security_baseline`) | `review.cheaper_models*`, `review.security.baseline_model` |
| `decision_model` | optional Jev | `decision_model` |

**Other providers (adapters `adapters/codex.py`, `adapters/opencode.py`).** A role's `runtime`:
`claude | codex | opencode`. Every call goes through `RuntimeRouter` to the adapter from the role
binding; the runtime name is in the run handle, so recovery after a restart reaches the right one.

| Runtime | Models | Result | Cost | Notes |
|---|---|---|---|---|
| `claude` | Claude (CLI) | `--json-schema`, `structured_output` | from the CLI, cap `--max-budget-usd` | default |
| `codex` | OpenAI (`codex exec --json`, ChatGPT login or `OPENAI_API_KEY`) | envelope from text (last ```json block) | tokens; cost from `prices`, no price = full reservation | Codex's own sandbox does not start inside the factory sandbox, so it runs with `--dangerously-bypass-approvals-and-sandbox` (a flag for environments with an external sandbox); the boundary is the factory sandbox |
| `opencode` | `provider/model` (Gemini, OpenAI, Anthropic API) or a model from `endpoints` | envelope from text | from opencode (`step_finish.cost`; 0 locally) | per-call configuration (`OPENCODE_CONFIG`: skill instructions, `edit`/`bash` permissions, endpoint provider), isolated `XDG_*`: the operator's private configuration and logins are not read |

Codex and opencode have no CLI-side cost limit; the timeout, stall detection and the job budget
apply. Tools are not restricted by a list as in Claude: a read-only reviewer is
enforced by the sandbox (worktree without write access).

- **`pass_env`** (`runtimes.<name>`): `{variable in the agent: variable on the operator side}`. Only these variables
  reach the agent; the agent can read them. For shared keys a proxy is better.
- **`endpoints`**: OpenAI API-compatible servers (Ollama, LM Studio, llama.cpp, vLLM), local or on the
  network (`base_url`, `models`, `max_concurrent`, optionally `key_env`). A role with
  `endpoints: [a, b]` (only `runtime: opencode`, `model` = a name from `models`) gets a randomly chosen
  server that is reachable (`GET /models`, result cached for 30 s) and free (`slot:endpoint:<name>`).
- **`fallback`** (`roles.<role>.fallback: {runtime, model}`): used when the runtime is not installed or
  no endpoint is free and reachable (event `runtime.fallback`). Without `fallback` →
  `BLOCKED_ENV` with a reason.
- **`prices`**: `{model: {input_per_mtok, output_per_mtok}}` for runtimes without a cost report (codex).
- `routing` and `review_models` specify Claude models and apply only to roles on `claude`; a role on
  another runtime always uses its own `model`.
- `factory doctor`: `runtimes` (installed, variables from `pass_env`, roles) and `endpoints`
  (reachability, missing models, a warning for `http://` to another machine).

## Role → model (`roles`)

A skill's role comes from `factory.py::SKILL_ROLE`; no role entry → the `default` binding.

| Role | Skills | Model | `effort` | Tools |
|---|---|---|---|---|
| `default` | (fallback) | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `intake` | factory-intake, (factory-repo-map) | `claude-haiku-5-5` | — | Read, Glob, Grep |
| `classifier` | factory-classify-feature | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `planner` | factory-options-review, factory-plan | `claude-opus-5-5` | high | Read, Glob, Grep |
| `decision` | factory-select-option | `claude-opus-5-5` | high | Read, Glob, Grep |
| `spec_author` | factory-spec-author | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `spec_evaluator` | factory-spec-evaluate | `claude-opus-5-5` | high | Read, Glob, Grep |
| `decomposition_reviewer` | factory-decomposition-review | `claude-opus-5-5` | high | Read, Glob, Grep |
| `builder` | factory-implement, factory-root-cause | `claude-sonnet-5-5` | high | Read, Edit, Write, Glob, Grep, Bash |
| `code_reviewer` | factory-code-review | `claude-opus-5-5` | medium | Read, Glob, Grep |
| `security_reviewer` | factory-security-review | `claude-opus-5-5` | high | Read, Glob, Grep |
| `ui_reviewer` | factory-ui-review | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `product_evaluator` | factory-product-verify (task review and feature acceptance) | `claude-sonnet-5-5` | medium | Read, Glob, Grep, Bash |
| `test_reviewer` | factory-test-adequacy | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `integration_evaluator` | factory-integration-review | `claude-opus-5-5` | medium | Read, Glob, Grep |

Details of the CLI mapping (`adapters/claude.py::_argv`): `--model <model>`, `--effort <effort>`
(if set), `--tools` and `--allowedTools` from the list. For calls without write access
(`writable=False`, i.e. everything except `factory-implement`) the list is additionally filtered to
`Read, Glob, Grep, Bash` (`factory.py::READ_ONLY_TOOLS + ["Bash"]`), and `factory-root-cause` is
called as read-only despite the `builder` role. The `max_turns` field is accepted in the configuration and
passed in `AgentRequest`, but the adapter does **not** pass it to the CLI. The timeout of every call
(including the builder) is `execution.reviewer_timeout_seconds` (900 s).
Independently of that, `execution.agent_stall_seconds` (300 s) aborts a call whose stream has not
sent a new event; if the CLI managed to commit the result with the StructuredOutput tool, the result is
recovered (cost unknown, counted as a full reservation), otherwise the controller retries the call.
Every call gets an envelope schema with the exact `payload` schema of its skill.

The model actually used is read from the stream's `system/init` event and stored in
`attempts.model_id`, `usage.model_id` and the result's `producer.model_id`. The live smoke
(`tests/live/test_live_claude.py`) uses `claude-haiku-5-5` by default (`AGENT_FACTORY_LIVE_MODEL`).

## Prices and budget

The factory keeps no price list. Cost comes from the provider:

- Before every call (including every retry and JSON repair) `budgets.reserve` reserves
  `budget.max_cost_per_agent_call` (USD 1.50). The same value goes to the CLI as `--max-budget-usd`
  (CLI-side cap).
- After the call `budgets.settle` settles `total_cost_usd` from the `result` event. Input tokens =
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
- **Unknown cost** (no `total_cost_usd`) is counted as a full reservation (`usage.cost_known=0`).
- A cost higher than the reservation is booked in full and produces the event `budget.overrun`.
- Verification reserve: the `analysis` and `build` purposes may take at most `(1 − 0.25) × limit`;
  `review`, `acceptance`, `integration` may reach the full limit.
- `budget.warning` at ≥ 70% of the limit; exceeding it → `BUDGET_EXHAUSTED`.
- `budget.unknown_price_policy` is validated but unused in the code.

## How to change a model

1. Change the role's `model` (and possibly `effort`) in `factory/models.yaml` as a separate factory change.
2. `uv run factory doctor` (validation), `uv run pytest`, and for a new model the live smoke:
   `AGENT_FACTORY_LIVE=1 AGENT_FACTORY_LIVE_MODEL=<model> uv run pytest tests/live`.
3. Record in the commit why you are changing it (comparison result, cost).

Effects on running jobs:

- A model change **does not reset counters**: analysis rounds (`analysis_rounds`), task and feature attempts
  and the spent budget are stored in SQLite per job (`workflow.yaml: reset_on_model_switch: false`; a configuration
  with the value `true` is rejected).
- Because `policy_digest` covers the whole contents of `config.yaml` and `models.yaml`, a model change invalidates all
  results that depend on `policy_digest` in running jobs — the assessments have to be produced again (see the known
  issue in `state-machine.md`). `model_id` alone is not a result identity field.

## Rejected API requests (fail fast)

When the CLI gets a permanent error from the API (`API Error: 400/401/403/404`, e.g. "Claude Code 2.1.74 does not
support this model; version 2.1.280 or newer is required"), it reports it as a synthetic assistant
message and can spin in the StructuredOutput hook loop up to its own limit (live: 1756 turns,
10 min, ending with a misleading "Prompt is too long"). `ClaudeRuntime.poll` detects such an error in the stream,
kills the process and returns `failed` with `retryable=False`; the controller does not retry the call and immediately
moves to `BLOCKED_ENV` with the error text. 429/5xx remain transient errors. The 5.5 models
require Claude CLI ≥ 2.1.280 (`claude --version` in `factory doctor`).

## Task difficulty and builder model (`routing`, `decision_model`)

Every plan task gets a `difficulty` (`easy | medium | hard`). The final rating is the maximum of:

1. the planner's label (`difficulty`, missing = `medium`) with a justification in `difficulty_reasons`;
2. the controller's lower bound from the task record (`difficulty.structural_floor`): folders/globs in
   `file_scope` or > 3 production files → medium, > 6 → hard; risk > low, > 3 requirements,
   > 2 output contracts → medium; tests do not count;
3. optionally the Jev decision model (TypeSafe AI), when `decision_model.provider: jev` and
   `TYPESAFE_API_KEY` is set. A `choice` question with a rubric from `difficulty.CRITERIA`; the answer counts
   only with `confidence ≥ min_confidence` (0.6) and can only raise the rating. An error, timeout
   or missing key → step skipped, with no effect on the job. Only the controller reads the key (agents do not
   get it). The cost (~$0.00004 per task) is in the `plan.difficulty` event (`decider_usd`).

The rating is computed once per plan revision (`ctx.task_difficulty`), passed to the decomposition
review (`controller_difficulty`), and a `hard` task without `difficulty_reasons: ["indivisible: …"]` is
a controller problem: the planner must split it. The atomicity criteria are in
`factory-plan/references/task-schema.md`.

Builder model: `routing.builder_models[level]` (easy and medium: Sonnet, hard: Opus). Haiku only
for the first feature attempt on the trivial path (`routing.trivial_path_model`: atomic change,
low risk, known pattern); the next attempt already follows the map. Principle: better to pay for
Sonnet up front than for fixes; with `escalate_on_retry` each subsequent attempt of a
task goes one level higher (easy → medium → hard). The `task.routed` event records the level and
model of each attempt. An empty `builder_models` map keeps the `builder` role binding.
`factory doctor` shows `routing` and whether the decision model is active; `factory status` shows
the difficulty of each task.
