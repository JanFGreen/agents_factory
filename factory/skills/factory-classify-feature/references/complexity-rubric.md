# Complexity and risk rubric

Blueprint §7.2 and `workflow.yaml: complexity`. The LLM scores; **the controller
sums and routes**. Risk is a separate axis.

## Dimensions (0-2 each, `null` = unknown)

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| `scope` | one place, one behavior (label, constant) | one module plus its direct view/test | several modules or layers |
| `coupling` | no other module depends on the touched code | a few known callers, same package | shared library/contract used across the repo |
| `state_and_data` | no persisted state change | new query/field on existing data, no migration | schema migration, backfill, data rewrite |
| `interfaces` | no public interface change | additive change to an internal API or UI contract | new/changed public API, event or cross-service contract |
| `uncertainty` | existing pattern cited, behavior clear | minor unknowns resolvable from the repo | unknown external system, unclear behavior, needs a spike |
| `verification` | an existing test pattern covers it directly | new tests at one level | E2E/multi-system, timing, concurrency or hard-to-observe effects |

`null` means the evidence could not be established. **Any `null` disqualifies
trivial** (`unknown_dimensions_allow_trivial: false`). The controller treats the
dimension as unknown, never as 0.

## Path thresholds (computed by the controller)

- `trivial`: sum ≤ 2 **and all** preconditions: no `null`, `risk == low`,
  `single_atomic_task == true`, `known_pattern == true`, `new_trust_boundary == false`,
  `forced_complex_reasons == []`.
- `standard`: sum 3-6, or sum ≤ 2 with any trivial precondition unmet.
- `complex`: sum ≥ 7, **or** any `forced_complex_reasons` entry.

## Forcing rules for complex

- cross-cutting change of shared contracts,
- significant data migration,
- unknown external system,
- hard-to-reverse effects (data deletion, money movement, external messages),
- a single dimension so expensive that analysis is unavoidable (state it).

## Risk (separate)

| Risk | Typical signals |
|---|---|
| `low` | cosmetic/UI text, internal refactor without data or auth effects |
| `medium` | reads or writes user data within existing auth, new internal endpoint |
| `high` | auth/authz, tenant isolation changes, credentials, payments, webhooks, migrations, irreversible effects |

Risk = max(intake, path rules, actual diff, new permissions, reviewer observations).
Policy floors cannot be lowered by the classifier.

## Calibration examples

| Feature | Scores (sc,co,st,if,un,ve) | Sum | Other facts | Path / risk |
|---|---|---|---|---|
| Fix label "Sumbit" → "Submit" | 0,0,0,0,0,0 | 0 | atomic, known pattern | trivial / low |
| One-line change to permission check | 0,1,0,0,0,1 | 2 | authz path rule | standard / high (risk blocks fast path) |
| New history list with filter and API | 1,1,1,1,0,1 | 5 | new endpoint = new trust boundary | standard / medium |
| Multi-tenant login with user migration | 2,2,2,2,1,2 | 11 | migration, cross-cutting | complex / high |

## Re-classification

After the real diff exists, re-score. Hidden complexity moves a fast-path feature
into analysis while keeping progress, attempt counters and budget. The class never
goes down within a job.
