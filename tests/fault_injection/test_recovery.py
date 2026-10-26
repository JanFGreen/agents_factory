from __future__ import annotations

import json
import subprocess
import sys
import time

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.adapters.protocol import RunSnapshot, Usage
from agent_factory.recovery import recover_all

from ..conftest import GREET_BANG, GREET_TEST


def test_af22_controller_killed_mid_build_adopts_live_builder(make_env):
    env = make_env("trivial")
    job = env.submit()
    for _ in range(50):
        if env.state(job) == "BUILD_TASK":
            break
        env.factory.step(job)
    # simulate: controller started a builder process, then died before recording its result
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    with env.store.tx() as conn:
        conn.execute("UPDATE jobs SET context_json = json_set(context_json, '$.current_task', 'TASK-01') WHERE job_id = ?", (job,))
        conn.execute(
            "INSERT INTO attempts (attempt_id, job_id, task_id, role, state, pid, handle_json, started_at)"
            " VALUES ('att-live', ?, 'TASK-01', 'builder', 'running', ?, ?, 't')",
            (job, proc.pid, json.dumps({"run_id": "fake-live", "pid": proc.pid, "log": None, "session_id": "s", "meta": {}})),
        )
    env.reload()  # new controller instance
    report = recover_all(env.factory)[0]
    assert any("will be adopted" in a for a in report.actions)

    wt = env.store.job(job)["worktree"]

    async def poll_live(handle):
        if proc.poll() is None:
            return RunSnapshot("running")
        from pathlib import Path
        (Path(wt) / "app" / "greet.py").write_text(GREET_BANG)
        (Path(wt) / "tests" / "test_greet_bang.py").write_text(GREET_TEST)
        return RunSnapshot("succeeded", output_text="```json\n" + json.dumps(envelope("implement", payload={})) + "\n```", usage=Usage(1, 1, 0.01))

    env.runtime.poll = poll_live
    starts_before = env.calls("factory-implement")
    env.factory.step(job)  # BUILD_TASK -> adopts instead of starting a second builder
    assert env.calls("factory-implement") == starts_before
    assert env.events(job, "recovery.adopted")
    assert env.state(job) == "CHECKS"


def test_af22_dead_attempt_marked_lost_and_budget_released(make_env):
    env = make_env("trivial")
    job = env.submit()
    with env.store.tx() as conn:
        conn.execute("INSERT INTO attempts (attempt_id, job_id, task_id, role, state, pid, started_at) VALUES ('att-dead', ?, NULL, 'intake', 'running', 999999, 't')", (job,))
        conn.execute("INSERT INTO budget_reservations VALUES ('bud-x', ?, 'analysis', 1.5, 'open', NULL, 't')", (job,))
    report = recover_all(env.factory)[0]
    assert env.store.one("SELECT state FROM attempts WHERE attempt_id = 'att-dead'")["state"] == "lost"
    assert env.store.one("SELECT state FROM budget_reservations WHERE reservation_id = 'bud-x'")["state"] == "released"
    assert any("released" in a for a in report.actions)


def test_af37_restart_preserves_identity_and_budget(make_env):
    env = make_env("trivial")
    job = env.submit()
    for _ in range(60):
        if env.state(job) == "REVIEW":
            break
        env.factory.step(job)
    before_ident = env.factory.controller.identity(job)
    before_budget = env.store.one("SELECT SUM(settled_usd) FROM budget_reservations WHERE job_id = ?", (job,))[0]
    env.reload()
    assert env.factory.controller.identity(job) == before_ident
    assert env.store.one("SELECT SUM(settled_usd) FROM budget_reservations WHERE job_id = ?", (job,))[0] == before_budget
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_af26_transient_provider_failures_retry_without_corrupting_state(make_env):
    env = make_env("trivial", responders={"factory-intake": [
        FakeReply(state="failed"), FakeReply(state="failed"),
        FakeReply(envelope("intake", payload={"goal": "g"})),
    ]})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-intake") == 3
    reservations = env.store.all("SELECT state FROM budget_reservations WHERE job_id = ? AND purpose = 'analysis'", (job,))
    assert all(r["state"] == "settled" for r in reservations)


def test_af26_persistent_provider_failure_blocks_env(make_env):
    env = make_env("trivial", responders={"factory-intake": FakeReply(state="failed")})
    job = env.submit()
    assert env.factory.run(job) == "BLOCKED_ENV"
    assert env.calls("factory-intake") == 3


def test_af28_budget_exhaustion_keeps_checkpoint(make_env):
    env = make_env("trivial", **{"budget.hard_limit_per_job": 2.0})
    job = env.submit()
    assert env.factory.run(job) == "BUDGET_EXHAUSTED"
    assert env.store.job_context(job)["last_checkpoint"]
    from agent_factory import budgets
    budgets.raise_budget(env.store, job, 50, "operator approved")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_invalid_structured_output_gets_one_repair_then_blocks(make_env):
    env = make_env("trivial", responders={"factory-intake": FakeReply(raw_text="I think it's fine")})
    job = env.submit()
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert env.calls("factory-intake") == 2


def test_cancel_stops_job(make_env):
    env = make_env("trivial")
    job = env.submit()
    env.factory.step(job)
    assert env.factory.cancel(job) == "CANCELLED"
    assert env.store.job(job)["active"] == 0
