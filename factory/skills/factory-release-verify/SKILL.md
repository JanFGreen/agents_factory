---
name: factory-release-verify
description: Use only when the operator has enabled release, for the release entity in state VERIFYING (and as a pre-release check in PENDING), to verify a deployed artifact against version, health, a synthetic critical flow, errors and SLOs within a bounded observation window.
---

# factory-release-verify

Follow ../_shared/contract.md

## Purpose

Optional post-merge loop (blueprint §14.1), an extra skill beyond the 21 core
skills. `MERGED` is not `DEPLOYED`, and `DEPLOYED` is not `VERIFIED`. You run as
the product evaluator with trusted release tools: before release you confirm the
preconditions; after release you observe the deployed artifact for a bounded
window and report whether it can be marked `VERIFIED`. A failure becomes a
separate repair task with incident priority; rollback is performed by the release
broker only within its prior authorization — never by you.

## Required inputs

- Release policy: `release_enabled`, authorized environments, observation window,
  SLO thresholds, rollback authorization scope.
- Release identity: `artifact_id` (image digest/package version), target
  `environment`, merged SHA and its relation to the tested tree.
- Rollback plan and data-migration notes for the change.
- Read-only access to monitoring/logs/health endpoints and a synthetic account for
  the critical flow in that environment (sandbox recipients only).

## Procedure

1. If `release_enabled` is false: return `PASS` with
   `not_applicable_reason: "release not authorized"`, `release_state` unchanged
   (`PENDING`), no checks. This records MERGED without claiming DEPLOYED/VERIFIED.
2. **Pre-release (PENDING):** require `artifact_id`, `environment`, permissions for
   the release tools and a rollback plan (`pre_release.rollback_plan`,
   `pre_release.permissions_ok`). Any missing => `BLOCKED`.
3. **Post-release (VERIFYING)**, within `observation_window_minutes`:
   - `version`: the environment reports the expected `artifact_id`/SHA.
   - `healthcheck`: health/readiness endpoints succeed throughout the window.
   - `synthetic_flow`: the critical user flow of the feature (e.g. open history,
     filter, paginate as a synthetic user) succeeds and its data effect is correct.
   - `error_rate`: error rate and new exception types versus the pre-release baseline.
   - `slo`: latency/availability against configured SLOs.
   Each check has `observed` and `evidence_ids` from the trusted tools.
4. No access to monitoring/logs => that check is `UNKNOWN`; the release is never
   marked `VERIFIED` on missing signals.
5. Any `FAIL`: verdict `CHANGES`, `release_state: FAILED`, a finding routed to a new
   repair task; set `rollback_recommended: true` when the rollback plan covers it.
   If the change includes a data migration, recommend rollback only when a verified
   reverse procedure exists; otherwise recommend forward fix and say so.
6. All PASS for the whole window => `release_state: VERIFIED`.

## Output

Envelope per contract, `kind: "release-verify"`. Payload:

```json
{
  "release_enabled": true,
  "not_applicable_reason": null,
  "artifact_id": "ghcr.io/acme/app@sha256:...",
  "environment": "staging",
  "pre_release": {"rollback_plan": true, "permissions_ok": true},
  "observation_window_minutes": 30,
  "checks": [{"name": "healthcheck", "result": "PASS", "observed": "200 OK 180/180 probes", "evidence_ids": ["ev-rel-0501"]}],
  "release_state": "VERIFIED",
  "rollback_recommended": false
}
```

- `PASS`: all checks PASS over the full window (`VERIFIED`), or release disabled (not-applicable).
- `CHANGES`: a post-release check FAILED; repair task with incident priority, rollback recommendation.
- `BLOCKED`: pre-release requirements missing (artifact, environment, permissions, rollback plan).
- `UNKNOWN`: monitoring/log access missing or signals inconclusive; never `VERIFIED`.

## Limits

- Never deploy, roll back, scale or change configuration yourself; the release broker
  acts within prior authorization.
- Never test financial operations or outbound messages on real recipients; synthetic
  accounts and sandbox recipients only.
- Never recommend automatic reversal of data migrations without a verified procedure.
- Do not shorten the observation window or relax SLO thresholds to reach VERIFIED.
- Do not mark DEPLOYED or VERIFIED from a merge, a CI result or a deploy log alone.
