"""Parallel task lanes (execution.max_builders_per_job > 1)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.lanes import pick_wave, scopes_overlap

from ..conftest import FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, plan_reply, review_pass, sh

INDEPENDENT = [
    {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [], "file_scope": ["app/greet.py", "tests"],
     "acceptance": "greeting ends with !", "estimated_minutes": 20},
    {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": [], "file_scope": ["app/farewell.py", "tests"],
     "acceptance": "farewell exists", "estimated_minutes": 20},
]


def _task(request) -> str:
    return "TASK-02" if "task=TASK-02" in request.prompt else "TASK-01"


def _impl(edits: dict[str, str]) -> FakeReply:
    return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [], "handoff": "done",
                                                    "claimed_checks": []}), edits=edits)


EDITS = {"TASK-01": {"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": GREET_TEST},
         "TASK-02": {"app/farewell.py": FAREWELL, "tests/test_farewell.py": FAREWELL_TEST}}


def _env(make_env, responders: dict):
    return make_env("standard", responders={"factory-plan": plan_reply(INDEPENDENT), **responders},
                    **{"execution.max_builders_per_job": 2})


def _run(env, job) -> str:
    final = env.factory.run(job)
    if final != "READY_TO_MERGE":
        print("blocked:", env.store.job(job)["blocked_reason"])
    return final


def _tasks(env, job) -> dict[str, dict]:
    return {r["task_id"]: dict(r) for r in env.store.all("SELECT task_id, state, attempts FROM tasks WHERE job_id = ?", (job,))}


def test_independent_tasks_build_at_the_same_time_and_both_land(make_env):
    together = threading.Barrier(2, timeout=20)  # breaks (and fails the build) if the builders run one after the other

    def implement(request):
        together.wait()
        return _impl(EDITS[_task(request)])

    env = _env(make_env, {"factory-implement": implement})
    job = env.submit("Add exclamation greeting and farewell")
    assert _run(env, job) == "READY_TO_MERGE"
    assert {t["state"] for t in _tasks(env, job).values()} == {"ACCEPTED"}
    assert all(t["attempts"] == 1 for t in _tasks(env, job).values())
    wt = Path(env.store.job(job)["worktree"])
    assert (wt / "app" / "farewell.py").read_text() == FAREWELL and (wt / "app" / "greet.py").read_text() == GREET_BANG
    builds = [c for c in env.runtime.calls if c.skill == "factory-implement"]
    assert {c.cwd.name for c in builds} == {f"{job}--task-01", f"{job}--task-02"}  # each builder had its own lane
    assert not any(p.name.startswith(f"{job}--") for p in wt.parent.iterdir())  # lanes are removed
    assert any(e["kind"] == "lanes.started" for e in env.store.events_for(job))
    assert "--" not in sh(wt, "git", "worktree", "list")  # no lane left registered


def test_lane_agents_cannot_read_the_job_worktree_or_other_lanes(make_env):
    env = _env(make_env, {"factory-implement": lambda r: _impl(EDITS[_task(r)])})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    wt = Path(env.store.job(job)["worktree"])
    for call in (c for c in env.runtime.calls if c.cwd.name.startswith(f"{job}--")):
        denied = {Path(p).resolve() for p in call.sandbox_policy.denied_read}
        assert wt.resolve() in denied
        assert call.cwd.resolve() not in denied


def test_merge_conflict_requeues_the_later_lane_without_using_its_attempt(make_env):
    shared = "tests/test_shared.py"

    def implement(request):
        task = _task(request)
        return _impl({**EDITS[task], shared: f"def test_{task.lower().replace('-', '_')}():\n    assert 1 + 1 == 2\n"})

    env = _env(make_env, {"factory-implement": implement})
    job = env.submit("Add exclamation greeting and farewell")
    assert _run(env, job) == "READY_TO_MERGE"
    tasks = _tasks(env, job)
    assert tasks["TASK-01"]["attempts"] == 1 and tasks["TASK-02"]["attempts"] == 1  # requeue + rebuild = one attempt
    requeued = [json.loads(e["payload_json"]) for e in env.store.events_for(job) if e["kind"] == "task.requeued"]
    assert requeued and requeued[0]["task_id"] == "TASK-02" and requeued[0]["verdict"] == "MERGE_CONFLICT"


def test_lanes_that_break_each_other_are_caught_by_checks_on_the_combined_tree(make_env):
    old_behaviour = 'from app.greet import greet\n\n\ndef test_plain():\n    assert greet("Ada") == "Hello, Ada."\n'
    calls = {"TASK-02": 0}

    def implement(request):
        task = _task(request)
        if task == "TASK-01":
            return _impl(EDITS["TASK-01"])
        calls[task] += 1
        # First try relies on the old greeting (passes alone in its lane, fails next to TASK-01).
        test = old_behaviour if calls[task] == 1 else FAREWELL_TEST
        return _impl({"app/farewell.py": FAREWELL, "tests/test_farewell.py": test})

    env = _env(make_env, {"factory-implement": implement})
    job = env.submit("Add exclamation greeting and farewell")
    assert _run(env, job) == "READY_TO_MERGE"
    requeued = [json.loads(e["payload_json"]) for e in env.store.events_for(job) if e["kind"] == "task.requeued"]
    assert [r["verdict"] for r in requeued] == ["COMBINED_FAIL_CODE"]
    assert _tasks(env, job)["TASK-02"]["attempts"] == 1
    wt = Path(env.store.job(job)["worktree"])
    assert "Hello, Ada." not in (wt / "tests" / "test_farewell.py").read_text()


def test_review_changes_in_one_lane_do_not_stop_the_other(make_env):
    seen = {"TASK-02": 0}

    def code_review(request):
        if _task(request) == "TASK-02":
            seen["TASK-02"] += 1
            if seen["TASK-02"] == 1:
                return FakeReply(envelope("code-review", "CHANGES", payload={}, findings=[
                    {"id": "CR-1", "severity": "high", "blocking": True, "problem": "farewell lacks a docstring", "status": "open"}]))
        return review_pass("code-review")

    env = _env(make_env, {"factory-implement": lambda r: _impl(EDITS[_task(r)]), "factory-code-review": code_review})
    job = env.submit("Add exclamation greeting and farewell")
    assert _run(env, job) == "READY_TO_MERGE"
    tasks = _tasks(env, job)
    assert tasks["TASK-01"]["attempts"] == 1 and tasks["TASK-02"]["attempts"] == 2
    rework = [json.loads(e["payload_json"]) for e in env.store.events_for(job) if e["kind"] == "task.rework"]
    assert [r["task_id"] for r in rework] == ["TASK-02"]


def test_budget_stop_in_a_lane_keeps_the_other_lane_and_returns_the_attempt(make_env, monkeypatch):
    from agent_factory import budgets

    env = _env(make_env, {"factory-implement": lambda r: _impl(EDITS[_task(r)])})
    real_call = env.factory.call_agent
    refused: list[str] = []

    def call_agent(job_id, skill, inputs, **kw):
        if skill == "factory-implement" and kw.get("task_id") == "TASK-02" and not refused:
            refused.append("TASK-02")
            raise budgets.BudgetExhausted("reserving $1.50 for build would exceed the limit")
        return real_call(job_id, skill, inputs, **kw)

    monkeypatch.setattr(env.factory, "call_agent", call_agent)
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "BUDGET_EXHAUSTED"
    tasks = _tasks(env, job)
    assert tasks["TASK-01"]["state"] == "ACCEPTED"
    assert tasks["TASK-02"] == {"task_id": "TASK-02", "state": "PENDING", "attempts": 0}
    env.factory.resume(job)
    assert _run(env, job) == "READY_TO_MERGE"


def test_controller_crash_mid_wave_is_recovered_on_the_next_step(make_env):
    env = _env(make_env, {"factory-implement": lambda r: _impl(EDITS[_task(r)])})
    job = env.submit("Add exclamation greeting and farewell")
    for _ in range(60):
        if env.state(job) == "BUILD_TASK":
            break
        env.factory.step(job)
    job_row = env.store.job(job)
    lane = env.factory._lane_path(job, "TASK-01")
    sh(Path(job_row["repo_path"]), "git", "worktree", "add", "-q", "--detach", str(lane), job_row["head_sha"])
    with env.store.tx() as conn:
        conn.execute("UPDATE tasks SET state = 'BUILDING', attempts = 1 WHERE job_id = ?", (job,))
    env.store.update_job_context(job, parallel_wave=["TASK-01", "TASK-02"])
    assert _run(env, job) == "READY_TO_MERGE"
    assert any(e["kind"] == "lanes.recovered" for e in env.store.events_for(job))
    assert all(t["attempts"] == 1 for t in _tasks(env, job).values())


def test_one_builder_per_job_builds_sequentially(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(INDEPENDENT)}, **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    assert _run(env, job) == "READY_TO_MERGE"
    assert not any(e["kind"] == "lanes.started" for e in env.store.events_for(job))


@pytest.mark.parametrize("a,b,overlap", [
    (["src/domain/srs.ts"], ["src/domain/queue.ts"], False),
    (["src/domain/"], ["src/domain/queue.ts"], True),
    (["src/ui/*.tsx"], ["src/ui/Card.tsx"], True),
    (["src/a.ts", "tests"], ["src/b.ts", "tests/test_b.py"], False),  # test paths never decide
    (["src/a.ts", "src/a.test.ts"], ["src/b.ts", "src/a.test.ts"], False),
    ([], ["src/b.ts"], True),
    (["tests"], ["src/b.ts"], True),  # no code scope = unknown = conservative
])
def test_scope_overlap(a, b, overlap):
    assert scopes_overlap(a, b) is overlap


def test_wave_takes_disjoint_tasks_in_plan_order_up_to_the_limit():
    tasks = [("T1", ["src/a.ts"]), ("T2", ["src/a.ts"]), ("T3", ["src/b.ts"]), ("T4", ["src/c.ts"])]
    assert pick_wave(tasks, 2) == ["T1", "T3"]
    assert pick_wave(tasks, 4) == ["T1", "T3", "T4"]
