"""A task that uses all its attempts stops only itself and its dependents (live findings F1/F2)."""

from __future__ import annotations

import pytest

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.controller import TransitionRefused

from ..conftest import FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, plan_reply

BROKEN_TEST = 'from app.greet import greet\n\n\ndef test_greet_broken():\n    assert greet("Ada") == "nope"\n'


def _tasks(second_depends_on_first: bool) -> list[dict]:
    return [
        {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [], "file_scope": ["app/greet.py", "tests"],
         "acceptance": "greeting ends with !", "estimated_minutes": 20},
        {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": ["TASK-01"] if second_depends_on_first else [],
         "file_scope": ["app/farewell.py", "tests"], "acceptance": "farewell exists", "estimated_minutes": 20},
    ]


class Builder:
    """TASK-01 keeps breaking its own test until `fixed` is set."""

    def __init__(self):
        self.fixed = False

    def __call__(self, request) -> FakeReply:
        if "task=TASK-02" in request.prompt:
            edits = {"app/farewell.py": FAREWELL, "tests/test_farewell.py": FAREWELL_TEST}
        else:
            edits = {"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": GREET_TEST if self.fixed else BROKEN_TEST}
        return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [], "handoff": "done",
                                                        "claimed_checks": []}), edits=edits)


def _states(env, job) -> dict[str, str]:
    return {r["task_id"]: r["state"] for r in env.store.all("SELECT task_id, state FROM tasks WHERE job_id = ?", (job,))}


@pytest.mark.parametrize("builders", [1, 2])
def test_failed_task_does_not_stop_an_independent_one(make_env, builders):
    builder = Builder()
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(False)), "factory-implement": builder},
                   **{"execution.max_builders_per_job": builders})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert _states(env, job) == {"TASK-01": "FAILED", "TASK-02": "ACCEPTED"}
    reason = env.store.job(job)["blocked_reason"]
    assert "['TASK-01']" in reason and "accepted 1/2" in reason and "factory task retry" in reason

    builder.fixed = True
    env.factory.retry_task(job, "TASK-01", 1, "test fixed by the operator's hint")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert any(e["kind"] == "task.retry" for e in env.store.events_for(job))


def test_dependents_of_a_failed_task_are_reported_as_blocked(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(True)), "factory-implement": Builder()},
                   **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert _states(env, job) == {"TASK-01": "FAILED", "TASK-02": "PENDING"}
    assert "blocked by them: ['TASK-02']" in env.store.job(job)["blocked_reason"]


def test_retry_needs_a_failed_task_and_a_halted_job(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(False)), "factory-implement": Builder()},
                   **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    with pytest.raises(TransitionRefused):
        env.factory.retry_task(job, "TASK-02", 1, "accepted tasks are not retried")
    env.factory.retry_task(job, "TASK-01", 2, "ok")
    with pytest.raises(TransitionRefused):
        env.factory.retry_task(job, "TASK-01", 1, "no longer FAILED")
