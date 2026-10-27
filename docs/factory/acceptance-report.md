# Raport odbioru — Agent Factory, wariant A

Data: 2026-10-27 · Blueprint: `AGENT_FACTORY_AUTONOMOUS.md` v3.1-A · Host: macOS (arm64), jeden host, `mode: local`.

Statusy: **PASS** — test uruchomiony i zaliczony; **NOT_RUN** — nie uruchomiono albo nie zaimplementowano (nigdy nie liczone jako PASS).
Poziomy: unit / integration / fault / live / eval.

## 1. Co zbudowano i jak uruchomić

| Element | Gdzie |
|---|---|
| Kontroler (maszyna stanów, guardy, orkiestracja) | `src/agent_factory/controller.py`, `factory.py` |
| Stan (SQLite WAL, migracje, CAS) | `src/agent_factory/state.py` |
| Tożsamość wejść i macierz unieważniania | `src/agent_factory/identities.py` |
| Podpisane dowody (HMAC, content-addressed artefakty) | `src/agent_factory/evidence.py` |
| Sandbox (`sandbox-exec`, fail closed) i zaufany runner | `src/agent_factory/sandbox.py`, `runner.py` |
| Polityka diffu, routing ryzyka, broker skutków | `src/agent_factory/policy.py` |
| Wspólny licznik 3 rund, scoring wariantów, DAG, dzierżawy | `analysis.py`, `decisions.py`, `scheduler.py` |
| Budżet, outbox, recovery, worktree | `budgets.py`, `outbox.py`, `recovery.py`, `workspaces.py` |
| Adapter Claude (rzeczywisty), GitHub (stub-tested), fake (tylko testy) | `src/agent_factory/adapters/` |
| CLI `factory`, dashboard tylko do odczytu, eval | `cli.py`, `dashboard/`, `evals.py` |
| 22 skille, wspólny kontrakt, prompty ról | `factory/skills/`, `factory/prompts/` |
| Schemat OpenSpec `factory-autonomous` | `openspec/schemas/factory-autonomous/` |

```bash
uv sync
uv run pytest                       # testy offline
uv run factory doctor
uv run factory init --repo <path> --mode local
uv run factory submit --text "..."  # albo --file feature.yaml
uv run factory run --job <job-id>
uv run factory --json status --job <job-id>
AGENT_FACTORY_LIVE=1 uv run pytest tests/live   # live smoke (zużywa limit konta Claude)
```

Szczegóły operacyjne: `docs/factory/operations.md`.

## 2. Wersje

| Składnik | Wersja / źródło |
|---|---|
| Python | 3.12.12 (uv), Pydantic 2.14.1, PyYAML 6.0.3, pytest 8.4.2, hypothesis 6 — przypięte w `uv.lock` |
| Claude Code CLI | 2.1.74, tryb `claude -p --output-format stream-json`; uwierzytelnienie: logowanie claude.ai (bez klucza API w środowisku) |
| OpenSpec | `@fission-ai/openspec` 1.15.0, przypięty w `tools/openspec` i `factory.lock.json`; Node v20.20.0 |
| Modele | konfiguracja domyślna ról w `factory/config.yaml` (opus/sonnet/haiku 5.5) to wybór operatora bez benchmarku ról; **live smoke i eval uruchomiono z `claude-haiku-5-5` dla wszystkich ról** |
| Sandbox | `macos-sandbox-exec` (probe: zapis poza ścieżką i odczyt sekretu odrzucone) |
| Polityka | `factory/config.yaml` + `workflow.yaml` + `selection.yaml` → `policy_digest`; manifest checków i skille → `checks_digest` (liczony na żywo) |

## 3. Macierz AF-01–AF-60

Ostatni pełny przebieg offline: **163 passed, 2 skipped** (dwa pominięte to testy live uruchamiane osobno, patrz §4).

