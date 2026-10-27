"""Claude runtime adapter over the documented programmatic CLI mode (``claude -p``).

Uses ``--output-format stream-json`` (documented event stream with a final
``result`` event carrying ``session_id``, ``usage`` and ``total_cost_usd``),
``--session-id``/``--resume`` for resumption, ``--json-schema`` for structured
output, ``--max-budget-usd`` as a provider-side cap, and runs the whole process
tree inside the enforced sandbox. Private transcripts are never parsed.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

from ..sandbox import Sandbox, SandboxPolicy
from .protocol import (
    AgentRequest,
    CancelResult,
    ResumeRequest,
    RunHandle,
    RunSnapshot,
    RuntimeCapabilities,
    Usage,
)

# Environment the agent process may see. Operator tokens (GH_TOKEN, cloud keys…) never pass.
_AGENT_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL", "TMPDIR")


class ClaudeRuntime:
    def __init__(self, binary: str, sandbox: Sandbox, log_dir: Path):
        self.binary = binary
        self.sandbox = sandbox
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        self._procs: dict[str, subprocess.Popen] = {}

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

    def _policy(self, request: AgentRequest) -> SandboxPolicy:
        home = Path.home()
        # cwd is writable only when the controller granted it (builder); reviewers stay read-only.
        writable = [
            home / ".claude", home / ".claude.json", home / ".claude.json.backup", home / ".cache",
            home / "Library" / "Caches", home / ".local" / "state", home / ".local" / "share" / "claude",
            Path(os.environ.get("TMPDIR", "/tmp")), Path("/private/tmp"), Path("/private/var/folders"),
        ]
        base = request.sandbox_policy
        denied = base.denied_read if base else ()
        granted = base.writable if base else ()
        read_only = () if any(Path(os.path.realpath(request.cwd)) == Path(os.path.realpath(p)) for p in granted) else (request.cwd,)
        return SandboxPolicy(writable=tuple(writable) + granted, denied_read=denied, network="full", denied_write=read_only)

    def _spawn(self, request: AgentRequest, *, resume: bool) -> RunHandle:
        run_id = f"claude-{uuid.uuid4().hex[:12]}"
        log_path = self.log_dir / f"{run_id}.jsonl"
        err_path = self.log_dir / f"{run_id}.stderr"
        env = {k: os.environ[k] for k in _AGENT_ENV_KEYS if k in os.environ}
        env["HOME"] = str(Path.home())
        env["PYTHONDONTWRITEBYTECODE"] = "1"  # CLI auth lives in the user's keychain/config, not in env
        argv = self.sandbox.wrap(self._argv(request, resume=resume), self._policy(request))
        with open(log_path, "wb") as out, open(err_path, "wb") as err:
            proc = subprocess.Popen(
                argv, cwd=request.cwd, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL, start_new_session=True
            )
        self._procs[run_id] = proc
        return RunHandle(
            run_id, request.job_id, request.attempt_id, request.session_id, proc.pid, log_path, time.time(),
            meta={
                "stderr": str(err_path), "deadline": time.time() + request.timeout_seconds, "model": request.model,
                "cwd": str(request.cwd), "tools": list(request.tools),
            },
        )

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

    def _alive(self, handle: RunHandle) -> bool:
        proc = self._procs.get(handle.run_id)
        if proc is not None:
            return proc.poll() is None
        if handle.pid is None:
            return False
        try:
            os.kill(handle.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def poll(self, handle: RunHandle) -> RunSnapshot:
        if handle.log_path is None or not handle.log_path.exists():
            return RunSnapshot("unknown", error="no event log")
        events = []
        for line in handle.log_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        model_id = None
        for event in events:
            if event.get("type") == "system" and event.get("subtype") == "init":
                model_id = event.get("model")
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if result is None:
            if self._alive(handle):
                return RunSnapshot("running", model_id=model_id, events_seen=len(events))
            delivered = _delivered_structured_output(events)
            if delivered is not None:
                # The CLI accepted the schema-validated output but the stream stalled before its result
                # event. The controller re-validates it; the cost is unknown (settled at the reservation).
                return RunSnapshot("succeeded", output_text=f"```json\n{json.dumps(delivered)}\n```", structured=delivered,
                                   model_id=model_id, events_seen=len(events))
            stderr = Path(handle.meta.get("stderr", "")).read_text(errors="replace")[-2000:] if handle.meta.get("stderr") else ""
            return RunSnapshot("failed", model_id=model_id, events_seen=len(events), error=f"exited without result event: {stderr}")
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

    async def cancel(self, handle: RunHandle) -> CancelResult:
        if handle.pid is None:
            return CancelResult(False, "no pid")
        for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(handle.pid, sig)
            except ProcessLookupError:
                break
            deadline = time.time() + wait
            while time.time() < deadline and self._alive(handle):
                await asyncio.sleep(0.1)
            if not self._alive(handle):
                break
        proc = self._procs.get(handle.run_id)
        if proc is not None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        stopped = not self._alive(handle)
        return CancelResult(stopped, "process group terminated" if stopped else "process still alive")


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
