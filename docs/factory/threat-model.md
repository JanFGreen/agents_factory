# Threat model

Scope: a single host (macOS or Linux), a single OS user, the `factory` controller run by
the operator, Claude CLI agents and test commands run in a sandbox. The description matches the code in
`src/agent_factory/`.

## Trust boundaries

| Zone | Trust | What runs in it |
|---|---|---|
| Operator + controller | trusted | `factory` process (CLI), SQLite, HMAC key, `gh` auth, `factory/` |
| Agent (Claude CLI and its tools) | untrusted | `claude -p` and child processes (e.g. the builder's Bash) in `MacSandbox`/`BwrapSandbox` |
| Check commands | untrusted (they execute code from the job branch) | `TrustedRunner.run_check` in the sandbox, with checks from the controller's manifest |
| Input | untrusted data | feature text, issues, repo files, logs, agent output |
| Git remote / GitHub | external | `origin` (a local bare remote in `local` mode), GitHub API via `gh` (unused in the CLI) |
| Operator's browser | partial | dashboard on localhost |

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| Evidence key | `data_dir/keys/evidence.key` (0600) | whoever knows it can forge a PASS |
| State and evidence | `data_dir/state.db`, `data_dir/artifacts/` | source of truth for guards |
| Configuration and checks | `factory/` in the factory repo | they define what counts as PASS |
| Holdout | `data_dir/holdout/` | private evaluator scenarios (the mechanism is not implemented; only the deny exists) |
| Operator credentials | `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.netrc`, `~/.docker`, `~/.kube`, `~/.npmrc`, `~/.pypirc`, variables such as `GH_TOKEN` | access to external systems |
| Base branch | `origin/<base>` | merge of unverified code |
| Other jobs' worktrees | `data_dir/worktrees/<other-job>` | job isolation |

## Threats and controls in code

### 1. Agent/test writes outside its directory or reads secrets

Control: `sandbox.py`.

- `MacSandbox.profile` generates SBPL: `(version 1)`, `(allow default)`, then
  `(deny file-write*)` and `(allow file-write* (subpath <writable>…) /dev/null /dev/zero /dev/tty /dev/dtracehelper)`,
  and finally `(deny file-read* file-write* (subpath <denied>…))`. Paths are passed through
  `realpath` and quoted (`_sbpl_quote`).
- Network: for `network != "full"` — `(deny network-outbound (remote ip))`; for `localhost` additionally
  `(allow network-outbound (remote ip "localhost:*"))`. `BwrapSandbox` uses `--unshare-net`
  (loopback only), `--ro-bind / /`, `--bind` for writable paths, `--tmpfs` or `/dev/null` for
  blocked ones, `--die-with-parent`.
- `default_denied_reads`: `data_dir/{keys,state.db,holdout,artifacts}`, all other worktrees in
  `data_dir/worktrees`, the `HOME_SECRET_DIRS` directories.
- `probe()` on first use checks that a write outside the allowed directory and a read of a
  blocked file actually fail; otherwise `SandboxUnavailable`.
- Fail-closed: `get_sandbox` raises `SandboxUnavailable` when `/usr/bin/sandbox-exec` (Darwin) or
  `bwrap` (Linux) is missing, the probe fails, or `AGENT_FACTORY_DISABLE_SANDBOX=1`. The CLI then exits with
  exit 4 and runs no code. `runtime.sandbox` in the configuration accepts only `required`.
- Runner: `writable = (worktree, scratch)`, network from the manifest (`none`/`localhost`; `full` not allowed
  in `CheckSpec`).
- Agent: `ClaudeRuntime._policy` — writes to paths granted by the controller
  (`SandboxPolicy.writable`: worktree only for `factory-implement`; review roles have no write access to the
  repo) and to `~/.claude`, `~/.claude.json*`,
  `~/.cache`, `~/Library/Caches`, `~/.local/state`, `~/.local/share/claude`, `$TMPDIR`, `/private/tmp`,
  `/private/var/folders`; deny as above; `network="full"`.

### 2. Credential leak through the environment

- Runner: `scrubbed_env` passes only `PATH, LANG, LC_ALL, LC_CTYPE, TERM`, sets its own
  `HOME`/`TMPDIR` in scratch, `PYTHONDONTWRITEBYTECODE=1`. `CheckEvidence.env_keys` records the names
  (not the values) of the variables.
- Agent: `_AGENT_ENV_KEYS = PATH, LANG, LC_ALL, TERM, USER, LOGNAME, SHELL, TMPDIR`, `HOME` = the real
  home (Claude CLI auth is in the keychain/configuration). `GH_TOKEN`, `ANTHROPIC_API_KEY`, cloud keys
  are not passed.
- `gh` runs only in the controller process (`GhCliTransport`), never in the sandbox.

### 3. False PASS (agent claims tests passed / plants a report)

- Guards read only the `results` table via `EvidenceStore.current_pass`. A row is accepted
  when the HMAC-SHA256 (key from `keys/evidence.key`) over `{result_id, job_id, task_id, kind, verdict,
  identity, producer, payload_artifact_id, payload_sha256}` matches (`payload_sha256` is the hash of the
  payload artifact, and `EvidenceStore.read` compares it with the file content) and the producer is in `TRUSTED_PRODUCERS`
  (`trusted-runner`, `agent-adapter`, `controller`, `operator`).
- `InputIdentity.validate_real` rejects placeholders in place of SHAs/digests.
- A result is valid only for the current identity (`stale_fields`).
- Tests are counted by the runner from junit (`parse_junit`); zero executed cases or fewer than `min_tests` →
  `FAIL_POLICY`, no report → `ERROR_ENV`, timeout → `TIMEOUT` (process group kill).
- Exports in `openspec/changes/<id>/` (e.g. `spec-evaluation.json`) are never read as evidence.
- An agent result from a stale lease epoch is rejected (`check_fence` before writing `attempts`).

### 4. Agent changes the evaluation rules

- `policy.evaluate_diff` on the builder's diff (`build_start_sha..HEAD`): a change to a file in
  `policy.protected_paths` (`factory/*`, `openspec/*`, `.github/*`, `pytest.ini`, `conftest.py`,
  `pyproject.toml`, `package.json`, `Makefile`…) → violation; a change outside `task.file_scope` (other than
  test files) → violation.
- Test-weakening detection (regex heuristics): added `skip/xfail/it.only/describe.skip`,
  `assert True`, a deleted test file, more removed than added test functions or assertions.
- Rewriting history from before the build (`git merge-base --is-ancestor` fails) → `FAIL_POLICY`.
- After `FAIL_POLICY` the patch is saved as a `rejected-attempt-patch` artifact, and the worktree is reset to
  `build_start_sha` (`git reset --hard` + `git clean -fdq`), so the forbidden change does not carry over to the
  next attempt.
- The check manifest (`factory/checks/default.yaml`) is read by the controller from the factory repo, not from the job branch.
  `Controller.checks_digest()` computes live a manifest of the `factory/checks/` and `factory/skills/` directories,
  so editing checks or skills invalidates `gate` and review results.
  Placeholders in `argv` are limited to `{python}`, `{report}`, `{worktree}`.

### 5. Unauthorized external side effects

- Broker `policy.authorize_side_effect`: the operation must be in `SIDE_EFFECTS` and allowed in `policy.mode`
  (e.g. `local.merge` in `local/pr/automerge/release`, `github.merge` only `automerge/release`,
  `github.comment` requires `external_messages: true`). Called in `GitHubAdapter.perform` and
  `LocalMergeHandler.perform`; denial → `broker.denied` in the audit log, no retry.
- Outbox: idempotency key `repo:job:candidate_sha:kind`, `lookup` before retry after
  `inflight/unknown`.
- Local merge: `git push --force-with-lease=refs/heads/<base>:<tested_base_sha>`; moved base →
  `BASE_MOVED` → `INTEGRATION`. `LocalMergeHandler` also checks that the worktree HEAD = the tested candidate.
- GitHub merge (code, unused in the CLI): requires all check-runs to be `completed/success`,
  passes the `sha` of the tested head, no admin bypass (`allow_admin_bypass: Literal[False]`).
- `merge.enabled: false` (default) blocks `MERGING` in the guard.

### 6. Prompt injection from issue/repo/log content

- The prompt (`Factory._run_agent_once`) wraps data in `<inputs>…</inputs>` with an explicit instruction that it is
  DATA, untrusted, not commands and not consent, and provides a "Snapshot identity" line (revision,
  scope/spec/decision/plan/policy digests, base/head, round). `skills/_shared/contract.md` states
  that inputs arrive as `<inputs>` fields, not files.
- `GitHubAdapter.issue` takes only `number/title/body/labels`.
- All git calls are argument arrays; `job_id` and branch names are validated by regex
  (`JOB_ID`, `BRANCH`).
- Spec file paths from the agent must stay within the change directory (`_write_spec_files`); `Location.path`
  in findings must be relative and without `..`.
- The agent gets `--permission-mode dontAsk`, `--strict-mcp-config`, `--disable-slash-commands`,
  `--setting-sources project`, an explicit `--tools`/`--allowedTools` list; non-builder roles have only
  `Read/Glob/Grep` (+ `Bash`, if it is in the role binding — currently `product_evaluator`).
- Prompt injection cannot grant a PASS: verdicts go through the schema + guards (item 3).

### 7. Dashboard

`dashboard/__init__.py`: binds only `127.0.0.1`/`localhost`/`::1` (another host → `ValueError`); every
value goes through `html.escape(quote=True)`; Markdown and agent text as `<pre>` (no HTML rendering);
headers `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'` and
`X-Content-Type-Options: nosniff`; only the routes `/` and `/job/job-<12 hex>`; `POST` → 405. No
state-changing actions, so no need for CSRF. No authentication — any local user/process
on the host can read the dashboard.

### 8. Concurrency and restart

Leases with an epoch (`scheduler.acquire_lease`): takeover only when the lease has expired **and** the owner's PID is
dead; the heartbeat checks the fence. After a restart `_adopt_running` waits for the live agent process instead of
starting a second one. CAS on `jobs.version` on every change.

## Known residual risks

1. **The agent has full outbound network access.** Claude CLI needs the provider API, so
   `ClaudeRuntime._policy` sets `network="full"`. Child processes of the builder's `Bash` tool (and
   `product_evaluator`'s) inherit this profile — they can download code and send data from the worktree. There is no
   network broker/proxy with an allow-list.
2. **The SBPL profile is a deny-list on top of `(allow default)`.** The agent writes and reads `~/.claude`
   (required by the CLI), can use Mach services (e.g. keychain) and UNIX sockets
   (`/var/run/docker.sock` is not blocked). Reading is allowed everywhere outside the deny list — including the
   factory repo, e.g. `tests/evals/dev.yaml` with the eval golden tests (the builder does not receive them in
   `<inputs>` or in the worktree, but the sandbox does not block reading them by path).
3. **`sandbox-exec` is marked as deprecated by Apple.** It works on macOS 26.6.2 (the probe
   passes), but it may disappear or change semantics. `bwrap` has not been tested on this host.
4. **Agent logs and runner scratch are not blocked** for other jobs (`data_dir/agent-logs`,
   `data_dir/runs`, `data_dir/evals` are not in `default_denied_reads`).
5. **Single host, SQLite.** WAL + `busy_timeout`, no replication; NFS not supported. The HMAC key and
   the database are files of the same OS user — protection relies solely on sandbox rules.
6. **Test-weakening detection is heuristic** (regex on `--unified=0` diff lines). It does not detect,
   e.g., a change to an expected value, mocking the function under test, or moving logic into a fixture.
7. **Holdout is only a deny on the `data_dir/holdout` directory.** There is no mechanism for private evaluator
   scenarios in jobs; the eval golden tests are in the public `tests/evals/`.
8. **Resetting a rejected attempt (`FAIL_POLICY`) uses `git reset --hard` + `git clean -fdq`**, only in the
   worktree owned by the controller (`data_dir/worktrees/<job-id>`), to the recorded `build_start_sha`;
   the attempt's patch is first saved as a `rejected-attempt-patch` artifact. The user's checkout
   is not touched. Untracked, uncommitted files in the job's worktree are lost.
9. **Not everything invalidates results.** `policy_digest` covers only `config.yaml`, `workflow.yaml`,
   `selection.yaml`; `environment_digest` is frozen at `INTAKE`; `prompt_digest`, `model_id`,
   `skill_digests` are not result identity fields (skills have an effect only through `checks_digest`, and that
   is not a dependency of analysis results). `factory/prompts/` is not part of any digest (and is not
   loaded).
10. **GitHub live has not been tested** against the real API; `GitHubAdapter` is verified only against
    `StubGitHub` and is not wired into the CLI.