| ID | Scenariusz (skrót) | Test | Poziom | Wynik |
|---|---|---|---|---|
| AF-01 | instalacja na świeżym repo | `test_install_manifest.py::test_af01_manifest_files_exist_and_parse`, `test_fresh_install_config_loads_and_doctor_runs` | integration | PASS |
| AF-02 | baseline nie przechodzi | `test_pipeline.py::test_af02_failing_baseline_blocks_env` | integration | PASS |
| AF-03 | spec gubi wymaganie | `test_af03_spec_eval_finds_missing_requirement_before_build` | integration | PASS |
| AF-04 | brak wykonalnej opcji | `test_af04_no_feasible_option_spikes_once_then_fails`, `test_decisions.py::test_af04_no_feasible_option` | integration, unit | PASS |
| AF-05 | zły wybór / dwa wybory | `test_af05_wrong_ai_choice_is_rejected_and_repaired_without_human`, `test_af05_lower_score_choice_rejected`, `test_af05_duplicate_option_ids_rejected` | integration, unit | PASS |
| AF-06 | cykl / brak zależności | `test_af06_af52_cycle_is_fixed_automatically_within_the_limit`, `test_dag.py::test_af06_cycle_and_missing_dependency` | integration, unit | PASS |
| AF-07 | pusty/fikcyjny plik review | `test_af07_empty_or_fake_review_file_gives_no_rights`, `test_af07_placeholder_identity_rejected` | unit | PASS |
| AF-08 | podrobiony PASS | `test_af08_forged_pass_row_is_rejected`, `test_af08_untrusted_producer_cannot_record`, `test_payload_tampering_breaks_signature` | unit, integration | PASS |
| AF-09 | HEAD zmienia się po review | `test_af09_head_change_after_review_invalidates_old_pass` | integration | PASS |
| AF-10 | spec zmienia się po build | `test_af10_spec_change_after_build_requires_new_evaluation` | integration | PASS |
| AF-11 | „UI”, a diff dodaje auth endpoint | `test_af11_ui_intake_but_auth_endpoint_raises_risk_and_security`, `test_policy.py::test_af11_*` | integration, unit | PASS |
| AF-12 | zero/skip testów | `test_runner_sandbox.py::test_af12_zero_or_skipped_tests_never_pass` | integration | PASS |
| AF-13 | osłabienie testów/configu | `test_af13_weakened_tests_and_config_fail_policy`, `test_policy.py::test_af13_*` (8 wariantów) | integration, unit | PASS |
| AF-14 | znany wadliwy kod | `test_af14_known_defective_code_is_caught_and_reworked` | integration | PASS |
| AF-15 | UI „sukces”, brak zmiany w danych | `test_af15_af59_ui_success_without_db_effect_is_caught_by_feature_acceptance` | integration | PASS |
| AF-16 | cudze rekordy | `test_af16_cross_user_leak_is_rejected` | integration | PASS |
| AF-17 | złośliwe issue/log | `test_af17_malicious_issue_text_grants_nothing` | integration | PASS |
| AF-18 | odczyt sekretu/holdoutu/innego joba | `test_af18_builder_cannot_read_keys_holdout_or_other_job`, `test_af18_cannot_write_outside_worktree_or_into_controller`, `test_af18_no_outbound_network_and_no_secrets_in_env` — prawdziwe próby w `sandbox-exec` | integration | PASS |
| AF-19 | dwa joby równocześnie | `test_workspaces.py::test_af19_two_jobs_get_separate_worktrees_branches_and_ports` | integration | PASS |
| AF-20 | dwa kontrolery claimują zadanie | `test_af20_only_one_lease_holder`, `test_live_pid_keeps_expired_lease` | unit | PASS |
| AF-21 | stary worker po przejęciu | `test_af21_stale_epoch_result_rejected` | unit | PASS |
| AF-22 | kill kontrolera w trakcie build | `test_af22_controller_killed_mid_build_adopts_live_builder`, `test_af22_dead_attempt_marked_lost_and_budget_released` | fault | PASS |
| AF-23 | kill po utworzeniu PR | `test_af23_lost_response_after_pr_created_is_reconciled_not_duplicated` (stub GitHub) | fault | PASS |
| AF-24 | timeout operacji zewnętrznej | ten sam test: stan `unknown` i lookup przed retry | fault | PASS |
| AF-25 | zawieszony gate/dev server | `test_af25_hanging_process_group_is_killed_at_deadline`, `test_orphaned_children_die_when_check_exits` | integration | PASS |
| AF-26 | 429/5xx, brak ceny | `test_af26_transient_provider_failures_retry_without_corrupting_state`, `test_af26_persistent_provider_failure_blocks_env`, `test_af26_unknown_cost_charges_full_reservation` | fault, unit | PASS |
| AF-27 | trzy identyczne porażki | `test_af27_repeated_identical_failure_triggers_root_cause_then_stops` | integration | PASS |
| AF-28 | budżet kończy się w trakcie | `test_af28_reservations_stop_new_parallel_starts`, `test_af28_budget_exhaustion_keeps_checkpoint` | unit, fault | PASS |
| AF-29 | timeout jednego reviewera | `test_af29_one_reviewer_unknown_blocks_acceptance` | integration | PASS |
| AF-30 | równoległe zmiany semantyki | `test_af30_base_moved_after_candidate_rebuilds_candidate` | integration | PASS |
| AF-31 | baza rusza podczas testów | `test_af31_base_moves_during_merge_is_rejected_atomically`, `test_workspaces.py::test_af31_push_rejected_when_base_moved` | integration | PASS |
| AF-32 | CI pending/failing; approval | `test_af32_pending_or_failing_ci_blocks_merge` (stub), `test_af45_*`, `test_af48_af42_*` | fault, integration | PASS |
| AF-33 | brak sandboxa | `test_af33_no_sandbox_fails_closed`, `test_af33_non_enforcing_sandbox_fails_probe` | integration | PASS |
| AF-34 | cleanup na niezatwierdzonych plikach | `test_af34_cleanup_preserves_uncommitted_work`, `test_cleanup_dry_run_and_never_force` | integration | PASS |
| AF-35 | merge OK, cleanup zawiódł | `test_af35_merge_ok_cleanup_fails_stays_merged` | integration | PASS |
| AF-36 | archiwizacja spec zmienia tree | — krok archiwizacji OpenSpec nie jest zaimplementowany | — | **NOT_RUN** |
| AF-37 | restart odtwarza handoff | `test_af37_restart_preserves_identity_and_budget` | fault | PASS |
| AF-38 | złośliwy Markdown | `test_af38_markdown_and_html_are_not_executed`, `test_af38_dashboard_routes_are_whitelisted_and_read_only` | unit | PASS |
| AF-39 | realny runtime, mały task | `tests/live/test_live_claude.py` (§4) | live | PASS |
| AF-40 | komentarz/deploy w trybie local | `test_af40_local_mode_broker_denies_comments_and_deploy`, `test_af40_local_mode_comment_denied_and_audited` | unit, fault | PASS |
| AF-41 | zwykły job bez ręcznej zgody na spec | `test_af41_standard_path_builds_after_ai_evaluation_without_human` | integration | PASS |
| AF-42 | merge enabled + `off` | `test_af48_af42_trivial_fast_path_merges_automatically` | integration | PASS |
| AF-43 | `before_merge` | `test_af43_before_merge_single_human_stage`, `test_af43_rework_and_reject` | integration | PASS |
| AF-44 | `on_risk` | `test_af44_on_risk_low_merges_high_waits`, `test_af44_on_risk_high_waits` | integration | PASS |
| AF-45 | restart podczas oczekiwania | `test_af45_restart_while_waiting_never_implies_approval` | integration | PASS |
| AF-46 | remis / niepewny fakt | `test_af46_tie_break_is_deterministic` | unit | PASS |
| AF-47 | feature z pliku/CLI/issue | `test_af47_feature_normalisation_keeps_original`, `test_cli.py::test_cli_flow`; issue: tylko `test_issue_intake_takes_only_data_fields` (stub) | unit, integration | PASS (plik/CLI); issue live — NOT_RUN |
| AF-48 | bardzo prosta poprawka | `test_af48_af42_*`, `test_af48_label_fix_is_trivial` | integration, unit | PASS |
| AF-49 | prosta technicznie zmiana auth | `test_af49_one_line_auth_change_is_not_fast_path` | unit | PASS |
| AF-50 | feature complex | `test_af50_complex_feature_gets_contracts_and_reviewed_decomposition` | integration | PASS |
| AF-51 | plan pomija wymaganie / dubluje | `test_af51_plan_missing_requirement_is_caught`, `test_dag.py::test_af51_*` | integration, unit | PASS |
| AF-52 | liść za duży / cykl | `test_af06_af52_*`, `test_dag.py::test_af52_oversized_leaf_and_depth` | integration, unit | PASS |
| AF-53 | przyszła potrzeba hipotetyczna | `test_af53_hypothetical_need_is_recorded_not_built` | integration | PASS |
| AF-54 | spec PASS w rundzie 1 | `test_af41_*` (asercja: 1 runda) | integration | PASS |
| AF-55 | trzecia runda nadal CHANGES | `test_af55_third_failed_round_stops_without_build_or_fourth_round` | integration | PASS |
| AF-56 | spec i podział naprzemiennie | `test_af56_spec_and_decomposition_share_one_counter` | integration | PASS |
| AF-57 | restart / zmiana modelu / podtask | `test_af57_restart_and_model_switch_do_not_reset_rounds`, `test_round_counter_is_shared_and_bounded` | integration, unit | PASS |
| AF-58 | ukryta złożoność w diffie | `test_af58_hidden_complexity_reclassifies_fast_path_keeping_counters` | integration | PASS |
| AF-59 | taski zielone, feature nie działa | `test_af15_af59_*` | integration | PASS |
| AF-60 | przekroczony cap/budżet | `test_af60_task_cap_blocks_without_scope_reduction`, `test_af28_budget_exhaustion_keeps_checkpoint` | integration, fault | PASS |

