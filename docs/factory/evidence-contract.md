# Kontrakt dowodów

Źródła: `src/agent_factory/schemas/__init__.py`, `identities.py`, `evidence.py`, `runner.py`.

## 1. Koperta agenta (`AgentEnvelope`)

Każde wywołanie skilla musi zwrócić jedną kopertę JSON. Adapter Claude przekazuje
`AgentEnvelope.model_json_schema()` jako `--json-schema`; gdy CLI zwróci `structured_output`, jest on
używany, w przeciwnym razie `extract_json_object` bierze ostatni blok ```` ```json ```` (albo cały tekst,
jeśli to goły obiekt). Wszystkie modele mają `extra="forbid"`.

| Pole | Typ / ograniczenie |
|---|---|
| `schema_version` | `"1"` |
| `kind` | ≤ 60 znaków; musi równać się nazwie skilla bez prefiksu `factory-` (inaczej `ContractError`) |
| `verdict` | `PASS` \| `CHANGES` \| `BLOCKED` \| `UNKNOWN` |
| `summary` | ≤ 8000 znaków |
| `findings` | lista `Finding` |
| `checked_criteria`, `limitations` | listy tekstów |
| `payload` | obiekt; walidowany modelem z `PAYLOADS` tylko dla `PASS`/`CHANGES` |

Reguła: `PASS` z otwartym blokującym findingiem jest niezgodny z kontraktem (walidator modelu).
Błąd kontraktu → jedna próba naprawy z komunikatem walidatora, potem `NEEDS_HUMAN`.

`Finding`: `id` (`^[A-Za-z0-9_.-]{1,40}$`), `severity` (`low|medium|high|critical`), `blocking`,
`requirement_ids`, `location {path (względna, bez ..), line ≥ 1}`, `problem` (1–4000), `evidence_ids`,
`expected_fix`, `status` (`open|fixed|disputed|accepted-risk`, domyślnie `open`).

### Payloady (`PAYLOADS`)

| `kind` | Model | Kluczowe pola |
|---|---|---|
| `intake` | `IntakePayload` | `goal`, `acceptance_criteria`, `constraints`, `open_questions`, `future_directions` |
| `classify-feature` | `ClassificationPayload` | `dimensions{name: {score 0–2 \| null, evidence}}`, `risk`, `single_atomic_task`, `known_pattern`, `new_trust_boundary`, `forced_complex_reasons` |
| `options-review`, `select-option` | `OptionsPayload` | `options[{option_id, feasible, infeasible_reasons, ratings{goal_fit, correctness, maintainability, delivery_time, cost: 0–5}, estimated_cost_usd, estimated_minutes}]`, `selected_option_id`, `spike_requested` |
| `spec-author` | `SpecPayload` | `requirements[{id REQ-*, text, source}]`, `intent_map[{intent, requirement_ids}]`, `acceptance_criteria[{id, requirement_ids, observable}]`, `micro_spec`, `files{rel_path: content}` |
| `spec-evaluate` | `SpecEvaluationPayload` | `dimensions{name: PASS\|CHANGES}`, `counterexample`, `uncovered_requirements` |
| `plan` | `PlanPayload` | `tasks[TaskSpec]`, `future_needs[{description, status: required_now\|committed_next\|hypothetical}]` |
| `decomposition-review` | `DecompositionPayload` | `requirements_map{REQ: [TASK]}`, `per_task`, `dag_ok` |
| `implement` | `ImplementPayload` | `changed_files`, `tests_added`, `handoff`, `claimed_checks` (deklaracje, nie dowód) |

`TaskSpec`: `task_id` (`TASK-*`), `requirement_ids`, `depends_on`, `inputs`, `outputs`, `file_scope`,
`risk`, `estimated_minutes`, `acceptance`, `verification`, `parallel_safe_with`,
`future_constraints`, `parent`.

Skille review (`code-review`, `security-review`, `ui-review`, `product-verify`, `test-adequacy`,
`integration-review`, `root-cause`) nie mają modelu payloadu; kontroler czyta z `payload` jedynie
`error_class` (`spec`/`decomposition` → powrót do analizy) i `owner_task_id` (akceptacja feature'a).

Kontroler dokłada deterministyczne sprawdzenia, które mogą zmienić `PASS` na `CHANGES`:
`spec_structure_problems` (śledzenie REQ ↔ intencja ↔ kryteria), wymagane wymiary spec
(`workflow.spec_evaluation.mandatory_dimensions`), niepusty `counterexample`, `validate_dag` i mapa
wymagań reviewera dekompozycji.

## 2. Tożsamość wejść (`InputIdentity`)

Model zamrożony, `extra="forbid"`; pola nieaplikowalne to jawne `None`.

| Pole | Jak wypełnia `Controller.identity` |
|---|---|
| `repo_id`, `job_id`, `task_id` | z joba / argumentu |
| `attempt_id` | nie ustawiane |
| `base_sha` | `jobs.base_sha`; dla kandydata integracji (`candidate.sha == HEAD`) — `candidate.base_sha` |
| `head_sha` | `git rev-parse HEAD` worktree |
| `candidate_tree_sha` | `HEAD^{tree}` tylko, gdy HEAD jest kandydatem integracji |
| `feature_revision` | `jobs.scope_revision` |
| `complexity_digest`, `scope_digest`, `spec_digest`, `decision_digest`, `plan_digest` | z `context_json` (digest treści) |
| `decomposition_digest`, `analysis_round_id` | nie ustawiane (runda trafia do `results.analysis_round`) |
| `policy_digest` | `LoadedConfig.policy_digest` (digest `config.yaml` + `workflow.yaml` + `selection.yaml`) |
| `checks_digest` | na żywo: `Controller.checks_digest()` = digest manifestów katalogów `factory/checks/` i `factory/skills/` (`directory_manifest`) |
| `environment_digest` | z kontekstu, zapisany przy `INTAKE` (wersja Pythona + nazwa sandboxa) |
| `acceptance_suite_digest` | digest `[acceptance_criteria spec, ChecksManifest.digest()]` zapisany w kontekście przy autorstwie spec |
| `runtime_version`, `model_id`, `prompt_digest`, `skill_digests`, `grader_version` | nie ustawiane |

`digest()` = SHA-256 kanonicznego JSON (`sort_keys`, bez spacji). `tasks.md` ma digest planu z
wyzerowanymi checkboxami (`tasks_plan_digest`), więc odhaczenie postępu nie unieważnia oceny.
`validate_real()` odrzuca wartości, które nie są 40-znakowym SHA Git / 64-znakowym SHA-256.

## 3. Macierz unieważniania (`RESULT_DEPENDENCIES`)

Wynik jest aktualny, gdy każde pole z jego wiersza równa się bieżącej tożsamości (`stale_fields`).
Skróty: ANALYSIS = `feature_revision, scope_digest, policy_digest`;
CODE = `head_sha, spec_digest, plan_digest, policy_digest, checks_digest`.

| `ResultKind` | Zależy od |
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

`invalidated_kinds(ChangeKind)` daje widok „co unieważnia zmiana”: `scope`, `plan`, `code`, `base`,
`policy`, `formatting-only` (domyślnie unieważnia jak zmiana spec; brak zdarzenia równoważności).
`human-approval` nie jest zapisywany jako wynik — zgoda człowieka jest w tabeli `approvals` i
porównywana przez `criteria_digest` (`{head, base, spec, policy}`).

## 4. Podpis i weryfikacja (`EvidenceStore`)

Zapis (`record_result`):

1. Producent musi być w `TRUSTED_PRODUCERS` = `trusted-runner`, `agent-adapter`, `controller`, `operator`.
2. `identity.validate_real()` musi być puste.
3. Payload → artefakt `result:<kind>` (SHA-256 treści, ścieżka `artifacts/aa/bb/<sha>`).
4. Ciało `{result_id, job_id, task_id, kind, verdict, identity, producer, payload_artifact_id,
   payload_sha256}` (`payload_sha256` = `artifacts.sha256` payloadu) → HMAC-SHA256 kluczem `data_dir/keys/evidence.key` (32 bajty z `os.urandom`, plik `0600`).
5. W tej samej transakcji: wiersz `results`, aktualizacja findings, zdarzenie `result.recorded`.

Odczyt (`_row_to_result`): ponowne obliczenie HMAC (z `payload_sha256` odczytanym z `artifacts`) i
`hmac.compare_digest`; producent zaufany; payload czytany przez `EvidenceStore.read`, który porównuje
SHA-256 pliku z `artifacts.sha256`. Podmiana pliku payloadu albo wiersza `artifacts` unieważnia więc
wynik.
`current_pass` bierze **najnowszy** wiersz danego `kind` (i `task_id`) — starszy PASS nie ratuje
nowszego FAIL. `factory verify <id>` i `recover` weryfikują wszystkie wyniki joba.

Producenci w praktyce: `trusted-runner/gate` (checki), `agent-adapter/<rola>` z `runtime_run_id` i
`model_id` (oceny agentów), `controller/controller` (np. `FAIL_POLICY` z policy gate, PASS
klasyfikacji, „not applicable” dekompozycji na ścieżce trivial).

## 5. Dowód runnera (`CheckEvidence`)

Jeden rekord na komendę z manifestu (`factory/checks/default.yaml`, `ChecksManifest`):

| Pole | Znaczenie |
|---|---|
| `name`, `argv` | nazwa checku i argv po podstawieniu `{python}`, `{report}`, `{worktree}` |
| `cwd`, `env_keys`, `sandbox` | worktree, nazwy zmiennych środowiska (bez wartości), nazwa sandboxa |
| `started_at`, `ended_at` | czasy epoch |
| `exit_code`, `signal`, `timed_out` | wynik procesu; timeout zabija grupę procesów (SIGTERM, po 3 s SIGKILL) |
| `stdout_artifact`, `stderr_artifact`, `stdout_sha256`, `stderr_sha256` | pełne logi (do 2 MB) jako artefakty i ich digest |
| `excerpt` | końcówka stdout+stderr (~4 KB) do kontekstu agentów |
| `tests` | `{collected, failed, errors, skipped}` z junit (dla `report: junit`) |
| `verdict`, `reason` | `GateVerdict` i powód |
| `head_sha`, `tree_sha` | snapshot, na którym uruchomiono |

Klasyfikacja (`_classify`): timeout → `TIMEOUT`; sygnał → `CANCELLED` (TERM/INT) lub `UNKNOWN`; exit 127
/ „command not found” / „No module named” → `ERROR_ENV`; junit: brak raportu → `ERROR_ENV`, 0 lub mniej
niż `min_tests` wykonanych → `FAIL_POLICY`, porażki/błędy/exit ≠ 0 → `FAIL_CODE`; bez raportu exit ≠ 0
→ `FAIL_CODE`. Brak narzędzia w `PATH` przed startem → `ERROR_ENV`.

Bramka (`run_gate`): brak wymaganych checków → `FAIL_POLICY`; inaczej pierwszy występujący werdykt
w kolejności `ERROR_ENV > TIMEOUT > FAIL_POLICY > FAIL_CODE > CANCELLED > UNKNOWN`, w przeciwnym razie
`PASS`. Etapy: `task` (checki `task`), `acceptance` (`task`+`acceptance`), `integration` (wszystkie).
Obecny manifest ma jeden check `unit` (pytest, junit, `min_tests: 1`, sieć `localhost`, 600 s).

## 6. Cykl życia findings

- Klucz `finding_key = <job_id>:<task_id lub '-'>:<kind>:<finding.id>`; zapis w tej samej transakcji
  co wynik. Te same identyfikatory w różnych zadaniach nie kolidują.
- Nowy wynik danego `kind` dla danego `task_id` oznacza jako `fixed` wszystkie otwarte findings tego
  `kind`/`task_id`, których evaluator nie zgłosił ponownie (gdy nowy wynik nie ma żadnych findings —
  wszystkie). Zamknięcie potwierdza więc nowe uruchomienie evaluatora, nigdy builder.
- Findings zgłoszone ponownie są upsertowane (status, `result_id`, `updated_at`).
- Guard `TASK_ACCEPTED` wymaga braku otwartych blokujących findings zadania; otwarte findings zadania
  (i bez zadania) trafiają do wejścia buildera.
- Statusy `disputed`/`accepted-risk` może ustawić tylko evaluator w kopercie; brak komendy operatora
  ani assessora sporów (prompt `factory/prompts/decision-assessor.v1.md` nie jest używany przez kod).
