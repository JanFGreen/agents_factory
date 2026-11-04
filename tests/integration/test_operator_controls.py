"""Operator controls and rework costs from LearnEnglish F5a: pause after some tasks, a rework lane that builds on the
rejected attempt, model escalation only on a repeated failure, and the cost of a killed agent run in the budget."""

from __future__ import annotations

import json

import pytest

from agent_factory import budgets
from agent_factory.adapters.claude import estimate_from_log
from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.controller import TransitionRefused
from agent_factory.recovery import recover_job

from ..conftest import ALL_SCENARIOS_PASS, FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, plan_reply, request_inputs

TASKS = [
    {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [], "file_scope": ["app/greet.py", "tests"],
     "acceptance": "greeting ends with !", "estimated_minutes": 20},
    {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": [], "file_scope": ["app/farewell.py", "tests"],
     "acceptance": "farewell exists", "estimated_minutes": 20},
]


def _reply(edits: dict) -> FakeReply:
    return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [],
                                                    "handoff": "Done: as tasked\nSelf-review: no changes", "claimed_checks": []}), edits=edits)


class Builder:
    """Records whether a TASK-01 rework found its earlier attempt in the worktree."""

    def __init__(self):
        self.found_earlier: list[bool] = []

    def __call__(self, request) -> FakeReply:
        if "task=TASK-02" in request.prompt:
            return _reply({"app/farewell.py": FAREWELL, "tests/test_farewell.py": FAREWELL_TEST})
        if request_inputs(request).get("open_findings"):
            self.found_earlier.append((request.cwd / "tests" / "test_greet_bang.py").exists())
            return _reply({"app/greet.py": GREET_BANG + "# guarded\n"})  # fixes only what was found
        return _reply({"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": GREET_TEST})


def _modes(request) -> list[dict]:
    return [{"path": s["path"], "operation": ", ".join(s["kinds"]), "scenarios": ALL_SCENARIOS_PASS, "evidence": "fixture"}
            for s in request_inputs(request).get("effect_sites", [])]


def changes_once(seen: list):
    """TASK-01's first code review asks for one change; every later review passes."""

    def reply(request) -> FakeReply:
        seen.append(request_inputs(request) | {"_task": "TASK-02" if "task=TASK-02" in request.prompt else "TASK-01"})
        if seen[-1]["_task"] == "TASK-01" and sum(1 for s in seen if s["_task"] == "TASK-01") == 1:
            finding = {"id": "F-1", "severity": "medium", "blocking": True, "requirement_ids": [],
                       "location": {"path": "app/greet.py", "line": 1}, "problem": "race", "expected_fix": "guard it", "status": "open"}
            return FakeReply(envelope("code-review", "CHANGES", payload={"failure_modes": _modes(request)}, findings=[finding]))
        return FakeReply(envelope("code-review", payload={"failure_modes": _modes(request)}))

    return reply


def _states(env, job) -> dict[str, str]:
    return {r["task_id"]: r["state"] for r in env.store.all("SELECT task_id, state FROM tasks WHERE job_id = ?", (job,))}


# --- 1. a rework lane continues the rejected attempt ------------------------------------------


def test_a_rework_lane_builds_on_the_rejected_attempt_and_gets_a_fix_check(make_env):
    builder, seen = Builder(), []
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS), "factory-implement": builder,
                                          "factory-code-review": changes_once(seen)},
                   **{"execution.max_builders_per_job": 2, "review.cheaper_models_for_risk": []})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert builder.found_earlier == [True]  # the rework lane had attempt 1's test file
    assert env.events(job, "lanes.rework_continued")
    second = [s for s in seen if s["_task"] == "TASK-01"][1]
    assert second["review_mode"] == "fix_check" and "# guarded" in second["fix_delta"]
    assert "test_greet_bang.py" not in second["fix_delta"]  # attempt 1's work is not part of the delta
    assert not env.store.one("SELECT 1 FROM events WHERE job_id = ? AND kind = 'lanes.rework_restarted'", (job,))
    assert env.factory._ctx(job).get("lane_rework") == {}  # dropped once the task was accepted


# --- 2. escalation only when a failure repeats --------------------------------------------------


def test_one_failure_does_not_move_the_builder_up_a_level(make_env):
    seen: list = []
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS), "factory-implement": Builder(),
                                          "factory-code-review": changes_once(seen)}, **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    routed = [json.loads(e["payload_json"]) for e in env.events(job, "task.routed")]
    first, second = [r for r in routed if r["task_id"] == "TASK-01"][:2]
    assert second["attempt"] == 2 and second["level"] == first["level"] == first["difficulty"]


