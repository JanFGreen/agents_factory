# Routing modeli

**Przypisania modeli w `factory/config.yaml` to wybór operatora, nie wynik benchmarku.** Blueprint
(§3 pkt 6, §13.2) wymaga małego benchmarku ról. `factory eval` (`evals.py`) porównuje całą fabrykę z
pojedynczym agentem, ale nie porównuje modeli per rola; benchmark ról nie został wykonany. Wyniki
pilota eval są w `acceptance-report.md`. Traktuj poniższe wartości jako punkt startowy.

## Role → model (`runtime.roles`)

Rola skilla wynika z `factory.py::SKILL_ROLE`; brak wpisu roli → binding `default`.

| Rola | Skille | Model | `effort` | Narzędzia |
|---|---|---|---|---|
| `default` | (fallback) | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `intake` | factory-intake, (factory-repo-map) | `claude-haiku-5-5` | — | Read, Glob, Grep |
| `classifier` | factory-classify-feature | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `planner` | factory-options-review, factory-plan | `claude-opus-5-5` | high | Read, Glob, Grep |
| `decision` | factory-select-option | `claude-opus-5-5` | high | Read, Glob, Grep |
| `spec_author` | factory-spec-author | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `spec_evaluator` | factory-spec-evaluate | `claude-opus-5-5` | high | Read, Glob, Grep |
| `decomposition_reviewer` | factory-decomposition-review | `claude-opus-5-5` | high | Read, Glob, Grep |
| `builder` | factory-implement, factory-root-cause | `claude-sonnet-5-5` | high | Read, Edit, Write, Glob, Grep, Bash |
| `code_reviewer` | factory-code-review | `claude-opus-5-5` | medium | Read, Glob, Grep |
| `security_reviewer` | factory-security-review | `claude-opus-5-5` | high | Read, Glob, Grep |
| `ui_reviewer` | factory-ui-review | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `product_evaluator` | factory-product-verify (review zadania i akceptacja feature'a) | `claude-sonnet-5-5` | medium | Read, Glob, Grep, Bash |
| `test_reviewer` | factory-test-adequacy | `claude-sonnet-5-5` | medium | Read, Glob, Grep |
| `integration_evaluator` | factory-integration-review | `claude-opus-5-5` | medium | Read, Glob, Grep |

Szczegóły mapowania na CLI (`adapters/claude.py::_argv`): `--model <model>`, `--effort <effort>`
(jeśli ustawiony), `--tools` i `--allowedTools` z listy. Dla wywołań bez zapisu
(`writable=False`, czyli wszystko poza `factory-implement`) lista jest dodatkowo filtrowana do
`Read, Glob, Grep, Bash` (`factory.py::READ_ONLY_TOOLS + ["Bash"]`), a `factory-root-cause` jest
wywoływany jako read-only mimo roli `builder`. Pole `max_turns` jest przyjmowane w konfiguracji i
przekazywane w `AgentRequest`, ale adapter go **nie** przekazuje do CLI. Timeout każdego wywołania
(także buildera) to `execution.reviewer_timeout_seconds` (900 s).
Niezależnie od tego `execution.agent_stall_seconds` (300 s) przerywa wywołanie, którego strumień nie
wysłał nowego zdarzenia; jeśli CLI zdążyło zatwierdzić wynik narzędziem StructuredOutput, wynik jest
odzyskiwany (koszt nieznany, liczony jako pełna rezerwacja), inaczej kontroler ponawia wywołanie.
Każde wywołanie dostaje schemat koperty z dokładnym schematem `payload` swojego skilla.

Model faktycznie użyty jest odczytywany z eventu `system/init` strumienia i zapisywany w
`attempts.model_id`, `usage.model_id` oraz `producer.model_id` wyniku. Live smoke
(`tests/live/test_live_claude.py`) używa domyślnie `claude-haiku-5-5` (`AGENT_FACTORY_LIVE_MODEL`).

## Ceny i budżet

Fabryka nie trzyma cennika. Koszt pochodzi z dostawcy:

- Przed każdym wywołaniem (także każdym ponowieniem i naprawą JSON) `budgets.reserve` rezerwuje
  `budget.max_cost_per_agent_call` (1,50 USD). Ta sama wartość idzie do CLI jako `--max-budget-usd`
  (cap po stronie CLI).
- Po wywołaniu `budgets.settle` rozlicza `total_cost_usd` z eventu `result`. Tokeny wejścia =
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
- **Nieznany koszt** (brak `total_cost_usd`) jest liczony jako pełna rezerwacja (`usage.cost_known=0`).
- Koszt wyższy od rezerwacji jest księgowany w całości i daje zdarzenie `budget.overrun`.
- Rezerwa na weryfikację: cele `analysis` i `build` mogą zająć najwyżej `(1 − 0,25) × limit`;
  `review`, `acceptance`, `integration` mogą sięgać pełnego limitu.
- `budget.warning` przy ≥ 70% limitu; przekroczenie → `BUDGET_EXHAUSTED`.
- `budget.unknown_price_policy` jest walidowane, ale nieużywane w kodzie.

## Jak zmienić model

1. Zmień `model` (i ewentualnie `effort`) roli w `factory/config.yaml` jako osobną zmianę fabryki.
2. `uv run factory doctor` (walidacja), `uv run pytest`, a dla nowego modelu live smoke:
   `AGENT_FACTORY_LIVE=1 AGENT_FACTORY_LIVE_MODEL=<model> uv run pytest tests/live`.
3. Zapisz w commicie, dlaczego zmieniasz (wynik porównania, koszt).

Skutki dla trwających jobów:

- Zmiana modelu **nie resetuje liczników**: rundy analizy (`analysis_rounds`), próby zadań i feature'a
  oraz wydany budżet są w SQLite per job (`workflow.yaml: reset_on_model_switch: false`; konfiguracja
  z wartością `true` jest odrzucana).
- Ponieważ `policy_digest` obejmuje całą treść `config.yaml`, zmiana modelu unieważnia wszystkie
  wyniki zależne od `policy_digest` w trwających jobach — oceny muszą powstać ponownie (patrz znany
  problem w `state-machine.md`). Sam `model_id` nie jest polem tożsamości wyniku.
