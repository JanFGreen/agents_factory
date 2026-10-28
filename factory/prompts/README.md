# factory/prompts — versioned role prompts

Each file is a short prompt for one logical role from §10.1 of the blueprint
(`<role>.v<N>.md`). A prompt does not repeat process logic: it points to the skill(s) in
`factory/skills/<name>/SKILL.md` and the shared contract
`factory/skills/_shared/contract.md`.

## Files

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
| `operations-analyst.v1.md` | factory-recover, factory-retrospective (auxiliary role outside the §10.1 table) |

## Versioning

1. **A prompt change ⇒ a new version file** (`builder.v2.md`). Never edit
   an existing version in place: running jobs have the `prompt_digest` and
   `skill_digests` (§5) recorded in `input_identity` and must finish on the same content.
2. The frontmatter (`role`, `version`, `skills`) must match the file name.
3. A new version does not become active automatically. The operator activates it
   through the factory configuration after the evals pass (§10.3, §14.3); old versions
   stay in the repo as long as some job uses them.
4. A change to `SKILL.md` or `_shared/contract.md` is treated the same way: it changes
   `skill_digests`, invalidates dependent results of new runs and requires an eval.
   The controller does not swap the prompt or skill in a running job.
5. A typo fix that does not change meaning also gets a new version — the controller does not guess
   content equivalence.
