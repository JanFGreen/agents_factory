# Threat checklist and risk-raising triggers

Source: blueprint §7.2, §7.8, §13.2.

## Depth

- **baseline** (every task): the threats of each controller trigger in the table below,
  plus always: secrets in logs/errors/responses, authz on touched handlers, input validation
  on new parameters, unsafe defaults. Only the diff.
- **full** (once per feature with a trigger or high risk; per task only when policy says so):
  boundary map, threat enumeration, evidence per threat, sandbox confirmation where possible,
  chains across the feature's tasks.

## Risk-raising triggers (from the actual diff, regardless of intake label)

Controller trigger names in brackets.

| Trigger | Typical signal | Minimum observed risk |
|---|---|---|
| New or changed data endpoint [`endpoint`] | route/handler returning stored data | medium |
| Credential or secret handling [`secrets`] | tokens, keys, password fields, env secrets | high |
| Webhook / inbound callback [`webhook`] | signature verification, replay | high |
| SQL / raw query [`sql`] | string-built SQL, ORM `raw()` | medium |
| AuthN/AuthZ change [`auth`, `sensitive-path`] | session, roles, permissions, tenant scoping | high |
| Outbound URL from input [`outbound-request`] | fetch/redirect to user-provided URL (SSRF) | high |
| File upload / path from input [`file-upload`] | path traversal, content type | medium |
| New dependency or lockfile change [`dependency`] | supply chain | medium |
| HTML sink with data [`html-sink`] | `dangerouslySetInnerHTML`, `innerHTML`, `v-html`, `|safe` (XSS) | medium |
| Process execution / dynamic code [`process-exec`] | `child_process`, `subprocess`, `eval` (command injection) | high |
| Weak randomness or hashing [`crypto`] | `Math.random()` for tokens, md5/sha1 for passwords | medium |
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
