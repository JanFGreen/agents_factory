# Evidence identity and freshness rules (blueprint §5, §11.2, §11.3)

## Identity a verdict is bound to

```
repo_id, job_id, task_id, attempt_id,
base_sha, head_sha, candidate_tree_sha,
feature_revision, complexity_digest, scope_digest, spec_digest, decision_digest,
plan_digest, decomposition_digest, analysis_round_id,
policy_digest, checks_digest, environment_digest,
runtime_version, model_id, prompt_digest, skill_digests,
acceptance_suite_digest, grader_version
```

Non-applicable fields are explicit `null`, never a fake hash. Placeholder-looking
hashes in a real result are rejected.

## Invalidation matrix (what makes evidence stale)

| Change | Stale |
|---|---|
| Behavior/scope (new scope/spec revision) | option selection, spec evaluation, all code reviews and acceptance |
| Plan without requirement change | decomposition review, affected task results |
| Code (new head) | all reviews and tests of the old head, unless deterministic equivalence is recorded |
| Base | integration candidate, CI, critical scenarios, impact review |
| Policy, commands, runner deps, test suite | every result produced under the old digest |
| Description/format only | stale by default unless an explicit equivalence event exists |

## Acceptance matrix row

```yaml
- id: REQ-004
  scenarios: [SC-004-own, SC-004-other, SC-004-empty]
  tasks: [TASK-002]
  evidence:
    - id: EV-004
      artifact_id: artifact-e2e-004
      assertion: User A cannot export records of user B.
      result: PASS
  status: verified
```

## Status rules

| Status | Requires |
|---|---|
| `verified` | executable evidence, identity match, trusted producer, assertion proves the requirement |
| `stale` | evidence exists but identity differs from the candidate |
| `missing` | no evidence, zero executed tests, skipped suite, or untrusted producer only |
| `not_applicable` | reason + reference to an authorized decision; never a shortcut around a mandatory gate |

## Trusted producers

- Trusted runner records argv, cwd, redacted env, versions, timestamps, exit code,
  stdout/stderr hashes and number of executed tests.
- Authenticated adapter results (CI, GitHub checks) bound to the candidate.
- Agent reviews are judgments; they are evidence of review, not of passing tests.
- A file in the repo (e.g. exported `spec-evaluation.json`) is an export and grants nothing.
