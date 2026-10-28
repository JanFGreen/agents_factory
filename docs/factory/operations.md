# Operacje

Wszystkie komendy uruchamiaj z katalogu głównego repo fabryki (`factory doctor` szuka
`factory.lock.json` i `tools/openspec/...` względem bieżącego katalogu). Flagi globalne podawaj
**przed** podkomendą: `uv run factory --json --data-dir <dir> --factory-dir <dir> <cmd>`.
`--factory-dir` domyślnie `$AGENT_FACTORY_DIR` albo `factory`.

Exit codes (`cli.py`): `0` ok, `2` błąd użycia/konfiguracji, `3` odmowa (`TransitionRefused`, zły
`--revision`, `--mode` niezgodny z konfiguracją), `4` job zatrzymany (`HALTED`) lub brak sandboxa.

## Instalacja

```sh
uv sync                        # Python 3.12 (.python-version), zależności z uv.lock
uv run pytest                  # offline; testy live są pomijane
(cd tools/openspec && npm ci)  # tylko jeśli brakuje node_modules; wersja w factory.lock.json
```

Claude CLI musi być zainstalowane (`runtime.claude_binary`, domyślnie `claude` w `PATH`) i zalogowane
lokalnie. Fabryka nie przekazuje klucza API przez środowisko.

## `factory doctor`

Raportuje: walidację konfiguracji i `policy_digest`, `data_dir`, wersję schematu bazy, wynik probe
sandboxa, ścieżkę i wersję `claude`, `factory.lock.json`, obecność binarki OpenSpec, listę skilli,
zarejestrowane repo, informację o poświadczeniach. Exit 4, gdy sandbox nie działa lub brak `claude`.
Doctor nie wykonuje płatnego wywołania modelu — działanie auth potwierdza dopiero live smoke
(`AGENT_FACTORY_LIVE=1 uv run pytest tests/live`).

## Rejestracja repo

```sh
uv run factory init --repo /path/to/repo --mode local [--repo-id app] [--base-branch main] [--checks node]
```

Repo musi mieć `.git` i remote `origin` (worktree powstaje z `origin/<base>` po `git fetch`).
`--mode` musi być równy `policy.mode` w `factory/config.yaml` — zmiana trybu to zmiana konfiguracji
przez operatora. Bez `--base-branch` gałąź bazowa jest ustalana z `origin/HEAD` lub `ls-remote`.
Rejestr: `data_dir/repos.json`.

`--checks <name>` wybiera zaufany manifest `factory/checks/<name>.yaml` dla repo (domyślnie
`default.yaml`, pytest). Nazwa jest zapisywana w kontekście joba przy `submit`, więc job nie zmienia
manifestu w trakcie; digest katalogu `checks/` nadal unieważnia wyniki po edycji. `node.yaml`
(vitest + `tsc` + `eslint --max-warnings=0` + `expo export --platform web`) ma
`link_from_checkout: ["node_modules"]`: operator robi `npm ci` w checkoutcie repo, a każdy worktree
dostaje symlink do tych zależności (checki nie mają sieci). Link musi być ignorowany przez git
(`node_modules` bez końcowego `/`), inaczej job kończy się `BLOCKED_ENV`; brak katalogu w checkoutcie
— też `BLOCKED_ENV`. Narzędzia nie mogą pisać do linkowanego katalogu (sandbox), więc cache trzymaj
w repo (np. `cacheDir` vitest, `--configLoader runner`). Nowa zależność = operator aktualizuje
checkout. Node musi być w `PATH` procesu fabryki (np. `nvm use 24`).

## Zgłoszenie i uruchomienie

```sh
uv run factory submit --file feature.yaml [--repo-id app] [--mode guided|auto]   # albo --text "opis"
uv run factory run --job job-0123456789ab
uv run factory run [--max-jobs N]                            # aktywne joby po kolei; --queue nic nie zmienia
```

Plik YAML (`features.FeatureFile`, nieznane pola odrzucane): wymagane jest tylko
`original_request`; opcjonalne `feature_id`, `repo_id` (musi zgadzać się z wybranym repo), `goal`,
`constraints`, `acceptance_criteria`, `future_directions`. Brak `feature_id` → `FEAT-<8 hex z
hasha treści>`. (`workflow.yaml: feature_input.required_fields` jest walidowane, ale kod go nie
egzekwuje.) Ponowne `submit` identycznej treści zwraca istniejący aktywny job; zmieniona treść tego
samego `feature_id` tworzy nową rewizję i nowy job. `--issue` nie jest obsługiwane.

