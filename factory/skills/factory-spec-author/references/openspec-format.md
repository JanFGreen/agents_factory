# OpenSpec format for the factory-autonomous schema

Installed CLI: `@fission-ai/openspec` 1.15.0 at `tools/openspec` (see `factory.lock.json`).
Schema: `openspec/schemas/factory-autonomous/schema.yaml`. Use the local binary
`tools/openspec/node_modules/.bin/openspec`, never `npx openspec@latest`.

## Artifact graph (standard/complex)

```
proposal -> specs -> options -> decision -> spec-evaluation
         -> design -> test-plan -> tasks -> decomposition-review
apply.requires = [tasks, decision, spec-evaluation, decomposition-review]
```

The spec author writes `proposal.md`, `specs/**/spec.md`, `intent-map.yaml`,
`acceptance-map.yaml` and `future-needs.md`. JSON artifacts are controller exports.

## proposal.md

Sections: `## Original Request` (verbatim), `## Why`, `## What Changes`,
`## Capabilities` (`### New Capabilities`, `### Modified Capabilities`), `## Impact`.
Capabilities are durable behaviors (`operation-history`), not work items.

## specs/<capability>/spec.md

```markdown
# Spec Delta

## Purpose

Lets a signed-in user find their own earlier operations quickly.

## ADDED Requirements

### Requirement: REQ-1 User-scoped history
The system SHALL return only operations owned by the authenticated user.

#### Scenario: Own operations listed
- **WHEN** user A with two operations opens the history
- **THEN** both operations of A are listed, newest first

#### Scenario: Foreign operations hidden
- **WHEN** user A requests history while user B has operations
- **THEN** no operation of B is returned
```

Rules from the installed schema: scenarios use exactly `####`; SHALL/MUST wording;
every requirement has a scenario; requirement description ≤ 500 characters
(`--strict` fails otherwise); `## Purpose` only for new capabilities; MODIFIED blocks
copy the full existing requirement. A change with no behavior delta sets
`skip_specs: true` in `.openspec.yaml`.

## intent-map.yaml / acceptance-map.yaml

```yaml
# intent-map.yaml
- intent: "find own earlier operation"
  requirement_ids: [REQ-1, REQ-2]
- intent: "filter and paginate together, also after reload"
  requirement_ids: [REQ-2, REQ-3]
```

```yaml
# acceptance-map.yaml
- requirement_id: REQ-1
  scenarios: [SC-1-1, SC-1-2]
  acceptance_ids: [AC-1, AC-4]
  planned_evidence: [history-api-contract-test, tenant-isolation-test]
```

## Micro-spec (trivial path)

```yaml
goal: "The submit button reads 'Submit'."
change: "Fix typo in src/web/components/SubmitButton.tsx label."
acceptance_criteria:
  - "The rendered checkout form shows a button labelled 'Submit'."
task:
  task_id: TASK-01
  description: "Replace label text and update snapshot test."
  file_scope: [src/web/components/SubmitButton.tsx, tests/web/SubmitButton.test.tsx]
  verification: ["npm test -- SubmitButton"]
behavior_change: true
```

1-3 criteria, exactly one task, no DAG, no forced alternatives.

## Validation

`openspec validate <change-id> --strict --no-interactive` checks format only. Semantics
are judged by `factory-spec-evaluate`; a valid format is not a PASS.
