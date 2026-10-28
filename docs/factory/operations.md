# Operations

Run all commands from the root of the factory repo (`factory doctor` looks for
`factory.lock.json` and `tools/openspec/...` relative to the current directory). Pass global flags
**before** the subcommand: `uv run factory --json --data-dir <dir> --factory-dir <dir> <cmd>`.
`--factory-dir` defaults to `$AGENT_FACTORY_DIR` or `factory`.

Exit codes (`cli.py`): `0` ok, `2` usage/configuration error, `3` refusal (`TransitionRefused`, wrong
`--revision`, `--mode` not matching the configuration), `4` job halted (`HALTED`) or no sandbox.

## Installation

```sh
uv sync                        # Python 3.12 (.python-version), dependencies from uv.lock
uv run pytest                  # offline; live tests are skipped
(cd tools/openspec && npm ci)  # only if node_modules is missing; version in factory.lock.json
```

The Claude CLI must be installed (`runtime.claude_binary`, default `claude` in `PATH`) and logged in
locally. The factory does not pass an API key through the environment.

## `factory doctor`

Reports: configuration validation and `policy_digest`, `data_dir`, database schema version, sandbox
probe result, path and version of `claude`, `factory.lock.json`, presence of the OpenSpec binary, the
skill list, registered repos, credential information. Exit 4 when the sandbox does not work or `claude`
is missing. Doctor makes no paid model call — working auth is confirmed only by the live smoke
(`AGENT_FACTORY_LIVE=1 uv run pytest tests/live`).

## Registering a repo

```sh
uv run factory init --repo /path/to/repo --mode local [--repo-id app] [--base-branch main] [--checks node]
```

The repo must have `.git` and an `origin` remote (the worktree is created from `origin/<base>` after
`git fetch`). `--mode` must equal `policy.mode` in `factory/config.yaml` — changing the mode is a
configuration change made by the operator. Without `--base-branch` the base branch is determined from
`origin/HEAD` or `ls-remote`. Registry: `data_dir/repos.json`.

`--checks <name>` selects the trusted manifest `factory/checks/<name>.yaml` for the repo (default
`default.yaml`, pytest). The name is stored in the job context at `submit`, so a job does not change
its manifest midway; the digest of the `checks/` directory still invalidates results after an edit.
`node.yaml` (vitest + `tsc` + `eslint --max-warnings=0` + `expo export --platform web`) has
`link_from_checkout: ["node_modules"]`: the operator runs `npm ci` in the repo checkout, and each
worktree gets a symlink to those dependencies (checks have no network). The link must be ignored by git
(`node_modules` without a trailing `/`), otherwise the job ends in `BLOCKED_ENV`; a missing directory in
the checkout also gives `BLOCKED_ENV`. Tools cannot write to the linked directory (sandbox), so keep
caches in the repo (e.g. vitest `cacheDir`, `--configLoader runner`). New dependency = the operator
updates the checkout. Node must be in the factory process `PATH` (e.g. `nvm use 24`).

## Submitting and running

```sh
uv run factory submit --file feature.yaml [--repo-id app] [--mode guided|auto]   # or --text "description"
uv run factory run --job job-0123456789ab
uv run factory run [--max-jobs N]                            # active jobs one by one; --queue changes nothing
```

YAML file (`features.FeatureFile`, unknown fields rejected): only `original_request` is required;
optional are `feature_id`, `repo_id` (must match the selected repo), `goal`, `constraints`,
`acceptance_criteria`, `future_directions`. No `feature_id` → `FEAT-<8 hex of the content
hash>`. (`workflow.yaml: feature_input.required_fields` is validated, but the code does not enforce
it.) Resubmitting identical content returns the existing active job; changed content for the same
`feature_id` creates a new revision and a new job. `--issue` is not supported.

### Spec mode: `guided` or `auto` (`spec`)

At `submit` you choose who settles the product decisions the request leaves open (limits, default
behavior, who can do what, what happens on error, visual direction). Without `--mode` the CLI asks in
the terminal; without a terminal it uses `spec.default_mode` (default `guided`).

- `guided`: intake returns up to `spec.max_decisions` (7) decisions, each with options and a proposed
  answer. The job stops in `NEEDS_INFO` before classification and analysis (cost: intake only).
