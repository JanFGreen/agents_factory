"""Scripted runtime for tests only. Never selectable in a live configuration."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .protocol import AgentRequest, CancelResult, ResumeRequest, RunHandle, RunSnapshot, RuntimeCapabilities, Usage


@dataclass
class FakeReply:
    """A scripted agent turn: the JSON envelope to return and optional file edits."""

    envelope: dict[str, Any] | None = None
    edits: dict[str, str | None] | None = None  # path -> content (None deletes)
    raw_text: str | None = None
    state: str = "succeeded"
    cost_usd: float | None = 0.01
    hang: bool = False


Responder = Callable[[AgentRequest], FakeReply]


def envelope(kind: str, verdict: str = "PASS", payload: dict | None = None, findings: list | None = None, summary: str = "") -> dict:
    return {
        "schema_version": "1",
        "kind": kind,
        "verdict": verdict,
        "summary": summary or f"{kind} {verdict}",
        "findings": findings or [],
        "checked_criteria": ["scripted"],
        "limitations": [],
        "payload": payload or {},
    }


class FakeRuntime:
    def __init__(self, responders: dict[str, Responder | list[FakeReply] | FakeReply]):
        self.responders = responders
        self.calls: list[AgentRequest] = []
        self._results: dict[str, RunSnapshot] = {}
        self.cancelled: list[str] = []

    async def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities("fake", "test", True, True, True, True, True, True, True)

    def _reply(self, request: AgentRequest) -> FakeReply:
        responder = self.responders.get(request.skill) or self.responders.get(request.role)
        if responder is None:
            raise KeyError(f"no fake reply scripted for skill {request.skill!r} / role {request.role!r}")
        if isinstance(responder, FakeReply):
            return responder
        if isinstance(responder, list):
            if not responder:
                raise KeyError(f"scripted replies for {request.skill!r} exhausted")
            item = responder.pop(0) if len(responder) > 1 else responder[0]
            return item(request) if callable(item) else item
        return responder(request)

    async def start(self, request: AgentRequest) -> RunHandle:
        self.calls.append(request)
        reply = self._reply(request)
        run_id = f"fake-{uuid.uuid4().hex[:8]}"
        if reply.hang:
            self._results[run_id] = RunSnapshot("running")
        else:
            for rel, content in (reply.edits or {}).items():
                target = (request.cwd / rel).resolve()
                if not str(target).startswith(str(request.cwd.resolve())):
                    raise PermissionError(f"fake edit escapes cwd: {rel}")
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
            text = reply.raw_text if reply.raw_text is not None else f"```json\n{json.dumps(reply.envelope)}\n```"
            self._results[run_id] = RunSnapshot(
                reply.state, output_text=text, usage=Usage(100, 50, reply.cost_usd), model_id=request.model, events_seen=1
            )
        return RunHandle(run_id, request.job_id, request.attempt_id, request.session_id, None, None, time.time())

    async def poll(self, handle: RunHandle) -> RunSnapshot:
        return self._results.get(handle.run_id, RunSnapshot("unknown"))

    async def cancel(self, handle: RunHandle) -> CancelResult:
        self.cancelled.append(handle.run_id)
        self._results[handle.run_id] = RunSnapshot("cancelled")
        return CancelResult(True, "fake cancelled")

    async def resume(self, handle: RunHandle, request: ResumeRequest) -> RunHandle:
        return handle


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
