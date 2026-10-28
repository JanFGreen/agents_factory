# Environment report (B0)

Measurement date: 2026-10-26. Host: the operator's development machine. Results are the actual command output.

## Tools

| Command | Result |
|---|---|
| `sw_vers` | `ProductName: macOS`, `ProductVersion: 26.6.2`, `BuildVersion: 25G83` |
| `uname -m` | `arm64` |
| `python3 --version` | `Python 3.14.4` (system; not used by the project) |
| `uv --version` | `uv 0.13.0 (4ecd0eff2 2026-10-09 aarch64-apple-darwin)` |
| `uv run python --version` | `Python 3.12.12` (from `.python-version` = `3.12`) |
| `node --version` | `v20.20.0` |
| `git --version` | `git version 2.50.1 (Apple Git-155)` |
| `claude --version` | `2.1.74 (Claude Code)` |
| `which gh` | `/opt/homebrew/bin/gh` |
| `which docker` | `/usr/local/bin/docker` |
| `which sandbox-exec` | `/usr/bin/sandbox-exec` |
| `which bwrap` | `bwrap not found` |
| `OPENSPEC_TELEMETRY=0 tools/openspec/node_modules/.bin/openspec --version` | `1.15.0` |

## Decisions resulting from discovery

- **Docker**: the binary is present, but the daemon did not respond during discovery (`docker info` hangs
  on this host; it was not restarted). Docker is not used.
- **Sandbox**: `sandbox-exec` (`MacSandbox`) was chosen. `uv run factory doctor` reports
  `sandbox: ok, name: macos-sandbox-exec, probe: write outside and secret read denied`.
  `bwrap` (Linux) was not tested on this host.
- **Target repo**: none was specified, so the factory was created as a separate project in this directory
  (blueprint §3 item 1). Tests run on the `tests/fixtures/repos/greeter` fixture with a local bare
  remote.
- **CI**: no CI configured (no `.github/workflows`). The only gate is the local `uv run pytest`.
- **OpenSpec**: `@fission-ai/openspec` 1.15.0 pinned in `tools/openspec` (`factory.lock.json`,
  requires Node `>=20.19.0`, verified `v20.20.0`).

## Credentials

| Integration | State |
|---|---|
| Claude CLI | auth via local CLI login (keychain/configuration in `~/.claude`), not via environment variables. `factory doctor` does not check auth — only a live smoke will confirm it (`AGENT_FACTORY_LIVE=1 uv run pytest tests/live`); live smoke was **not run** for this report |
| GitHub | `gh auth status`: `github.com`, `✓ Logged in to github.com account janzielinski-soter (keyring)`, `Active account: true`. Unused: `local` mode, and `GitHubAdapter` is not wired into the CLI |

## Factory baseline

| Command | Result |
|---|---|
| `uv run factory doctor` | config ok, `policy_digest e5388270…5538f29`, `data_dir ~/.local/share/agent-factory`, `mode local`, database schema 2, sandbox ok, `claude` 2.1.74 found |
| `uv run pytest -q` | `144 passed, 2 skipped in 88.55s` — the skipped ones are 2 `tests/live` tests (they require `AGENT_FACTORY_LIVE=1`), i.e. NOT_RUN |

## Gaps to fill

- Live smoke with the real Claude CLI (API cost) — not performed as part of this report.
- No `bwrap` test on Linux.
- No `GitHubAdapter` test against real GitHub.
- No CI.