Dodatkowe wymagania §17: property-based test maszyny stanów (`test_random_walks_never_reach_merged_without_merging`, hypothesis), migracja schematu (`test_schema_migration_from_v1`), backup/restore bazy (`test_backup_and_restore_roundtrip`) — PASS. Fault injection na granicach każdej operacji outbox — częściowo: create_pr, comment, merge (stub), local.merge; brak pełnej macierzy dla każdej operacji.

Testy offline używają skryptowanego `FakeRuntime` dla odpowiedzi modelu. Rzeczywiste są: git, lokalny bare remote, sandbox, runner, SQLite i pliki. Te testy dowodzą zachowania kontrolera, nie jakości modelu.

## 4. Live smoke (gate B4, AF-39)

Przebieg na finalnym kodzie, 2026-10-27, wszystkie role `claude-haiku-5-5`, fixture `tests/fixtures/repos/greeter`, lokalny bare remote, `mode: local`, `merge.enabled: false`.

| Test | Wynik | Dowód |
|---|---|---|
| `test_live_adapter_structured_call_and_cancel` | PASS | structured output zgodny z kontraktem; usage 12 865 in / 305 out; szacowany koszt $0.016; cancel zatrzymał grupę procesów (`process group terminated`, proces nie istnieje) |
| `test_live_af39_small_task_end_to_end` | PASS | `READY_TO_MERGE` w 288 s; 16 uruchomień agenta, każde z `runtime_run_id` i `session_id`; gate z zaufanego runnera w sandboxie; szacowany koszt $3.33 |

