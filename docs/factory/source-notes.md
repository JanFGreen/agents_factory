# Source notes (blueprint §2)

In short: what from each source made it into the code. The sources are not a benchmark of this factory.

**S1. Anthropic — Building effective agents (2024).** A predictable workflow with explicit boundaries
instead of a free-form agent: transition table and guards (`controller.py`), the agent is free only within
a single skill. Evaluator–optimizer as the builder → checks → review loop (`factory.py::_step_review`).

**S2. Effective context engineering (2025).** A small input package per call (`call_agent` passes
only the needed fields), a selectively loaded skill (`_skill_text`: contract + one `SKILL.md`),
restricted tools per role, a structured handoff instead of history (`Factory._checkpoint`).

**S3. Effective harnesses for long-running agents (2025).** Environment check before work (a baseline
of checks on the untouched base in `_prepare_build`, `factory doctor`), durable state in SQLite and Git,
resumption without resetting counters (`resume`, `_adopt_running`).

**S4. Harness design for long-running apps (2026).** Separation of planner / generator / evaluator: separate
roles and fresh calls (`SKILL_ROLE`), independent review of every HEAD, acceptance of the whole feature
(`_step_feature_acceptance`). The number of stages is configurable, but their usefulness has not been measured.

**S5. Demystifying evals (2026).** Code-based grading (runner, junit, `min_tests`) separated from model-based grading
(verdicts in the envelope), a skipped live test = NOT_RUN (`pytest` marker `live`). Factory eval
`evals.run_suite`: `full` vs `single-agent`, success from golden tests hidden from the builder, counted
`false_accepts`/`false_rejects`; with n < 30 the report is marked as a pilot.

**S6. Cursor — Scaling long-running autonomous coding (2026).** An explicit task owner and short
leases with an epoch (`scheduler.acquire_lease`, `check_fence`), a dependency DAG (`validate_dag`,
`ready_tasks`), limited parallelism (`agent_process` slot, one builder per job).

**S7. StrongDM — Software Factories (2026).** Protecting evaluation against overfitting: protected paths and
test-weakening detection (`policy.evaluate_diff`), the checks manifest outside the job branch, deny for
`holdout/`. Service stubs: `StubGitHub`, `fake.py`. Private holdout scenarios are not implemented.

**S8. OpenAI — Harness engineering (2026).** Worktree per job (`workspaces.py`), a short `AGENTS.md`
as a map, rules enforced by tools (policy gate, sandbox), not by the prompt. Per-job application runtime
(ports, dev server) — only `allocate_port`, unused.

**S9. Google Research — Scaling agent systems (2026).** Tasks in a job run sequentially by default;
parallelization only of disjoint scopes (`parallel_safe_with` without shared files in `validate_dag`),
although parallel execution is not implemented. Cost measured per call (`usage`).

**S10. Fission-AI — OpenSpec.** The `openspec/schemas/factory-autonomous` schema, CLI pinned
(`factory.lock.json`, 1.15.0), export of spec/plan to `openspec/changes/<id>/`. Quality gates belong to the
controller, not the format validator. The controller does not call `openspec validate` or archiving.
