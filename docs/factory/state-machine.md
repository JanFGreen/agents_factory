# Maszyna stanów

Źródło: `src/agent_factory/controller.py` (stany, przejścia, guardy) i `src/agent_factory/factory.py`
(kto i kiedy wywołuje przejście). Każde przejście idzie przez `Controller.transition`: sprawdza
`legal(src, dst)`, uruchamia guard stanu docelowego (o ile cel nie jest stanem bocznym), robi CAS
wersji joba i zapisuje zdarzenie `state.transition` z `from_state/to_state/from_version/to_version`.
Odmowa to wyjątek `TransitionRefused` (CLI: exit 3).

## Stany (`class S`)

| Grupa | Stany |
|---|---|
| Główna ścieżka | `FEATURE_RECEIVED`, `INTAKE`, `COMPLEXITY_ASSESSMENT`, `MICRO_SPEC`, `ANALYSIS_ROUND`, `OPTIONS_DRAFT`, `AI_OPTION_REVIEW`, `OPTION_SELECTED`, `AI_SPEC_EVALUATION`, `SINGLE_TASK_READY`, `DECOMPOSE`, `AI_DECOMPOSITION_REVIEW`, `TASK_GRAPH_READY`, `BUILD_TASK`, `CHECKS`, `REVIEW`, `TASK_ACCEPTED`, `FEATURE_ACCEPTANCE`, `INTEGRATION`, `READY_TO_MERGE`, `AWAITING_HUMAN_MERGE`, `MERGING`, `MERGED` |
| Boczne (`SIDE`) | `NEEDS_INFO`, `BLOCKED_ENV`, `NEEDS_HUMAN`, `BUDGET_EXHAUSTED`, `CANCELLING`, `FAILED`, `ANALYSIS_LIMIT_REACHED`, `PAUSED`, `FAILED_NO_FEASIBLE_OPTION` |
| Terminalne (`TERMINAL`) | `MERGED`, `CANCELLED`, `REJECTED`, `FAILED`, `FAILED_NO_FEASIBLE_OPTION` |
| Zatrzymane (`HALTED`) — czekają na operatora | `ANALYSIS_LIMIT_REACHED`, `BUDGET_EXHAUSTED`, `NEEDS_INFO`, `BLOCKED_ENV`, `NEEDS_HUMAN`, `PAUSED`, `AWAITING_HUMAN_MERGE` |

`ANALYSIS_STATES` (`MICRO_SPEC` … `AI_DECOMPOSITION_REVIEW`) jest zdefiniowane, ale nieużywane w logice.
`PAUSED` nie ma komendy CLI, która by go ustawiała.

## Przejścia główne (`_MAIN`)

| Z | Do |
|---|---|
| `FEATURE_RECEIVED` | `INTAKE` |
| `INTAKE` | `COMPLEXITY_ASSESSMENT` |
| `COMPLEXITY_ASSESSMENT` | `MICRO_SPEC`, `ANALYSIS_ROUND` |
| `MICRO_SPEC` | `AI_SPEC_EVALUATION` |
| `ANALYSIS_ROUND` | `OPTIONS_DRAFT` |
| `OPTIONS_DRAFT` | `AI_OPTION_REVIEW` |
| `AI_OPTION_REVIEW` | `OPTIONS_DRAFT` (spike), `OPTION_SELECTED`, `ANALYSIS_ROUND` |
| `OPTION_SELECTED` | `AI_SPEC_EVALUATION` |
| `AI_SPEC_EVALUATION` | `SINGLE_TASK_READY`, `MICRO_SPEC`, `DECOMPOSE`, `ANALYSIS_ROUND` |
| `DECOMPOSE` | `AI_DECOMPOSITION_REVIEW` |
| `AI_DECOMPOSITION_REVIEW` | `TASK_GRAPH_READY`, `ANALYSIS_ROUND` |
| `SINGLE_TASK_READY`, `TASK_GRAPH_READY` | `BUILD_TASK` |
| `BUILD_TASK` | `CHECKS`, `FEATURE_ACCEPTANCE` (fala torów zaakceptowała ostatnie taski) |
| `CHECKS` | `BUILD_TASK`, `REVIEW`, `ANALYSIS_ROUND` (reklasyfikacja) |
| `REVIEW` | `BUILD_TASK`, `TASK_ACCEPTED`, `ANALYSIS_ROUND`, `OPTIONS_DRAFT`, `MICRO_SPEC` (trivial), `CHECKS` (nieaktualny gate) |
| `TASK_ACCEPTED` | `BUILD_TASK`, `FEATURE_ACCEPTANCE` |
| `FEATURE_ACCEPTANCE` | `INTEGRATION`, `BUILD_TASK`, `ANALYSIS_ROUND`, `OPTIONS_DRAFT`, `MICRO_SPEC` (trivial) |
| `INTEGRATION` | `READY_TO_MERGE`, `BUILD_TASK` |
| `READY_TO_MERGE` | `MERGING`, `AWAITING_HUMAN_MERGE`, `INTEGRATION` |
| `AWAITING_HUMAN_MERGE` | `MERGING`, `BUILD_TASK`, `REJECTED`, `INTEGRATION` |
| `MERGING` | `MERGED`, `INTEGRATION` |
| `CANCELLING` | `CANCELLED` |
| `ANALYSIS_LIMIT_REACHED` | `ANALYSIS_ROUND`, `MICRO_SPEC` |
| `BLOCKED_ENV` | `BUILD_TASK` |
| `NEEDS_INFO` | `INTAKE` |

