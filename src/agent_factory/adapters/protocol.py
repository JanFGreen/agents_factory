"""The factory's runtime contract (blueprint §15.1). Not a copy of any SDK's API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from ..sandbox import SandboxPolicy

RunState = Literal["queued", "running", "waiting", "succeeded", "failed", "cancelled", "unknown"]


@dataclass(frozen=True)
class RuntimeCapabilities:
    name: str
    version: str
    supports_resume: bool
    supports_cancel: bool
    supports_json_schema: bool
    supports_budget_cap: bool
    supports_effort: bool
    reports_usage: bool
    reports_cost: bool


@dataclass(frozen=True)
class AgentRequest:
    job_id: str
    attempt_id: str
    role: str
    skill: str
    prompt: str
    system_append: str
    cwd: Path
    model: str
    tools: list[str]
    timeout_seconds: int
    session_id: str
    effort: str | None = None
    max_turns: int | None = None
    json_schema: dict[str, Any] | None = None
    max_budget_usd: float | None = None
    sandbox_policy: SandboxPolicy | None = None
    lease_epoch: int | None = None


@dataclass
class RunHandle:
    run_id: str
    job_id: str
    attempt_id: str
    session_id: str
    pid: int | None
    log_path: Path | None
    started_at: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass
class RunSnapshot:
    state: RunState
    output_text: str = ""
    structured: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    model_id: str | None = None
    events_seen: int = 0
    error: str | None = None


@dataclass
class CancelResult:
    stopped: bool
    detail: str


@dataclass(frozen=True)
class ResumeRequest:
    prompt: str
    timeout_seconds: int


class ResumeUnsupported(RuntimeError):
    pass


class AgentRuntime(Protocol):
    async def capabilities(self) -> RuntimeCapabilities: ...
    async def start(self, request: AgentRequest) -> RunHandle: ...
    async def poll(self, handle: RunHandle) -> RunSnapshot: ...
    async def cancel(self, handle: RunHandle) -> CancelResult: ...
    async def resume(self, handle: RunHandle, request: ResumeRequest) -> RunHandle: ...