@pytest.mark.parametrize("fps,attempt,expected", [
    (["a"], 2, "easy"),             # one failure: same level
    (["a", "a"], 3, "medium"),      # the same failure twice: one level up
    (["a", "b"], 3, "medium"),      # different failures, but the last attempt still gets one level up
    (["a", "b"], 2, "easy"),
])
def test_escalation_counts_repeated_failures(make_env, fps, attempt, expected):
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS)},
                   **{"workflow.implementation.max_attempts_per_leaf_task": 3})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory._set_ctx(job, task_difficulty={"tasks": {"TASK-01": {"difficulty": "easy"}}}, failure_fingerprints={"TASK-01": fps})
    models = env.cfg.factory.routing.builder_models
    assert env.factory._builder_model(job, "TASK-01", attempt) == models[expected]


# --- 3. pause after some tasks, pause now ------------------------------------------------------


@pytest.mark.parametrize("builders", [1, 2])
def test_pause_after_a_task_starts_no_other_and_resume_goes_on(make_env, builders):
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS)}, **{"execution.max_builders_per_job": builders})
    job = env.submit("Add exclamation greeting and farewell")
    while env.state(job) not in ("BUILD_TASK",):  # analysis first: the tasks exist from here
        env.factory.step(job)
    env.factory.request_pause(job, ["TASK-01"], "two tasks only")
    assert env.factory.run(job) == "PAUSED"
    assert _states(env, job) == {"TASK-01": "ACCEPTED", "TASK-02": "PENDING"}
    assert "paused after TASK-01" in env.store.job(job)["blocked_reason"]
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_a_signal_pause_lets_the_running_agent_finish_and_starts_nothing_new(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply([{**TASKS[0]}, {**TASKS[1], "depends_on": ["TASK-01"]}])},
                   **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")

    def implement(request):
        if env.calls("factory-implement") == 1:
            env.factory.stop_requested = "signal SIGINT"  # arrives while the first builder runs
        return Builder()(request)

    env.runtime.responders["factory-implement"] = implement
    assert env.factory.run(job) == "PAUSED"
    assert env.calls("factory-implement") == 1 and _states(env, job)["TASK-02"] == "PENDING"
    env.factory.stop_requested = None  # a new process
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_pause_refuses_unknown_tasks(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS)})
    job = env.submit("Add exclamation greeting and farewell")
    with pytest.raises(Exception, match="unknown tasks"):
        env.factory.request_pause(job, ["TASK-99"])


# --- 4. a killed run's cost --------------------------------------------------------------------


def _log(path, *, model="claude-sonnet-5-5", result=False):
    usage = {"input_tokens": 10, "cache_read_input_tokens": 100_000, "cache_creation_input_tokens": 20_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 20_000}, "output_tokens": 5}
    lines = [{"type": "system", "subtype": "init", "model": model},
             {"type": "assistant", "message": {"id": "m1", "model": model, "usage": usage,
                                               "content": [{"type": "text", "text": "x" * 1800}]}},
             {"type": "assistant", "message": {"id": "m1", "model": model, "usage": usage,  # the same message again
                                               "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"p": 1}}]}}]
    if result:
        lines.append({"type": "result", "usage": usage, "total_cost_usd": 0.5})
    path.write_text("\n".join(json.dumps(line) for line in lines))
    return path


def test_estimate_from_log(tmp_path):
    exact = estimate_from_log(_log(tmp_path / "a.jsonl", result=True))
    assert exact["cost_usd"] == 0.5 and not exact["estimated"]
    est = estimate_from_log(_log(tmp_path / "b.jsonl"))
    out = round((1800 + len(json.dumps({"p": 1}))) / 1.8)
    assert est["estimated"] and est["output_tokens"] == out  # each message counted once, output from its length
    assert est["cost_usd"] == pytest.approx(2.0 * (10 + 0.05 * 100_000 + 2 * 20_000 + 5 * out) / 1e6)
    assert estimate_from_log(_log(tmp_path / "c.jsonl", model="claude-haiku-5-5")) is None
    assert estimate_from_log(tmp_path / "missing.jsonl") is None


@pytest.mark.parametrize("released", [False, True])
def test_recovery_charges_a_killed_run_from_its_log(make_env, tmp_path, released):
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS)})
    job = env.submit("Add exclamation greeting and farewell")
    reservation = budgets.reserve(env.store, env.cfg.factory.budget, job, "build")
    if released:  # Ctrl-C: the call loop released it on the way out
        budgets.release(env.store, reservation)
    log = _log(tmp_path / "killed.jsonl")
    with env.store.tx() as conn:
        conn.execute("INSERT INTO attempts (attempt_id, job_id, task_id, role, state, lease_epoch, runtime, model_id, started_at,"
                     " pid, handle_json, reservation_id) VALUES ('att-dead', ?, 'TASK-01', 'builder', 'running', 1, 'claude',"
                     " 'claude-sonnet-5-5', '2026-11-03', 999999, ?, ?)",
                     (job, json.dumps({"run_id": "r", "pid": 999999, "log": str(log), "session_id": "s", "meta": {}}), reservation))
    before = budgets.status(env.store, job).spent
    report = recover_job(env.factory, job)
    expected = estimate_from_log(log)["cost_usd"]
    assert budgets.status(env.store, job).spent == pytest.approx(before + expected)
    row = env.store.one("SELECT cost_usd, cost_known, source FROM usage WHERE attempt_id = 'att-dead'")
    assert row["cost_usd"] == pytest.approx(expected) and row["cost_known"] == 0 and row["source"] == "recovery:claude"
    assert any("charged" in a for a in report.actions)
    recover_job(env.factory, job)  # idempotent
    assert env.store.one("SELECT COUNT(*) AS n FROM usage WHERE attempt_id = 'att-dead'")["n"] == 1


