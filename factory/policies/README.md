# Policies

Policy is enforced in code (`src/agent_factory/policy.py`) based on `factory/config.yaml`:

- `policy.mode` and the side-effect broker (`authorize_side_effect`): in `local` mode there are no comments, PRs or deploys.
- `policy.protected_paths`: a change to these files by the builder yields `FAIL_POLICY` (protected evaluation inputs).
- `policy.risk_path_rules` + diff content rules: raise risk; they never lower it.
- Test-weakening detection: skip/xfail, `assert True`, removed tests and assertions, removed test files.

A policy change is a separate controller change (operator, tests, review). It changes `policy_digest`,
so all dependent results of running jobs become stale.
