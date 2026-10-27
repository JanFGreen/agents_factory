"""The accepted plan stops with a cost estimate until the operator approves it."""

from __future__ import annotations

import pytest

from agent_factory.controller import S, TransitionRefused


def _env(make_env, **overrides):
    return make_env("standard", **{"budget.plan_approval": "required", "budget.auto_approve_below_usd": 0.5, **overrides})


def test_build_waits_for_the_estimate_to_be_approved(make_env):
    env = _env(make_env)
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert env.calls("factory-implement") == 0  # nothing was built before the approval
    est = env.store.job_context(job)["plan_estimate"]
    assert est["tasks"] == 2 and est["estimate_usd"] > est["spent_usd"] and est["suggested_limit_usd"] > 0
    assert "factory plan approve" in env.store.job(job)["blocked_reason"]
    result = env.factory.approve_plan(job, None, "tester", "fine")
    assert env.store.job(job)["budget_limit_usd"] == max(result["budget_usd"], 25.0)
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_resume_without_approval_stops_again(make_env):
    env = _env(make_env)
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    env.factory.resume(job)
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert env.calls("factory-implement") == 0


def test_the_guard_refuses_building_an_unapproved_plan(make_env):
    env = _env(make_env)
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    assert not env.factory.controller.plan_approval(job).ok
    with env.store.tx() as conn:  # even a forged state cannot start the build
        version = conn.execute("SELECT version FROM jobs WHERE job_id = ?", (job,)).fetchone()[0]
        env.store.cas_update_job(conn, job, version, state="TASK_GRAPH_READY")
    with pytest.raises(TransitionRefused, match="not approved"):
        env.factory.controller.transition(job, S.BUILD_TASK)


def test_an_approval_is_bound_to_the_plan(make_env):
    env = _env(make_env)
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    env.factory.approve_plan(job, 40, "tester", "ok")
    env.store.update_job_context(job, plan_digest="a-different-plan")
    assert not env.factory.controller.plan_approval(job).ok


def test_small_estimates_go_ahead_without_approval(make_env):
    env = make_env("trivial", **{"budget.plan_approval": "required", "budget.auto_approve_below_usd": 100})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.store.job_context(job)["plan_estimate"]["estimate_usd"] <= 100


def test_approve_before_the_estimate_is_refused(make_env):
    env = _env(make_env)
    job = env.submit("Add exclamation greeting and farewell")
    with pytest.raises(TransitionRefused):
        env.factory.approve_plan(job, 30, "tester", "too early")
