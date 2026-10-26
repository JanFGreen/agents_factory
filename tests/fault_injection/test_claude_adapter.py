"""Adapter mechanics against a stand-in binary (no paid calls). The real live smoke is tests/live."""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

from agent_factory.adapters.claude import ClaudeRuntime
from agent_factory.adapters.protocol import AgentRequest, ResumeRequest
from agent_factory.sandbox import get_sandbox

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude.py"


@pytest.fixture
def runtime(tmp_path):
    shim = tmp_path / "claude"
    shim.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    shim.chmod(0o755)
    return ClaudeRuntime(str(shim), get_sandbox(), tmp_path / "logs")


def req(tmp_path, prompt="review", timeout=60):
    return AgentRequest(job_id="job-1", attempt_id="att-1", role="code_reviewer", skill="factory-code-review", prompt=prompt,
                        system_append="", cwd=tmp_path, model="claude-sonnet-5-5", tools=["Read"], timeout_seconds=timeout,
                        session_id=str(uuid.uuid4()), json_schema={"type": "object"}, max_budget_usd=0.5)


async def wait(rt, handle, limit=20):
    end = time.time() + limit
    while time.time() < end:
        snap = await rt.poll(handle)
        if snap.state not in ("running", "queued"):
            return snap
        await asyncio.sleep(0.1)
    return snap


def test_capabilities_are_discovered(runtime):
    caps = asyncio.run(runtime.capabilities())
    assert caps.supports_resume and caps.supports_json_schema and caps.supports_budget_cap and caps.version.startswith("9.9.9")


def test_start_poll_reports_usage_cost_and_structured_output(runtime, tmp_path):
    async def go():
        h = await runtime.start(req(tmp_path))
        return h, await wait(runtime, h)
    handle, snap = asyncio.run(go())
    assert snap.state == "succeeded" and snap.structured["verdict"] == "PASS"
    assert snap.usage.cost_usd == 0.0123 and snap.usage.input_tokens == 12 and snap.model_id == "claude-sonnet-5-5"
    argv_log = handle.log_path.read_text()
    assert handle.session_id in argv_log


def test_cancel_really_stops_the_process(runtime, tmp_path):
    async def go():
        h = await runtime.start(req(tmp_path, "HANG"))
        await asyncio.sleep(0.5)
        assert (await runtime.poll(h)).state == "running"
        result = await runtime.cancel(h)
        return h, result, await runtime.poll(h)
    handle, result, snap = asyncio.run(go())
    assert result.stopped
    with pytest.raises(ProcessLookupError):
        os.kill(handle.pid, 0)
    assert snap.state == "failed"


def test_crash_without_result_is_failed_not_success(runtime, tmp_path):
    async def go():
        h = await runtime.start(req(tmp_path, "CRASH"))
        return await wait(runtime, h)
    assert asyncio.run(go()).state == "failed"


def test_resume_uses_same_session(runtime, tmp_path):
    async def go():
        h = await runtime.start(req(tmp_path))
        await wait(runtime, h)
        h2 = await runtime.resume(h, ResumeRequest("continue", 30))
        return h, h2, await wait(runtime, h2)
    h, h2, snap = asyncio.run(go())
    assert h2.session_id == h.session_id and snap.state == "succeeded"


def test_agent_process_is_sandboxed(runtime, tmp_path):
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    from agent_factory.sandbox import SandboxPolicy
    policy = runtime._policy(AgentRequest(**{**req(tmp_path).__dict__, "sandbox_policy": SandboxPolicy(writable=(), denied_read=(secret_dir,))}))
    assert Path(os.path.realpath(secret_dir)) in policy.resolved().denied_read and policy.network == "full"
    argv = runtime._argv(req(tmp_path), resume=False)
    assert "--permission-mode" in argv and "dontAsk" in argv and "--strict-mcp-config" in argv
