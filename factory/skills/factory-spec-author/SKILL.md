---
name: factory-spec-author
description: Use in controller states MICRO_SPEC (trivial path) or ANALYSIS_ROUND (standard/complex) to write the micro-spec or the OpenSpec change artifacts plus intent map and acceptance criteria, so that no requirement of the original request is lost.
---

# factory-spec-author

Follow ../_shared/contract.md

## Purpose

Write the behavior contract for the feature in the format of its path. On the
trivial path this is a micro-spec (goal, exact change, 1-3 criteria, one task, how
to verify). On standard/complex it is the OpenSpec change `proposal.md`,
`specs/**/spec.md`, `intent-map.yaml` and `acceptance-map.yaml` under the
`factory-autonomous` schema (see `references/openspec-format.md`). Every intent of
the original request must map to requirement IDs; nothing is dropped to make the
spec easier to pass. The spec is evaluated by `factory-spec-evaluate` in a fresh
context; nobody approves it manually.

## Required inputs

- Original request, normalized feature (`factory-intake`) and its revision, with
  `feature.decisions`: settled product decisions (`source`: `human` or `accepted` = the
  operator's answer, `assumed` = the intake's default in auto mode).
- Classification facts (path computed by the controller, risk, dimension evidence).
- `factory-repo-map` result and relevant baseline specs under `openspec/specs/`
  (`openspec list --specs`, `openspec show <spec> --type spec`).
- Standard/complex: the selected option (`decision.json`) when it exists in this round.
- Previous round artifacts and open findings from `factory-spec-evaluate` (rounds 2-3).
- `analysis_round_id` and the change id (e.g. `add-operation-history`).

## Procedure

1. Confirm digests of the feature revision and previous findings.
2. Split the request into intents; give each requirement a stable ID (`REQ-1`, ...)
   and keep IDs from earlier rounds. `source` is `original_request`, `constraint`, or
   `derived_necessary_dependency` (only when another requirement cannot be correct without it),
   or `decision:Q-n` for a requirement that implements an answered product decision.
   Every decision answered by the operator (`human`, `accepted`) is binding like the
   original request: it maps to a requirement or a constraint and is never contradicted.
   An `assumed` decision is the default to follow unless the repository shows it is wrong
   (then say so in a non-blocking finding).
3. Map every intent to requirement IDs in `intent_map`. An intent without a
   requirement is a defect you must fix before returning.
4. Write acceptance criteria as observable results with requirement links, including
   failure and boundary cases (foreign user's data, empty state, invalid filter).
5. Trivial path: fill `micro_spec` with exactly one task; set `behavior_change`. If no
   behavior changes, use OpenSpec `skip_specs: true` instead of inventing a requirement.
6. Standard/complex: author the OpenSpec artifacts (`proposal.md`, `specs/<capability>/spec.md`,
   `design.md`) following the `factory-autonomous` schema and return their contents in
   `payload.files` (path relative to the change directory). Every requirement heading
   starts with its ID and has at least one `#### Scenario:`. This run is read-only: the
   controller writes the files into the change directory and runs the OpenSpec validator.
7. In rounds 2-3 change only artifacts touched by open findings; answer each
   finding ID in `checked_criteria` ("F-3 addressed: ...").
8. Do not turn a current-code bug into a requirement; describe intended behavior.

## Output

Envelope per contract, `kind: "spec-author"`. Payload (`micro_spec` only on the trivial path):

```json
{
  "micro_spec": {"goal": "...", "change": "...", "acceptance_criteria": ["..."],
    "task": {"task_id": "TASK-01", "description": "...", "file_scope": ["..."], "verification": ["..."]},
    "behavior_change": true},
  "requirements": [{"id": "REQ-1", "text": "...", "source": "original_request"}],
  "intent_map": [{"intent": "...", "requirement_ids": ["REQ-1"]}],
  "acceptance_criteria": [{"id": "AC-1", "requirement_ids": ["REQ-1"], "observable": "..."}]
}
```

- `PASS`: artifacts returned in `payload.files`, intent map complete, format follows the schema (the controller runs the validator).
- `CHANGES`: you found an inconsistency you could not resolve inside the round
  (e.g. baseline spec contradicts the request and needs an explicit MODIFIED delta decision).
- `BLOCKED`: a needed business decision is missing. Never BLOCKED because this run cannot write files or run the validator.

## Limits

- Shares the feature budget of max 3 analysis rounds; it is never reset by a model switch,
  replan or child task.
- No product implementation, no edits outside `openspec/changes/<change-id>/` and the micro-spec location.
- Never reduce scope, weaken criteria or move a required intent to `future-needs.md` to pass.
- Never request human approval; do not edit `spec-evaluation.json`, `decision.json` or
  `decomposition-review.json` (exports written by the controller).
