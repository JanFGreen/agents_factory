"""Task review cost controls: review tiers and cheaper reviewers for low-risk jobs (live: F2 cost $50)."""

from __future__ import annotations

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import classify_reply, review_pass


def _changes_once():
    seen = {"n": 0}

    def code_review(request):
        seen["n"] += 1
        if seen["n"] == 1:
            return FakeReply(envelope("code-review", "CHANGES", payload={}, findings=[
                {"id": "CR-1", "severity": "high", "blocking": True, "problem": "missing error case", "status": "open"}]))
        return review_pass("code-review")

    return code_review


def _attempt_calls(env, skill: str) -> list[int]:
    return [i for i, c in enumerate(env.runtime.calls) if c.skill == skill]


def test_changes_in_the_first_tier_skip_the_expensive_reviews_for_that_attempt(make_env):
    env = make_env("trivial", responders={"factory-code-review": _changes_once()})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    builds = _attempt_calls(env, "factory-implement")
    assert len(builds) == 2
    # Tier 1 (code, test adequacy) ran on attempt 1; product-verify and security did not run before the rebuild.
    for skill in ("factory-security-review",):
        assert all(i > builds[1] for i in _attempt_calls(env, skill))
    task_product = [i for i, c in enumerate(env.runtime.calls) if c.skill == "factory-product-verify" and "task=TASK-01" in c.prompt]
    assert task_product and all(i > builds[1] for i in task_product)
    assert any(i < builds[1] for i in _attempt_calls(env, "factory-test-adequacy"))  # same tier as code review


def test_low_risk_task_reviews_use_the_cheaper_models_but_acceptance_does_not(make_env):
    env = make_env("trivial")
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.store.job(job)["risk"] == "low"
    cheap = env.cfg.factory.review.cheaper_models
    task_reviews = [c for c in env.runtime.calls if c.skill == "factory-code-review"]
    assert task_reviews and all(c.model == cheap["code_reviewer"] for c in task_reviews)
    acceptance = [c for c in env.runtime.calls if c.skill == "factory-product-verify" and "task=None" in c.prompt]
    assert acceptance and all(c.model == env.cfg.factory.runtime.roles["product_evaluator"].model for c in acceptance)


def test_medium_risk_jobs_keep_the_full_reviewers(make_env):
    env = make_env("standard", responders={"factory-classify-feature": classify_reply(4, risk="medium", atomic=False)})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    assert env.store.job(job)["risk"] == "medium"
    full = env.cfg.factory.runtime.roles["code_reviewer"].model
    assert all(c.model == full for c in env.runtime.calls if c.skill == "factory-code-review")


def test_review_tiers_must_cover_every_review_once():
    import pytest

    from agent_factory.config import ReviewPolicy

    with pytest.raises(ValueError):
        ReviewPolicy(tiers=[["code-review"]])
    with pytest.raises(ValueError):
        ReviewPolicy(cheaper_models_for_risk=["high"])
