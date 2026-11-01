"""The planner and the decomposition reviewer judge file scopes by the rule the diff policy enforces."""

from __future__ import annotations

from agent_factory.policy import FILE_SCOPE_RULE, FileDiff, evaluate_diff

from ..conftest import request_inputs


def test_the_rule_states_what_the_policy_enforces(make_env):
    policy = make_env("trivial").cfg.factory.policy
    scope = ["src/domain/limits.ts"]
    assert evaluate_diff([FileDiff("src/domain/limits.test.ts", "M", "+x"), FileDiff("src/ui/Home.test.tsx", "M", "+x")],
                         policy, task_file_scope=scope).ok
    assert not evaluate_diff([FileDiff("src/ui/Home.tsx", "M", "+x")], policy, task_file_scope=scope).ok
    assert "Test files are always in the builder's scope" in FILE_SCOPE_RULE and "non-test file" in FILE_SCOPE_RULE


def test_planner_and_decomposition_reviewer_get_the_rule(make_env):
    env = make_env("standard")
    env.factory.run(env.submit())
    for skill in ("factory-plan", "factory-decomposition-review"):
        calls = [request_inputs(c) for c in env.runtime.calls if c.skill == skill]
        assert calls and all(c["file_scope_rule"] == FILE_SCOPE_RULE for c in calls), skill
