"""Codex CLI runtime (OpenAI models) over ``codex exec --json``.

Events (JSONL): ``thread.started`` (thread id), ``item.*`` (agent messages, commands),
``turn.completed`` (usage), ``turn.failed`` / ``error``. Codex's own command sandbox cannot start
inside the factory sandbox, so it runs with ``--dangerously-bypass-approvals-and-sandbox``, which
its docs reserve for externally sandboxed environments: the factory sandbox is that boundary.
Codex has no structured-output flag the factory relies on, no budget cap and reports tokens, not
cost; the cost comes from ``prices`` in models.yaml when the model has one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..sandbox import Sandbox
from .cli_base import CliRuntime
from .protocol import AgentRequest, ResumeRequest, ResumeUnsupported, RunHandle, RunSnapshot, RuntimeCapabilities, Usage

# Errors a retry of the same call cannot fix (auth, unknown model, bad request).
_FATAL = re.compile(r"\b(400|401|403|404)\b|unauthori[sz]ed|not supported|does not exist|invalid model|model .* not found", re.I)


class CodexRuntime(CliRuntime):
    name = "codex"
    home_writable = (".codex", ".cache", "Library/Caches", ".local/state")

    def __init__(self, binary: str, sandbox: Sandbox, log_dir: Path, pass_env: dict[str, str] | None = None,
                 prices: dict[str, dict[str, float]] | None = None):
        super().__init__(binary, sandbox, log_dir, pass_env)
        self.prices = prices or {}

    async def capabilities(self) -> RuntimeCapabilities:
        version = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        return RuntimeCapabilities(
            name="codex-cli", version=version, supports_resume=False, supports_cancel=True, supports_json_schema=False,
            supports_budget_cap=False, supports_effort=True, reports_usage=True, reports_cost=False,
        )

    def _argv(self, request: AgentRequest, last_message: Path) -> list[str]:
        argv = [self.binary, "exec", "--json", "--skip-git-repo-check", "--ephemeral",
                "--dangerously-bypass-approvals-and-sandbox",  # the factory sandbox confines the whole process tree
                "-m", request.model, "-C", str(request.cwd), "-o", str(last_message)]
        if request.effort:
            argv += ["-c", f'model_reasoning_effort="{"high" if request.effort == "max" else request.effort}"']
        return [*argv, "-"]  # prompt on stdin: skills are long

    async def start(self, request: AgentRequest) -> RunHandle:
        run_dir = self.log_dir / f"codex-input-{request.attempt_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt = run_dir / "prompt.md"
        # Codex has no system-prompt flag: the skill goes first, the call's inputs after it.
        prompt.write_text(f"<instructions>\n{request.system_append}\n</instructions>\n\n{request.prompt}" if request.system_append
                          else request.prompt)
        last = run_dir / "last-message.txt"
        return self._launch(request, self._argv(request, last), self._policy(request, (run_dir,)), stdin=prompt,
                            meta={"last_message": str(last)})

    async def resume(self, handle: RunHandle, request: ResumeRequest) -> RunHandle:
        raise ResumeUnsupported("the codex runtime starts a fresh ephemeral session per call")

    async def poll(self, handle: RunHandle) -> RunSnapshot:
        events = self._events(handle)
        if events is None:
            return RunSnapshot("unknown", error="no event log")
        model = handle.meta.get("model")
        failure = next((e for e in reversed(events) if e.get("type") in ("turn.failed", "error")), None)
        if failure is not None:
            message = str((failure.get("error") or {}).get("message") or failure.get("message") or failure)[:500]
            if self._alive(handle):
                await self.cancel(handle)
            return RunSnapshot("failed", model_id=model, events_seen=len(events), error=message,
                               retryable=not _FATAL.search(message))
        if self._alive(handle):
            return RunSnapshot("running", model_id=model, events_seen=len(events))
        done = next((e for e in reversed(events) if e.get("type") == "turn.completed"), None)
        if done is None:
            return RunSnapshot("failed", model_id=model, events_seen=len(events),
                               error=f"exited without turn.completed: {self._stderr(handle)}")
        messages = [e["item"].get("text", "") for e in events
                    if e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "agent_message"]
        last = Path(handle.meta.get("last_message", ""))
        text = last.read_text(errors="replace") if handle.meta.get("last_message") and last.exists() else ""
        usage = done.get("usage") or {}
        tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
        return RunSnapshot("succeeded", output_text="\n\n".join(messages) or text, model_id=model, events_seen=len(events),
                           usage=Usage(tokens_in, tokens_out, self._cost(model, tokens_in, tokens_out)))

    def _cost(self, model: str | None, tokens_in: int | None, tokens_out: int | None) -> float | None:
        price = self.prices.get(model or "")
        if price is None or tokens_in is None or tokens_out is None:
            return None  # unknown: the controller books the whole reservation
        return round(tokens_in * price["input_per_mtok"] / 1e6 + tokens_out * price["output_per_mtok"] / 1e6, 6)