- `auto`: proposed answers are accepted as assumptions (`source: assumed`) and the job does not stop.

```sh
uv run factory status --job <id>                    # decisions: id, question, proposal, answer
uv run factory decide <id>                          # in the terminal: question by question (Enter = proposal, number = option)
uv run factory decide <id> --answer Q-1="text" --answer Q-3="..." [--accept-proposed]
```

After the last answer `decide` resumes the job (`factory run --job <id>`). The answers go into
`feature.decisions` of all subsequent stages: a human answer (`human`, `accepted`) is binding like the
request text — the spec maps it to a requirement (`source: decision:Q-n`), and the spec evaluator
blocks when it is missing or contradicted. An assumption from `auto` mode is only a default.
The `COMPLEXITY_ASSESSMENT` guard refuses the transition while any decision is awaiting an answer.

`factory run` ends at a terminal state, a halted state, or at `READY_TO_MERGE` when
`merge.enabled: false` (result accepted locally on the `factory/<job-id>` branch, without merge).

## Status and diagnosis

```sh
uv run factory --json status [--job <id>]
uv run factory inspect <id>                       # + last 50 events, findings, results
uv run factory feature classify <feature-id>
uv run factory feature inspect-analysis <feature-id>
uv run factory verify <id>                        # re-verify result signatures and hashes
```

`status` fields: `state`, `path`, `risk`, `blocked_reason`, `head_sha`, `base_sha`, `analysis_rounds`
(`used/limit`), `build_attempts`, `budget {limit, committed, spent}`, `tasks`, `last_checkpoint`
(handoff artifact id), `next_step`, `criteria_digest` (only in `AWAITING_HUMAN_MERGE`, otherwise `null`).

| `state` | `next_step` (from `cli._next_step`) | What to do |
|---|---|---|
| `ANALYSIS_LIMIT_REACHED` | inspect-analysis, raise-limit with a reason, or reject | `feature inspect-analysis`, then `feature raise-limit` and `resume`, or `cancel` |
| `BUDGET_EXHAUSTED` | raise the budget with a reason, `resume` | see "Budget" |
| `BLOCKED_ENV` | fix the environment per `blocked_reason`, `resume` | e.g. missing tool, baseline FAIL, missing skill, agent run `failed`; `resume` works only when `resume_state` = `BUILD_TASK` (see "Resuming") |
| `NEEDS_INFO` | new feature revision | fix the file and `submit` again; the old job stays in `NEEDS_INFO` (cancel it) |
| `NEEDS_HUMAN` | read `blocked_reason`; `resume` or `cancel` | `guard refused: …` = stale evidence (see "Changing and reverting a factory change") |
| `AWAITING_HUMAN_MERGE` | `approve|reject|rework --revision <digest>` | |
| `READY_TO_MERGE` | merge disabled | check the branch; enable `merge.enabled` in the configuration if you want a merge |

`blocked_reason` is the exact reason from `StageBlocked`/the exception (e.g. `guard for REVIEW refused: gate result
is stale for head_sha`, `reserving $1.50 for build would exceed ...`).

## Resuming

```sh
uv run factory resume <id>
```

Restores the `resume_state` saved at the halt and immediately calls `run`. Round, attempt and budget
counters are not reset. The transition must be legal (`legal()`), otherwise exit 3; the target state's
guard is not checked on resume, only on the next transition. For a state that is not halted the
command changes nothing and only starts `run`.

Known limitation: from `BLOCKED_ENV` the only legal return is to `BUILD_TASK`, and from `NEEDS_INFO`
only to `INTAKE`. A job halted in `BLOCKED_ENV` from another state (e.g. baseline in `SINGLE_TASK_READY`,
gate `ERROR_ENV` in `CHECKS`, missing handler in `MERGING`) cannot be resumed with `resume`
(`cannot resume BLOCKED_ENV -> …`) — what remains is `cancel` and a fresh `submit`. Details in
`state-machine.md`.

## Analysis round limit

```sh
uv run factory feature raise-limit <feature-id> --analysis-rounds 5 --reason "..."
uv run factory resume <job-id>
```

The new limit must be higher than the current one; a reason is required; event `analysis.limit_raised`.

## Budget

```sh
uv run factory budget raise <job-id> --to 40 --reason "reason for the raise"
uv run factory resume <job-id>
```

