"""Trusted runner: executes manifest commands in the sandbox and records evidence.

The builder's claim "tests passed" is never evidence. The runner records argv, cwd,
redacted env, timings, exit code/signal, timeout, stdout/stderr hashes and the number
of test cases that really executed. Zero executed tests where tests are required, a
missing tool or a timeout can never produce PASS.
"""

from __future__ import annotations

import os
import json
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import load_yaml
from .evidence import EvidenceStore, Producer
from .identities import digest
from .sandbox import Sandbox, SandboxPolicy, scrubbed_env

MAX_LOG_BYTES = 2_000_000
EXCERPT_BYTES = 4000
# {factory}: the factory checkout (pinned tools, local rule files); {base_sha}: the job base, for checks of new code only.
_PLACEHOLDERS = {"{python}", "{report}", "{worktree}", "{factory}", "{base_sha}"}
CHECKS_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")


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


class PreviewBackend(BaseModel):
    """The app's own server for the feature walkthrough, so screens behind sign-in can be reached.

    Live finding (LearnEnglish F5c): the plan section of the Account screen needs a session and the API; the static
    preview redirected /account to onboarding and the walkthrough came back UNKNOWN twice. The server runs in the
    sandbox (writes only to its scratch dir, so the database starts empty; localhost network only) on a free port; the
    preview serves the build with ``build_url`` (the API base URL baked into it) replaced by that server's URL.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    argv: list[str]  # may use {python}, {worktree} and {port}
    env: dict[str, str] = {}  # values may use {port}, {origin} (the preview), {secret} (random), {scratch}
    build_url: str = Field(pattern=r"^https?://[^/\s]+$")  # e.g. http://127.0.0.1:3000 from app.json
    ready_path: str = Field(default="/", pattern=r"^/")
    ready_timeout_seconds: int = Field(default=60, ge=1, le=600)


class PreviewSeed(BaseModel):
    """Sample data for UI review: a command that writes a Playwright storage state (localStorage, cookies).

    Live finding (LearnEnglish F6b): a fresh preview profile has no words due, so the exercise screens never rendered
    in task-level UI review and their defects first showed up in the feature walkthrough. The command runs in the
    sandbox (writes only its scratch dir, localhost network, so it may also create data through the backend).
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    argv: list[str]  # may use {python}, {worktree}, {out} (the state file to write), {origin} (the preview), {backend}
    shows: str = Field(min_length=1, max_length=300)  # what the seeded app shows, e.g. "a learner with 12 reviews due"
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class PreviewSpec(BaseModel):
    """Where a gate-built static web candidate lives, for UI review in a browser."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    static_dir: str  # relative to the worktree, written by a check of the same stage (e.g. web-build -> dist)
    spa_fallback: bool = True  # unknown paths serve index.html (client-side routing)
    backend: PreviewBackend | None = None  # started for the feature walkthrough and product-verify only
    seed: PreviewSeed | None = None

    @field_validator("static_dir")
    @classmethod
    def _relative(cls, path: str) -> str:
        parts = Path(path).parts
        if not parts or Path(path).is_absolute() or ".." in parts:
            raise ValueError(f"preview.static_dir must be a relative path inside the worktree: {path!r}")
        return path


class ChecksManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    checks: list[CheckSpec]
    # Dependency directories (e.g. node_modules) the operator installed in the repo checkout;
    # linked read-only into each worktree because checks run without network.
    link_from_checkout: list[str] = []
    preview: PreviewSpec | None = None

    @field_validator("link_from_checkout")
    @classmethod
    def _plain_relative(cls, paths: list[str]) -> list[str]:
        for path in paths:
            parts = Path(path).parts
            if not parts or Path(path).is_absolute() or ".." in parts or path.startswith("."):
                raise ValueError(f"link_from_checkout entries must be plain relative paths: {path!r}")
        return paths

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
    failing_files: list[str] = field(default_factory=list)  # test files with a failed or errored case (junit file/classname)

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
    flaky_files: list[str] | None = None  # failed first, passed when run again; none of them was changed by the job


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
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            where = case.get("file") or case.get("classname") or ""
            if "/" in where or where.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
                counts.failing_files.append(where)
    counts.failing_files = sorted(set(counts.failing_files))
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


class ServiceError(RuntimeError):
    pass


@dataclass
class RunningService:
    proc: subprocess.Popen
    url: str
    log: Path

    def log_tail(self, size: int = 1500) -> str:
        try:
            return self.log.read_bytes()[-size:].decode("utf-8", "replace")
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc.poll() is None or _group_alive(self.proc.pid):
            _kill_group(self.proc)


class TrustedRunner:
    def __init__(self, sandbox: Sandbox, evidence: EvidenceStore, work_root: Path, factory_root: Path | None = None):
        self.sandbox = sandbox
        self.evidence = evidence
        self.work_root = work_root
        self.factory_root = factory_root

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
        base_sha: str | None = None,
    ) -> CheckEvidence:
        scratch = self.work_root / job_id / f"run-{time.time_ns()}"
        home, tmpdir = scratch / "home", scratch / "tmp"
        for path in (home, tmpdir):
            path.mkdir(parents=True, exist_ok=True)
        report = scratch / "report.xml"
        values = {"{python}": sys.executable, "{report}": str(report), "{worktree}": str(worktree),
                  "{factory}": str(self.factory_root) if self.factory_root else None, "{base_sha}": base_sha}
        argv = []
        for arg in check.argv:
            for key, value in values.items():
                if key in arg:
                    if value is None:
                        return self._env_error(check, check.argv, worktree, {}, time.time(), f"no value for {key}", head_sha, tree_sha)
                    arg = arg.replace(key, value)
            argv.append(arg)
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

    def start_service(self, spec: PreviewBackend, worktree: Path, *, job_id: str, port: int, origin: str) -> "RunningService":
        """Start the preview backend in the sandbox and wait until it answers; raises ServiceError with its log tail."""
        if not self.sandbox.serves_localhost:
            raise ServiceError(f"the {self.sandbox.name} sandbox cannot give a server localhost-only network")
        scratch = self.work_root / job_id / f"service-{time.time_ns()}"
        home, tmpdir = scratch / "home", scratch / "tmp"
        for path in (home, tmpdir):
            path.mkdir(parents=True, exist_ok=True)
        values = {"{python}": sys.executable, "{worktree}": str(worktree), "{port}": str(port), "{origin}": origin,
                  "{secret}": secrets.token_hex(32), "{scratch}": str(scratch)}

        def fill(text: str) -> str:
            for key, value in values.items():
                text = text.replace(key, value)
            return text

        argv = [fill(a) for a in spec.argv]
        policy = SandboxPolicy(writable=(scratch,), denied_read=tuple(self._denied(worktree)), network="localhost",
                               env={k: fill(v) for k, v in spec.env.items()})
        env = scrubbed_env(policy, home, tmpdir)
        if shutil.which(argv[0], path=env.get("PATH")) is None:
            raise ServiceError(f"required tool not found: {argv[0]}")
        log = scratch / "service.log"
        with open(log, "wb") as out:
            proc = subprocess.Popen(self.sandbox.wrap(argv, policy), cwd=worktree, env=env, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        service = RunningService(proc, f"http://127.0.0.1:{port}", log)
        deadline = time.time() + spec.ready_timeout_seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                service.stop()
                raise ServiceError(f"backend exited with {proc.returncode}: {service.log_tail()}")
            try:
                with urllib.request.urlopen(service.url + spec.ready_path, timeout=2):
                    return service
            except urllib.error.HTTPError as exc:
                if exc.code < 500:  # it answers; a 404 or 401 on the ready path still means it is up
                    return service
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        service.stop()
        raise ServiceError(f"backend did not answer {spec.ready_path} within {spec.ready_timeout_seconds}s: {service.log_tail()}")

    def run_seed(self, spec: PreviewSeed, worktree: Path, *, job_id: str, origin: str, backend: str | None) -> dict:
        """Run the seed command in the sandbox; returns the storage state it wrote, or raises ServiceError."""
        scratch = self.work_root / job_id / f"seed-{time.time_ns()}"
        home, tmpdir = scratch / "home", scratch / "tmp"
        for path in (home, tmpdir):
            path.mkdir(parents=True, exist_ok=True)
        out = scratch / "state.json"
        values = {"{python}": sys.executable, "{worktree}": str(worktree), "{out}": str(out), "{origin}": origin,
                  "{backend}": backend or ""}
        argv = []
        for arg in spec.argv:
            for key, value in values.items():
                arg = arg.replace(key, value)
            argv.append(arg)
        policy = SandboxPolicy(writable=(scratch,), denied_read=tuple(self._denied(worktree)), network="localhost")
        env = scrubbed_env(policy, home, tmpdir)
        if shutil.which(argv[0], path=env.get("PATH")) is None:
            raise ServiceError(f"required tool not found: {argv[0]}")
        log = scratch / "seed.log"
        with open(log, "wb") as fh:
            proc = subprocess.Popen(self.sandbox.wrap(argv, policy), cwd=worktree, env=env, stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                proc.wait(timeout=spec.timeout_seconds)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                raise ServiceError(f"seed timed out after {spec.timeout_seconds}s") from None
        tail = log.read_bytes()[-1500:].decode("utf-8", "replace")
        if proc.returncode != 0:
            raise ServiceError(f"seed exited with {proc.returncode}: {tail}")
        try:
            state = json.loads(out.read_text())
        except (OSError, ValueError) as exc:
            raise ServiceError(f"seed wrote no storage state to {{out}}: {exc}") from exc
        if not (isinstance(state, dict) and isinstance(state.get("cookies", []), list) and isinstance(state.get("origins", []), list)
                and set(state) <= {"cookies", "origins"}):
            raise ServiceError("seed state must be a Playwright storage state: {\"cookies\": [...], \"origins\": [...]}")
        return state

    def _rerun_if_flaky(self, ev: CheckEvidence, check: CheckSpec, worktree: Path, changed: set[str], **kw) -> CheckEvidence:
        """A test check that failed only in test files the job did not change runs once more; a pass there is a flake.

        Live finding (LearnEnglish F5c TASK-06): an onboarding test the task never touched failed once under load; the task
        lost its last attempt, and the retry's builder weakened that test to get green (the gate refused it).
        """
        failing = (ev.tests or {}).get("failing_files") or []
        if ev.verdict != str(GateVerdict.FAIL_CODE) or not failing or any(f in changed for f in failing):
            return ev
        again = self.run_check(check, worktree, **kw)
        if again.verdict != str(GateVerdict.PASS):
            return ev
        again.flaky_files = failing
        return again

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
        base_sha: str | None = None,
        changed_files: set[str] | None = None,
    ) -> GateResult:
        evidence = [
            self.run_check(c, worktree, job_id=job_id, head_sha=head_sha, tree_sha=tree_sha, extra_env=extra_env, base_sha=base_sha)
            for c in checks
        ]
        if changed_files is not None:
            evidence = [self._rerun_if_flaky(e, c, worktree, changed_files, job_id=job_id, head_sha=head_sha, tree_sha=tree_sha,
                                             extra_env=extra_env, base_sha=base_sha) for e, c in zip(evidence, checks)]
        required = [e for e, c in zip(evidence, checks) if c.required]
        if not required:
            return GateResult(GateVerdict.FAIL_POLICY, "no required checks configured", evidence)
        for priority in (GateVerdict.ERROR_ENV, GateVerdict.TIMEOUT, GateVerdict.FAIL_POLICY, GateVerdict.FAIL_CODE,
                         GateVerdict.CANCELLED, GateVerdict.UNKNOWN):
            failing = [e for e in required if e.verdict == priority]
            if failing:
                return GateResult(priority, "; ".join(f"{e.name}: {e.reason}" for e in failing), evidence)
        return GateResult(GateVerdict.PASS, "all required checks passed", evidence)
