"""Stalled Claude CLI streams (eval finding): runs hung ~15 min after delivering their structured output."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

from agent_factory.adapters import claude as claude_adapter
from agent_factory.adapters.claude import ClaudeRuntime
from agent_factory.adapters.protocol import RunHandle

ENVELOPE = {"schema_version": "1", "kind": "code-review", "verdict": "PASS", "summary": "ok", "payload": {}}


def _log(tmp_path: Path, events: list[dict]) -> RunHandle:
    log = tmp_path / "run.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return RunHandle("run-1", "job-x", "att-x", "s", None, log, time.time(), meta={})


def _tool_use(input_: dict) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "StructuredOutput", "input": input_}]}}


def _tool_result(text: str, is_error: bool = False) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": text, "is_error": is_error}]}}


def test_stopped_run_with_accepted_structured_output_is_salvaged(tmp_path):
    handle = _log(tmp_path, [{"type": "system", "subtype": "init", "model": "m"}, _tool_use(ENVELOPE),
                             _tool_result("Structured output provided successfully")])
    snap = asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(handle))  # type: ignore[arg-type]
    assert snap.state == "succeeded" and snap.structured == ENVELOPE
    assert snap.usage.cost_usd is None  # unknown cost: the controller charges the full reservation


def test_rejected_or_unconfirmed_structured_output_is_not_salvaged(tmp_path):
    for events in ([_tool_use(ENVELOPE)], [_tool_use(ENVELOPE), _tool_result("schema validation failed", is_error=True)]):
        snap = asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(_log(tmp_path, events)))  # type: ignore[arg-type]
        assert snap.state == "failed"


def _synthetic(text: str) -> dict:
    return {"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}}


TOO_OLD = ('API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"Claude Code 2.1.74 does not'
           ' support this model; version 2.1.280 or newer is required."}}')
NUDGE = {"type": "user", "message": {"content": [{"type": "text", "text": "Stop hook feedback: You MUST call the StructuredOutput tool"}]}}


def test_rejected_api_request_fails_fast_without_retry(tmp_path):
    """Live finding: an outdated CLI looped ~1750 synthetic turns for 10 minutes, then said 'Prompt is too long'."""
    looping = [{"type": "system", "subtype": "init", "model": "claude-opus-5-5"}, _synthetic(TOO_OLD), NUDGE, _synthetic(TOO_OLD)]
    ended = [*looping, {"type": "result", "subtype": "success", "is_error": True, "result": "Prompt is too long", "total_cost_usd": 0}]
    for events in (looping, ended):
        snap = asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(_log(tmp_path, events)))  # type: ignore[arg-type]
        assert snap.state == "failed" and not snap.retryable
        assert "2.1.280 or newer" in (snap.error or "")


def test_rate_limit_and_server_errors_stay_retryable(tmp_path):
    for text in ("API Error: 429 rate limited", "API Error: 529 overloaded", "API Error: 500 internal"):
        snap = asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(_log(tmp_path, [_synthetic(text)])))  # type: ignore[arg-type]
        assert snap.state == "failed" and snap.retryable


def test_rejected_login_on_the_result_event_names_the_reason(tmp_path):
    """Live finding: a revoked OAuth token ended as 'failed: success', hiding the 401 behind the subtype."""
    revoked = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 401, "total_cost_usd": 0,
               "result": "Failed to authenticate: OAuth token revoked. Please log in again or contact your administrator."}
    snap = asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(_log(tmp_path, [revoked])))  # type: ignore[arg-type]
    assert snap.state == "failed" and not snap.retryable
    assert "401" in (snap.error or "") and "OAuth token revoked" in (snap.error or "")
    other = {**revoked, "api_error_status": None, "result": "Prompt is too long"}
    snap = asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(_log(tmp_path, [other])))  # type: ignore[arg-type]
    assert snap.state == "failed" and snap.error == "Prompt is too long"


def _alive_handle(tmp_path: Path, events: list[dict]) -> tuple[RunHandle, subprocess.Popen]:
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    time.sleep(0.2)  # until the child leads its own process group (killpg before that is EPERM on macOS)
    handle = _log(tmp_path, events)
    handle.pid = proc.pid
    return handle, proc


INIT = {"type": "system", "subtype": "init", "model": "claude-opus-5-5"}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "done", "total_cost_usd": 0.5,
          "structured_output": ENVELOPE}


def test_a_result_before_the_last_init_does_not_end_a_resumed_run(tmp_path):
    """Live finding (tic-tac-toe2 TASK-09): a resumed session's 0-turn result ended the run while it kept editing."""
    events = [INIT, {**RESULT, "num_turns": 0, "structured_output": None}, INIT, _tool_use({"file_path": "src/ui/app.css"})]
    handle, proc = _alive_handle(tmp_path, events)
    try:
        assert asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(handle)).state == "running"  # type: ignore[arg-type]
        proc.kill()
        proc.wait()
        snap = asyncio.run(ClaudeRuntime("claude", None, tmp_path).poll(handle))  # type: ignore[arg-type]
        assert snap.state == "failed" and "without result event" in (snap.error or "")
    finally:
        proc.kill()


def test_a_process_alive_after_its_result_is_stopped_before_the_run_ends(tmp_path, monkeypatch):
    handle, proc = _alive_handle(tmp_path, [INIT, RESULT])
    runtime = ClaudeRuntime("claude", None, tmp_path)  # type: ignore[arg-type]
    runtime._procs[handle.run_id] = proc  # as after a launch: the runtime reaps its own child
    try:
        assert asyncio.run(runtime.poll(handle)).state == "running"  # within the grace: the CLI may still be exiting
        assert proc.poll() is None
        monkeypatch.setattr(claude_adapter, "RESULT_EXIT_GRACE_SECONDS", 0.0)
        snap = asyncio.run(runtime.poll(handle))
        assert snap.state == "succeeded" and snap.structured == ENVELOPE
        assert proc.wait(timeout=10) is not None  # its process group was stopped
    finally:
        proc.kill()
