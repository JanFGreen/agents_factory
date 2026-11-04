"""A rejected plan for the same spec is patched, not rewritten; analysis agents get the exported API up front.

Live finding (LearnEnglish F5b): the decomposition review rejected one task's merge contract; the planner wrote all
nine tasks again (32k output tokens on Opus, $1.09) and the reviewer re-read the whole plan ($0.70). The planner had
also read 18 whole files for the signatures it planned against.
"""

from __future__ import annotations

import json

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import decomposition_reply, plan_reply, request_inputs

TASKS = [
    {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [], "file_scope": ["app/greet.py", "tests"],
     "acceptance": "greeting ends with !", "estimated_minutes": 20},
    {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": ["TASK-01"], "file_scope": ["app/farewell.py", "tests"],
     "acceptance": "farewell", "estimated_minutes": 20},
]


def _patching_planner(seen: list[dict], patch: dict | None = None):
    def plan(request) -> FakeReply:
        inputs = request_inputs(request)
        seen.append(inputs)
        if "revision" not in inputs:
            return plan_reply(TASKS)
        fixed = {**TASKS[1], "acceptance": "farewell exists and ends with a full stop"}
        return FakeReply(envelope("plan", payload=patch or {"tasks": [fixed], "revision_of": inputs["revision"]["plan_digest"]}))
    return plan


def test_a_rejected_plan_is_patched_and_the_review_checks_the_patch(make_env):
    seen, reviews = [], []

    def review(request) -> FakeReply:
        reviews.append(request_inputs(request))
        return decomposition_reply("CHANGES" if len(reviews) == 1 else "PASS")

    env = make_env("standard", responders={"factory-plan": _patching_planner(seen), "factory-decomposition-review": review})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert "revision" not in seen[0] and seen[1]["revision"]["mode"] == "patch"
    assert seen[1]["previous_plan"]["tasks"][0]["task_id"] == "TASK-01"
    plan = env.store.job_context(job)["plan"]
    assert [t["task_id"] for t in plan["tasks"]] == ["TASK-01", "TASK-02"]  # the untouched task is kept, in its place
    assert plan["tasks"][1]["acceptance"].endswith("full stop") and "revision_of" not in plan
    assert plan["future_needs"]  # kept from the previous plan
    assert "revision" not in reviews[0]
    assert reviews[1]["revision"]["changed"] == ["TASK-02"] and "cycle" in reviews[1]["revision"]["previous_findings"]
    revised = [json.loads(e["payload_json"]) for e in env.events(job, "plan.revised")]
    assert revised == [{"changed": ["TASK-02"], "added": [], "removed": []}]


def test_a_patch_can_add_and_remove_tasks(make_env):
    seen = []

    def plan(request) -> FakeReply:
        inputs = request_inputs(request)
        if "revision" not in inputs:
            return plan_reply([*TASKS, {"task_id": "TASK-03", "requirement_ids": ["REQ-2"], "depends_on": ["TASK-02"],
                                        "file_scope": ["app/extra.py"], "acceptance": "extra"}])
        merged = {**TASKS[1], "task_id": "TASK-04", "depends_on": ["TASK-01"]}
        return FakeReply(envelope("plan", payload={"tasks": [merged], "removed_task_ids": ["TASK-02", "TASK-03"],
                                                   "revision_of": inputs["revision"]["plan_digest"]}))

    env = make_env("standard", responders={"factory-plan": plan,
                                           "factory-decomposition-review": [decomposition_reply("CHANGES"), decomposition_reply()]})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    tasks = [t["task_id"] for t in env.store.job_context(job)["plan"]["tasks"]]
    assert tasks == ["TASK-01", "TASK-04"]
    assert [json.loads(e["payload_json"]) for e in env.events(job, "plan.revised")] == [
        {"changed": [], "added": ["TASK-04"], "removed": ["TASK-02", "TASK-03"]}]


def test_a_patch_of_another_plan_stops_the_job(make_env):
    seen = []
    env = make_env("standard", responders={
        "factory-plan": _patching_planner(seen, {"tasks": [TASKS[1]], "revision_of": "sha256:not-this-plan"}),
        "factory-decomposition-review": [decomposition_reply("CHANGES"), decomposition_reply()]})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_INFO"
    assert "does not match the current plan" in env.store.job(job)["blocked_reason"]


def test_a_full_plan_still_replaces_the_rejected_one(make_env):
    """A planner may answer a revision with a whole new plan (no revision_of) when a patch cannot fix the findings."""
    env = make_env("standard", responders={"factory-plan": [plan_reply(TASKS), plan_reply(TASKS[:1] + [{**TASKS[1], "acceptance": "new"}])],
                                           "factory-decomposition-review": [decomposition_reply("CHANGES"), decomposition_reply()]})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.store.job_context(job)["plan"]["tasks"][1]["acceptance"] == "new"
    assert not env.events(job, "plan.revised") and env.store.job_context(job)["plan_revision"] is None


def test_analysis_agents_get_the_api_map(make_env):
    env = make_env("standard")
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    for skill in ("factory-options-review", "factory-spec-evaluate", "factory-plan", "factory-decomposition-review"):
        call = next(c for c in env.runtime.calls if c.skill == skill)
        api = request_inputs(call)["api_map"]
        assert "def greet(" in api["files"]["app/greet.py"], skill
        assert not any(path.startswith("tests/") for path in api["files"]), skill