`budgets.raise_budget`: the new limit must be higher than the current one, a reason is required, actor = `$USER`,
event `budget.raised`. An agent cannot raise the limit.

## Cancelling

```sh
uv run factory cancel <id>
```

`CANCELLING` → stop live agent processes (SIGTERM, then SIGKILL of the group) → `CANCELLED`,
`active=0`. The worktree stays (remove it with `cleanup`).

## Human decision at merge

Active only with `merge.enabled: true` and `human_interaction` = `before_merge` or `on_risk`.
`--revision` is the `criteria_digest` = digest of `{head, base, spec_digest, policy_digest}` of the current
candidate. Get it from `uv run factory --json status --job <id>` (field `criteria_digest`).

```sh
uv run factory approve <id> --revision <digest>
uv run factory reject  <id> --revision <digest> --reason "..."
uv run factory rework  <id> --file feedback.md --revision <digest>
```

Stale `--revision` → exit 3. An approval whose digest no longer matches (new commit, new base,
spec or policy change) is invalidated (`approval.invalidated`). Rework passes the file contents to the
builder's `human_feedback` and returns to `BUILD_TASK` of the last task. All three commands require
`--revision`.

## Recovery after a crash

```sh
uv run factory recover --all
```

For each active job (`recovery.recover_job`): verify result signatures → check that the worktree
exists and is on `factory/<job-id>` → live agent processes are kept for adoption, dead ones are marked
`lost` → release expired leases of dead owners → reconcile the outbox (`lookup` before
retrying; a confirmed merge in `MERGING` → `MERGED`, `active=0` and `_post_merge_cleanup`) →
release orphaned budget reservations. `actions`/`problems` report in JSON. Then
`factory run --job <id>`.

## Cleanup

```sh
uv run factory cleanup <id> --dry-run
uv run factory cleanup <id>
```

`git worktree remove` without `--force`; a worktree with uncommitted changes is kept (error).
The `factory/<job-id>` branch is deleted only with `git branch -d` and only when the job is `MERGED`. After
a successful merge cleanup runs automatically (`_post_merge_cleanup`); a cleanup error produces the event
`cleanup.warning`, and the state stays `MERGED`.

## Factory eval

```sh
uv run factory eval --suite tests/evals/dev.yaml --profile full|single-agent
```

`evals.run_suite`: each task from the suite file (`tests/evals/dev.yaml`: 20 tasks in the categories
simple-fix, regression, crud, api, ui, auth, migration, incomplete) gets a fresh copy of the fixture
(`tests/fixtures/repos/greeter`) with a local bare remote, its own `state.db` and `data_dir` in
`data_dir/evals/<suite>-<profile>-<id>/`. `full` runs the whole factory, `single-agent` a single
`factory-implement` call without analysis or review. Success is decided by the golden tests from the suite file,
added to a scratch copy of the final tree (without `.git`) and run by `TrustedRunner` in the
sandbox. Report (`report.json`): `success`, `false_accepts`, `false_rejects`, cost, median time,
and per task: final state, golden result, cost, attempts, rounds. With n < 30 the report marks the result as
a pilot. The command makes real, paid Claude calls. Results: `acceptance-report.md`.

## Feature walkthrough (UX at acceptance)

Live finding (tic-tac-toe): the task UI reviews judged single screens and passed a game whose
settings sat below the fold on a phone, whose next round took two clicks, whose board jumped by
about 68px at start, and whose result said "X" instead of "You". A checked option under the pointer
was also hard to read. So when the job has a `ui` surface and a served build, feature acceptance
first runs one UI review in `mode: feature` (`_feature_ui_review`). Besides the screen checks, the
reviewer must deliver:

- `flows` (at least two): the first run on a phone and the action the user repeats most, with
  every step, the number of actions and screenshots;
- `first_screen`: at 360x640 before scrolling, the primary action **and** the choices it needs;
- `layout_shifts`: the main element measured with `getBoundingClientRect()` before and after each
  state change;
- `interaction_states`: hover and focus (and checked, checked+hover, checked+focus for selectable
  controls), with contrast measured from computed colors.

