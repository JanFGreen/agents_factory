"""Task difficulty picks the builder model, retries escalate it, and a hard leaf must be split."""

from __future__ import annotations

import json

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.difficulty import Choice

from ..conftest import FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, plan_reply

BROKEN_TEST = 'from app.greet import greet\n\n\ndef test_greet_broken():\n    assert greet("Ada") == "nope"\n'


def _tasks(first: dict | None = None, second: dict | None = None) -> list[dict]:
    return [
        {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [], "file_scope": ["app/greet.py", "tests"],
         "acceptance": "greeting ends with !", "estimated_minutes": 20, "difficulty": "easy", **(first or {})},
        {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": ["TASK-01"], "file_scope": ["app/farewell.py", "tests"],
         "acceptance": "farewell exists", "estimated_minutes": 20, "difficulty": "medium", **(second or {})},
    ]


class Builder:
    """TASK-01 breaks its test on the first attempt."""

    def __init__(self):
        self.greet_calls = 0

    def __call__(self, request) -> FakeReply:
        if "task=TASK-02" in request.prompt:
            edits = {"app/farewell.py": FAREWELL, "tests/test_farewell.py": FAREWELL_TEST}
        else:
            self.greet_calls += 1
            edits = {"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": BROKEN_TEST if self.greet_calls == 1 else GREET_TEST}
        return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [], "handoff": "done",
                                                        "claimed_checks": []}), edits=edits)


class FixedDecider:
    name = "fixed"

    def __init__(self, choice):
        self.choice = choice
        self.last_error = None

    def choose(self, state, instructions, criteria):
        return self.choice


def _builds(env) -> list[tuple[str, str]]:
    return [("TASK-02" if "task=TASK-02" in c.prompt else "TASK-01", c.model) for c in env.runtime.calls if c.skill == "factory-implement"]


def test_builder_model_follows_difficulty_and_escalates_on_retry(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks()), "factory-implement": Builder()},
                   **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    models = env.cfg.factory.routing.builder_models
    assert _builds(env) == [("TASK-01", models["easy"]), ("TASK-01", models["medium"]), ("TASK-02", models["medium"])]
    routed = [e for e in env.store.events_for(job) if e["kind"] == "task.routed"]
    assert len(routed) == 3
    difficulty = env.store.job_context(job)["task_difficulty"]["tasks"]
    assert difficulty["TASK-01"]["difficulty"] == "easy" and difficulty["TASK-02"]["difficulty"] == "medium"


def test_haiku_only_gets_the_first_attempt_of_a_trivial_change(make_env):
    builder = Builder()
    env = make_env("trivial", responders={"factory-implement": builder})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    routing = env.cfg.factory.routing
    assert [m for _, m in _builds(env)] == [routing.trivial_path_model, routing.builder_models["medium"]]
    assert routing.trivial_path_model == "claude-haiku-5-5" and routing.builder_models["easy"] == "claude-sonnet-5-5"


def test_empty_routing_keeps_the_builder_binding(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks())}, **{"routing.builder_models": {}})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    binding = env.cfg.factory.runtime.roles["builder"].model
    assert {m for _, m in _builds(env)} == {binding}


def test_a_hard_leaf_must_be_split_or_declared_indivisible(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(second={"difficulty": "hard"}))})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    problems = env.store.job_context(job).get("analysis_findings", [])
    assert any("TASK-02 is hard" in p and "indivisible" in p for p in problems)
    assert env.calls("factory-implement") == 0


def test_an_indivisible_hard_leaf_is_built_by_the_hard_model(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(
        second={"difficulty": "hard", "difficulty_reasons": ["indivisible: one function and its single test"]}))})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert ("TASK-02", env.cfg.factory.routing.builder_models["hard"]) in _builds(env)


def test_a_confident_decision_model_raises_the_level_and_its_cost_is_recorded(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks())}, **{"execution.max_builders_per_job": 1})
    env.factory.decider = FixedDecider(Choice("medium", 0.95, 2000))
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert {m for _, m in _builds(env)} == {env.cfg.factory.routing.builder_models["medium"]}
    event = next(e for e in env.store.events_for(job) if e["kind"] == "plan.difficulty")
    payload = json.loads(event["payload_json"])
    assert payload["decider"] == "fixed" and payload["decider_usd"] == round(2 * 2000 * 0.042 / 1e6, 6)  # two tasks


def test_an_unavailable_decision_model_changes_nothing(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks())}, **{"execution.max_builders_per_job": 1})
    env.factory.decider = FixedDecider(None)
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert _builds(env)[0] == ("TASK-01", env.cfg.factory.routing.builder_models["easy"])