### Tryb specyfikacji: `guided` albo `auto` (`spec`)

Przy `submit` wybierasz, kto rozstrzyga decyzje produktowe, których zgłoszenie nie przesądza
(limity, domyślne zachowanie, kto co może, co przy błędzie, kierunek wizualny). Bez `--mode` CLI
pyta w terminalu; bez terminala bierze `spec.default_mode` (domyślnie `guided`).

- `guided`: intake zwraca do `spec.max_decisions` (7) decyzji, każdą z opcjami i proponowaną
  odpowiedzią. Job staje w `NEEDS_INFO` przed klasyfikacją i analizą (koszt: tylko intake).
- `auto`: proponowane odpowiedzi są przyjmowane jako założenia (`source: assumed`) i job nie staje.

```sh
uv run factory status --job <id>                    # decisions: id, pytanie, propozycja, odpowiedź
uv run factory decide <id>                          # w terminalu: pytanie po pytaniu (Enter = propozycja, numer = opcja)
uv run factory decide <id> --answer Q-1="tekst" --answer Q-3="..." [--accept-proposed]
```

Po ostatniej odpowiedzi `decide` wznawia job (`factory run --job <id>`). Odpowiedzi trafiają do
`feature.decisions` wszystkich kolejnych etapów: odpowiedź człowieka (`human`, `accepted`) jest
wiążąca jak treść zgłoszenia — spec mapuje ją na wymaganie (`source: decision:Q-n`), a ewaluator
spec blokuje, gdy jej brakuje lub jest sprzeczna. Założenie z trybu `auto` jest tylko domyślne.
Guard `COMPLEXITY_ASSESSMENT` odmawia przejścia, dopóki jakaś decyzja czeka na odpowiedź.

`factory run` kończy się na stanie terminalnym, zatrzymanym albo na `READY_TO_MERGE`, gdy
`merge.enabled: false` (wynik zaakceptowany lokalnie na gałęzi `factory/<job-id>`, bez merge).

## Status i diagnoza

```sh
uv run factory --json status [--job <id>]
uv run factory inspect <id>                       # + ostatnie 50 zdarzeń, findings, wyniki
uv run factory feature classify <feature-id>
uv run factory feature inspect-analysis <feature-id>
uv run factory verify <id>                        # ponowna weryfikacja podpisów i hashy wyników
```

Pola `status`: `state`, `path`, `risk`, `blocked_reason`, `head_sha`, `base_sha`, `analysis_rounds`
(`użyte/limit`), `build_attempts`, `budget {limit, committed, spent}`, `tasks`, `last_checkpoint`
(id artefaktu handoffu), `next_step`, `criteria_digest` (tylko w `AWAITING_HUMAN_MERGE`, inaczej `null`).

| `state` | `next_step` (z `cli._next_step`) | Co zrobić |
|---|---|---|
| `ANALYSIS_LIMIT_REACHED` | inspect-analysis, raise-limit z powodem lub reject | `feature inspect-analysis`, potem `feature raise-limit` i `resume` albo `cancel` |
| `BUDGET_EXHAUSTED` | podnieś budżet z powodem, `resume` | patrz „Budżet” |
| `BLOCKED_ENV` | napraw środowisko z `blocked_reason`, `resume` | np. brak narzędzia, baseline FAIL, brak skilla, run agenta `failed`; `resume` działa tylko, gdy `resume_state` = `BUILD_TASK` (patrz „Wznowienie”) |
| `NEEDS_INFO` | nowa rewizja feature'a | popraw plik i `submit` ponownie; stary job zostaje w `NEEDS_INFO` (anuluj go) |
| `NEEDS_HUMAN` | przeczytaj `blocked_reason`; `resume` lub `cancel` | `guard refused: …` = nieaktualne dowody (patrz „Zmiana i wycofanie zmiany fabryki”) |
| `AWAITING_HUMAN_MERGE` | `approve|reject|rework --revision <digest>` | |
| `READY_TO_MERGE` | merge wyłączony | sprawdź gałąź; włącz `merge.enabled` w konfiguracji, jeśli chcesz merge |

`blocked_reason` to dokładny powód z `StageBlocked`/wyjątku (np. `guard for REVIEW refused: gate result
is stale for head_sha`, `reserving $1.50 for build would exceed ...`).

## Wznowienie

```sh
uv run factory resume <id>
```

