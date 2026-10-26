# Threat checklist and risk-raising triggers

Source: blueprint §7.2, §7.8, §13.2.

## Depth

- **baseline** (every task): secrets in diff/fixtures/logs, authz on touched
  handlers, input validation on new parameters, dependency changes, unsafe defaults.
- **full** (risk medium/high, policy minimum, or any trigger below): boundary map,
  threat enumeration, evidence per threat, sandbox confirmation where possible.

## Risk-raising triggers (from the actual diff, regardless of intake label)

| Trigger | Typical signal | Minimum observed risk |
|---|---|---|
| New or changed data endpoint | route/handler returning stored data | medium |
| Credential or secret handling | tokens, keys, password fields, env secrets | high |
| Webhook / inbound callback | signature verification, replay | high |
| SQL / raw query | string-built SQL, ORM `raw()` | medium |
| AuthN/AuthZ change | session, roles, permissions, tenant scoping | high |
| Outbound URL from input | fetch/redirect to user-provided URL (SSRF) | high |
| File upload / path from input | path traversal, content type | medium |
| New dependency or lockfile change | supply chain | medium |
| Data migration touching PII | backfill, export | high |

Observed risk = max(intake risk, path rules, diff triggers, new permissions,
reviewer observations). An LLM never lowers it below the policy minimum.

## Threat categories

`authn`, `authz` (IDOR, missing tenant filter), `injection` (SQL, command, template,
XSS), `secrets`, `data_exposure` (over-broad responses, logs), `ssrf`, `dependency`,
`supply_chain`, `other`.

## Evidence that counts

file:line of the control + a test or sandbox run with synthetic data showing the
attack input is rejected. A control without a test is "unverified", not mitigated
for high-risk boundaries.

## Never

production actions, real credentials, real recipients, external scanning,
copying a detected secret into any output.