Ścieżka stanów: `INTAKE → COMPLEXITY_ASSESSMENT → MICRO_SPEC → AI_SPEC_EVALUATION → SINGLE_TASK_READY → BUILD_TASK → CHECKS → REVIEW → BUILD_TASK → CHECKS → REVIEW → TASK_ACCEPTED → FEATURE_ACCEPTANCE → INTEGRATION → READY_TO_MERGE`.

Koszty to `total_cost_usd` raportowane przez CLI (szacunek po cenach API). Przy logowaniu claude.ai wywołania zużywają limit planu.

Wcześniejsze przebiegi live, które nie przeszły, wykryły realne błędy fabryki. Wszystkie naprawiono, a każdy ma test regresji:
1. Kontrakt wejść skilli: skille oczekiwały plików, a kontroler przekazuje dane w `<inputs>`. Dodano sekcje „How inputs/outputs are delivered” w `_shared/contract.md`.
2. Reviewer mógł zapisywać w worktree, gdy `data_dir` leżał pod `/tmp`. Dodano `denied_write` dla ról read-only i wymóg czystego worktree po review.
3. Kontroler commitował `__pycache__/*.pyc` z uruchomień buildera. Skutkiem była fałszywa reklasyfikacja i odrzucenia „poza zakresem”. Pliki generowane są teraz wykluczone (`GENERATED_EXCLUDES`).
4. Upsert findings nie aktualizował `blocking`, gdy reviewer obniżył wagę uwagi. Job utykał mimo samych PASS.

