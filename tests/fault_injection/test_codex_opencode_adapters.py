"""Codex and opencode adapters against stand-in binaries with their recorded event formats (no paid calls)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import pytest

from agent_factory.adapters.codex import CodexRuntime
from agent_factory.adapters.opencode import OpencodeRuntime
from agent_factory.adapters.protocol import AgentRequest, ResumeRequest, ResumeUnsupported
from agent_factory.adapters.router import RuntimeRouter
from agent_factory.sandbox import SandboxPolicy, get_sandbox
from agent_factory.schemas import parse_envelope

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def shim(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\nexec {sys.executable} {FIXTURES / f'fake_{name}.py'} \"$@\"\n")
    path.chmod(0o755)
    return str(path)


def req(tmp_path, runtime, prompt="review", model="m", writable=False, options=None, tools=("Read", "Bash")):
    cwd = tmp_path / "repo"
    cwd.mkdir(exist_ok=True)
    log = tmp_path / "argv.json"
    policy = SandboxPolicy(writable=(cwd,) if writable else (), env={"FAKE_ARGV_LOG": str(log)})
    return AgentRequest(job_id="job-1", attempt_id=f"att-{uuid.uuid4().hex[:6]}", role="code_reviewer", skill="factory-code-review",
                        prompt=prompt, system_append="SKILL TEXT", cwd=cwd, model=model, tools=list(tools), timeout_seconds=60,
                        session_id=str(uuid.uuid4()), effort="high", sandbox_policy=policy, runtime=runtime,
                        runtime_options=options or {}), log


async def wait(rt, handle, limit=20):
    end = time.time() + limit
    while time.time() < end:
        snap = await rt.poll(handle)
        if snap.state not in ("running", "queued"):
            return snap
        await asyncio.sleep(0.1)
    return snap


def run(rt, request):
    async def go():
        handle = await rt.start(request)
        return handle, await wait(rt, handle)
    return asyncio.run(go())


@pytest.fixture
def codex(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return CodexRuntime(shim(tmp_path, "codex"), get_sandbox(), tmp_path / "logs", {"OPENAI_API_KEY": "OPENAI_API_KEY"},
                        {"gpt-x": {"input_per_mtok": 1.0, "output_per_mtok": 10.0}})


@pytest.fixture
def opencode(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("GH_TOKEN", "never-passed")
    monkeypatch.setenv("MINI_KEY", "endpoint-secret")
    return OpencodeRuntime(shim(tmp_path, "opencode"), get_sandbox(), tmp_path / "logs",
                           {"GOOGLE_GENERATIVE_AI_API_KEY": "GEMINI_API_KEY"})


# --- codex ---------------------------------------------------------------------------------


def test_codex_runs_the_skill_from_stdin_and_reports_tokens_and_priced_cost(codex, tmp_path):
    request, log = req(tmp_path, "codex", model="gpt-x")
    handle, snap = run(codex, request)
    assert snap.state == "succeeded" and snap.model_id == "gpt-x"
    env, _ = parse_envelope(snap.output_text, "code-review")
    assert env.verdict == "PASS"
    assert snap.usage.input_tokens == 2000 and snap.usage.output_tokens == 100
    assert snap.usage.cost_usd == round(2000 * 1.0 / 1e6 + 100 * 10.0 / 1e6, 6)
    seen = json.loads(log.read_text())
    assert seen["prompt"].startswith("<instructions>\nSKILL TEXT") and seen["prompt"].endswith("review")
    assert "--dangerously-bypass-approvals-and-sandbox" in seen["argv"] and seen["argv"][seen["argv"].index("-m") + 1] == "gpt-x"
    assert 'model_reasoning_effort="high"' in seen["argv"] and seen["env_key"] == "sk-test"
    assert handle.meta["runtime"] == "codex"


def test_codex_unpriced_model_has_unknown_cost(codex, tmp_path):
    assert run(codex, req(tmp_path, "codex", model="other")[0])[1].usage.cost_usd is None


@pytest.mark.parametrize("prompt,retryable", [("AUTHFAIL", False), ("OVERLOADED", True)])
def test_codex_failures_and_whether_a_retry_can_help(codex, tmp_path, prompt, retryable):
    snap = run(codex, req(tmp_path, "codex", prompt=prompt)[0])[1]
    assert snap.state == "failed" and snap.retryable is retryable


def test_codex_exit_without_completion_fails(codex, tmp_path):
    snap = run(codex, req(tmp_path, "codex", prompt="NOFINISH")[0])[1]
    assert snap.state == "failed" and "turn.completed" in snap.error


def test_codex_cancel_and_no_resume(codex, tmp_path):
    async def go():
        handle = await codex.start(req(tmp_path, "codex", prompt="HANG")[0])
        await asyncio.sleep(0.5)
        result = await codex.cancel(handle)
        with pytest.raises(ResumeUnsupported):
            await codex.resume(handle, ResumeRequest("again", 10))
        return result
    assert asyncio.run(go()).stopped


# --- opencode ------------------------------------------------------------------------------


def test_opencode_cloud_model_gets_a_private_config_and_only_its_keys(opencode, tmp_path):
    request, log = req(tmp_path, "opencode", model="google/gemini-x")
    handle, snap = run(opencode, request)
    assert snap.state == "succeeded" and parse_envelope(snap.output_text, "code-review")[0].verdict == "PASS"
    assert snap.usage.input_tokens == 2 * 1100 and snap.usage.output_tokens == 400 and snap.usage.cost_usd == 0.003
    seen = json.loads(log.read_text())
    argv = seen["argv"]
    assert argv[:4] == ["run", "--format", "json", "--pure"] and argv[argv.index("-m") + 1] == "google/gemini-x"
    assert argv[argv.index("--variant") + 1] == "high"
    config = seen["config"]
    assert config["permission"] == {"edit": "deny", "bash": "allow", "webfetch": "deny"} and "provider" not in config
    assert Path(config["instructions"][0]).read_text() == "SKILL TEXT"
    env = seen["env"]
    assert env["GOOGLE_GENERATIVE_AI_API_KEY"] == "g-test" and env["GH_TOKEN"] is None
    assert str(tmp_path / "logs") in env["XDG_CONFIG_HOME"] and str(Path.home()) not in env["XDG_DATA_HOME"].replace(str(tmp_path), "")


def test_opencode_endpoint_model_uses_the_picked_server(opencode, tmp_path):
    endpoint = {"name": "mini-1", "base_url": "http://10.0.0.11:11434/v1", "key_env": "MINI_KEY"}
    request, log = req(tmp_path, "opencode", model="qwen3-coder:30b", writable=True, options={"endpoint": endpoint})
    snap = run(opencode, request)[1]
    assert snap.state == "succeeded"
    seen = json.loads(log.read_text())
    assert seen["argv"][seen["argv"].index("-m") + 1] == "mini-1/qwen3-coder:30b"
    provider = seen["config"]["provider"]["mini-1"]
    assert provider["options"]["baseURL"] == endpoint["base_url"] and "qwen3-coder:30b" in provider["models"]
    assert provider["options"]["apiKey"] == "{env:FACTORY_ENDPOINT_KEY}" and seen["env"]["FACTORY_ENDPOINT_KEY"] == "endpoint-secret"
    assert seen["config"]["permission"]["edit"] == "allow"  # the builder's worktree is writable


def test_opencode_without_bash_tool_denies_the_shell(opencode, tmp_path):
    request, log = req(tmp_path, "opencode", tools=("Read",))
    run(opencode, request)
    assert json.loads(log.read_text())["config"]["permission"]["bash"] == "deny"


def test_opencode_quota_error_is_not_retried(opencode, tmp_path):
    snap = run(opencode, req(tmp_path, "opencode", prompt="QUOTA")[0])[1]
    assert snap.state == "failed" and not snap.retryable and "depleted" in snap.error


# --- router --------------------------------------------------------------------------------


def test_router_sends_calls_and_follow_ups_to_the_runtime_that_started_them(codex, opencode, tmp_path):
    router = RuntimeRouter({"claude": codex, "codex": codex, "opencode": opencode})
    handle, snap = run(router, req(tmp_path, "opencode")[0])
    assert handle.meta["runtime"] == "opencode" and snap.state == "succeeded"
    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(RuntimeRouter({"claude": codex}).start(req(tmp_path, "opencode")[0]))
    with pytest.raises(ValueError):
        RuntimeRouter({"codex": codex})