A walkthrough that leaves a part out is inconclusive and re-runs with a note saying what is
missing. Measurements over the limits in `config.yaml → design` (`max_layout_shift_px: 8`,
`min_text_contrast: 4.5`, `min_indicator_contrast: 3.0`) and a first screen without the needed
choices become blocking `CTRL-*` findings. The walkthrough then returns `CHANGES` and the owner
task is rebuilt. The guard into INTEGRATION requires a current PASS of the walkthrough
(`feature_ui_required`). Heuristic H2 now asks for people's names ("You", "Computer") rather than
tokens ("X", "O"). `DESIGN.md` gains a "First screen and stability" section, and the focus ring
must reach 3:1 on the checked background too.

## Failure modes, probes and the second look at findings

Live finding (tic-tac-toe): an external review found four defects the factory reviewers passed.
A retry duplicated a saved result, a hung request blocked saving, focus on a checked option was
invisible, and the typed nickname was lost after visiting the leaderboard. The code reviewer had
reported the last one, but rated it `low`. Three controls address this class:

- **Failure-mode table (code review).** The controller finds the diff's effect sites itself
  (`policy.effect_sites`: network calls, request handlers, persistent writes, browser storage,
  timers). For every such file the code review must fill `failure_modes`: `timeout`,
  `cancellation`, `retry_idempotent`, `lost_response`, `double_submit`, `unmount_or_navigation`
  and `partial_failure`, each `PASS | FAIL | NOT_APPLICABLE` with evidence
  (`code-review/references/failure-modes.md`). A missing file makes the review inconclusive
  (`code_review.inconclusive`), and it re-runs with a controller note.
