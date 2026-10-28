# CLAUDE.md

Shared rules, repo map and commands: **[AGENTS.md](AGENTS.md)**. Documentation: `docs/factory/`.

Most important rules:

1. Do not change `factory/` (policy, checks, skills, prompts) or `openspec/` as part of an application job.
   A factory change is a separate controller change with tests.
2. Agent text is never evidence. State transitions require signed results from `EvidenceStore`.
3. The sandbox is mandatory and fail-closed. Do not bypass it and do not add a "no sandbox" mode.
4. `adapters/fake.py` is for tests only. Run `live` tests only with `AGENT_FACTORY_LIVE=1`.
   A skipped live test means NOT_RUN, not PASS.
5. Before finishing your work, run `uv run pytest`.
