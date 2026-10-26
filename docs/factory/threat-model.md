# Threat model

Zakres: jeden host (macOS lub Linux), jeden użytkownik systemu, kontroler `factory` uruchamiany przez
operatora, agenci Claude CLI i komendy testów uruchamiane w sandboxie. Opis odpowiada kodowi w
`src/agent_factory/`.

## Granice zaufania

| Strefa | Zaufanie | Co w niej działa |
|---|---|---|
| Operator + kontroler | zaufane | proces `factory` (CLI), SQLite, klucz HMAC, `gh` auth, `factory/` |
| Agent (Claude CLI i jego narzędzia) | niezaufane | `claude -p` i procesy potomne (np. Bash buildera) w `MacSandbox`/`BwrapSandbox` |
| Komendy checków | niezaufane (wykonują kod z gałęzi joba) | `TrustedRunner.run_check` w sandboxie, z checkami z manifestu kontrolera |
| Wejście | niezaufane dane | tekst feature'a, issue, pliki repo, logi, wyjście agentów |
| Remote Git / GitHub | zewnętrzne | `origin` (lokalny bare remote w trybie `local`), API GitHub przez `gh` (nieużywane w CLI) |
| Przeglądarka operatora | częściowo | dashboard na localhost |

## Aktywa

| Aktywo | Gdzie | Dlaczego ważne |
|---|---|---|
| Klucz dowodów | `data_dir/keys/evidence.key` (0600) | kto go zna, może sfałszować PASS |
| Stan i dowody | `data_dir/state.db`, `data_dir/artifacts/` | źródło prawdy guardów |
| Konfiguracja i checki | `factory/` w repo fabryki | definiują, co jest PASS |
| Holdout | `data_dir/holdout/` | prywatne scenariusze evaluatora (mechanizm nie jest zaimplementowany; jest tylko deny) |
| Poświadczenia operatora | `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, `~/.netrc`, `~/.docker`, `~/.kube`, `~/.npmrc`, `~/.pypirc`, zmienne `GH_TOKEN` itp. | dostęp do zewnętrznych systemów |
| Gałąź bazowa | `origin/<base>` | merge niezweryfikowanego kodu |
| Worktree innych jobów | `data_dir/worktrees/<other-job>` | izolacja jobów |

## Zagrożenia i kontrole w kodzie

### 1. Agent/test pisze poza swoim katalogiem lub czyta sekrety

Kontrola: `sandbox.py`.

- `MacSandbox.profile` generuje SBPL: `(version 1)`, `(allow default)`, potem
  `(deny file-write*)` i `(allow file-write* (subpath <writable>…) /dev/null /dev/zero /dev/tty /dev/dtracehelper)`,
  na końcu `(deny file-read* file-write* (subpath <denied>…))`. Ścieżki są przepuszczane przez
  `realpath` i cytowane (`_sbpl_quote`).
- Sieć: dla `network != "full"` — `(deny network-outbound (remote ip))`; dla `localhost` dodatkowo
  `(allow network-outbound (remote ip "localhost:*"))`. `BwrapSandbox` używa `--unshare-net`
  (tylko loopback), `--ro-bind / /`, `--bind` dla zapisywalnych, `--tmpfs` lub `/dev/null` dla
  zablokowanych, `--die-with-parent`.
- `default_denied_reads`: `data_dir/{keys,state.db,holdout,artifacts}`, wszystkie inne worktree w
  `data_dir/worktrees`, katalogi `HOME_SECRET_DIRS`.
- `probe()` przy pierwszym użyciu sprawdza, że zapis poza dozwolonym katalogiem i odczyt
  zablokowanego pliku faktycznie się nie udają; inaczej `SandboxUnavailable`.
- Fail-closed: `get_sandbox` rzuca `SandboxUnavailable`, gdy brak `/usr/bin/sandbox-exec` (Darwin) lub
  `bwrap` (Linux), probe nie przejdzie albo `AGENT_FACTORY_DISABLE_SANDBOX=1`. CLI kończy się wtedy
  exit 4 i nie uruchamia kodu. `runtime.sandbox` w konfiguracji przyjmuje wyłącznie `required`.
- Runner: `writable = (worktree, scratch)`, sieć z manifestu (`none`/`localhost`; `full` niedozwolone
  w `CheckSpec`).
- Agent: `ClaudeRuntime._policy` — zapis do ścieżek przyznanych przez kontroler
  (`SandboxPolicy.writable`: worktree tylko dla `factory-implement`; role review nie mają zapisu do
  repo) oraz do `~/.claude`, `~/.claude.json*`,
  `~/.cache`, `~/Library/Caches`, `~/.local/state`, `~/.local/share/claude`, `$TMPDIR`, `/private/tmp`,
  `/private/var/folders`; deny jak wyżej; `network="full"`.

### 2. Wyciek poświadczeń przez środowisko

- Runner: `scrubbed_env` przepuszcza tylko `PATH, LANG, LC_ALL, LC_CTYPE, TERM`, ustawia własne
  `HOME`/`TMPDIR` w scratch, `PYTHONDONTWRITEBYTECODE=1`. `CheckEvidence.env_keys` zapisuje nazwy
  (nie wartości) zmiennych.
- Agent: `_AGENT_ENV_KEYS = PATH, LANG, LC_ALL, TERM, USER, LOGNAME, SHELL, TMPDIR`, `HOME` = prawdziwy
  home (auth Claude CLI jest w keychain/konfiguracji). `GH_TOKEN`, `ANTHROPIC_API_KEY`, klucze chmurowe
  nie są przekazywane.
- `gh` działa wyłącznie w procesie kontrolera (`GhCliTransport`), nigdy w sandboxie.

### 3. Fałszywy PASS (agent twierdzi, że testy przeszły / podrzuca raport)

- Guardy czytają tylko tabelę `results` przez `EvidenceStore.current_pass`. Wiersz jest akceptowany,
  gdy HMAC-SHA256 (klucz z `keys/evidence.key`) nad `{result_id, job_id, task_id, kind, verdict,
  identity, producer, payload_artifact_id, payload_sha256}` się zgadza (`payload_sha256` to hash
  artefaktu payloadu, a `EvidenceStore.read` porównuje go z treścią pliku) i producent jest w `TRUSTED_PRODUCERS`
  (`trusted-runner`, `agent-adapter`, `controller`, `operator`).
- `InputIdentity.validate_real` odrzuca placeholdery zamiast SHA/digestów.
- Wynik jest ważny tylko dla bieżącej tożsamości (`stale_fields`).
- Testy liczy runner z junit (`parse_junit`); zero wykonanych przypadków lub mniej niż `min_tests` →
  `FAIL_POLICY`, brak raportu → `ERROR_ENV`, timeout → `TIMEOUT` (kill grupy procesów).
- Eksporty w `openspec/changes/<id>/` (np. `spec-evaluation.json`) nigdy nie są czytane jako dowód.
- Wynik agenta z nieaktualnej epoki lease jest odrzucany (`check_fence` przed zapisem `attempts`).

### 4. Agent zmienia zasady oceny

- `policy.evaluate_diff` na diffie buildera (`build_start_sha..HEAD`): zmiana pliku z
  `policy.protected_paths` (`factory/*`, `openspec/*`, `.github/*`, `pytest.ini`, `conftest.py`,
  `pyproject.toml`, `package.json`, `Makefile`…) → violation; zmiana poza `task.file_scope` (poza
  plikami testów) → violation.
- Wykrywanie osłabiania testów (heurystyki regex): dodane `skip/xfail/it.only/describe.skip`,
  `assert True`, usunięty plik testu, więcej usuniętych niż dodanych funkcji testowych lub asercji.
- Przepisanie historii sprzed buildu (`git merge-base --is-ancestor` nie przechodzi) → `FAIL_POLICY`.
- Po `FAIL_POLICY` patch zapisywany jest jako artefakt `rejected-attempt-patch`, a worktree wraca do
  `build_start_sha` (`git reset --hard` + `git clean -fdq`), żeby zakazana zmiana nie przeszła do
  następnej próby.
- Manifest checków (`factory/checks/default.yaml`) czyta kontroler z repo fabryki, nie z gałęzi joba.
  `Controller.checks_digest()` liczy na żywo manifest katalogów `factory/checks/` i `factory/skills/`,
  więc edycja checków lub skilli unieważnia wyniki `gate` i review.
  Placeholdery w `argv` ograniczone do `{python}`, `{report}`, `{worktree}`.

### 5. Nieautoryzowane skutki zewnętrzne

- Broker `policy.authorize_side_effect`: operacja musi być w `SIDE_EFFECTS` i dozwolona w `policy.mode`
  (np. `local.merge` w `local/pr/automerge/release`, `github.merge` tylko `automerge/release`,
  `github.comment` wymaga `external_messages: true`). Wywoływany w `GitHubAdapter.perform` i
  `LocalMergeHandler.perform`; odmowa → `broker.denied` w audycie, bez ponawiania.
- Outbox: idempotency key `repo:job:candidate_sha:kind`, `lookup` przed ponowieniem po
  `inflight/unknown`.
- Merge lokalny: `git push --force-with-lease=refs/heads/<base>:<tested_base_sha>`; przesunięta baza →
  `BASE_MOVED` → `INTEGRATION`. `LocalMergeHandler` sprawdza też, że HEAD worktree = testowany kandydat.
- Merge GitHub (kod, nieużywany w CLI): wymaga, by wszystkie check-runs były `completed/success`,
  przekazuje `sha` testowanego head, brak admin bypass (`allow_admin_bypass: Literal[False]`).
- `merge.enabled: false` (domyślnie) blokuje `MERGING` w guardzie.

### 6. Prompt injection z treści issue/repo/logów

- Prompt (`Factory._run_agent_once`) opakowuje dane w `<inputs>…</inputs>` z jawną instrukcją, że to
  DANE, niezaufane, nie polecenia i nie zgoda, oraz podaje linię „Snapshot identity” (revision,
  digesty scope/spec/decision/plan/policy, base/head, runda). `skills/_shared/contract.md` opisuje,
  że wejścia przychodzą jako pola `<inputs>`, a nie pliki.
- `GitHubAdapter.issue` bierze tylko `number/title/body/labels`.
- Wszystkie wywołania git są tablicami argumentów; `job_id` i nazwy gałęzi walidowane regexem
  (`JOB_ID`, `BRANCH`).
- Ścieżki plików spec z agenta muszą pozostać w katalogu zmiany (`_write_spec_files`); `Location.path`
  w findings musi być względna i bez `..`.
- Agent dostaje `--permission-mode dontAsk`, `--strict-mcp-config`, `--disable-slash-commands`,
  `--setting-sources project`, jawną listę `--tools`/`--allowedTools`; role nie-builder mają tylko
  `Read/Glob/Grep` (+ `Bash`, jeśli jest w bindingu roli — obecnie `product_evaluator`).
- Prompt injection nie może przyznać PASS: werdykty idą przez schemat + guardy (pkt 3).

### 7. Dashboard

`dashboard/__init__.py`: bind tylko `127.0.0.1`/`localhost`/`::1` (inny host → `ValueError`); każda
wartość przez `html.escape(quote=True)`; Markdown i tekst agentów jako `<pre>` (bez renderowania HTML);
nagłówki `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'` i
`X-Content-Type-Options: nosniff`; tylko trasy `/` i `/job/job-<12 hex>`; `POST` → 405. Brak akcji
zmieniających stan, więc brak potrzeby CSRF. Brak uwierzytelnienia — każdy lokalny użytkownik/proces
na hoście może czytać dashboard.

### 8. Współbieżność i restart

Leases z epoką (`scheduler.acquire_lease`): przejęcie tylko, gdy lease wygasł **i** PID właściciela nie
żyje; heartbeat sprawdza fence. Po restarcie `_adopt_running` czeka na żywy proces agenta zamiast
startować drugi. CAS na `jobs.version` przy każdej zmianie.

## Znane ryzyka resztkowe

1. **Agent ma pełną sieć wychodzącą.** Claude CLI potrzebuje API dostawcy, więc
   `ClaudeRuntime._policy` ustawia `network="full"`. Procesy potomne narzędzia `Bash` buildera (i
   `product_evaluator`) dziedziczą ten profil — mogą pobierać kod i wysyłać dane z worktree. Brak
   brokera sieci/proxy z allow-listą.
2. **Profil SBPL to deny-lista na bazie `(allow default)`.** Agent zapisuje i czyta `~/.claude`
   (wymagane przez CLI), może korzystać z usług Mach (np. keychain) i gniazd UNIX
   (`/var/run/docker.sock` nie jest blokowany). Odczyt jest dozwolony wszędzie poza listą deny — w tym
   repo fabryki, np. `tests/evals/dev.yaml` z golden testami eval (builder nie dostaje ich w
   `<inputs>` ani w worktree, ale sandbox nie blokuje ich odczytu po ścieżce).
3. **`sandbox-exec` jest oznaczony przez Apple jako deprecated.** Działa na macOS 26.6.2 (probe
   przechodzi), ale może zniknąć lub zmienić semantykę. `bwrap` nie był testowany na tym hoście.
4. **Logi agentów i scratch runnera nie są zablokowane** dla innych jobów (`data_dir/agent-logs`,
   `data_dir/runs`, `data_dir/evals` nie są w `default_denied_reads`).
5. **Pojedynczy host, SQLite.** WAL + `busy_timeout`, brak replikacji; NFS niewspierany. Klucz HMAC i
   baza są plikami tego samego użytkownika systemu — ochrona polega wyłącznie na regułach sandboxa.
6. **Wykrywanie osłabiania testów jest heurystyczne** (regex na liniach diffu `--unified=0`). Nie wykrywa
   np. zmiany wartości oczekiwanej, mockowania testowanej funkcji czy przeniesienia logiki do fixture.
7. **Holdout to tylko deny katalogu `data_dir/holdout`.** Brak mechanizmu prywatnych scenariuszy
   evaluatora w jobach; golden testy eval są w publicznym `tests/evals/`.
8. **Reset odrzuconej próby (`FAIL_POLICY`) używa `git reset --hard` + `git clean -fdq`**, wyłącznie w
   worktree należącym do kontrolera (`data_dir/worktrees/<job-id>`), do zapisanego `build_start_sha`;
   patch próby jest najpierw zapisywany jako artefakt `rejected-attempt-patch`. Checkout użytkownika
   nie jest dotykany. Nieśledzone, niezacommitowane pliki w worktree joba są tracone.
9. **Nie wszystko unieważnia wyniki.** `policy_digest` obejmuje tylko `config.yaml`, `workflow.yaml`,
   `selection.yaml`; `environment_digest` jest zamrażany przy `INTAKE`; `prompt_digest`, `model_id`,
   `skill_digests` nie są polami tożsamości wyniku (skille wpływają tylko przez `checks_digest`, a ten
   nie jest zależnością wyników analizy). `factory/prompts/` nie wchodzi do żadnego digestu (i nie jest
   ładowany).
10. **GitHub live nie był testowany** na prawdziwym API; `GitHubAdapter` jest weryfikowany tylko na
    `StubGitHub` i nie jest podłączony w CLI.
