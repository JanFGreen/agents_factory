"""Claude runtime adapter over the documented programmatic CLI mode (``claude -p``).

Uses ``--output-format stream-json`` (documented event stream with a final
``result`` event carrying ``session_id``, ``usage`` and ``total_cost_usd``),
``--session-id``/``--resume`` for resumption, ``--json-schema`` for structured
output, ``--max-budget-usd`` as a provider-side cap, and runs the whole process
tree inside the enforced sandbox. Private transcripts are never parsed.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from ..sandbox import Sandbox
from .cli_base import CliRuntime
from .protocol import (
    AgentRequest,
    ResumeRequest,
    RunHandle,
    RunSnapshot,
    RuntimeCapabilities,
    Usage,
)


class ClaudeRuntime(CliRuntime):
    name = "claude"
    home_writable = (".claude", ".claude.json", ".claude.json.backup", ".cache", "Library/Caches", ".local/state",
                     ".local/share/claude")

    def __init__(self, binary: str, sandbox: Sandbox, log_dir: Path):
        super().__init__(binary, sandbox, log_dir)  # CLI auth lives in the user's keychain/config, not in env

    # --- discovery -----------------------------------------------------------------

    async def capabilities(self) -> RuntimeCapabilities:
        version = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        help_text = subprocess.run([self.binary, "--help"], capture_output=True, text=True, timeout=30).stdout
        has = lambda flag: flag in help_text  # noqa: E731
        return RuntimeCapabilities(
            name="claude-code-cli",
            version=version,
            supports_resume=has("--resume") and has("--session-id"),
            supports_cancel=True,  # process-group kill, verified by poll
            supports_json_schema=has("--json-schema"),
            supports_budget_cap=has("--max-budget-usd"),
            supports_effort=has("--effort"),
            reports_usage=has("stream-json"),
            reports_cost=has("stream-json"),
        )

    # --- lifecycle -------------------------------------------------------------------

    def _argv(self, request: AgentRequest, *, resume: bool) -> list[str]:
        argv = [
            self.binary, "-p", request.prompt,
            "--output-format", "stream-json", "--verbose",
            "--model", request.model,
            "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--disable-slash-commands",
            "--setting-sources", "project",
        ]
        argv += ["--resume", request.session_id] if resume else ["--session-id", request.session_id]
        if request.tools:
            argv += ["--tools", ",".join(request.tools), "--allowedTools", ",".join(request.tools)]
        else:
            argv += ["--tools", ""]
        if request.system_append:
            argv += ["--append-system-prompt", request.system_append]
        if request.effort:
            argv += ["--effort", request.effort]
        if request.json_schema is not None:
            argv += ["--json-schema", json.dumps(request.json_schema)]
        if request.max_budget_usd is not None:
            argv += ["--max-budget-usd", f"{request.max_budget_usd:.2f}"]
        return argv

    def _spawn(self, request: AgentRequest, *, resume: bool) -> RunHandle:
        return self._launch(request, self._argv(request, resume=resume), self._policy(request))

    async def start(self, request: AgentRequest) -> RunHandle:
        uuid.UUID(request.session_id)  # the CLI requires a valid UUID
        return self._spawn(request, resume=False)

    async def resume(self, handle: RunHandle, request: ResumeRequest) -> RunHandle:
        snapshot = await self.poll(handle)
        if snapshot.state in ("running", "queued", "waiting"):
            raise RuntimeError("previous session is still running; cancel or wait before resuming")
        original = AgentRequest(
            job_id=handle.job_id, attempt_id=handle.attempt_id, role="resume", skill="resume", prompt=request.prompt,
            system_append="", cwd=Path(handle.meta["cwd"]), model=handle.meta["model"], tools=handle.meta["tools"],
            timeout_seconds=request.timeout_seconds, session_id=handle.session_id,
        )
        return self._spawn(original, resume=True)

    async def poll(self, handle: RunHandle) -> RunSnapshot:
        events = self._events(handle)
        if events is None:
            return RunSnapshot("unknown", error="no event log")
        model_id = None
        for event in events:
            if event.get("type") == "system" and event.get("subtype") == "init":
                model_id = event.get("model")
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        fatal = _fatal_api_error(events)
        if fatal is not None:
            # The CLI keeps re-prompting itself after a rejected request (old CLI for the model,
            # auth, bad request) until its own timeout; stop it now, a retry would fail the same way.
            if result is None and self._alive(handle):
                await self.cancel(handle)
            return RunSnapshot("failed", model_id=model_id, events_seen=len(events), error=f"API rejected the request: {fatal}",
                               retryable=False)
        if result is None:
            if self._alive(handle):
                return RunSnapshot("running", model_id=model_id, events_seen=len(events))
            delivered = _delivered_structured_output(events)
            if delivered is not None:
                # The CLI accepted the schema-validated output but the stream stalled before its result
                # event. The controller re-validates it; the cost is unknown (settled at the reservation).
                return RunSnapshot("succeeded", output_text=f"```json\n{json.dumps(delivered)}\n```", structured=delivered,
                                   model_id=model_id, events_seen=len(events))
            return RunSnapshot("failed", model_id=model_id, events_seen=len(events),
                               error=f"exited without result event: {self._stderr(handle)}")
        usage = result.get("usage") or {}
        snap_usage = Usage(
            input_tokens=(usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0),
            output_tokens=usage.get("output_tokens"),
            cost_usd=result.get("total_cost_usd"),
        )
        structured = result.get("structured_output")
        text = result.get("result") or ""
        if structured is not None and not isinstance(structured, dict):
            structured = None
        is_error = bool(result.get("is_error")) or result.get("subtype") not in (None, "success")
        return RunSnapshot(
            "failed" if is_error else "succeeded",
            output_text=text if structured is None else f"```json\n{json.dumps(structured)}\n```",
            structured=structured,
            usage=snap_usage,
            model_id=model_id,
            events_seen=len(events),
            error=result.get("subtype") if is_error else None,
        )


_FATAL_API_ERROR = re.compile(r"^API Error: (400|401|403|404)\b")


def _fatal_api_error(events: list[dict]) -> str | None:
    """A non-retryable API error the CLI reported as a synthetic assistant message."""
    for event in events:
        message = event.get("message") or {}
        if event.get("type") != "assistant" or message.get("model") != "<synthetic>":
            continue
        for block in message.get("content") or []:
            text = block.get("text", "") if isinstance(block, dict) else ""
            if _FATAL_API_ERROR.match(text):
                return text[:500]
    return None


def _delivered_structured_output(events: list[dict]) -> dict | None:
    """The StructuredOutput tool input, but only once the CLI confirmed it ("provided successfully")."""
    calls: dict[str, dict] = {}
    for event in events:
        content = (event.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
                if isinstance(block.get("input"), dict):
                    calls[block.get("id")] = block["input"]
            elif event.get("type") == "user" and block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                text = block.get("content")
                text = text if isinstance(text, str) else json.dumps(text)
                if not block.get("is_error") and "provided successfully" in text:
                    return calls[block["tool_use_id"]]
    return None
