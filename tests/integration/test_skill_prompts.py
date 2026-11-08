"""Every file a skill points to travels in the agent's prompt: agents cannot read factory/."""

from __future__ import annotations

import pytest

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.controller import S
from agent_factory.factory import StageBlocked

from ..conftest import implement_by_task


def test_principles_and_references_reach_the_agents_that_follow_them(make_env):
    env = make_env("trivial")
    implement = env.factory._skill_text("factory-implement")
    assert "# Engineering principles" in implement and "EP-10" in implement
    assert "# Reference: references/handoff.md" in implement
    assert "# Reference: references/senior-bar.md" in implement and "Self-review" in implement
    review = env.factory._skill_text("factory-code-review")
    assert "# Engineering principles" in review and "# Reference: references/review-checklist.md" in review
    assert "# Reference: references/production-and-legal.md" in review and "legal cost" in review
    assert "# Engineering principles" in env.factory._skill_text("factory-plan")
    for skill in ("factory-implement", "factory-code-review", "factory-plan"):  # the object-oriented default
        assert "EP-15" in env.factory._skill_text(skill)
    for skill in ("factory-implement", "factory-code-review"):  # the smallest complete change and its lean pass
        text = env.factory._skill_text(skill)
        assert all(rule in text for rule in ("EP-20", "EP-21", "What lean never cuts", "shortcut:", "Skipped:"))
    assert "climb the EP-5 ladder" in implement and "lean pass" in review
    intake = env.factory._skill_text("factory-intake")
    assert "# Engineering principles" not in intake and "# Reference: references/normalization.md" in intake


def test_the_prompt_sent_to_the_builder_carries_the_principles(make_env):
    env = make_env("trivial")
    env.factory.run(env.submit())
    builds = [c for c in env.runtime.calls if c.skill == "factory-implement"]
    assert builds and all("EP-4" in (c.system_append + c.prompt) for c in builds)


def test_a_missing_shared_document_blocks_instead_of_running_without_it(make_env):
    env = make_env("trivial")
    (env.factory_dir / "skills" / "_shared" / "engineering-principles.md").unlink()
    with pytest.raises(StageBlocked) as exc:
        env.factory._skill_text("factory-implement")
    assert exc.value.state is S.BLOCKED_ENV and "engineering-principles.md" in exc.value.reason


def test_a_builder_handoff_without_its_self_review_is_sent_back(make_env):
    builds = []

    def implement(request):
        builds.append(request)
        reply = implement_by_task(request)
        handoff = "Done: as tasked" if len(builds) == 1 else "Done: as tasked\nSelf-review: renamed `x` to `greeting`"
        return FakeReply(envelope("implement", payload={**reply.envelope["payload"], "handoff": handoff}), edits=reply.edits)

    env = make_env("trivial", responders={"factory-implement": implement})
    assert env.factory.run(env.submit()) == "READY_TO_MERGE"
    assert len(builds) == 2 and "Self-review:" in builds[1].prompt  # the validator's note asks for it
