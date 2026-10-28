# ADR-001: Controller stack and agent runtime

Status: accepted (implemented state). Date: 2026-10-26.

## Context

The blueprint (§3 items 2–4) recommends a controller in Python 3.11+ with SQLite, Pydantic 2, pytest and a CLI, without
Redis/Kubernetes on a single host, and a first Claude runtime via the official SDK or the
documented programmatic mode of the CLI. No target repo was specified; the host is macOS arm64 without a
running Docker daemon (`environment-report.md`).

## Decision

| Area | Choice | Version (uv.lock) |
|---|---|---|
| Language | Python 3.12 (`requires-python >=3.12`, `.python-version`) | 3.12.12 |
| Dependencies and lock | uv + `uv.lock`, `uv_build` backend | uv 0.13.0 |
| Contract validation | Pydantic 2 (`extra="forbid"`, `strict` for manifests) | pydantic 2.14.1 |
| Configuration | PyYAML | 6.0.3 |
| State | SQLite from the standard library: WAL, `busy_timeout=30000`, `BEGIN IMMEDIATE`, CAS on `jobs.version`, migrations in `state.py` | — |
| Tests | pytest + hypothesis (state machine property tests, `tests/unit/test_state_machine.py`) | pytest 8.4.2, hypothesis 6.170.12 |
| Orchestration | own state machine (`controller.py`) + the `Factory.run` loop; no Temporal/LangGraph/Celery | — |
| Agent runtime | Claude Code CLI in programmatic mode | tested with 2.1.74 |
| Sandbox | `sandbox-exec` (SBPL) on macOS, `bwrap` on Linux; fail-closed | — |
| Spec | OpenSpec CLI pinned locally in `tools/openspec` | 1.15.0 |

Agent invocation (`adapters/claude.py::_argv`):

```text
claude -p <prompt> --output-format stream-json --verbose --model <model>
  --permission-mode dontAsk --strict-mcp-config --disable-slash-commands --setting-sources project
  --session-id <uuid> | --resume <uuid>
  --tools <list> --allowedTools <list> --append-system-prompt <skill>
  [--effort <e>] --json-schema <AgentEnvelope schema> --max-budget-usd <cap>
```

The whole process (with descendants) is wrapped by `Sandbox.wrap`. The adapter reads the run state from the
JSONL stream file: the `system/init` event (model), the `result` event (`structured_output`, `result`,
`usage`, `total_cost_usd`, `is_error`, `subtype`). Cancellation: SIGTERM/SIGKILL of the process group.

## Rationale

- **CLI instead of the Agent SDK**: the CLI is already installed and logged in on the operator's machine (auth in the keychain,
  no API key in env), provides documented flags for sessions, output schema and cost limit, and
  the process can be placed entirely in the system sandbox and killed as a group. The adapter maps this onto
  its own `AgentRuntime` contract (`adapters/protocol.py`), so the SDK can be added as a second adapter.
  `capabilities()` detects flag support from `claude --help`.
- **SQLite**: one host, short transactions, no additional service; WAL lets the dashboard read
  while the controller is working.
- **Own state machine instead of Temporal/LangGraph**: durability is provided by SQLite + idempotent steps
  + outbox; a second orchestration would add a separate state store without a demonstrated need (blueprint §3 item 4).
- **sandbox-exec/bwrap instead of a container**: Docker daemon unavailable on the host; the system sandbox
  is enforced by the kernel and verified by a probe at startup.

## Consequences

- Dependence on the behavior and `stream-json` format of a specific Claude CLI version; a CLI change requires
  a repeated live smoke. The adapter does not parse private transcripts.
- The agent needs network access to the API, so the agent's sandbox profile has `network="full"` (risk in
  `threat-model.md`). An SDK with a controller-side broker could narrow this — not implemented.
- `--max-turns` is not passed (the `max_turns` field in the configuration is dead).
- `sandbox-exec` is marked deprecated by Apple; there is no alternative without a VM/container.
- SQLite limits the factory to one host; multiple hosts would require a migration (e.g. Postgres).
- The blueprint allows Python 3.11, but the project requires 3.12 (`StrEnum`, `datetime.UTC` are available
  since 3.11; choosing 3.12 is a pinning decision, not a language requirement).
