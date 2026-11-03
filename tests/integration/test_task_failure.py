"""A task that uses all its attempts stops only itself and its dependents (live findings F1/F2)."""

from __future__ import annotations

import json

import pytest

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.controller import TransitionRefused

from ..conftest import FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, plan_reply, request_inputs

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
        return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [], "handoff": "Done: as tasked\nSelf-review: no changes",
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


def test_the_retry_reason_reaches_the_builder(make_env):
    """Live finding (LearnEnglish F5a TASK-08): the operator saw how to fix a task within its scope, but the retry
    reason only went into an event, so the builder repeated the out-of-scope fix."""
    builder = Builder()

    def build(request) -> FakeReply:
        builder.fixed = "use the shared key helper" in request.prompt
        return builder(request)

    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(False)), "factory-implement": build})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_HUMAN"
    env.factory.retry_task(job, "TASK-01", 1, "use the shared key helper inside your own file")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_the_feature_attempt_cap_halts_and_the_operator_can_raise_it(make_env):
    """Live finding (LearnEnglish F5a): 20 tasks hit the fixed cap of 30 build attempts with 13 accepted, and the
    job ended FAILED with no way on. The cap now scales with the plan and stops in NEEDS_HUMAN, like the budget."""
    builder = Builder()
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(True)), "factory-implement": builder})
    workflow = env.factory_dir / "workflow.yaml"
    workflow.write_text(workflow.read_text().replace("max_build_attempts_per_feature: 30", "max_build_attempts_per_feature: 2")
                        .replace("max_attempts_per_leaf_task: 3", "max_attempts_per_leaf_task: 10"))
    env.reload()
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert env.factory._build_attempts_limit(job) == 4  # two per planned task beats the configured 2
    assert "used all 4 build attempts" in env.store.job(job)["blocked_reason"]
    with pytest.raises(TransitionRefused):
        env.factory.raise_build_attempts(job, 3, "lower")
    builder.fixed = True
    env.factory.raise_build_attempts(job, 8, "TASK-01 is close")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.events(job, "build_attempts.raised")


def test_reopen_brings_back_only_a_job_failed_for_the_attempt_cap(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(False))})
    job = env.submit("Add exclamation greeting and farewell")
    with pytest.raises(TransitionRefused):
        env.factory.reopen(job, "not failed")
    row = env.store.job(job)
    with env.store.tx() as conn:  # what an older controller left behind
        env.store.cas_update_job(conn, job, row["version"], state="FAILED", blocked_reason="feature used all 30 build attempts")
    env.factory.reopen(job, "the cap is a halt now")
    assert env.state(job) == "NEEDS_HUMAN" and env.events(job, "job.reopened")
    assert env.store.job_context(job)["resume_state"] == "BUILD_TASK"


@pytest.mark.parametrize("builders", [1, 2])
def test_a_retry_can_allow_a_file_outside_the_task_scope(make_env, builders):
    """Live finding (LearnEnglish F5a): a defect in a shared Button no task owned; the owner task could not touch it."""
    builder = Builder()
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(False)), "factory-implement": builder},
                   **{"execution.max_builders_per_job": builders})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_HUMAN"
    builder.fixed = True
    seen = []

    def implement(request):
        reply = builder(request)
        if "task=TASK-01" in request.prompt:
            seen.append(request_inputs(request)["task"]["file_scope"])
            reply = FakeReply(reply.envelope, edits={**reply.edits, "app/shared.py": "X = 1\n"})  # outside its scope
        return reply

    env.runtime.responders["factory-implement"] = implement
    env.factory.retry_task(job, "TASK-01", 1, "fix the shared helper too", allow_files=["app/shared.py"])
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert seen and all("app/shared.py" in scope for scope in seen)
    retry = [json.loads(e["payload_json"]) for e in env.store.events_for(job) if e["kind"] == "task.retry"]
    assert retry[-1]["allow_files"] == ["app/shared.py"]


def test_allow_file_takes_repo_relative_paths(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(False)), "factory-implement": Builder()},
                   **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    with pytest.raises(TransitionRefused):
        env.factory.retry_task(job, "TASK-01", 1, "no", allow_files=["../outside.py"])