Jedna obserwacja z live ma wartość merytoryczną. W jednym z przebiegów ewaluator spec AI znalazł kontrprzykład: implementacja zwracająca na sztywno `'Hello, Ada!'` przechodziła kryteria. Wymusił kryterium z drugim imieniem przed kodowaniem.

## 5. Eval i ablacja (gate B8)

Zestaw: `tests/evals/dev.yaml` (20 zadań, 8 kategorii). Golden tests są ukryte przed builderem: kontroler dodaje je do kopii finalnego drzewa, a uruchamia zaufany runner w sandboxie. Pilotaż: zadania E05-notes-delete (crud) i E11-api-isolation (auth), wszystkie role `claude-haiku-5-5`, po 1 próbie na zadanie i profil, 2026-10-27. Pilotaż powtórzono 5 razy; między przebiegami poprawiano fabrykę na podstawie śladów z poprzedniego.

| Przebieg | single-agent (golden) | full: golden | full: zaakceptowane przez fabrykę | full: fałszywe akceptacje | full: koszt (szac.) | full: czas / zadanie |
|---|---|---|---|---|---|---|
| 1 | 2/2, $0.52 | 0/2 | 0/2 | 0 | $9.00 | 413–495 s |
| 2 | 2/2, $0.63 | 1/2 | 0/2 | 0 | $12.23 (limit $8/job) | 40–53 min* |
| 3 | 2/2, $0.61 | 0/2 | 0/2 | 0 | $6.82 (limit $8/job) | 3–21 min* |
| 4 | 2/2, $0.51 | **2/2** | 0/2 | 0 | $28.65 | 44–173 min* |
| 5 | 2/2, $0.50 | **2/2** | 0/2 | 0 | $13.45 | **8–11 min** |

\* Czasy z gwiazdką zawyża usypianie się Maca w nocy (log `pmset`): zamrożony proces CLI tracił połączenie i kończył się dopiero po limicie. Przebieg 5 uruchomiono pod `caffeinate -s` i nie miał zawieszeń.

Co poprawiono między przebiegami (każda poprawka ma test regresji w `tests/integration/test_regressions.py` lub `tests/unit/test_claude_adapter_stall.py`):

