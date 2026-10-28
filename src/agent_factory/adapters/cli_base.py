"""Shared mechanics of agent CLIs run as sandboxed child processes.

Each runtime (Claude CLI, Codex CLI, opencode) differs in argv and event format; launching inside
the enforced sandbox, reading the JSONL event log, liveness and process-group cancel are the same.
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
from .protocol import AgentRequest, CancelResult, RunHandle

# Environment the agent process may see. Operator tokens (GH_TOKEN, cloud keys…) never pass,
# except the variables a runtime's `pass_env` names explicitly (models.yaml).
AGENT_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL", "TMPDIR")


class CliRuntime:
    name = "cli"
    # Home-relative paths the CLI writes (auth refresh, caches, session state); everything else stays read-only.
    home_writable: tuple[str, ...] = (".cache", "Library/Caches", ".local/state")

    def __init__(self, binary: str, sandbox: Sandbox, log_dir: Path, pass_env: dict[str, str] | None = None):
        self.binary = binary
        self.sandbox = sandbox
        self.log_dir = log_dir
        self.pass_env = pass_env or {}  # target variable -> source variable in the controller environment
        log_dir.mkdir(parents=True, exist_ok=True)
        self._procs: dict[str, subprocess.Popen] = {}

    # --- launching -------------------------------------------------------------------

    def _policy(self, request: AgentRequest, extra_writable: tuple[Path, ...] = ()) -> SandboxPolicy:
        home = Path.home()
        writable = [home / p for p in self.home_writable]
        writable += [Path(os.environ.get("TMPDIR", "/tmp")), Path("/private/tmp"), Path("/private/var/folders")]
        base = request.sandbox_policy
        denied = base.denied_read if base else ()
        granted = base.writable if base else ()
        # cwd is writable only when the controller granted it (builder); reviewers stay read-only.
        read_only = () if any(Path(os.path.realpath(request.cwd)) == Path(os.path.realpath(p)) for p in granted) else (request.cwd,)
        return SandboxPolicy(writable=tuple(writable) + extra_writable + granted, denied_read=denied, network="full",
                             denied_write=read_only)

    def _env(self, request: AgentRequest) -> dict[str, str]:
        env = {k: os.environ[k] for k in AGENT_ENV_KEYS if k in os.environ}
        env["HOME"] = str(Path.home())
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update({target: os.environ[source] for target, source in self.pass_env.items() if source in os.environ})
        if request.sandbox_policy is not None:
            env.update(request.sandbox_policy.env)  # e.g. PATH with the repo's pinned Node first
        return env

    def _launch(self, request: AgentRequest, argv: list[str], policy: SandboxPolicy, *, env: dict[str, str] | None = None,
                stdin: Path | None = None, meta: dict | None = None) -> RunHandle:
        run_id = f"{self.name}-{uuid.uuid4().hex[:12]}"
        log_path = self.log_dir / f"{run_id}.jsonl"
        err_path = self.log_dir / f"{run_id}.stderr"
        with open(log_path, "wb") as out, open(err_path, "wb") as err, \
                (open(stdin, "rb") if stdin else open(os.devnull, "rb")) as inp:
            proc = subprocess.Popen(self.sandbox.wrap(argv, policy), cwd=request.cwd, env=env or self._env(request),
                                    stdout=out, stderr=err, stdin=inp, start_new_session=True)
        self._procs[run_id] = proc
        return RunHandle(
            run_id, request.job_id, request.attempt_id, request.session_id, proc.pid, log_path, time.time(),
            meta={"stderr": str(err_path), "deadline": time.time() + request.timeout_seconds, "model": request.model,
                  "cwd": str(request.cwd), "tools": list(request.tools), "runtime": self.name, **(meta or {})},
        )

    # --- observing -------------------------------------------------------------------

    @staticmethod
    def _events(handle: RunHandle) -> list[dict] | None:
        if handle.log_path is None or not handle.log_path.exists():
            return None
        events = []
        for line in handle.log_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    @staticmethod
    def _stderr(handle: RunHandle) -> str:
        path = handle.meta.get("stderr")
        return Path(path).read_text(errors="replace")[-2000:] if path and Path(path).exists() else ""

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

    def _exit_code(self, handle: RunHandle) -> int | None:
        proc = self._procs.get(handle.run_id)
        return proc.poll() if proc is not None else None

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