Przywraca `resume_state` zapisany przy zatrzymaniu i od razu wywołuje `run`. Liczniki rund, prób i
budżetu nie są resetowane. Przejście musi być legalne (`legal()`), inaczej exit 3; guard stanu
docelowego nie jest sprawdzany przy wznowieniu, tylko przy kolejnym przejściu. Dla stanu
niezatrzymanego komenda nic nie zmienia i tylko uruchamia `run`.

Znane ograniczenie: z `BLOCKED_ENV` legalny jest tylko powrót do `BUILD_TASK`, a z `NEEDS_INFO`
tylko do `INTAKE`. Job zatrzymany w `BLOCKED_ENV` w innym stanie (np. baseline w `SINGLE_TASK_READY`,
gate `ERROR_ENV` w `CHECKS`, brak handlera w `MERGING`) nie da się wznowić przez `resume`
(`cannot resume BLOCKED_ENV -> …`) — pozostaje `cancel` i ponowne `submit`. Szczegóły w
`state-machine.md`.

## Limit rund analizy

```sh
uv run factory feature raise-limit <feature-id> --analysis-rounds 5 --reason "..."
uv run factory resume <job-id>
```

Nowy limit musi być wyższy od obecnego; powód jest wymagany; zdarzenie `analysis.limit_raised`.

## Budżet

```sh
uv run factory budget raise <job-id> --to 40 --reason "powód podniesienia"
uv run factory resume <job-id>
```

`budgets.raise_budget`: nowy limit musi być wyższy od obecnego, powód jest wymagany, aktor = `$USER`,
zdarzenie `budget.raised`. Agent nie może podnieść limitu.

## Anulowanie

```sh
uv run factory cancel <id>
```

`CANCELLING` → zatrzymanie żywych procesów agentów (SIGTERM, potem SIGKILL grupy) → `CANCELLED`,
`active=0`. Worktree zostaje (usuń przez `cleanup`).

## Decyzja człowieka przy merge

Aktywna tylko przy `merge.enabled: true` i `human_interaction` = `before_merge` lub `on_risk`.
`--revision` to `criteria_digest` = digest `{head, base, spec_digest, policy_digest}` bieżącego
kandydata. Pobierz go z `uv run factory --json status --job <id>` (pole `criteria_digest`).

```sh
uv run factory approve <id> --revision <digest>
uv run factory reject  <id> --revision <digest> --reason "..."
uv run factory rework  <id> --file feedback.md --revision <digest>
```

Nieaktualny `--revision` → exit 3. Zgoda, której digest przestał pasować (nowy commit, nowa baza,
zmiana spec lub polityki), jest unieważniana (`approval.invalidated`). Rework przenosi treść pliku do
`human_feedback` buildera i wraca do `BUILD_TASK` ostatniego zadania. Wszystkie trzy komendy wymagają
`--revision`.

## Recovery po awarii

```sh
uv run factory recover --all
```

Dla każdego aktywnego joba (`recovery.recover_job`): weryfikacja podpisów wyników → czy worktree
istnieje i jest na `factory/<job-id>` → żywe procesy agentów zostają do adopcji, martwe oznaczane
`lost` → zwolnienie wygasłych lease martwych właścicieli → rekonsyliacja outboxu (`lookup` przed
ponowieniem; potwierdzony merge w `MERGING` → `MERGED`, `active=0` i `_post_merge_cleanup`) →
zwolnienie osieroconych rezerwacji budżetu. Raport `actions`/`problems` w JSON. Potem
`factory run --job <id>`.

## Cleanup

```sh
uv run factory cleanup <id> --dry-run
uv run factory cleanup <id>
```

`git worktree remove` bez `--force`; worktree z niezacommitowanymi zmianami jest zachowany (błąd).
Gałąź `factory/<job-id>` usuwana tylko przez `git branch -d` i tylko gdy job jest `MERGED`. Po
udanym merge cleanup wykonuje się automatycznie (`_post_merge_cleanup`); błąd cleanup daje zdarzenie
`cleanup.warning`, stan zostaje `MERGED`.

## Eval fabryki

```sh
uv run factory eval --suite tests/evals/dev.yaml --profile full|single-agent
```