- **Probes.** For those reviews the reviewer gets a writable, disposable copy of the candidate
  (`data_dir/probes/…`, Bash, and the repo's `test_command`) and may prove a `FAIL` with a probe
  test. The controller re-runs every named probe in the trusted runner. A probe that fails there
  makes its finding blocking (at least `medium`) and the review `CHANGES`. A probe that passes
  changes nothing. Probes are stored as `probe` artifacts (event `review.probes`), and the copy
  is removed afterwards.
- **Severity rubric** (`_shared/contract.md`): lost, duplicated or wrong user data and a stuck
  user are blocking, whatever the size of the fix.
- **Second look at acceptance.** Feature acceptance (product-verify) gets every open non-blocking
  finding (`open_findings`) and may list confirmed user-facing defects in `payload.escalate`.
  The controller accepts only findings that are on record, makes them blocking, sets acceptance
  to `CHANGES` and reopens the finding's task (event `findings.escalated`). An escalation is
  sticky: a later review may close the finding but cannot rate it non-blocking again.

## Dashboard

```sh
nvm use 24                                   # actions run the factory with this process's PATH
uv run factory dashboard [--port 8765] [--read-only]
# open http://127.0.0.1:8765
```

Live view (refreshes every 3–5 s, without reloading the page):

- **job list** in groups: "Needs you" (halted states), "Running", "Ready to merge", "Finished";
  each with task progress and budget;
- **job**: state, path, risk, goal; KPIs (spent / budget with the estimate marker, tasks, attempts,
  time); **task graph** (columns by dependency, state color, attempts, cost; selecting a task
  shows the verdicts of all reviews, criteria, files and open findings); cost per role;
  **screenshots** from browser reviews; findings (open / blocking / all); event timeline;
  background run log.

Actions (without `--read-only`), each is the same CLI command as in the terminal (`dashboard/actions.py`,
fixed argv list, no shell), so the controller's guards work unchanged:

| Button | Command | Mode |
|---|---|---|
| Save answers / Accept all proposals | `factory decide <id> --answer Q-n=… / --accept-proposed` | immediate |
| Approve plan and build | `factory plan approve <id> --budget X`, then `resume` | immediate + background |
| Raise budget and resume | `factory budget raise <id> --to X --reason …`, then `resume` | immediate + background |
| Run / Resume | `factory run --job <id>` / `factory resume <id>` | background (`caffeinate -i` on macOS) |
| Approve merge / Reject | `factory approve|reject <id> --revision <digest>` | background |
| Cancel job | `factory cancel <id>` | immediate |

Background runs: one per job at a time; log in `data_dir/dashboard/runs/`. A controller refusal
(exit 3) comes back as a message, not as success.

Security: `127.0.0.1` only; the `Host` header must point to localhost (DNS rebinding);
POST requires a token generated at server start (in the page) and this server's `Origin` (CSRF);
CSP `script-src 'self'`, no external scripts; data is inserted only as text. Of the artifacts, only
screenshots are served. `/job/<id>` and `/plain` are pages without JavaScript.

## Backup and restore

Stop `factory run` before the backup or use the SQLite backup API (safe with WAL):

```sh
D=~/.local/share/agent-factory
sqlite3 "$D/state.db" ".backup '$BACKUP/state.db'"
# or through the Python API (same as Store.conn.backup):
# uv run python -c "import sqlite3; sqlite3.connect('$D/state.db').backup(sqlite3.connect('$BACKUP/state.db'))"
rsync -a "$D/artifacts/" "$BACKUP/artifacts/"
cp -p "$D/keys/evidence.key" "$BACKUP/keys/"   # without the key no result will pass verification
cp -p "$D/repos.json" "$BACKUP/"
```

The `evidence.key` key is a secret — store the backup with `0600` permissions. Restore: stop the
controller, restore `state.db` (remove old `-wal`/`-shm`), `artifacts/`, `keys/`, `repos.json`, then
run `factory recover --all` and `factory verify <id>` for active jobs. Worktrees (`data_dir/worktrees`)
are Git data — restore them from the `factory/<job-id>` branches in the target repo or from a copy of the directory; recover
reports a missing worktree as a problem.

## Retention

There is no automatic retention. Artifacts, agent logs (`agent-logs/`), runner scratch (`runs/`) and
events grow without limit. Logs may contain data from the repo — clean them up manually after jobs finish
(delete artifacts referenced by `results` only together with their rows, otherwise `verify` reports an error).

## Changing and reverting a factory change

A change to the controller, policy, checks or skills is a separate change in the factory repo (tests + review), not
part of an application job. Revert: `git revert <commit>` in the factory repo and `uv run pytest`.

Effects on running jobs:

- `policy_digest` = digest of the contents of `config.yaml` + `models.yaml` + `workflow.yaml` + `selection.yaml`. Any change
  (including a role's model) makes stale all results that depend on `policy_digest` (that is,
  practically all of them). A revert restores exactly the previous digest, so results from before the change
  are current again, and results produced under the faulty configuration become stale.
- A change to `factory/checks/` or `factory/skills/` changes `checks_digest` (computed live by
  `Controller.checks_digest()`), so it invalidates `gate`, review, acceptance and integration results. A job in
  `REVIEW` with a stale gate returns to `CHECKS` and reruns the checks without a rebuild. Analysis results
  (spec, decomposition) do not depend on `checks_digest`.
- `factory/prompts/` and `environment_digest` (frozen at `INTAKE`) invalidate nothing.
- When a guard rejects a transition because of stale evidence, the job moves to `NEEDS_HUMAN` with
  `blocked_reason = "guard refused: …"`. For analysis results after a policy change there is no path
  for re-evaluation (`resume` repeats the refusal) — workaround: `cancel` and a fresh `submit`.

## Plan cost estimate and approval (`budget.plan_approval`)

After the plan (or micro-spec) is accepted, the controller computes an estimate (`estimate.py`): per task
`rounds × (builder + reviewers [+ ui-review for UI files])` plus feature verification, from factory
history (average call cost per role, average number of attempts per accepted task; ≥ 5 samples),
otherwise from default values. Backtest on LearnEnglish: −20%…+25% relative to the actual cost.
The job stops in `NEEDS_HUMAN` with the amount, the basis and a suggested limit (25% margin + reserve for
verification), unless the estimate is ≤ `auto_approve_below_usd`. The `BUILD_TASK` guard requires an approval
for the current `plan_digest` — a new plan requires a new approval.

```sh
uv run factory --json status --job <id>            # plan_estimate field
uv run factory plan approve <id> [--budget 60]     # sets the job limit (default: the suggested one)
uv run factory resume <id>
```

## A task that exhausted its attempts

The task moves to `FAILED` on its own: its rejected attempts are reverted from the job branch (patch stored as an artifact),
and independent tasks keep building. When nothing is left to build, the job waits in `NEEDS_HUMAN`
with a list of failed, blocked and accepted tasks.

```sh
uv run factory inspect <id>                                        # task findings
uv run factory task retry <id> TASK-04 --attempts 1 --reason "..."  # more attempts (1-3)
uv run factory resume <id>
```

## Review cost (`review`)

Task reviews run in tiers (`review.tiers`): CHANGES in the first tier (code, test adequacy,
UI) skips the expensive product-verify and security-review in that attempt; they run once the tier passes.
Tasks of jobs whose risk is in `cheaper_models_for_risk` (default `low`) get cheaper reviewers
(`cheaper_models`); feature acceptance and integration always use the full role bindings.

## Security: tools, triggers, deduplication (`review.security`)

Deterministic checks run before the agent review:

- **secret scan** in the policy gate (`policy.py::scan_secrets`), always on, no dependencies.
  It blocks secret files (`.env`, `*.pem`, `*.key`, `credentials.json`; `.env.example` templates
  pass) and key-shaped literals: private key, AWS, GitHub, OpenAI/Anthropic, Google,
  Slack, Stripe live, JWT, and outside tests also `password = "<random value>"`. The result is FAIL_POLICY
  with the file and line. The value never goes into evidence;
- **`sast`** (`node.yaml` manifest): pinned semgrep (`tools/semgrep`, `uv sync --project tools/semgrep`)
  with the local rules `factory/checks/semgrep/security.yml`. Works offline, with no network in the sandbox,
  `--metrics=off`. With `--baseline-commit {base_sha}` it reports only code added by the job.
  The rules cover eval, command and SQL injection, HTML sinks (XSS), weak randomness and password hashes,
  unverified JWT, disabled TLS and open redirect. Without the program, a repo with `sast` stops in BLOCKED_ENV
  (`factory doctor` → `sast.present`). Another manifest adds the same check;
- **`npm audit` is not a check**: the gate has no network, and `package.json` and the lockfile are protected,
  so a new dependency and its audit are done by the operator.

Agent review:

- **per task: baseline** on the model `review_models.security_baseline` (Sonnet). The controller passes
  `security_triggers` from the actual diff (`auth`, `secrets`, `endpoint`, `sql`, `outbound-request`,
  `html-sink`, `process-exec`, `file-upload`, `webhook`, `crypto`, `dependency`, `sensitive-path`)
  and `known_findings`. The reviewer checks the threats of those triggers, not the whole threat model;
- **once per feature: full analysis** (role `security_reviewer`, Opus) of the whole diff before acceptance,
  when there was a trigger or high risk (`feature_review: on_trigger`; `always`/`off`). CHANGES reopens
  the task whose `file_scope` covers the finding's file. The guard for the transition to INTEGRATION requires
  a current PASS of this review;
- **triage in the controller**: a finding in a file outside the diff never blocks (prefix `[outside this
  diff]`), and a finding another task has already reported (same path, ≥60% of the same words, not
  `fixed`) is skipped. A CHANGES that had only such findings becomes PASS. Event:
  `security.findings_triaged`;
- `full_per_task_for_risk: [high]` restores a full review of every task for high risk.

## Engineering principles and quality checks (`_shared/engineering-principles.md`)

Concrete rules instead of SOLID/DRY/KISS slogans: each has an ID (`EP-1`…`EP-14`) and is marked
as *gate* (checked by a tool) or *review* (judged by the code reviewer). The builder, code reviewer and
planner have them in their prompt (`Follow ../_shared/engineering-principles.md` in `SKILL.md`). The controller
appends to the prompt every `_shared` file the skill points to, plus the skill's own `references/*.md`:
the agent does not read `factory/`, so otherwise the checklists would not reach it.

- **Gate (Node profile, `checks/node.yaml`)**: lint with limits (complexity ≤ 10, nesting ≤ 3,
  ≤ 4 parameters, function ≤ 60 lines, file ≤ 300 lines), layer boundaries (`no-restricted-imports`),
  `@typescript-eslint/no-deprecated` and `duplicates`: jscpd with a baseline, a new clone ≥ 50 tokens
  fails the gate. The configuration lives in the application repo (`eslint.config.*`, `.jscpd.json`,
  `.jscpd-baseline.json`) and is protected (`policy.protected_paths`): the builder cannot change it.
- **Baseline**: code from before the limits is on an explicit list (the "Legacy baseline" block in ESLint, clones in
  `.jscpd-baseline.json`). The baseline only shrinks; after a refactor the operator narrows it manually
  (`npx jscpd --config .jscpd.json --baseline .jscpd-baseline.json --update-baseline`).
- **Policy**: disabling a check with a comment (`eslint-disable` of a whole block or of a limit
  or layer rule, `@ts-ignore`, `@ts-nocheck`, `jscpd:ignore`, `noqa: C901`, bare `noqa`,
  `#[allow(clippy::too_many_lines)]`, `CHECKSTYLE:OFF`) in a code file is a policy violation.
  Targeted exceptions (`eslint-disable-next-line react-hooks/exhaustive-deps`, `@ts-expect-error`)
  are allowed.
- **Review**: the code reviewer reports only *review* rules, with ID and cost; it does not repeat what
  the gate checked. The Python, Rust and Java profiles (tools for each *gate* rule) are described in the
  principles document; their checks manifest has to be added with the first such repo.

## UI review in the browser (`browser`, `preview` in the checks manifest)

When the repo's checks manifest has `preview` (e.g. `node.yaml`: `static_dir: dist`, built by the
`web-build` check), and `config.yaml` has `browser`, ui-review and product-verify (task review and feature
acceptance) get the `served` environment instead of `local-checkout`:

