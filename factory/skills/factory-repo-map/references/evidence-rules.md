# Repo-map evidence rules

## Acceptable evidence

| Claim | Evidence required |
|---|---|
| "The test command is X" | `path:line` in CI workflow, task runner or README; `verified` only after a trusted run |
| "Baseline is green" | trusted-runner artifact ID with exit code **and** `tests_executed > 0` |
| "Module A depends on B" | import statement, package manifest entry or build config (cite one) |
| "This is a trust boundary" | route registration, auth middleware, client constructor, migration file |
| "No command exists for typecheck" | list of files searched, recorded in `unknowns` |

Not evidence: file names alone, the builder's statement, comments like "tests pass",
prior conversation, generic framework conventions not confirmed in this repo.

## Source priority for commands

1. CI workflow (`.github/workflows/*.yml`, other CI configs) — what the project gates on.
2. Task runner (`Makefile`, `justfile`, `package.json` scripts, `pyproject.toml` tool sections).
3. Contributor docs (`README`, `CONTRIBUTING`, `AGENTS.md`).

If sources disagree, record both and raise a finding; do not pick silently.

## Baseline facts

- Exit code 0 with 0 executed tests is **not** a passing baseline.
- Skipped suites, missing browsers or missing packages are recorded as failures of the
  environment (`ERROR_ENV`), not as passes.
- Tests failing on the clean base go to `failing_before_change`; they are never
  turned into new requirements or silently fixed in a feature job.

## Redaction

Never copy secret values (`.env`, credentials files, tokens in configs). Record only
the file path and the variable names.