`evals.run_suite`: każde zadanie z pliku suite (`tests/evals/dev.yaml`: 20 zadań w kategoriach
simple-fix, regression, crud, api, ui, auth, migration, incomplete) dostaje świeżą kopię fixture
(`tests/fixtures/repos/greeter`) z lokalnym bare remote, osobny `state.db` i `data_dir` w
`data_dir/evals/<suite>-<profile>-<id>/`. `full` uruchamia całą fabrykę, `single-agent` jedno
wywołanie `factory-implement` bez analizy i review. Sukces rozstrzygają golden testy z pliku suite,
dokładane do kopii scratch finalnego drzewa (bez `.git`) i uruchamiane przez `TrustedRunner` w
sandboxie. Raport (`report.json`): `success`, `false_accepts`, `false_rejects`, koszt, mediana czasu,
per zadanie: stan końcowy, wynik golden, koszt, próby, rundy. Przy n < 30 raport oznacza wynik jako
pilot. Komenda wykonuje prawdziwe, płatne wywołania Claude. Wyniki: `acceptance-report.md`.

## Dashboard

```sh
uv run factory dashboard [--host 127.0.0.1] [--port 8765]
```

Tylko odczyt, tylko localhost. Lista jobów i strona joba (powód blokady, zadania, findings, zdarzenia).

## Backup i restore

Zatrzymaj `factory run` przed backupem albo użyj API backupu SQLite (bezpieczne przy WAL):

```sh
D=~/.local/share/agent-factory
sqlite3 "$D/state.db" ".backup '$BACKUP/state.db'"
# albo przez API Pythona (to samo, co Store.conn.backup):
# uv run python -c "import sqlite3; sqlite3.connect('$D/state.db').backup(sqlite3.connect('$BACKUP/state.db'))"
rsync -a "$D/artifacts/" "$BACKUP/artifacts/"
cp -p "$D/keys/evidence.key" "$BACKUP/keys/"   # bez klucza żaden wynik nie przejdzie weryfikacji
cp -p "$D/repos.json" "$BACKUP/"
```

Klucz `evidence.key` jest sekretem — przechowuj backup z prawami `0600`. Restore: zatrzymaj
kontroler, odtwórz `state.db` (usuń stare `-wal`/`-shm`), `artifacts/`, `keys/`, `repos.json`, potem
`factory recover --all` i `factory verify <id>` dla aktywnych jobów. Worktree (`data_dir/worktrees`)
to dane Git — odtwarzaj je z gałęzi `factory/<job-id>` w repo docelowym lub z kopii katalogu; recover
zgłosi brakujący worktree jako problem.

## Retencja

Brak automatycznej retencji. Artefakty, logi agentów (`agent-logs/`), scratch runnera (`runs/`) i
zdarzenia rosną bez limitu. Logi mogą zawierać dane z repo — czyść je ręcznie po zakończeniu jobów
(artefakty referencjonowane przez `results` usuwaj tylko razem z wierszami, inaczej `verify` zgłosi błąd).

## Zmiana i wycofanie zmiany fabryki

Zmiana kontrolera, polityki, checków lub skilli to osobna zmiana w repo fabryki (testy + review), nie
część joba aplikacyjnego. Wycofanie: `git revert <commit>` w repo fabryki i `uv run pytest`.

Skutki dla trwających jobów:

- `policy_digest` = digest treści `config.yaml` + `models.yaml` + `workflow.yaml` + `selection.yaml`. Każda zmiana
  (także modelu roli) czyni nieaktualnymi wszystkie wyniki zależne od `policy_digest` (czyli
  praktycznie wszystkie). Revert przywraca dokładnie poprzedni digest, więc wyniki sprzed zmiany
  znów są aktualne, a wyniki wyprodukowane w czasie wadliwej konfiguracji stają się nieaktualne.
- Zmiana `factory/checks/` lub `factory/skills/` zmienia `checks_digest` (liczony na żywo przez
  `Controller.checks_digest()`), więc unieważnia wyniki `gate`, review, akceptacji i integracji. Job w
  `REVIEW` z nieaktualnym gate wraca do `CHECKS` i ponawia checki bez rebuildu. Wyniki analizy
  (spec, dekompozycja) od `checks_digest` nie zależą.
- `factory/prompts/` i `environment_digest` (zamrożony przy `INTAKE`) nie unieważniają niczego.
- Gdy guard odrzuci przejście z powodu nieaktualnych dowodów, job przechodzi do `NEEDS_HUMAN` z
  `blocked_reason = "guard refused: …"`. Dla wyników analizy po zmianie polityki nie ma ścieżki
  ponownej ewaluacji (`resume` powtórzy odmowę) — obejście: `cancel` i ponowne `submit`.

## Szacunek kosztu planu i akceptacja (`budget.plan_approval`)

