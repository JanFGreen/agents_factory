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
import time
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


# How long a process may stay alive after its result event before its process group is stopped.
RESULT_EXIT_GRACE_SECONDS = 15.0


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
        return self._spawn(request, resume=request.resume)

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
        snapshot = await self._snapshot(handle, events)
        snapshot.plan_usage = plan_usage(events)
        return snapshot

    async def _snapshot(self, handle: RunHandle, events: list[dict]) -> RunSnapshot:
        model_id = None
        last_init = -1
        for i, event in enumerate(events):
            if event.get("type") == "system" and event.get("subtype") == "init":
                model_id, last_init = event.get("model"), i
        # Only a result of the current turn ends the run. Live finding (tic-tac-toe2 TASK-09): a resumed session first
        # emitted a 0-turn result for its stopped background shell, then a new init and five more minutes of edits.
        result_at = next((i for i in range(len(events) - 1, last_init, -1) if events[i].get("type") == "result"), None)
        result = events[result_at] if result_at is not None else None
        if result is not None and self._alive(handle):
            # The CLI exits right after its result; one still alive is waiting on something it started (a background
            # shell, a server) that must not outlive the run and write to the worktree under the reviewers.
            seen = handle.meta.get("result_seen")
            if not seen or seen[0] != result_at:
                handle.meta["result_seen"] = seen = [result_at, time.time()]
            if time.time() - seen[1] < RESULT_EXIT_GRACE_SECONDS:
                return RunSnapshot("running", model_id=model_id, events_seen=len(events))
            await self.cancel(handle)  # the result stands; whatever kept the process alive is stopped with its group
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
        status = result.get("api_error_status")
        if is_error and str(status) in _FATAL_STATUSES:
            # Newer CLIs report the rejected request (e.g. a revoked login) on the result event.
            return RunSnapshot("failed", usage=snap_usage, model_id=model_id, events_seen=len(events),
                               error=f"API rejected the request: {status} {text[:500]}", retryable=False)
        error = result.get("subtype")
        if error in (None, "success"):
            error = text[:500] or "error result without a message"
        return RunSnapshot(
            "failed" if is_error else "succeeded",
            output_text=text if structured is None else f"```json\n{json.dumps(structured)}\n```",
            structured=structured,
            usage=snap_usage,
            model_id=model_id,
            events_seen=len(events),
            error=error if is_error else None,
        )


def plan_usage(events: list[dict]) -> dict[str, dict[str, float]] | None:
    """The subscription windows of the last `rate_limit_event` (utilization 0..1 and the reset time)."""
    info = next((e.get("rate_limit_info") or {} for e in reversed(events) if e.get("type") == "rate_limit_event"), None)
    if not info:
        return None
    windows = info.get("unifiedWindows") or {info.get("rateLimitType") or "plan": info}
    out = {name: {"utilization": float(w["utilization"]), "resets_at": float(w.get("resetsAt") or 0)}
           for name, w in windows.items() if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float))}
    return out or None


_FATAL_STATUSES = ("400", "401", "403", "404")
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


# Input price per million tokens; cache reads cost 0.05x, 5-minute cache writes 1.25x, 1-hour writes 2x, output 5x.
# Fitted to the CLI's own `total_cost_usd` over the factory's runs (2026-11: 638 Sonnet, 407 Opus results).
# A model not listed here has no estimate: its killed run is charged the full reservation.
INPUT_PRICE_PER_MTOK = {"claude-opus-5-5": 4.0, "claude-sonnet-5-5": 2.0}


def estimate_from_log(path: Path) -> dict | None:
    """Usage and cost of a run from its event log, for a run killed before its result event.

    A result event, when present, gives the exact cost. Otherwise every assistant message's usage counts once (the
    CLI repeats a message per content block) at the listed prices, with the output estimated from its length. None
    when the log or the model's price is missing.
    """
    try:
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    model = next((e.get("model") for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    messages: dict[str, dict] = {}
    blocks: dict[str, int] = {}  # visible output: text, tool inputs and thinking, each block once
    for event in events:
        message = event.get("message") or {}
        if event.get("type") != "assistant" or message.get("model") == "<synthetic>":
            continue
        if message.get("usage"):
            messages[message.get("id") or str(len(messages))] = message["usage"]
        for block in message.get("content") or []:
            if isinstance(block, dict):
                text = block.get("text") or block.get("thinking") or (json.dumps(block["input"]) if "input" in block else "")
                blocks[json.dumps(block, sort_keys=True)] = len(text)
    usages = [result["usage"]] if result and result.get("usage") else list(messages.values())
    tokens = {k: sum(u.get(k) or 0 for u in usages) for k in ("input_tokens", "cache_read_input_tokens", "output_tokens")}
    if not result:
        # Streamed messages carry their input and cache tokens but not the final output count; the factory's runs
        # average 1.87 visible characters per output token (hidden thinking included), 1.8 errs on the high side.
        tokens["output_tokens"] = max(tokens["output_tokens"], round(sum(blocks.values()) / 1.8))
    m5 = sum((u.get("cache_creation") or {}).get("ephemeral_5m_input_tokens") or 0 for u in usages)
    h1 = sum((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", u.get("cache_creation_input_tokens") or 0)
             for u in usages)
    summary = {"model": model, "input_tokens": tokens["input_tokens"] + tokens["cache_read_input_tokens"] + m5 + h1,
               "output_tokens": tokens["output_tokens"]}
    if result and result.get("total_cost_usd") is not None:
        return {**summary, "cost_usd": result["total_cost_usd"], "estimated": False}
    price = INPUT_PRICE_PER_MTOK.get(model or "")
    if price is None or not usages:
        return None
    units = tokens["input_tokens"] + 0.05 * tokens["cache_read_input_tokens"] + 1.25 * m5 + 2 * h1 + 5 * tokens["output_tokens"]
    return {**summary, "cost_usd": round(units * price / 1e6, 6), "estimated": True}
