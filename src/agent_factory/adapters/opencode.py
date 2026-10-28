"""opencode runtime (``opencode run --format json``): Gemini, OpenAI, Anthropic API and local models.

Every call gets its own generated config (``OPENCODE_CONFIG``): the skill as instructions, the
permission set and, for a local or self-hosted model, the endpoint the controller picked as an
OpenAI-compatible provider. Config, data and state live in per-call XDG directories, so the
operator's own opencode config and logins are never read; keys come only through ``pass_env``.

Events (JSONL): ``step_start``, ``text``, ``tool_use``, ``step_finish`` (tokens, cost) and
``error`` (with ``isRetryable``). No structured-output flag and no budget cap.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..sandbox import Sandbox
from .cli_base import CliRuntime
from .protocol import AgentRequest, ResumeRequest, ResumeUnsupported, RunHandle, RunSnapshot, RuntimeCapabilities, Usage

_EFFORT = {"low": "low", "medium": "medium", "high": "high", "max": "max"}


class OpencodeRuntime(CliRuntime):
    name = "opencode"
    home_writable = ("Library/Caches", ".local/state")

    def __init__(self, binary: str, sandbox: Sandbox, log_dir: Path, pass_env: dict[str, str] | None = None):
        super().__init__(binary, sandbox, log_dir, pass_env)
        self.cache_dir = log_dir / "opencode-cache"  # provider packages, shared by calls
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def capabilities(self) -> RuntimeCapabilities:
        version = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        return RuntimeCapabilities(
            name="opencode", version=version, supports_resume=False, supports_cancel=True, supports_json_schema=False,
            supports_budget_cap=False, supports_effort=True, reports_usage=True, reports_cost=True,
        )

    def config(self, request: AgentRequest, instructions: Path) -> dict:
        writable = bool(request.sandbox_policy and any(
            Path(p).resolve() == Path(request.cwd).resolve() for p in request.sandbox_policy.writable))
        config: dict = {
            "$schema": "https://opencode.ai/config.json",
            "instructions": [str(instructions)],
            "permission": {"edit": "allow" if writable else "deny", "bash": "allow" if "Bash" in request.tools else "deny",
                           "webfetch": "deny"},
            "autoupdate": False,
            "share": "disabled",
        }
        endpoint = request.runtime_options.get("endpoint")
        if endpoint:
            config["provider"] = {endpoint["name"]: {
                "npm": "@ai-sdk/openai-compatible", "name": endpoint["name"],
                "options": {"baseURL": endpoint["base_url"], **({"apiKey": "{env:FACTORY_ENDPOINT_KEY}"} if endpoint.get("key_env") else {})},
                "models": {request.model: {"name": request.model}},
            }}
        return config

    def model_ref(self, request: AgentRequest) -> str:
        endpoint = request.runtime_options.get("endpoint")
        return f"{endpoint['name']}/{request.model}" if endpoint else request.model  # cloud models: provider/model

    async def start(self, request: AgentRequest) -> RunHandle:
        run_dir = self.log_dir / f"opencode-input-{request.attempt_id}"
        for sub in ("config", "data", "state"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        instructions = run_dir / "instructions.md"
        instructions.write_text(request.system_append or "Follow the request.")
        config_path = run_dir / "opencode.json"
        config_path.write_text(json.dumps(self.config(request, instructions), indent=1))
        argv = [self.binary, "run", "--format", "json", "--pure", "-m", self.model_ref(request)]
        if request.effort:
            argv += ["--variant", _EFFORT.get(request.effort, request.effort)]
        argv.append(request.prompt)
        env = self._env(request)
        env.update({"OPENCODE_CONFIG": str(config_path), "XDG_CONFIG_HOME": str(run_dir / "config"),
                    "XDG_DATA_HOME": str(run_dir / "data"), "XDG_STATE_HOME": str(run_dir / "state"),
                    "XDG_CACHE_HOME": str(self.cache_dir)})
        endpoint = request.runtime_options.get("endpoint") or {}
        if endpoint.get("key_env") and endpoint["key_env"] in os.environ:
            env["FACTORY_ENDPOINT_KEY"] = os.environ[endpoint["key_env"]]
        return self._launch(request, argv, self._policy(request, (run_dir, self.cache_dir)), env=env)

    async def resume(self, handle: RunHandle, request: ResumeRequest) -> RunHandle:
        raise ResumeUnsupported("the opencode runtime starts a fresh session per call")

    async def poll(self, handle: RunHandle) -> RunSnapshot:
        events = self._events(handle)
        if events is None:
            return RunSnapshot("unknown", error="no event log")
        model = handle.meta.get("model")
        error = next((e for e in reversed(events) if e.get("type") == "error"), None)
        if error is not None:
            data = (error.get("error") or {}).get("data") or {}
            message = str(data.get("message") or error.get("error"))[:500]
            if self._alive(handle):
                await self.cancel(handle)
            return RunSnapshot("failed", model_id=model, events_seen=len(events), error=message,
                               retryable=bool(data.get("isRetryable", True)) and data.get("statusCode") not in (400, 401, 402, 403, 404))
        if self._alive(handle):
            return RunSnapshot("running", model_id=model, events_seen=len(events))
        finishes = [e["part"] for e in events if e.get("type") == "step_finish" and isinstance(e.get("part"), dict)]
        if not finishes or finishes[-1].get("reason") not in ("stop", "end_turn", "length"):
            return RunSnapshot("failed", model_id=model, events_seen=len(events),
                               error=f"exited without a final step (exit {self._exit_code(handle)}): {self._stderr(handle)}")
        text = "\n".join(e["part"].get("text", "") for e in events if e.get("type") == "text" and isinstance(e.get("part"), dict))
        tokens_in = sum((f.get("tokens") or {}).get("input", 0) + ((f.get("tokens") or {}).get("cache") or {}).get("read", 0) for f in finishes)
        tokens_out = sum((f.get("tokens") or {}).get("output", 0) for f in finishes)
        cost = sum(float(f.get("cost") or 0) for f in finishes)
        return RunSnapshot("succeeded", output_text=text, model_id=model, events_seen=len(events),
                           usage=Usage(tokens_in, tokens_out, round(cost, 6)))