Po zaakceptowaniu planu (lub micro-spec) kontroler liczy szacunek (`estimate.py`): na task
`rundy × (builder + recenzenci [+ ui-review dla plików UI])` plus weryfikacja feature'a, z historii
fabryki (średni koszt wywołania na rolę, średnia liczba prób na zaakceptowany task; ≥ 5 próbek),
inaczej z wartości domyślnych. Backtest na LearnEnglish: −20%…+25% względem faktycznego kosztu.
Job zatrzymuje się w `NEEDS_HUMAN` z kwotą, podstawą i sugerowanym limitem (zapas 25% + rezerwa na
weryfikację), chyba że szacunek ≤ `auto_approve_below_usd`. Guard `BUILD_TASK` wymaga akceptacji
dla bieżącego `plan_digest` — nowy plan wymaga nowej akceptacji.

```sh
uv run factory --json status --job <id>            # pole plan_estimate
uv run factory plan approve <id> [--budget 60]     # ustawia limit joba (domyślnie sugerowany)
uv run factory resume <id>
```

## Task, który wyczerpał próby

Task przechodzi w `FAILED` sam: jego odrzucone próby są cofane z gałęzi joba (łatka jako artefakt),
niezależne taski budują się dalej. Gdy nic nie zostało do zbudowania, job czeka w `NEEDS_HUMAN`
z listą nieudanych, zablokowanych i zaakceptowanych tasków.

```sh
uv run factory inspect <id>                                        # findings taska
uv run factory task retry <id> TASK-04 --attempts 1 --reason "..."  # więcej prób (1-3)
uv run factory resume <id>
```

## Koszt recenzji (`review`)

Recenzje tasków idą poziomami (`review.tiers`): CHANGES w pierwszym poziomie (code, test adequacy,
UI) pomija drogie product-verify i security-review w tej próbie; uruchomią się, gdy poziom przejdzie.
Taski jobów o ryzyku z `cheaper_models_for_risk` (domyślnie `low`) mają tańszych recenzentów
(`cheaper_models`); akceptacja feature'a i integracja zawsze używają pełnych powiązań ról.

## Zasady inżynierskie i kontrole jakości (`_shared/engineering-principles.md`)

Konkretne reguły zamiast haseł SOLID/DRY/KISS: każda ma ID (`EP-1`…`EP-14`) i jest oznaczona
jako *gate* (sprawdza narzędzie) albo *review* (ocenia code reviewer). Builder, code reviewer i
planer mają je w prompcie (`Follow ../_shared/engineering-principles.md` w `SKILL.md`). Kontroler
dołącza do promptu każdy plik `_shared`, który skill wskazuje, oraz własne `references/*.md`
skilla: agent nie czyta `factory/`, więc inaczej checklisty by do niego nie dotarły.

- **Gate (profil Node, `checks/node.yaml`)**: lint z limitami (złożoność ≤ 10, zagnieżdżenie ≤ 3,
  ≤ 4 parametry, funkcja ≤ 60 linii, plik ≤ 300 linii), granice warstw (`no-restricted-imports`),
  `@typescript-eslint/no-deprecated` oraz `duplicates`: jscpd z baseline, nowy klon ≥ 50 tokenów
  oblewa gate. Konfiguracja leży w repo aplikacji (`eslint.config.*`, `.jscpd.json`,
  `.jscpd-baseline.json`) i jest chroniona (`policy.protected_paths`): builder jej nie zmieni.
- **Baseline**: kod sprzed limitów jest na jawnej liście (blok „Legacy baseline” w ESLint, klony w
  `.jscpd-baseline.json`). Baseline tylko maleje; po refaktorze operator zawęża go ręcznie
  (`npx jscpd --config .jscpd.json --baseline .jscpd-baseline.json --update-baseline`).
- **Policy**: wyłączenie kontroli komentarzem (`eslint-disable` całego bloku lub reguły limitu
  czy warstw, `@ts-ignore`, `@ts-nocheck`, `jscpd:ignore`, `noqa: C901`, gołe `noqa`,
  `#[allow(clippy::too_many_lines)]`, `CHECKSTYLE:OFF`) w pliku kodu to naruszenie polityki.
  Celowane wyjątki (`eslint-disable-next-line react-hooks/exhaustive-deps`, `@ts-expect-error`)
  są dozwolone.
- **Review**: code reviewer zgłasza tylko reguły *review* z ID i kosztem; nie powtarza tego, co
  sprawdził gate. Profile Python, Rust i Java (narzędzia dla każdej reguły *gate*) są opisane w
  dokumencie zasad; manifest checków dla nich trzeba dodać przy pierwszym takim repo.