Krawędzie `REVIEW → OPTIONS_DRAFT` i `FEATURE_ACCEPTANCE → OPTIONS_DRAFT` są legalne, ale
`factory.py` ich nie używa (błąd wymagań wraca do `ANALYSIS_ROUND`, nie do `OPTIONS_DRAFT` jak w §4.3).

## Reguły stanów bocznych (`legal()`)

Kolejność sprawdzania:

1. Para w `_MAIN` → legalna.
2. Źródło terminalne lub `CANCELLING` → nielegalne (poza `CANCELLING → CANCELLED` z pkt 1).
3. Cel w `SIDE` → legalne z dowolnego nieterminalnego stanu (stąd każdy stan może przejść do
   `NEEDS_HUMAN`, `BLOCKED_ENV`, `FAILED`, `CANCELLING` itd.).
4. Źródło `BUDGET_EXHAUSTED`, `PAUSED`, `NEEDS_HUMAN` → legalny dowolny cel nieterminalny
   („wznowienie do zapisanego stanu”).
5. W pozostałych przypadkach nielegalne. Np. `REJECTED` jest osiągalny tylko z `AWAITING_HUMAN_MERGE`,
   a `CANCELLED` tylko przez `CANCELLING`.

Guardy nie są uruchamiane dla celów w `SIDE`.

## Guardy i wymagane dowody

`current_pass(kind)` = najnowszy wynik danego rodzaju (dla zadania: z tym `task_id`) ma poprawny podpis
HMAC, zaufanego producenta, werdykt `PASS` i żadne pole z `RESULT_DEPENDENCIES[kind]` nie różni się
od bieżącej tożsamości (`Controller.identity`). Patrz `evidence-contract.md`.

| Stan docelowy | Guard | Wymagane dowody |
|---|---|---|
| `COMPLEXITY_ASSESSMENT` | `_g_decisions_answered` | brak decyzji produktowych bez odpowiedzi (tryb `guided`) |
| `SINGLE_TASK_READY`, `DECOMPOSE` | `_g_spec_pass` | aktualny PASS `spec-evaluation` |
| `TASK_GRAPH_READY` | `_g_decomposition_pass` | aktualny PASS `decomposition-review` + spec PASS |
| `BUILD_TASK` | `_g_build_allowed` | spec PASS; dla ścieżki innej niż `trivial` także decomposition PASS; co najmniej jeden wiersz w `tasks` |
| `REVIEW` | `_g_gate_pass` | aktualny PASS `gate` dla `current_task` (zależy też od `base_sha` i `environment_digest`) |
| `TASK_ACCEPTED` | `_g_reviews_pass` | niepusta lista `required_reviews[current_task]`; aktualny PASS `gate` i każdego wymaganego review; brak otwartych blokujących findings zadania |
| `FEATURE_ACCEPTANCE` | `_g_all_tasks_accepted` | wszystkie zadania w stanie `ACCEPTED` (i co najmniej jedno) |
| `INTEGRATION` | `_g_acceptance_pass` | aktualny PASS `feature-acceptance` |
| `READY_TO_MERGE` | `_g_integration_pass` | aktualny PASS `integration` (zależy od `candidate_tree_sha`) |
| `MERGING` | `_g_merge_allowed` | `merge.enabled: true`; integration PASS; gdy `human_required` — ważna zgoda z `criteria_digest` równym bieżącemu |
| `MERGED` | `_g_merge_confirmed` | `context.merge_result.merged == true` (z outboxu) |

`human_required`: `before_merge` → zawsze; `on_risk` → gdy `jobs.risk` (brak = `high`) jest w
`human_review_risk_levels`; `off` → nigdy. `criteria_digest` = digest `{head, base, spec, policy}`.

Wymagane review (`Factory._route_reviews`): zawsze `code-review`, `security-review`,
`product-verify`, `test-adequacy`; `ui-review` gdy diff dotyka powierzchni `ui`. Głębokość security:
`full` przy ryzyku `high` lub powierzchni `security`, inaczej `baseline` (przekazywane agentowi jako dane).

