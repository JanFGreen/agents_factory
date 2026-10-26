# Release entity and verification rules (blueprint §14.1, §4.3)

The release is a separate entity from the job. Job ends at `MERGED`.

```
release: PENDING -> VERIFYING -> VERIFIED
                        |-> FAILED -> (repair task, incident priority)
                        |-> ROLLED_BACK (broker, within prior authorization)
```

| Term | Means | Proven by |
|---|---|---|
| MERGED | change is on the target branch | server-confirmed merge SHA |
| DEPLOYED | artifact runs in the environment | `version` check reporting the expected artifact |
| VERIFIED | deployed artifact behaves correctly over the window | all checks PASS for the full observation window |

## Pre-release requirements

- `artifact_id` (immutable digest or exact version)
- target `environment` within the authorized list
- permissions for release tools (`permissions_ok`)
- rollback plan, including data-migration handling (`rollback_plan`)

## Post-release checks

| Check | PASS when | UNKNOWN when |
|---|---|---|
| version | environment reports the expected artifact/SHA | version endpoint unreachable |
| healthcheck | health/readiness succeed for the whole window | no probe access |
| synthetic_flow | critical flow as synthetic user succeeds, data effect correct | no synthetic account |
| error_rate | no new exception types, rate within baseline tolerance | no log/monitoring access |
| slo | latency/availability within configured SLO | no metrics access |

## Rules

- Release disabled: `not-applicable: release not authorized`; keep MERGED/DEPLOYED/VERIFIED distinct.
- Missing monitoring is `UNKNOWN`, never `VERIFIED`.
- FAIL creates a repair task; rollback only by the broker within prior authorization.
- No automatic reversal of data migrations without a verified procedure.
- No financial operations or messages to real recipients.