## Przegląd UI w przeglądarce (`browser`, `preview` w manifeście checków)

Gdy manifest checków repo ma `preview` (np. `node.yaml`: `static_dir: dist`, budowane przez check
`web-build`), a `config.yaml` ma `browser`, ui-review i product-verify (recenzja taska i akceptacja
feature'a) dostają środowisko `served` zamiast `local-checkout`:

- kontroler serwuje zbudowany katalog worktree na `127.0.0.1:<port z puli>` (`preview.py`; nieznane
  ścieżki → `index.html` dla SPA, `/x` → `x.html` dla eksportu statycznego); serwer żyje do końca
  `factory run` dla danego joba;
- recenzent dostaje Bash i Playwright CLI (`tools/playwright-cli`, wersja w `factory.lock.json`):
  `open`, `resize`, `snapshot` (drzewo dostępności z refami), `click`/`fill`/`press`, `console`,
  `screenshot`. Działa w sandboxie fabryki: worktree tylko do odczytu, zapis tylko do katalogu
  dowodów; `PLAYWRIGHT_MCP_SANDBOX=false`, bo wewnętrzny sandbox Chrome nie startuje w sandboxie
  fabryki, `ISOLATED=true` (profil w pamięci), `ALLOWED_ORIGINS` = adres podglądu;
- po wywołaniu kontroler zamyka sesję przeglądarki (`close`), a zrzuty z katalogu dowodów zapisuje
  jako artefakty (`screenshots` w wyniku recenzji, zdarzenie `browser.screenshots`).

Bez buildu (brak `dist/index.html`) albo bez programu (`factory doctor` → `browser.present: false`)
recenzja zostaje `local-checkout`. Instalacja: `cd tools/playwright-cli && npm ci`; przeglądarka
z cache Playwright (`playwright-cli install-browser`, jeśli jej brak). Aplikacja w podglądzie działa
bez backendu (serwer API nie jest uruchamiany).

## Ocena UX ze zrzutów i kierunek wizualny (`design`)

ui-review ocenia ekrany ze zrzutów, które sam zrobił (`factory-ui-review/references/ux-heuristics.md`):

- **heurystyki Nielsena** `H1`..`H10`: każda `PASS | FAIL | NOT_APPLICABLE | NOT_RUN` z zrzutami;
- **jakość wizualna**: `hierarchy`, `layout`, `typography`, `color`, `consistency`, `polish`, ocena 1-5
  z rubryki;
- **zgodność z kierunkiem wizualnym** (`design_direction`): porównanie z `docs/DESIGN.md` i obrazami
  wzorcowymi z `docs/design/` (ścieżki w `config.yaml` → `design`).

Kontroler nie wierzy deklaracjom recenzenta i sprawdza wynik ui-review:

- w trybie `served` każdy viewport z `browser.viewports` musi mieć zrzut zapisany w katalogu dowodów
  i wskazany w `viewports[].evidence_ids`. Nazwy w `evidence_ids` muszą być zebranymi plikami.
  Wszystkie 10 heurystyk i 6 ocen wizualnych muszą być wypełnione. Inaczej wynik to `UNKNOWN`
  (zdarzenie `ui_review.inconclusive`), a recenzja powtarza się z `controller_note`, co brakowało.
  Po 3 próbach job przechodzi do `NEEDS_HUMAN`;
- PASS z oceną wizualną poniżej `design.min_visual_score` (domyślnie 3) zamienia się na CHANGES
  z blokującym findingiem `CTRL-visual-<aspekt>`. Zamyka go dopiero kolejna recenzja, nie builder;
- gdy `docs/DESIGN.md` istnieje, `design_direction` musi być `PASS` albo `FAIL`, także bez przeglądarki.

Kierunek wizualny (`_shared/design-direction.md`): gdy feature zmienia ekrany, a repo nie ma
`docs/DESIGN.md`, intake w trybie guided pyta o kierunek: 2-4 konkretne warianty (charakter,
paleta, gęstość, produkt wzorcowy). Specyfikacja dodaje wymaganie zapisania dokumentu (kierunek,
tokeny, layout, komponenty, motion, ton). Plan robi z niego pierwszy task UI, od którego zależą
taski ekranów. Obrazy wzorcowe (makiety, zrzuty zaakceptowanego wyglądu) kładzie się do
`docs/design/`. Recenzent je otwiera i porównuje z nimi zrzuty.
