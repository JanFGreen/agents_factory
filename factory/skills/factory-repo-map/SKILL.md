---
name: factory-repo-map
description: Use when a repository is first registered or its base changed materially (controller states INTAKE / COMPLEXITY_ASSESSMENT) to map commands, modules, trust boundaries and the baseline test result, each backed by evidence.
---

# factory-repo-map

Follow ../_shared/contract.md

## Purpose

Produce a factual, evidence-backed map of the target repository that later skills
rely on: how to install, test, lint, type-check, build and run; which modules exist
and depend on each other; where the trust boundaries are; and what the baseline
looks like before the factory changes anything. The map is a description of the
current state, not a wish list: an accidental bug in current code is recorded as a
baseline failure, not as intended behavior. Commands you discover are proposals for
the trusted check manifest; only the controller/operator promotes them.

## Required inputs

- Read-only checkout at `base_sha` (worktree path) and `repo_id`.
- Repository instructions: `AGENTS.md`, `CLAUDE.md`, `README*`, `CONTRIBUTING*`.
- Build/CI metadata: `pyproject.toml`, `package.json`, `Makefile`, `.github/workflows/*`, lockfiles.
- Allowed commands for the baseline run (from policy; usually test/lint/typecheck in sandbox, no network).
- Previous repo map (if any) to report what changed.

## Procedure

1. Confirm the worktree HEAD equals `base_sha` and the tree is clean (`git status --porcelain`).
2. List repository instruction files you actually found (`repo_instructions`).
3. Derive commands from authoritative sources in this order: CI workflow, task
   runner (Makefile/justfile/package scripts), README. Record `source` as `path:line`.
   Never invent a command; if none exists for a purpose, omit it and add an `unknowns` entry.
4. Run only the allowed baseline commands through the trusted runner. Record argv,
   exit code, number of tests executed (`null` if not reported) and the evidence ID.
   A command that exits 0 with zero tests executed is not a passing test baseline.
5. Map top-level modules: path, one-line responsibility, direct dependencies
   (imports or package references, cite how you determined them).
6. Identify trust boundaries: HTTP routes, auth/session code, external API clients,
   webhooks, DB migrations, file upload, secrets loading, background jobs.
7. Record `failing_before_change`: tests or checks that fail on the clean base. These
   are baseline facts, not the factory's fault, and must not be "fixed" silently.
8. Compare with the previous map and report material changes as non-blocking findings.

## Output

Envelope per contract, `kind: "repo-map"`. Payload:

```json
{
  "repo_instructions": ["AGENTS.md"],
  "commands": [{"purpose": "test", "argv": ["uv", "run", "pytest", "-q"], "source": ".github/workflows/ci.yml:31", "verified": true}],
  "modules": [{"path": "src/history", "responsibility": "...", "depends_on": ["src/operations"]}],
  "trust_boundaries": [{"name": "HTTP API", "location": "src/api/routes.py", "notes": "session auth"}],
  "baseline": {"commands_run": [{"argv": ["uv", "run", "pytest", "-q"], "exit_code": 0, "tests_executed": 214, "evidence_id": "artifact-baseline-test"}], "failing_before_change": []},
  "unknowns": []
}
```

- `commands[].purpose` is one of `install | test | lint | typecheck | build | run | format`;
  `verified: true` only if the trusted runner executed it in this run.
- `PASS`: commands for test and at least one static check found with sources, baseline run recorded.
- `CHANGES`: the map is usable only after a fixable repository-side gap is addressed
  (e.g. CI and README disagree on the test command) — findings explain both sources.
- `BLOCKED`: checkout unreadable, dependencies cannot be installed without a credential
  or network not granted, or the sandbox is unavailable.

## Limits

- Read-only on the repository; never modify files to make a baseline pass.
- Never run deploy, publish, migration-against-real-data or networked commands.
- Do not record secrets: redact values from env files; report their presence only.
- Do not promote discovered commands into the trusted manifest yourself.