## Routing błędów (jak zaimplementowano w `factory.py`)

Wyjątki w `Factory.run`: `StageBlocked(state)` → `_halt` do wskazanego stanu bocznego;
`budgets.BudgetExhausted` → `BUDGET_EXHAUSTED`; `analysis.AnalysisLimitReached` →
`ANALYSIS_LIMIT_REACHED`. `_halt` zapisuje `resume_state`, `halted_reason`, `blocked_reason` i checkpoint.
`TransitionRefused` (guard odrzucił przejście, np. dowody stały się nieaktualne po zmianie polityki
lub checków) → `NEEDS_HUMAN` z `blocked_reason = "guard refused: …"`; kontroler nigdy nie forsuje
przejścia przez guard.

| Sytuacja | Gdzie | Dokąd |
|---|---|---|
| Intake `BLOCKED` | `_step_intake` | `NEEDS_INFO` |
| Tryb `guided`: decyzje produktowe bez odpowiedzi | `_step_intake` | `NEEDS_INFO` (`factory decide` zapisuje odpowiedzi i wznawia do `INTAKE` bez ponownego intake) |
| Brak payloadu klasyfikacji / opcji / spec / planu | `_step_*` | `NEEDS_INFO` |
| Brak payloadu wyboru opcji | `_step_ai_option_review` | `NEEDS_HUMAN` |
| Decyzja AI sprzeczna z deterministycznym wyborem | `_step_ai_option_review` | jedna naprawa w tej samej rundzie; potem koniec rundy `CHANGES` → `ANALYSIS_ROUND` (lub limit) |
| Brak wykonalnej opcji / near-tie z prośbą o spike | `_step_ai_option_review` | spike → `OPTIONS_DRAFT` (limit `max_feasibility_spikes_per_feature`); bez spike → `FAILED_NO_FEASIBLE_OPTION` |
| Spec/dekompozycja `CHANGES` (findings AI lub problemy kontrolera) | `_close_round_and_continue` | `MICRO_SPEC` (trivial) / `ANALYSIS_ROUND`; przy wyczerpanym limicie lub `max_consecutive_rounds_without_progress` → `ANALYSIS_LIMIT_REACHED` |
| Spec/dekompozycja `BLOCKED`/`UNKNOWN` bez konkretnych problemów | j.w. | `NEEDS_HUMAN` |
| Baseline nie przechodzi na nietkniętej bazie | `_prepare_build` | `BLOCKED_ENV` |
| Limit prób zadania | `_step_build_task` / `_parallel_wave` | task `FAILED`, jego próby cofnięte z gałęzi joba (łatka jako artefakt); niezależne taski budują się dalej; gdy nic nie zostało do zbudowania → `NEEDS_HUMAN` z listą zablokowanych; operator: `factory task retry <job> <task> --attempts N --reason …` + `factory resume` |
| Limit prób feature'a (`total_feature_build_attempts`) | `_step_build_task` | `FAILED` |
| Brak gotowego zadania (deadlock zależności) | `_step_build_task` | `NEEDS_HUMAN` |
| Builder przepisał historię / naruszenie polityki | `_step_checks` | wynik `gate=FAIL_POLICY`, patch odrzucony jako artefakt, `git reset --hard` do `build_start_sha` → `BUILD_TASK` |
| Cały diff feature'a względem bazy pusty (poza `openspec/`) | `_step_checks` | wynik `gate=FAIL_CODE` → `BUILD_TASK`. Próba bez zmian przy niepustym diffie feature'a nie jest porażką (wcześniejsza praca może już spełniać spec) — idzie do runnera |
| Testy FAIL, `TIMEOUT` | `_step_checks` | `BUILD_TASK` (fingerprint błędu; dwa identyczne z rzędu → `factory-root-cause` przed kolejnym buildem) |
| Gate `ERROR_ENV` / `CANCELLED` / `UNKNOWN` | `_step_checks` | `BLOCKED_ENV` |
| Ukryta złożoność w diffie ścieżki trivial (ryzyko ≠ low lub > 5 plików) | `_needs_reclassification` | ścieżka `standard`, `ANALYSIS_ROUND` (lub limit) |
| Wynik `gate` zadania nieaktualny przy wejściu do `REVIEW` (np. zmiana checków/skilli/polityki) | `_step_review` | `CHECKS` — ponowne checki zamiast rebuildu |
| Review `CHANGES` | `_step_review` | `BUILD_TASK` |
| Review `UNKNOWN`/`BLOCKED` bez `CHANGES` | `_step_review` | pozostaje w `REVIEW` (ponawia tylko nieaktualne review); po 3. próbie `NEEDS_HUMAN` |
| `payload.error_class` ∈ {`spec`, `decomposition`} w review lub akceptacji | `_back_to_analysis` | `ANALYSIS_ROUND` (trivial: `MICRO_SPEC`); brak rund → `ANALYSIS_LIMIT_REACHED` |
| HEAD zmienił się albo worktree nie jest czysty (`git status --porcelain`) po wywołaniu reviewera | `_step_review` | `NEEDS_HUMAN` |
| Akceptacja feature'a FAIL | `_step_feature_acceptance` | zadanie `owner_task_id` (lub ostatnie) → `REWORK`, `BUILD_TASK` |
| Konflikt lub FAIL integracji | `_integration_rebuild` | ostatnie zadanie → `REWORK`, `BUILD_TASK`; po `integration_rebuilds` → `NEEDS_HUMAN` |
| Baza ruszyła się przed/w trakcie merge | `_step_merging` | `INTEGRATION` |
| Merge odrzucony przez politykę zewnętrzną | `_step_merging` | `NEEDS_HUMAN` (`EXTERNAL_POLICY_REQUIRES_REVIEW`) |
| Wynik merge `unknown` | `_step_merging` | `NEEDS_HUMAN` („run `factory recover`”) |
| Agent: JSON niezgodny z kontraktem | `call_agent` | jedna naprawa (`structured_output_repair`), potem `NEEDS_HUMAN` |
| Agent: run `failed`/`unknown` | `call_agent` | 2 ponowienia z jitterem, potem `BLOCKED_ENV` |
| Utrata lease / lease zajęty | `_run_agent_once` | `NEEDS_HUMAN` |
| Guard odrzucił przejście (`TransitionRefused`) | `Factory.run` | `NEEDS_HUMAN` (`guard refused: …`) |
| Brak skilla / bindingu roli | `_skill_text`, `_binding` | `BLOCKED_ENV` |

