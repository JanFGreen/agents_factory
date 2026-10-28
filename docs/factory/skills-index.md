# Agent Factory skills index

Source: blueprint §10.2 (21 core skills) + §14.1 (`factory-release-verify`,
only with release enabled). Each skill: `factory/skills/<name>/SKILL.md`,
examples `examples/{pass,fail,blocked}.json`, references `references/`.
Shared output contract: `factory/skills/_shared/contract.md` — the last
```` ```json ```` block in the agent's final message, `kind` = the name without the `factory-` prefix.

| # | Skill | Trigger (when) | Output `kind` | Controller state | Role (prompt) |
|---|---|---|---|---|---|
| 1 | `factory-intake` | new feature from CLI / file / issue | `intake` | `INTAKE` | intake-analyst |
| 2 | `factory-classify-feature` | normalized feature + repo map; again after the actual diff | `classify-feature` | `COMPLEXITY_ASSESSMENT` (+ reclassification in `CHECKS`/`REVIEW`) | intake-analyst |
| 3 | `factory-repo-map` | new repo or significant base change | `repo-map` | `INTAKE` / `COMPLEXITY_ASSESSMENT` | intake-analyst |
| 4 | `factory-spec-author` | frozen scope; trivial → micro-spec, standard/complex → OpenSpec | `spec-author` | `MICRO_SPEC` / `ANALYSIS_ROUND` | spec-writer |
| 5 | `factory-options-review` | `options.md` of the current round (non-trivial feature) | `options-review` | `AI_OPTION_REVIEW` | option-reviewer |
| 6 | `factory-select-option` | options + review of the current round | `select-option` | `OPTION_SELECTED` | option-reviewer |
| 7 | `factory-spec-evaluate` | current spec or micro-spec | `spec-evaluate` | `AI_SPEC_EVALUATION` | spec-evaluator |
| 8 | `factory-plan` | selected option + spec with a current PASS | `plan` | `DECOMPOSE` | planner |
| 9 | `factory-test-design` | spec with a current PASS and risk | `test-design` | `DECOMPOSE` (`test-plan` artifact) | test-designer |
| 10 | `factory-decomposition-review` | DAG of a non-trivial feature | `decomposition-review` | `AI_DECOMPOSITION_REVIEW` | decomposition-reviewer |
| 11 | `factory-implement` | ready task (dependencies accepted) | `implement` | `BUILD_TASK` | builder |
| 12 | `factory-root-cause` | the same error fingerprint in two consecutive attempts | `root-cause` | `BUILD_TASK` (after a repeated FAIL in `CHECKS`/`REVIEW`) | builder (new session) |
| 13 | `factory-code-review` | checks PASS for the current HEAD | `code-review` | `REVIEW` | code-reviewer |
| 14 | `factory-security-review` | every task (baseline); full on risk / trust boundary | `security-review` | `REVIEW` | security-reviewer |
| 15 | `factory-test-adequacy` | diff adds or changes tests | `test-adequacy` | `REVIEW` | test-designer |
| 16 | `factory-ui-review` | user-visible change | `ui-review` | `REVIEW` | product-evaluator |
| 17 | `factory-product-verify` | L4 per task; acceptance of the whole feature (PM / QA) | `product-verify` | `REVIEW`, `FEATURE_ACCEPTANCE` | product-evaluator |
| 18 | `factory-integration-review` | new L5 integration candidate | `integration-review` | `INTEGRATION` | integration-evaluator |
| 19 | `factory-evidence-audit` | before a merge recommendation | `evidence-audit` | `READY_TO_MERGE` | decision-assessor |
| 20 | `factory-recover` | restart, lease timeout, lost worker, handoff | `recover` | any (before resuming) | operations-analyst |
| 21 | `factory-retrospective` | `FAILED`, incident or end of an eval cycle | `retrospective` | after a terminal state / eval | operations-analyst |
| 22 | `factory-release-verify` | only for an authorized release (§14.1) | `release-verify` | release: `PENDING` (pre-check), `VERIFYING` | product-evaluator |

## Shared rules

- No manual spec approval: the gate before build is a current PASS of
  `spec-evaluate` and (standard/complex) `decomposition-review`, checked by the controller.
- Skills 4–10 share one counter: max. **3 analysis rounds per feature**
  (the first one included), max. 1 spike; after the limit, `ANALYSIS_LIMIT_REACHED`.
- `classify-feature` does not choose the path — the controller sums the dimensions
  (trivial ≤2 + fast path conditions, standard 3–6, complex ≥7 or a forcing rule).
  Risk is a separate axis.
- `select-option`: the controller recomputes the score (0.30/0.25/0.20/0.15/0.10) and tie-break;
  `selected_option_id` must be its argmax.
- `implement`: `claimed_checks` are claims, not evidence; the builder does not close findings.
- `PASS` with an open blocking finding or with an empty `checked_criteria` is rejected.
- The `checks`, `merge`, `cleanup`, `lease`, `budget`, `advance` operations are
  deterministic controller code, not skills.
