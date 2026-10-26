# Indeks skilli Agent Factory

Źródło: blueprint §10.2 (21 skilli podstawowych) + §14.1 (`factory-release-verify`,
tylko przy włączonym release). Każdy skill: `factory/skills/<name>/SKILL.md`,
przykłady `examples/{pass,fail,blocked}.json`, referencje `references/`.
Wspólny kontrakt wyjścia: `factory/skills/_shared/contract.md` — ostatni blok
```` ```json ```` w końcowej wiadomości agenta, `kind` = nazwa bez prefiksu `factory-`.

| # | Skill | Trigger (kiedy) | Output `kind` | Stan kontrolera | Rola (prompt) |
|---|---|---|---|---|---|
| 1 | `factory-intake` | nowy feature z CLI / pliku / issue | `intake` | `INTAKE` | intake-analyst |
| 2 | `factory-classify-feature` | znormalizowany feature + mapa repo; ponownie po rzeczywistym diffie | `classify-feature` | `COMPLEXITY_ASSESSMENT` (+ reklasyfikacja w `CHECKS`/`REVIEW`) | intake-analyst |
| 3 | `factory-repo-map` | nowe repo lub istotna zmiana bazy | `repo-map` | `INTAKE` / `COMPLEXITY_ASSESSMENT` | intake-analyst |
| 4 | `factory-spec-author` | zamrożony scope; trivial → mikro-spec, standard/complex → OpenSpec | `spec-author` | `MICRO_SPEC` / `ANALYSIS_ROUND` | spec-writer |
| 5 | `factory-options-review` | `options.md` aktualnej rundy (nietrywialny feature) | `options-review` | `AI_OPTION_REVIEW` | option-reviewer |
| 6 | `factory-select-option` | opcje + review aktualnej rundy | `select-option` | `OPTION_SELECTED` | option-reviewer |
| 7 | `factory-spec-evaluate` | aktualna spec lub mikro-spec | `spec-evaluate` | `AI_SPEC_EVALUATION` | spec-evaluator |
| 8 | `factory-plan` | wybrana opcja + spec z aktualnym PASS | `plan` | `DECOMPOSE` | planner |
| 9 | `factory-test-design` | spec z aktualnym PASS i ryzyko | `test-design` | `DECOMPOSE` (artefakt `test-plan`) | test-designer |
| 10 | `factory-decomposition-review` | DAG nietrywialnego feature'a | `decomposition-review` | `AI_DECOMPOSITION_REVIEW` | decomposition-reviewer |
| 11 | `factory-implement` | gotowy task (zależności zaakceptowane) | `implement` | `BUILD_TASK` | builder |
| 12 | `factory-root-cause` | ten sam fingerprint błędu w dwóch kolejnych próbach | `root-cause` | `BUILD_TASK` (po powtórnym FAIL `CHECKS`/`REVIEW`) | builder (nowa sesja) |
| 13 | `factory-code-review` | checki PASS dla bieżącego HEAD | `code-review` | `REVIEW` | code-reviewer |
| 14 | `factory-security-review` | każdy task (baseline); pełne przy ryzyku / granicy zaufania | `security-review` | `REVIEW` | security-reviewer |
| 15 | `factory-test-adequacy` | diff dodaje lub zmienia testy | `test-adequacy` | `REVIEW` | test-designer |
| 16 | `factory-ui-review` | zmiana widoczna dla użytkownika | `ui-review` | `REVIEW` | product-evaluator |
| 17 | `factory-product-verify` | L4 per task; akceptacja całego feature'a (PM / QA) | `product-verify` | `REVIEW`, `FEATURE_ACCEPTANCE` | product-evaluator |
| 18 | `factory-integration-review` | nowy kandydat integracji L5 | `integration-review` | `INTEGRATION` | integration-evaluator |
| 19 | `factory-evidence-audit` | przed rekomendacją merge | `evidence-audit` | `READY_TO_MERGE` | decision-assessor |
| 20 | `factory-recover` | restart, timeout lease, utracony worker, handoff | `recover` | dowolny (przed wznowieniem) | operations-analyst |
| 21 | `factory-retrospective` | `FAILED`, incydent lub koniec cyklu eval | `retrospective` | po stanie terminalnym / eval | operations-analyst |
| 22 | `factory-release-verify` | tylko przy autoryzowanym release (§14.1) | `release-verify` | release: `PENDING` (pre-check), `VERIFYING` | product-evaluator |

## Zasady wspólne

- Brak ręcznego zatwierdzania spec: bramką przed buildem jest aktualny PASS
  `spec-evaluate` oraz (standard/complex) `decomposition-review`, sprawdzony przez kontroler.
- Skille 4–10 dzielą jeden licznik: maks. **3 rundy analizy na feature**
  (pierwsza wliczona), maks. 1 spike; po limicie `ANALYSIS_LIMIT_REACHED`.
- `classify-feature` nie wybiera ścieżki — kontroler sumuje wymiary
  (trivial ≤2 + warunki fast path, standard 3–6, complex ≥7 lub reguła wymuszająca).
  Ryzyko jest osobną osią.
- `select-option`: kontroler przelicza score (0.30/0.25/0.20/0.15/0.10) i tie-break;
  `selected_option_id` musi być jego argmax.
- `implement`: `claimed_checks` to deklaracje, nie dowody; builder nie zamyka findings.
- `PASS` z otwartym blocking finding lub z pustym `checked_criteria` jest odrzucany.
- Operacje `checks`, `merge`, `cleanup`, `lease`, `budget`, `advance` są
  deterministycznym kodem kontrolera, nie skillami.