## Wznowienie (`Factory.resume`)

Działa tylko dla `HALTED` poza `AWAITING_HUMAN_MERGE` (dla innych stanów nic nie robi). Cel:
`resume_state` zapisany przez `_halt` (domyślnie `INTAKE`); dla `ANALYSIS_LIMIT_REACHED` —
`MICRO_SPEC`/`ANALYSIS_ROUND`. Przed zapisem `resume` sprawdza `legal(state, target)` i przy braku
legalności rzuca `TransitionRefused` (CLI: exit 3). Stan jest zapisywany bezpośrednio przez
`cas_update_job` (zdarzenie `state.resumed`), **bez guarda stanu docelowego**. Kontrola dowodów
następuje dopiero przy kolejnym przejściu: krok stanu docelowego jest wykonywany od nowa, a guard
następnego stanu działa normalnie.

Konsekwencja reguł `legal()`: z `BUDGET_EXHAUSTED`, `NEEDS_HUMAN`, `PAUSED` można wznowić do
dowolnego nieterminalnego stanu. Z `BLOCKED_ENV` legalny jest tylko powrót do `BUILD_TASK`, a z
`NEEDS_INFO` tylko do `INTAKE` (patrz znane problemy).

## Znane problemy (z lektury kodu)

- **Wznowienie z `BLOCKED_ENV` i `NEEDS_INFO` jest odrzucane dla większości stanów.** `BLOCKED_ENV`
  powstaje m.in. w `SINGLE_TASK_READY`/`TASK_GRAPH_READY` (baseline FAIL), `CHECKS` (gate `ERROR_ENV`),
  `FEATURE_ACCEPTANCE`, `INTEGRATION`, `MERGING` (brak handlera) i w dowolnym stanie z agentem (run
  `failed`, brak skilla). `resume_state` to stan, w którym nastąpił halt, a w `_MAIN` jest tylko
  `(BLOCKED_ENV, BUILD_TASK)`, więc `factory resume` kończy się `cannot resume BLOCKED_ENV -> <stan>`.
  Analogicznie `NEEDS_INFO` z `COMPLEXITY_ASSESSMENT`, `OPTIONS_DRAFT`, `MICRO_SPEC`, `OPTION_SELECTED`,
  `DECOMPOSE`. Gałąź w `resume` dla `BLOCKED_ENV` (przypisanie `target = resume_state`) nic nie zmienia.
- **Nieaktualne wyniki analizy po zmianie polityki.** Job zatrzymuje się teraz widocznie w
  `NEEDS_HUMAN` (`guard refused: …`), ale nie ma ścieżki ponownej ewaluacji spec/dekompozycji:
  `resume` wraca do stanu, w którym guard odmówił, więc odmowa się powtórzy. Obejście: `cancel` i
  ponowne `submit`.
- Wznowienie `NEEDS_INFO` nie tworzy nowej rewizji; nowa rewizja (`submit`) zakłada nowy job, a stary
  pozostaje aktywny w `NEEDS_INFO`.
