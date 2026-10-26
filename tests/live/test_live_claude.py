"""B4 live smoke with the real Claude CLI. Runs only with AGENT_FACTORY_LIVE=1.

A skipped run here is NOT_RUN in the acceptance report, never PASS.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path

import pytest
import yaml

from agent_factory.adapters.claude import ClaudeRuntime
from agent_factory.adapters.github import LocalMergeHandler
from agent_factory.adapters.protocol import AgentRequest
from agent_factory.config import load_config
from agent_factory.factory import Factory
from agent_factory.features import normalize
from agent_factory.identities import ResultKind
from agent_factory.sandbox import get_sandbox
from agent_factory.schemas import envelope_json_schema, parse_envelope
from agent_factory.state import Store

from ..conftest import UNIT_CHECK, make_factory_dir, sh, write_checks

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("AGENT_FACTORY_LIVE") != "1", reason="live smoke requires AGENT_FACTORY_LIVE=1"),
    pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed"),
]
LIVE_MODEL = os.environ.get("AGENT_FACTORY_LIVE_MODEL", "claude-haiku-5-5")
REPORT = Path(os.environ.get("AGENT_FACTORY_LIVE_REPORT", "live-smoke.json"))


def _save(name: str, data: dict) -> None:
    existing = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    existing[name] = data
    REPORT.write_text(json.dumps(existing, indent=2, default=str))


def test_live_adapter_structured_call_and_cancel(tmp_path):
    runtime = ClaudeRuntime("claude", get_sandbox(), tmp_path / "logs")
    caps = asyncio.run(runtime.capabilities())
    request = AgentRequest(
        job_id="job-live", attempt_id="att-live", role="code_reviewer", skill="factory-code-review",
        prompt='Return a JSON envelope: schema_version "1", kind "code-review", verdict "PASS", summary "live smoke", '
               'findings [], checked_criteria ["smoke"], limitations [], payload {}. Do not use tools.',
        system_append="", cwd=tmp_path, model=LIVE_MODEL, tools=[], timeout_seconds=180,
        session_id=str(uuid.uuid4()), json_schema=envelope_json_schema(), max_budget_usd=0.25,
    )

    async def go():
        handle = await runtime.start(request)
        end = time.time() + 180
        while time.time() < end:
            snap = await runtime.poll(handle)
            if snap.state not in ("running", "queued"):
                return handle, snap
            await asyncio.sleep(0.5)
        await runtime.cancel(handle)
        raise AssertionError("live call timed out")

    handle, snap = asyncio.run(go())
    assert snap.state == "succeeded", snap.error
    env, _ = parse_envelope(snap.structured or snap.output_text, "code-review")
    assert env.verdict == "PASS"
    # cancellation stops a real running CLI process
    hang = AgentRequest(**{**request.__dict__, "session_id": str(uuid.uuid4()), "prompt": "Count slowly from 1 to 5000, one number per line."})

    async def cancel_it():
        h = await runtime.start(hang)
        await asyncio.sleep(3)
        return h, await runtime.cancel(h)

    h2, cancel = asyncio.run(cancel_it())
    with pytest.raises(ProcessLookupError):
        os.kill(h2.pid, 0)
    _save("adapter", {"capabilities": caps.__dict__, "run_id": handle.run_id, "session_id": handle.session_id, "model_id": snap.model_id,
                      "usage": snap.usage.__dict__, "cancel": cancel.__dict__})


@pytest.fixture
def live_repo(tmp_path):
    from ..conftest import FIXTURE_REPO, git_commit_all
    remote = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "trunk", str(remote))
    work = tmp_path / "user-checkout"
    shutil.copytree(FIXTURE_REPO, work, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sh(work, "git", "init", "-q", "-b", "trunk")
    git_commit_all(work, "initial")
    sh(work, "git", "remote", "add", "origin", str(remote))
    sh(work, "git", "push", "-q", "origin", "trunk")
    sh(work, "git", "remote", "set-head", "origin", "trunk")
    return work


def test_live_af39_small_task_end_to_end(tmp_path, live_repo):
    d = make_factory_dir(tmp_path, **{"budget.hard_limit_per_job": 8.0, "budget.max_cost_per_agent_call": 0.6})
    cfg = yaml.safe_load((d / "config.yaml").read_text())
    for binding in cfg["runtime"]["roles"].values():
        binding["model"] = LIVE_MODEL
        binding.pop("effort", None)
    (d / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    write_checks(d, [UNIT_CHECK])
    config = load_config(d)
    store = Store(config.data_dir() / "state.db")
    sandbox = get_sandbox()
    factory = Factory(config, store, ClaudeRuntime("claude", sandbox, config.data_dir() / "agent-logs"), sandbox,
                      merge_handler=LocalMergeHandler(config.factory.policy), poll_interval=1.0)
    feature = normalize({
        "original_request": "Make greet() in app/greet.py end with an exclamation mark instead of a period, e.g. greet('Ada') == 'Hello, Ada!'. Add a test.",
        "acceptance_criteria": ["greet('Ada') returns exactly 'Hello, Ada!'"],
    }, source_kind="cli", repo_id="greeter")
    job = factory.submit(feature, live_repo)
    started = time.time()
    final = factory.run(job)
    attempts = [dict(r) for r in store.all("SELECT role, state, runtime_run_id, session_id, model_id FROM attempts WHERE job_id = ?", (job,))]
    spend = store.one("SELECT COALESCE(SUM(settled_usd),0) FROM budget_reservations WHERE job_id = ?", (job,))[0]
    transitions = [f"{e['from_state']}->{e['to_state']}" for e in store.events_for(job) if e["kind"] == "state.transition"]
    _save("job", {"final_state": final, "blocked_reason": store.job(job)["blocked_reason"], "seconds": round(time.time() - started),
                  "spend_usd": spend, "attempts": attempts, "transitions": transitions})
    assert final == "READY_TO_MERGE", store.job(job)["blocked_reason"]
    wt = Path(store.job(job)["worktree"])
    assert "Hello, {name}!" in (wt / "app" / "greet.py").read_text() or "!" in (wt / "app" / "greet.py").read_text()
    gate = factory.evidence.latest(job, ResultKind.GATE, "TASK-01")
    assert gate.verdict == "PASS" and gate.producer["component"] == "trusted-runner"
    assert all(a["runtime_run_id"] for a in attempts)
