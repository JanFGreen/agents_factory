"""Every file a skill points to travels in the agent's prompt: agents cannot read factory/."""

from __future__ import annotations

import pytest

from agent_factory.controller import S
from agent_factory.factory import StageBlocked


def test_principles_and_references_reach_the_agents_that_follow_them(make_env):
    env = make_env("trivial")
    implement = env.factory._skill_text("factory-implement")
    assert "# Engineering principles" in implement and "EP-10" in implement
    assert "# Reference: references/handoff.md" in implement
    review = env.factory._skill_text("factory-code-review")
    assert "# Engineering principles" in review and "# Reference: references/review-checklist.md" in review
    assert "# Engineering principles" in env.factory._skill_text("factory-plan")
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
