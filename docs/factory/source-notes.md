# Notatki o źródłach (blueprint §2)

Krótko: co z każdego źródła trafiło do kodu. Źródła nie są benchmarkiem tej fabryki.

**S1. Anthropic — Building effective agents (2024).** Przewidywalny workflow z jawnymi granicami
zamiast swobodnego agenta: tabela przejść i guardy (`controller.py`), agent swobodny tylko w ramach
jednego skilla. Evaluator–optimizer jako pętla builder → checks → review (`factory.py::_step_review`).

**S2. Effective context engineering (2025).** Mały pakiet wejścia per wywołanie (`call_agent` przekazuje
tylko potrzebne pola), selektywnie ładowany skill (`_skill_text`: kontrakt + jeden `SKILL.md`),
ograniczone narzędzia na rolę, strukturalny handoff zamiast historii (`Factory._checkpoint`).

**S3. Effective harnesses for long-running agents (2025).** Kontrola środowiska przed pracą (baseline
checków na nietkniętej bazie w `_prepare_build`, `factory doctor`), trwały stan w SQLite i Git,
wznowienie bez resetu liczników (`resume`, `_adopt_running`).

**S4. Harness design for long-running apps (2026).** Rozdział planner / generator / evaluator: osobne
role i świeże wywołania (`SKILL_ROLE`), niezależne review każdego HEAD, akceptacja całego feature'a
(`_step_feature_acceptance`). Liczba etapów jest konfigurowalna, ale ich przydatność nie była mierzona.

**S5. Demystifying evals (2026).** Oceny kodowe (runner, junit, `min_tests`) oddzielone od modelowych
(werdykty w kopercie), pominięty test live = NOT_RUN (`pytest` marker `live`). Eval fabryki
`evals.run_suite`: `full` vs `single-agent`, sukces z ukrytych przed builderem golden testów, liczone
`false_accepts`/`false_rejects`; przy n < 30 raport oznaczony jako pilot.

**S6. Cursor — Scaling long-running autonomous coding (2026).** Jawny właściciel zadania i krótkie
dzierżawy z epoką (`scheduler.acquire_lease`, `check_fence`), DAG zależności (`validate_dag`,
`ready_tasks`), ograniczona równoległość (slot `agent_process`, jeden builder na job).

**S7. StrongDM — Software Factories (2026).** Ochrona ewaluacji przed dopasowaniem: protected paths i
wykrywanie osłabiania testów (`policy.evaluate_diff`), manifest checków poza gałęzią joba, deny dla
`holdout/`. Stuby usług: `StubGitHub`, `fake.py`. Prywatne scenariusze holdout nie są zaimplementowane.

**S8. OpenAI — Harness engineering (2026).** Worktree per job (`workspaces.py`), krótki `AGENTS.md`
jako mapa, zasady egzekwowane narzędziami (policy gate, sandbox), a nie promptem. Per-job runtime
aplikacji (porty, dev server) — tylko `allocate_port`, nieużywany.

**S9. Google Research — Scaling agent systems (2026).** Zadania w jobie domyślnie sekwencyjnie;
równoleglenie tylko rozłącznych zakresów (`parallel_safe_with` bez wspólnych plików w `validate_dag`),
choć wykonanie równoległe nie jest zaimplementowane. Koszt mierzony per wywołanie (`usage`).

**S10. Fission-AI — OpenSpec.** Schemat `openspec/schemas/factory-autonomous`, CLI przypięte
(`factory.lock.json`, 1.15.0), eksport spec/planu do `openspec/changes/<id>/`. Bramki jakości są
kontrolera, nie walidatora formatu. Kontroler nie wywołuje `openspec validate` ani archiwizacji.