- the controller serves the built worktree directory on `127.0.0.1:<port from the pool>` (`preview.py`; unknown
  paths → `index.html` for SPAs, `/x` → `x.html` for static export); the server lives until the end of
  `factory run` for the given job;
- the reviewer gets Bash and the Playwright CLI (`tools/playwright-cli`, version in `factory.lock.json`):
  `open`, `resize`, `snapshot` (accessibility tree with refs), `click`/`fill`/`press`, `console`,
  `screenshot`. It runs in the factory sandbox: worktree read-only, writes only to the evidence
  directory; `PLAYWRIGHT_MCP_SANDBOX=false`, because Chrome's internal sandbox does not start inside the factory
  sandbox, `ISOLATED=true` (in-memory profile), `ALLOWED_ORIGINS` = the preview address;
- after the call the controller closes the browser session (`close`), and stores the screenshots from the evidence directory
  as artifacts (`screenshots` in the review result, event `browser.screenshots`).

Without a build (no `dist/index.html`) or without the program (`factory doctor` → `browser.present: false`)
the review stays `local-checkout`. Installation: `cd tools/playwright-cli && npm ci`; the browser comes
from the Playwright cache (`playwright-cli install-browser`, if it is missing). The app in the preview runs
without a backend (the API server is not started).

## UX assessment from screenshots and visual direction (`design`)

