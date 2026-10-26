# factory/prompts — versioned role prompts

Każdy plik to krótki prompt jednej logicznej roli z §10.1 blueprintu
(`<role>.v<N>.md`). Prompt nie powtarza logiki procesu: wskazuje skill(e) w
`factory/skills/<name>/SKILL.md` oraz wspólny kontrakt
`factory/skills/_shared/contract.md`.

## Pliki

| Role prompt | Skills |
|---|---|
| `intake-analyst.v1.md` | factory-intake, factory-classify-feature, factory-repo-map |
| `spec-writer.v1.md` | factory-spec-author |
| `spec-evaluator.v1.md` | factory-spec-evaluate |
| `option-reviewer.v1.md` | factory-options-review, factory-select-option |
| `planner.v1.md` | factory-plan |
| `decomposition-reviewer.v1.md` | factory-decomposition-review |
| `test-designer.v1.md` | factory-test-design, factory-test-adequacy |
| `builder.v1.md` | factory-implement, factory-root-cause |
| `code-reviewer.v1.md` | factory-code-review |
| `security-reviewer.v1.md` | factory-security-review |
| `product-evaluator.v1.md` | factory-product-verify, factory-ui-review, factory-release-verify |
| `integration-evaluator.v1.md` | factory-integration-review |
| `decision-assessor.v1.md` | factory-evidence-audit |
| `operations-analyst.v1.md` | factory-recover, factory-retrospective (rola pomocnicza spoza tabeli §10.1) |

## Wersjonowanie

1. **Zmiana promptu ⇒ nowy plik wersji** (`builder.v2.md`). Nigdy nie edytuj
   istniejącej wersji w miejscu: trwające joby mają w `input_identity` zapisany
   `prompt_digest` i `skill_digests` (§5) i muszą dokończyć pracę na tej samej treści.
2. Frontmatter (`role`, `version`, `skills`) musi zgadzać się z nazwą pliku.
3. Nowa wersja nie staje się automatycznie aktywna. Aktywację wykonuje operator
   przez konfigurację fabryki po przejściu evali (§10.3, §14.3); stare wersje
   zostają w repo, dopóki istnieje job, który ich używa.
4. Zmiana `SKILL.md` lub `_shared/contract.md` jest traktowana tak samo: zmienia
   `skill_digests`, unieważnia zależne wyniki nowych uruchomień i wymaga evalu.
   Kontroler nie podmienia promptu ani skilla w trwającym jobie.
5. Literówka bez zmiany znaczenia też dostaje nową wersję — kontroler nie zgaduje
   równoważności treści.
