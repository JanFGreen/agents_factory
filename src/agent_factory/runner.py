"""Trusted runner: executes manifest commands in the sandbox and records evidence.

The builder's claim "tests passed" is never evidence. The runner records argv, cwd,
redacted env, timings, exit code/signal, timeout, stdout/stderr hashes and the number
of test cases that really executed. Zero executed tests where tests are required, a
missing tool or a timeout can never produce PASS.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import load_yaml
from .evidence import EvidenceStore, Producer
from .identities import digest
from .sandbox import Sandbox, SandboxPolicy, scrubbed_env

MAX_LOG_BYTES = 2_000_000
EXCERPT_BYTES = 4000
_PLACEHOLDERS = {"{python}", "{report}", "{worktree}"}


class GateVerdict(StrEnum):
    PASS = "PASS"
    FAIL_CODE = "FAIL_CODE"
    FAIL_POLICY = "FAIL_POLICY"
    ERROR_ENV = "ERROR_ENV"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class CheckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    argv: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(gt=0, le=7200)
    report: Literal["junit", "none"] = "none"
    min_tests: int = Field(default=0, ge=0)
    required: bool = True
    network: Literal["none", "localhost"] = "none"
    stage: Literal["task", "acceptance", "integration"] = "task"


class ChecksManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    checks: list[CheckSpec]

    def digest(self) -> str:
        return digest(self.model_dump())

    def for_stage(self, stage: str) -> list[CheckSpec]:
        order = {"task": ("task",), "acceptance": ("task", "acceptance"), "integration": ("task", "acceptance", "integration")}
        return [c for c in self.checks if c.stage in order[stage]]


def load_checks(path: Path) -> ChecksManifest:
    manifest = ChecksManifest.model_validate(load_yaml(path))
    for check in manifest.checks:
        for arg in check.argv:
            if "{" in arg and not any(p in arg for p in _PLACEHOLDERS):
                raise ValueError(f"check {check.name}: unknown placeholder in {arg!r}")
    return manifest


@dataclass
class TestCounts:
    collected: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0

    @property
    def executed(self) -> int:
        return self.collected - self.skipped


@dataclass
class CheckEvidence:
    name: str
    argv: list[str]
    cwd: str
    env_keys: list[str]
    sandbox: str
    started_at: float
    ended_at: float
    exit_code: int | None
    signal: int | None
    timed_out: bool
    stdout_artifact: str | None
    stderr_artifact: str | None
    stdout_sha256: str
    stderr_sha256: str
    excerpt: str
    tests: dict | None
    verdict: str
    reason: str
    head_sha: str | None
    tree_sha: str | None


@dataclass
class GateResult:
    verdict: GateVerdict
    reason: str
    checks: list[CheckEvidence] = field(default_factory=list)

    def as_payload(self) -> dict:
        return {"verdict": str(self.verdict), "reason": self.reason, "checks": [asdict(c) for c in self.checks]}


def parse_junit(path: Path) -> TestCounts | None:
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    counts = TestCounts()
    for suite in suites:
        counts.collected += int(suite.get("tests", 0))
        counts.failed += int(suite.get("failures", 0))
        counts.errors += int(suite.get("errors", 0))
        counts.skipped += int(suite.get("skipped", 0))
    return counts


def _kill_group(proc: subprocess.Popen, grace: float = 3.0) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class TrustedRunner:
    def __init__(self, sandbox: Sandbox, evidence: EvidenceStore, work_root: Path):
        self.sandbox = sandbox
        self.evidence = evidence
        self.work_root = work_root

    def run_check(
        self,
        check: CheckSpec,
        worktree: Path,
        *,
        job_id: str,
        head_sha: str | None,
        tree_sha: str | None,
        extra_env: dict[str, str] | None = None,
        timeout_override: int | None = None,
    ) -> CheckEvidence:
        scratch = self.work_root / job_id / f"run-{time.time_ns()}"
        home, tmpdir = scratch / "home", scratch / "tmp"
        for path in (home, tmpdir):
            path.mkdir(parents=True, exist_ok=True)
        report = scratch / "report.xml"
        argv = [
            arg.replace("{python}", sys.executable).replace("{report}", str(report)).replace("{worktree}", str(worktree))
            for arg in check.argv
        ]
        policy = SandboxPolicy(
            writable=(worktree, scratch),
            denied_read=tuple(self._denied(worktree)),
            network=check.network,
            env=dict(extra_env or {}),
        )
        env = scrubbed_env(policy, home, tmpdir)
        if shutil.which(argv[0], path=env.get("PATH")) is None:
            return self._env_error(check, argv, worktree, env, time.time(), f"required tool not found: {argv[0]}", head_sha, tree_sha)
        wrapped = self.sandbox.wrap(argv, policy)
        stdout_path, stderr_path = scratch / "stdout.log", scratch / "stderr.log"
        started = time.time()
        timed_out = False
        exit_code: int | None = None
        sig: int | None = None
        with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
            try:
                proc = subprocess.Popen(
                    wrapped, cwd=worktree, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                return self._env_error(check, argv, worktree, env, started, str(exc), head_sha, tree_sha)
            try:
                proc.wait(timeout=timeout_override or check.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
            else:
                # The direct child exited; children it left behind still die with the group.
                if _group_alive(proc.pid):
                    _kill_group(proc)
            code = proc.returncode
            if code is not None and code < 0:
                sig = -code
            else:
                exit_code = code
        ended = time.time()
        stdout = stdout_path.read_bytes()[:MAX_LOG_BYTES]
        stderr = stderr_path.read_bytes()[:MAX_LOG_BYTES]
        producer = Producer("trusted-runner", f"check:{check.name}")
        out_id = self.evidence.put(stdout, "log:stdout", producer, job_id)
        err_id = self.evidence.put(stderr, "log:stderr", producer, job_id)
        counts = parse_junit(report) if check.report == "junit" else None
        verdict, reason = self._classify(check, timed_out, exit_code, sig, counts, stderr)
        return CheckEvidence(
            name=check.name, argv=argv, cwd=str(worktree), env_keys=sorted(env), sandbox=self.sandbox.name,
            started_at=started, ended_at=ended, exit_code=exit_code, signal=sig, timed_out=timed_out,
            stdout_artifact=out_id, stderr_artifact=err_id, stdout_sha256=digest(stdout), stderr_sha256=digest(stderr),
            excerpt=(stdout[-EXCERPT_BYTES // 2 :] + stderr[-EXCERPT_BYTES // 2 :]).decode("utf-8", "replace"),
            tests=asdict(counts) if counts else None, verdict=str(verdict), reason=reason,
            head_sha=head_sha, tree_sha=tree_sha,
        )

    def _denied(self, worktree: Path) -> list[Path]:
        from .sandbox import default_denied_reads

        return default_denied_reads(self.evidence.root.parent, worktree)

    def _env_error(self, check, argv, worktree, env, started, reason, head_sha, tree_sha) -> CheckEvidence:
        return CheckEvidence(
            name=check.name, argv=argv, cwd=str(worktree), env_keys=sorted(env), sandbox=self.sandbox.name,
            started_at=started, ended_at=time.time(), exit_code=None, signal=None, timed_out=False,
            stdout_artifact=None, stderr_artifact=None, stdout_sha256=digest(b""), stderr_sha256=digest(b""),
            excerpt=reason, tests=None, verdict=str(GateVerdict.ERROR_ENV), reason=reason,
            head_sha=head_sha, tree_sha=tree_sha,
        )

    @staticmethod
    def _classify(
        check: CheckSpec, timed_out: bool, exit_code: int | None, sig: int | None, counts: TestCounts | None,
        stderr: bytes,
    ) -> tuple[GateVerdict, str]:
        if timed_out:
            return GateVerdict.TIMEOUT, f"deadline {check.timeout_seconds}s exceeded; process group killed"
        if sig is not None:
            return GateVerdict.CANCELLED if sig in (signal.SIGTERM, signal.SIGINT) else GateVerdict.UNKNOWN, f"killed by signal {sig}"
        if exit_code == 127 or b"command not found" in stderr or b"No module named" in stderr:
            return GateVerdict.ERROR_ENV, "required tool or module missing"
        if check.report == "junit":
            if counts is None:
                return GateVerdict.ERROR_ENV, "test report missing; cannot prove tests ran"
            if counts.executed == 0 or counts.executed < check.min_tests:
                return GateVerdict.FAIL_POLICY, f"only {counts.executed} test cases executed (min {check.min_tests})"
            if counts.failed or counts.errors or exit_code != 0:
                return GateVerdict.FAIL_CODE, f"{counts.failed} failed, {counts.errors} errors"
            return GateVerdict.PASS, f"{counts.executed} test cases executed"
        if exit_code != 0:
            return GateVerdict.FAIL_CODE, f"exit code {exit_code}"
        return GateVerdict.PASS, "exit 0"

    def run_gate(
        self,
        checks: list[CheckSpec],
        worktree: Path,
        *,
        job_id: str,
        head_sha: str | None,
        tree_sha: str | None,
        extra_env: dict[str, str] | None = None,
    ) -> GateResult:
        evidence = [
            self.run_check(c, worktree, job_id=job_id, head_sha=head_sha, tree_sha=tree_sha, extra_env=extra_env)
            for c in checks
        ]
        required = [e for e, c in zip(evidence, checks) if c.required]
        if not required:
            return GateResult(GateVerdict.FAIL_POLICY, "no required checks configured", evidence)
        for priority in (GateVerdict.ERROR_ENV, GateVerdict.TIMEOUT, GateVerdict.FAIL_POLICY, GateVerdict.FAIL_CODE,
                         GateVerdict.CANCELLED, GateVerdict.UNKNOWN):
            failing = [e for e in required if e.verdict == priority]
            if failing:
                return GateResult(priority, "; ".join(f"{e.name}: {e.reason}" for e in failing), evidence)
        return GateResult(GateVerdict.PASS, "all required checks passed", evidence)
