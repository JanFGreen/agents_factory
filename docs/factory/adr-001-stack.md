# ADR-001: Stack kontrolera i runtime agentów

Status: przyjęte (stan zaimplementowany). Data: 2026-10-26.

## Kontekst

Blueprint (§3 pkt 2–4) zaleca kontroler w Pythonie 3.11+ z SQLite, Pydantic 2, pytest i CLI, bez
Redis/Kubernetes na jednym hoście, oraz pierwszy runtime Claude przez oficjalne SDK albo
udokumentowany tryb programistyczny CLI. Docelowego repo nie wskazano; host to macOS arm64 bez
działającego daemona Dockera (`environment-report.md`).

## Decyzja

| Obszar | Wybór | Wersja (uv.lock) |
|---|---|---|
| Język | Python 3.12 (`requires-python >=3.12`, `.python-version`) | 3.12.12 |
| Zależności i lock | uv + `uv.lock`, backend `uv_build` | uv 0.13.0 |
| Walidacja kontraktów | Pydantic 2 (`extra="forbid"`, `strict` dla manifestów) | pydantic 2.14.1 |
| Konfiguracja | PyYAML | 6.0.3 |
| Stan | SQLite z biblioteki standardowej: WAL, `busy_timeout=30000`, `BEGIN IMMEDIATE`, CAS na `jobs.version`, migracje w `state.py` | — |
| Testy | pytest + hypothesis (testy własności maszyny stanów, `tests/unit/test_state_machine.py`) | pytest 8.4.2, hypothesis 6.170.12 |
| Orkiestracja | własna maszyna stanów (`controller.py`) + pętla `Factory.run`; brak Temporal/LangGraph/Celery | — |
| Runtime agenta | Claude Code CLI w trybie programistycznym | testowane z 2.1.74 |
| Sandbox | `sandbox-exec` (SBPL) na macOS, `bwrap` na Linuksie; fail-closed | — |
| Spec | OpenSpec CLI przypięty lokalnie w `tools/openspec` | 1.15.0 |

Wywołanie agenta (`adapters/claude.py::_argv`):

```text
claude -p <prompt> --output-format stream-json --verbose --model <model>
  --permission-mode dontAsk --strict-mcp-config --disable-slash-commands --setting-sources project
  --session-id <uuid> | --resume <uuid>
  --tools <list> --allowedTools <list> --append-system-prompt <skill>
  [--effort <e>] --json-schema <AgentEnvelope schema> --max-budget-usd <cap>
```

Cały proces (z potomkami) jest opakowany przez `Sandbox.wrap`. Stan runu adapter odczytuje z pliku
JSONL strumienia: event `system/init` (model), event `result` (`structured_output`, `result`,
`usage`, `total_cost_usd`, `is_error`, `subtype`). Anulowanie: SIGTERM/SIGKILL grupy procesów.

## Uzasadnienie

- **CLI zamiast Agent SDK**: CLI jest już zainstalowane i zalogowane u operatora (auth w keychain,
  brak klucza API w env), daje udokumentowane flagi dla sesji, schematu wyjścia i limitu kosztu, a
  proces da się w całości umieścić w sandboxie systemowym i zabić jako grupę. Adapter mapuje to na
  własny kontrakt `AgentRuntime` (`adapters/protocol.py`), więc SDK można dodać jako drugi adapter.
  `capabilities()` wykrywa obsługę flag z `claude --help`.
- **SQLite**: jeden host, krótkie transakcje, brak dodatkowej usługi; WAL pozwala czytać dashboardowi
  w trakcie pracy kontrolera.
- **Własna maszyna stanów zamiast Temporal/LangGraph**: trwałość zapewnia SQLite + idempotentne kroki
  + outbox; druga orkiestracja dodałaby osobny magazyn stanu bez wykazanej potrzeby (blueprint §3 pkt 4).
- **sandbox-exec/bwrap zamiast kontenera**: Docker daemon niedostępny na hoście; sandbox systemowy
  jest egzekwowany przez jądro i weryfikowany probe przy starcie.

## Konsekwencje

- Zależność od zachowania i formatu `stream-json` konkretnej wersji Claude CLI; zmiana CLI wymaga
  ponownego live smoke. Adapter nie parsuje prywatnych transkryptów.
- Agent wymaga sieci do API, więc profil sandboxa agenta ma `network="full"` (ryzyko w
  `threat-model.md`). SDK z brokerem po stronie kontrolera mógłby to zawęzić — niezrealizowane.
- `--max-turns` nie jest przekazywane (pole `max_turns` w konfiguracji jest martwe).
- `sandbox-exec` jest przez Apple oznaczony jako deprecated; brak alternatywy bez VM/kontenera.
- SQLite ogranicza fabrykę do jednego hosta; wiele hostów wymagałoby migracji (np. Postgres).
- Blueprint dopuszcza Python 3.11, ale projekt wymaga 3.12 (`StrEnum`, `datetime.UTC` są dostępne
  od 3.11; wybór 3.12 to decyzja pinu, nie wymóg języka).