| Po przebiegu | Objaw w śladzie | Poprawka |
|---|---|---|
| 1 | spec-author BLOCKED („nie mogę uruchomić walidatora”); planner dostał dwa różne `selected_option_id` | reguły werdyktu spec-author; jedna spójna decyzja przekazywana dalej |
| 1 | ~1/3 wywołań analitycznych powtarzanych, bo `payload` miał dodatkowe/źle typowane pola | CLI dostaje dokładny schemat `payload` danego skilla (`envelope_json_schema(kind)`); zero napraw formatu w przebiegach 2–5 |
| 1 | każda runda CHANGES ze specyfikacji od nowa szkicowała i wybierała warianty; ewaluator dokładał nowe przypadki brzegowe, 3 rundy bez buildu | CHANGES przy wykonalnym podejściu poprawia tylko spec (`AI_SPEC_EVALUATION → OPTION_SELECTED`); blokują tylko braki względem jawnej prośby lub kontroli wymaganych ryzykiem, reszta idzie do buildera jako `spec_advisories`; od przebiegu 2 spec przechodzi w 1. rundzie |
| 2 | CHANGES z przeglądu dekompozycji wracał do wariantów i specyfikacji; wywołania wisiały do limitu 900 s | nowy plan bez ruszania specyfikacji (`AI_DECOMPOSITION_REVIEW → DECOMPOSE`); watchdog bezczynności strumienia (`agent_stall_seconds`) i odzysk wyniku już zatwierdzonego przez CLI |
| 3 | planner NEEDS_INFO, bo nie dostał wyniku oceny specyfikacji | `spec_evaluation` w wejściach plannera |
| 4 | E11 rozbity na 7 tasków (taski samych testów, pusty task E2E); builder pomijał testy, bo `file_scope` nie obejmował `tests/` | najmniej tasków w limicie czasu; każdy task ma własne testy; pusty `file_scope` to problem planu; E2E robi etap FEATURE_ACCEPTANCE |
| 5 | integracja i akceptacja zwracały BLOCKED „brak danych” (mapa akceptacji, `candidate_tree_sha`, lista checków, „środowisko”), a kontroler robił z tego rebuild aż do wyczerpania prób; security-review blokował na modelu zaufania zadanym przez samą prośbę | wspólny `_verification_context` (kryteria, wymagane checki, drzewo, środowisko `local-checkout`); BLOCKED/UNKNOWN ewaluatora: jedna powtórka, potem NEEDS_HUMAN, bez rebuildu; security-review kieruje spór o model zaufania do analizy (`error_class: spec`) |

**Poprawek po przebiegu 5 nie zweryfikowano live.** Przebieg 6 nie był uruchomiony.

Wnioski:
- W przebiegach 4 i 5 kod wytworzony przez fabrykę przechodzi ukryte golden tests w obu zadaniach (2/2), tak jak u pojedynczego agenta. Fabryka nadal nie akceptuje go sama: 2 fałszywe odrzucenia, 0 fałszywych akceptacji we wszystkich przebiegach.
- Fabryka jest 25–30× droższa i ~25× wolniejsza od pojedynczego agenta na tych zadaniach (przebieg 5: $6–7.5 i 8–11 min na zadanie wobec $0.25 i 25 s).
- Pozostałe odrzucenia w przebiegu 5 wynikały z braków w kontrakcie wejść ewaluatorów, nie z jakości kodu. Poprawiono je, ale nie zmierzono.
- n=2, po jednej próbie. To pilotaż, nie statystyka. Te dwa zadania są małe i sekwencyjne, a w takich zadaniach koordynacja wielu ról nie musi się opłacać (S9). Uczciwe porównanie wymaga zadań, w których dekompozycja i niezależna ocena mają znaczenie, oraz kilku prób na zadanie.

Ablacji pojedynczego etapu nie przeprowadzono. Status: **NOT_RUN**. Współbieżność: zmierzony profil to 1 job. 2 i 3 joby nie były mierzone (**NOT_RUN**).

## 6. Uprawnienia, limity i merge