ui-review assesses screens from the screenshots it took itself (`factory-ui-review/references/ux-heuristics.md`):

- **Nielsen heuristics** `H1`..`H10`: each `PASS | FAIL | NOT_APPLICABLE | NOT_RUN` with screenshots;
- **visual quality**: `hierarchy`, `layout`, `typography`, `color`, `consistency`, `polish`, a 1-5 score
  from the rubric;
- **conformance to the visual direction** (`design_direction`): comparison with `docs/DESIGN.md` and the reference
  images in `docs/design/` (paths in `config.yaml` → `design`).

The controller does not trust the reviewer's claims and checks the ui-review result:

- in `served` mode every viewport from `browser.viewports` must have a screenshot saved in the evidence directory
  and listed in `viewports[].evidence_ids`. Names in `evidence_ids` must be collected files.
  All 10 heuristics and 6 visual scores must be filled in. Otherwise the result is `UNKNOWN`
  (event `ui_review.inconclusive`), and the review is repeated with a `controller_note` saying what was missing.
  After 3 attempts the job moves to `NEEDS_HUMAN`;
- a PASS with a visual score below `design.min_visual_score` (default 3) is turned into CHANGES
  with a blocking finding `CTRL-visual-<aspect>`. Only a subsequent review closes it, not the builder;
- when `docs/DESIGN.md` exists, `design_direction` must be `PASS` or `FAIL`, even without a browser.

Visual direction (`_shared/design-direction.md`): when a feature changes screens and the repo has no
`docs/DESIGN.md`, intake in guided mode asks about the direction: 2-4 concrete variants (character,
palette, density, reference product). The spec adds a requirement to write the document (direction,
tokens, layout, components, motion, tone). The plan turns it into the first UI task, on which the screen
tasks depend. Reference images (mockups, screenshots of the accepted look) go into
`docs/design/`. The reviewer opens them and compares the screenshots against them.