def test_a_dead_controllers_lease_and_slot_are_freed(make_env):
    """Live finding (LearnEnglish F5a): after a hard stop the next run halted on 'another worker holds lease'."""
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS)})
    job = env.submit("Add exclamation greeting and farewell")
    dead = "controller-999999-abcdef"
    with env.store.tx() as conn:
        conn.execute("INSERT INTO leases VALUES (?, ?, 1, ?, NULL)", (f"{job}:-:ui_reviewer", dead, 4e9))  # not expired
        conn.execute("INSERT INTO resource_allocations (alloc_id, kind, value, job_id, owner, created_at) VALUES"
                     " ('slot-dead', 'slot:agent_process', '1', ?, ?, '2026-11-03')", (job, dead))
    report = recover_job(env.factory, job)
    assert not env.store.one("SELECT 1 FROM leases WHERE owner_id = ?", (dead,))
    assert env.store.one("SELECT released_at FROM resource_allocations WHERE alloc_id = 'slot-dead'")["released_at"]
    assert len(report.actions) == 2


def test_a_hard_stop_frees_its_own_leases_and_slots(make_env):
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS)})
    job = env.submit("Add exclamation greeting and farewell")
    me = env.factory.owner_id
    with env.store.tx() as conn:
        conn.execute("INSERT INTO leases VALUES (?, ?, 1, ?, NULL)", (f"{job}:TASK-01:builder", me, 4e9))
        conn.execute("INSERT INTO resource_allocations (alloc_id, kind, value, job_id, owner, created_at) VALUES"
                     " ('slot-mine', 'slot:agent_process', '1', ?, ?, '2026-11-03')", (job, me))
    env.factory.stop_agents_now([job])
    assert not env.store.one("SELECT 1 FROM leases WHERE owner_id = ?", (me,))
    assert env.store.one("SELECT released_at FROM resource_allocations WHERE alloc_id = 'slot-mine'")["released_at"]


def test_one_controller_runs_a_job_at_a_time(make_env):
    """Live finding (LearnEnglish F5a): the terminal's `factory resume` and the dashboard's Resume ran one job at once;
    the second adopted the first one's reviewer and then stopped the job on its walkthrough lease."""
    env = make_env("standard", responders={"factory-plan": plan_reply(TASKS)})
    job = env.submit("Add exclamation greeting and farewell")
    before = env.state(job)
    other = "controller-1-other"
    with env.store.tx() as conn:  # a live controller (pid 1 is always alive) holds the job
        conn.execute("INSERT INTO leases VALUES (?, ?, 1, ?, 1)", (f"{job}:controller", other, 0))
    with pytest.raises(TransitionRefused, match="another controller is running"):
        env.factory.run(job)
    assert env.state(job) == before  # untouched, not halted
    with env.store.tx() as conn:  # that controller exited (its pid is gone): the lease is free at once
        conn.execute("UPDATE leases SET pid = 999999 WHERE lease_key = ?", (f"{job}:controller",))
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert not env.store.one("SELECT 1 FROM leases WHERE lease_key = ?", (f"{job}:controller",))  # released at the end