- Tryb `policy.mode: local`. Broker odmawia komentarzy, PR, merge GitHub i deployu (`authorize_side_effect`), a odmowa trafia do audytu (`broker.denied`).
- `merge.enabled: false` (domyślnie): wynik joba to lokalnie zaakceptowany kandydat (`READY_TO_MERGE`). Po włączeniu merge w trybie local kontroler wypycha przetestowanego kandydata na gałąź bazową przez `git push --force-with-lease=refs/heads/<base>:<tested_base_sha>`. Serwer odrzuca aktualizację, jeśli baza się przesunęła, i wtedy powstaje nowy kandydat. Serwerowej merge queue nie używamy.
- `merge.human_interaction: "off"`; obsługiwane `before_merge` i `on_risk`, z decyzją przypiętą do `criteria_digest` kandydata.
- Limity: 3 rundy analizy na feature, 3 próby na task, 30 prób buildu na feature, 2 przebudowy integracji, $25 na job, $1.50 rezerwacji na wywołanie agenta, 25% budżetu zarezerwowane na weryfikację.

## 7. Otwarte ograniczenia i co jest potrzebne do ich usunięcia

| Ograniczenie | Co trzeba zrobić |
|---|---|
| AF-36: brak kroku archiwizacji OpenSpec; kontroler nie wywołuje CLI OpenSpec (validate/archive) | dodać etap po akceptacji produktu: `openspec validate --strict`, `openspec archive`, nowy kandydat integracji i ponowne checki |
| Tryby `pr`/`automerge` niepodłączone: brak kroku `github.create_pr`, `submit --issue` nie działa; adapter GitHub testowany tylko na stubie | dołożyć krok PR w orkiestracji i handler w CLI; live smoke na wybranym repo testowym z autoryzacją operatora |
| Profil SBPL startuje od `(allow default)` i odcina konkretne ścieżki; usługi Mach i gniazda Unix nie są blokowane; `agent-logs`/`runs` czytelne między jobami | profil deny-by-default z listą wymaganych usług albo kontener/VM; dopisać te katalogi do odcinanych odczytów |
| Proces agenta (Claude CLI) potrzebuje sieci do API, więc jego narzędzie Bash też ma sieć | broker sieciowy / proxy z allowlistą tylko do API dostawcy |
| `environment_digest` zamrożony przy INTAKE; `prompt_digest`, `model_id` i digesty skilli poza `InputIdentity` | dodać te pola do tożsamości i macierzy unieważniania |
| Nieużywane klucze konfiguracji (`max_active_jobs`, `job_deadline_minutes`, `command_timeout_seconds`, `approval_timeout_action` i inne), `max_turns` nie trafia do CLI, `run --queue` nie planuje, brak komendy PAUSED, `allocate_port` niewywoływany | podłączyć do schedulera i adaptera albo usunąć ze schematu |
| Skille niewywoływane przez kontroler: repo-map, test-design, evidence-audit, recover, retrospective, release-verify; `factory/prompts/` nieładowane | podłączyć do odpowiednich stanów (test-design przed build — test-adequacy dopytywał o `test-plan.md`) |
| Taski wykonywane sekwencyjnie; brak równoległych worktree na task; współbieżność > 1 joba niezmierzona | równoległe gałęzie per task według DAG; pomiar 1/2/3 jobów |
| Release/deploy (§14.1) nie istnieje | osobny byt release z polityką i rollbackiem |
| Wykrywanie osłabienia testów jest heurystyczne (skip/xfail, `assert True`, usunięte testy/asercje) | mutacyjne sprawdzanie adekwatności testów na kandydacie |
| Modele ról nie są wybrane benchmarkiem | eval ról na zestawie dev, potem utrwalenie wyboru w `docs/factory/model-routing.md` |

Bez autoryzacji live nie uruchomiono: GitHub (PR/merge/CI), intake z issue i release. Ich implementacja i testy na stubie istnieją. Nie twierdzę, że działają na prawdziwym GitHubie.
