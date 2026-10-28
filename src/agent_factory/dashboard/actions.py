"""Operator actions from the dashboard, executed as the same CLI commands an operator would type.

The dashboard never changes state itself: each action becomes a fixed `factory` argv (no shell),
so the controller's guards, evidence checks and exit codes apply exactly as in the terminal.
Quick actions run to completion; actions that drive agents (run, resume, merge decisions) start a
background process whose log the dashboard shows. One background run per job at a time.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JOB_ID = re.compile(r"^job-[0-9a-f]{12}$")
DECISION_ID = re.compile(r"^Q-\d{1,2}$")
MAX_TEXT = 2000


class ActionError(ValueError):
    """The request is not a valid action (400); never a controller refusal."""


@dataclass
class ActionResult:
    ok: bool
    exit_code: int | None
    output: str
    background: bool = False


def _text(value: Any, field: str, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > MAX_TEXT or "\x00" in value:
        raise ActionError(f"{field}: text of 1..{MAX_TEXT} characters expected")
    return value


def _money(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= 10_000:
        raise ActionError(f"{field}: amount in USD between 0 and 10000 expected")
    return float(value)


def build_argv(action: str, job_id: str, body: dict[str, Any]) -> tuple[list[str], bool]:
    """(CLI arguments, runs in background) for a whitelisted action; ActionError otherwise."""
    if not JOB_ID.match(job_id):
        raise ActionError("unknown job id")
    if action == "decide":
        answers = body.get("answers") or {}
        if not isinstance(answers, dict) or len(answers) > 15:
            raise ActionError("answers: mapping of decision id to text expected")
        argv = ["decide", job_id]
        for qid, text in answers.items():
            if not isinstance(qid, str) or not DECISION_ID.match(qid):
                raise ActionError(f"unknown decision id {qid!r}")
            argv += ["--answer", f"{qid}={_text(text, qid)}"]
        if body.get("accept_proposed"):
            argv.append("--accept-proposed")
        if len(argv) == 2:
            raise ActionError("answer at least one decision or accept the proposals")
        return argv, False
    if action == "approve-plan":
        argv = ["plan", "approve", job_id]
        if body.get("budget") is not None:
            argv += ["--budget", f"{_money(body['budget'], 'budget'):.2f}"]
        return argv, False
    if action == "raise-budget":
        return ["budget", "raise", job_id, "--to", f"{_money(body.get('to'), 'to'):.2f}",
                "--reason", _text(body.get("reason"), "reason")], False
    if action == "cancel":
        return ["cancel", job_id], False
    if action == "run":
        return ["run", "--job", job_id], True
    if action == "resume":
        return ["resume", job_id], True
    if action == "merge-approve":
        return ["approve", job_id, "--revision", _text(body.get("revision"), "revision")], True
    if action == "merge-reject":
        return ["reject", job_id, "--revision", _text(body.get("revision"), "revision"), "--reason", _text(body.get("reason"), "reason")], True
    raise ActionError(f"unknown action {action!r}")


class ActionRunner:
    def __init__(self, data_dir: Path, factory_dir: Path, cwd: Path, python: str = sys.executable):
        self.base = [python, "-m", "agent_factory.cli", "--json", "--data-dir", str(data_dir), "--factory-dir", str(factory_dir)]
        self.cwd = cwd
        self.runs_dir = data_dir / "dashboard" / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _record(self, job_id: str) -> Path:
        return self.runs_dir / f"{job_id}.json"

    def background(self, job_id: str) -> dict[str, Any] | None:
        """The job's latest background run: argv, pid, alive, log tail."""
        path = self._record(job_id)
        if not path.exists():
            return None
        run = json.loads(path.read_text())
        log = Path(run["log"])
        tail = log.read_text(errors="replace")[-4000:] if log.exists() else ""
        return {**run, "alive": _alive(run["pid"]), "log_tail": tail}

    def execute(self, action: str, job_id: str, body: dict[str, Any]) -> ActionResult:
        argv, background = build_argv(action, job_id, body)
        running = self.background(job_id)
        if running and running["alive"]:
            raise ActionError(f"a run of this job is in progress ({' '.join(running['argv'])}); wait for it or cancel the job")
        if not background:
            proc = subprocess.run(self.base + argv, cwd=self.cwd, capture_output=True, text=True, timeout=120)
            return ActionResult(proc.returncode == 0, proc.returncode, (proc.stdout + proc.stderr)[-4000:])
        log = self.runs_dir / f"{job_id}-{int(time.time())}.log"
        prefix = ["caffeinate", "-i"] if shutil.which("caffeinate") else []  # keep a Mac awake while agents work
        with open(log, "wb") as out:
            proc = subprocess.Popen(prefix + self.base + argv, cwd=self.cwd, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        self._record(job_id).write_text(json.dumps({"argv": ["factory", *argv], "pid": proc.pid, "log": str(log),
                                                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
        return ActionResult(True, None, f"started: factory {' '.join(argv)}", background=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a finished child we started stays a zombie until reaped
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True
