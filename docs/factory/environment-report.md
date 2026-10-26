# Raport środowiska (B0)

Data pomiaru: 2026-10-26. Host: maszyna deweloperska operatora. Wyniki to faktyczne wyjście komend.

## Narzędzia

| Komenda | Wynik |
|---|---|
| `sw_vers` | `ProductName: macOS`, `ProductVersion: 26.6.2`, `BuildVersion: 25G83` |
| `uname -m` | `arm64` |
| `python3 --version` | `Python 3.14.4` (systemowy; projekt go nie używa) |
| `uv --version` | `uv 0.13.0 (4ecd0eff2 2026-10-09 aarch64-apple-darwin)` |
| `uv run python --version` | `Python 3.12.12` (z `.python-version` = `3.12`) |
| `node --version` | `v20.20.0` |
| `git --version` | `git version 2.50.1 (Apple Git-155)` |
| `claude --version` | `2.1.74 (Claude Code)` |
| `which gh` | `/opt/homebrew/bin/gh` |
| `which docker` | `/usr/local/bin/docker` |
| `which sandbox-exec` | `/usr/bin/sandbox-exec` |
| `which bwrap` | `bwrap not found` |
| `OPENSPEC_TELEMETRY=0 tools/openspec/node_modules/.bin/openspec --version` | `1.15.0` |

## Decyzje wynikające z discovery

- **Docker**: binarka jest, ale daemon nie odpowiedział podczas discovery (`docker info` zawiesza się
  na tym hoście; nie był uruchamiany ponownie). Docker nie jest używany.
- **Sandbox**: wybrano `sandbox-exec` (`MacSandbox`). `uv run factory doctor` raportuje
  `sandbox: ok, name: macos-sandbox-exec, probe: write outside and secret read denied`.
  `bwrap` (Linux) nie był testowany na tym hoście.
- **Repo docelowe**: nie wskazano, więc fabrykę utworzono jako osobny projekt w tym katalogu
  (blueprint §3 pkt 1). Testy działają na fixture `tests/fixtures/repos/greeter` z lokalnym bare
  remote.
- **CI**: brak skonfigurowanego CI (brak `.github/workflows`). Jedyną bramką jest lokalne `uv run pytest`.
- **OpenSpec**: `@fission-ai/openspec` 1.15.0 przypięty w `tools/openspec` (`factory.lock.json`,
  wymagany Node `>=20.19.0`, zweryfikowany `v20.20.0`).

## Poświadczenia

| Integracja | Stan |
|---|---|
| Claude CLI | auth przez lokalne logowanie CLI (keychain/konfiguracja w `~/.claude`), nie przez zmienne środowiska. `factory doctor` nie sprawdza auth — potwierdzi to dopiero live smoke (`AGENT_FACTORY_LIVE=1 uv run pytest tests/live`); w tym raporcie **nie uruchomiono** live smoke |
| GitHub | `gh auth status`: `github.com`, `✓ Logged in to github.com account janzielinski-soter (keyring)`, `Active account: true`. Nieużywane: tryb `local`, a `GitHubAdapter` nie jest podłączony w CLI |

## Baseline fabryki

| Komenda | Wynik |
|---|---|
| `uv run factory doctor` | config ok, `policy_digest e5388270…5538f29`, `data_dir ~/.local/share/agent-factory`, `mode local`, schemat bazy 2, sandbox ok, `claude` 2.1.74 znaleziony |
| `uv run pytest -q` | `144 passed, 2 skipped in 88.55s` — pominięte to 2 testy `tests/live` (wymagają `AGENT_FACTORY_LIVE=1`), czyli NOT_RUN |

## Braki do uzupełnienia

- Live smoke z prawdziwym Claude CLI (koszt API) — nie wykonany w ramach tego raportu.
- Brak testu `bwrap` na Linuksie.
- Brak testu `GitHubAdapter` na prawdziwym GitHub.
- Brak CI.
