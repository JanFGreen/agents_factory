"""Stalled Claude CLI streams (eval finding): runs hung ~15 min after delivering their structured output."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

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
