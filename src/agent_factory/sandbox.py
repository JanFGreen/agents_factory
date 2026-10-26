"""Enforced process sandbox. Fails closed when no working sandbox is available.

macOS: ``sandbox-exec`` with a generated SBPL profile (deny all writes except the
job's paths, deny reads of controller secrets/other jobs, deny outbound network
except localhost). Linux: ``bwrap`` when installed. A ``cwd``, a prompt or a tool
allow-list is never treated as isolation.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Network = Literal["none", "localhost", "full"]

SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
HOME_SECRET_DIRS = (".ssh", ".aws", ".gnupg", ".config/gh", ".netrc", ".docker", ".kube", ".npmrc", ".pypirc")


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxPolicy:
    writable: tuple[Path, ...]
    denied_read: tuple[Path, ...] = ()
    network: Network = "none"
    env: dict[str, str] = field(default_factory=dict)
    denied_write: tuple[Path, ...] = ()  # wins over writable (e.g. a read-only role's worktree under TMPDIR)

    def resolved(self) -> "SandboxPolicy":
        real = lambda paths: tuple(Path(os.path.realpath(p)) for p in paths)  # noqa: E731
        return SandboxPolicy(real(self.writable), real(self.denied_read), self.network, dict(self.env), real(self.denied_write))


def default_denied_reads(data_dir: Path, own_worktree: Path | None = None) -> list[Path]:
    """Controller secrets, other jobs' worktrees, holdout and operator credentials."""
    denied = [data_dir / "keys", data_dir / "state.db", data_dir / "holdout", data_dir / "artifacts"]
    worktrees = data_dir / "worktrees"
    if worktrees.exists():
        for child in worktrees.iterdir():
            if own_worktree is None or child.resolve() != own_worktree.resolve():
                denied.append(child)
    # golden/holdout eval cases of the factory itself are never readable by agents or checks
    factory_evals = Path(__file__).resolve().parents[2] / "tests" / "evals"
    if factory_evals.exists():
        denied.append(factory_evals)
    home = Path.home()
    denied.extend(home / name for name in HOME_SECRET_DIRS)
    return denied


def scrubbed_env(policy: SandboxPolicy, home: Path, tmpdir: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    env.update({"HOME": str(home), "TMPDIR": str(tmpdir), "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(policy.env)
    return env


def _sbpl_quote(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Sandbox:
    name = "abstract"

    def wrap(self, argv: list[str], policy: SandboxPolicy) -> list[str]:
        raise NotImplementedError


class MacSandbox(Sandbox):
    name = "macos-sandbox-exec"

    def __init__(self, binary: str = "/usr/bin/sandbox-exec"):
        self.binary = binary

    def profile(self, policy: SandboxPolicy) -> str:
        policy = policy.resolved()
        lines = ["(version 1)", "(allow default)"]
        if policy.network != "full":
            lines.append("(deny network-outbound (remote ip))")
            if policy.network == "localhost":
                lines.append('(allow network-outbound (remote ip "localhost:*"))')
        lines.append("(deny file-write*)")
        writable = " ".join(f"(subpath {_sbpl_quote(p)})" for p in policy.writable)
        devices = '(literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (literal "/dev/dtracehelper")'
        lines.append(f"(allow file-write* {writable} {devices})")
        if policy.denied_write:
            ro = " ".join(f"(subpath {_sbpl_quote(p)})" for p in policy.denied_write)
            lines.append(f"(deny file-write* {ro})")
        if policy.denied_read:
            denied = " ".join(f"(subpath {_sbpl_quote(p)})" for p in policy.denied_read)
            lines.append(f"(deny file-read* file-write* {denied})")
        return "\n".join(lines)

    def wrap(self, argv: list[str], policy: SandboxPolicy) -> list[str]:
        return [self.binary, "-p", self.profile(policy), *argv]


class BwrapSandbox(Sandbox):
    name = "linux-bwrap"

    def __init__(self, binary: str):
        self.binary = binary

    def wrap(self, argv: list[str], policy: SandboxPolicy) -> list[str]:
        policy = policy.resolved()
        args = [self.binary, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--die-with-parent"]
        for path in policy.writable:
            args += ["--bind", str(path), str(path)]
        for path in policy.denied_write:
            args += ["--ro-bind", str(path), str(path)]
        for path in policy.denied_read:
            if path.is_dir():
                args += ["--tmpfs", str(path)]
            elif path.exists():
                args += ["--ro-bind", "/dev/null", str(path)]
        if policy.network != "full":
            # bwrap cannot allow only localhost; a fresh netns has loopback only.
            args.append("--unshare-net")
        return [*args, *argv]


_PROBED: dict[str, Sandbox] = {}


def probe(sandbox: Sandbox) -> None:
    """Prove enforcement: a write outside the allowed path and a denied read must fail."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        allowed, outside, secret = root / "allowed", root / "outside", root / "secret"
        for path in (allowed, outside, secret):
            path.mkdir()
        (secret / "key").write_text("x")
        policy = SandboxPolicy(writable=(allowed,), denied_read=(secret,))
        script = (
            f"echo ok > '{allowed}/f' || exit 10; "
            f"echo bad > '{outside}/f' 2>/dev/null && exit 11; "
            f"cat '{secret}/key' >/dev/null 2>&1 && exit 12; exit 0"
        )
        proc = subprocess.run(sandbox.wrap(["/bin/sh", "-c", script], policy), capture_output=True, timeout=30)
        if proc.returncode != 0 or (outside / "f").exists():
            raise SandboxUnavailable(
                f"{sandbox.name} did not enforce the policy (exit {proc.returncode}): {proc.stderr.decode()[:300]}"
            )


def get_sandbox(force_none: bool = False) -> Sandbox:
    """Return a probed, enforcing sandbox or raise SandboxUnavailable (fail closed)."""
    if force_none or os.environ.get("AGENT_FACTORY_DISABLE_SANDBOX") == "1":
        raise SandboxUnavailable("sandbox disabled by configuration; code execution is refused")
    system = platform.system()
    candidate: Sandbox | None = None
    if system == "Darwin" and Path("/usr/bin/sandbox-exec").exists():
        candidate = MacSandbox()
    elif system == "Linux" and (bwrap := shutil.which("bwrap")):
        candidate = BwrapSandbox(bwrap)
    if candidate is None:
        raise SandboxUnavailable(f"no supported sandbox on {system}")
    if candidate.name not in _PROBED:
        probe(candidate)
        _PROBED[candidate.name] = candidate
    return _PROBED[candidate.name]
