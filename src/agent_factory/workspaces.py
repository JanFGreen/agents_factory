"""Git worktree lifecycle, integration candidates, safe cleanup and port allocation.

All git calls use argument arrays (never a shell) with validated identifiers. The
user's checkout is never modified; job work happens in controller-owned worktrees.
"""

from __future__ import annotations

import re
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .policy import FileDiff
from .state import Store, new_id, now

JOB_ID = re.compile(r"^job-[0-9a-f]{12}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
# Build/test by-products are never part of a candidate (they would pollute diffs, scope checks and reviews).
GENERATED_EXCLUDES = [
    ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc", ":(exclude,glob)**/.pytest_cache/**",
    ":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.DS_Store", ":(exclude,glob)**/.coverage",
]
AUTHOR = ["-c", "user.name=Agent Factory", "-c", "user.email=agent-factory@localhost", "-c", "commit.gpgsign=false"]


class GitError(RuntimeError):
    def __init__(self, argv: list[str], code: int, stderr: str):
        super().__init__(f"git {' '.join(argv)} failed ({code}): {stderr.strip()[:500]}")
        self.code = code
        self.stderr = stderr


def git(cwd: Path, *args: str, check: bool = True, timeout: int = 120) -> str:
    argv = ["git", *args]
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc.stdout.strip()


def default_branch(repo: Path, configured: str | None = None) -> str:
    """From config, else origin/HEAD, else the remote's advertised HEAD. Never assume ``main``."""
    if configured:
        if not BRANCH.match(configured):
            raise ValueError(f"invalid branch name {configured!r}")
        return configured
    ref = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    if ref.startswith("origin/"):
        return ref.removeprefix("origin/")
    out = git(repo, "ls-remote", "--symref", "origin", "HEAD", check=False)
    match = re.search(r"ref: refs/heads/(\S+)\s+HEAD", out)
    if match:
        return match.group(1)
    raise GitError(["default-branch"], 1, "cannot determine the default branch; set it in the repo config")


def fetch_base(repo: Path, branch: str) -> str:
    git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    return git(repo, "rev-parse", f"refs/remotes/origin/{branch}")


@dataclass
class Workspace:
    path: Path
    branch: str
    base_branch: str
    base_sha: str


class WorkspaceManager:
    def __init__(self, store: Store, data_dir: Path):
        self.store = store
        self.root = data_dir / "worktrees"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, job_id: str) -> Path:
        if not JOB_ID.match(job_id):
            raise ValueError(f"invalid job id {job_id!r}")
        return self.root / job_id

    def create(self, repo: Path, job_id: str, base_branch: str) -> Workspace:
        path = self.path_for(job_id)
        branch = f"factory/{job_id}"
        base_sha = fetch_base(repo, base_branch)
        if path.exists():
            # Resume: verify it is really our worktree on our branch.
            current = git(path, "rev-parse", "--abbrev-ref", "HEAD")
            if current != branch:
                raise GitError(["worktree-verify"], 1, f"{path} is on {current}, expected {branch}")
            return Workspace(path, branch, base_branch, git(path, "merge-base", "HEAD", f"refs/remotes/origin/{base_branch}"))
        git(repo, "worktree", "add", "--quiet", "-b", branch, str(path), base_sha)
        if git(path, "rev-parse", "HEAD") != base_sha or git(path, "status", "--porcelain"):
            raise GitError(["worktree-verify"], 1, "new worktree is not clean at base")
        return Workspace(path, branch, base_branch, base_sha)

    @staticmethod
    def head(path: Path) -> str:
        return git(path, "rev-parse", "HEAD")

    @staticmethod
    def tree(path: Path) -> str:
        return git(path, "rev-parse", "HEAD^{tree}")

    @staticmethod
    def commit_all(path: Path, message: str) -> str | None:
        """Controller-side commit of the builder's work. Returns the new sha or None if no change."""
        git(path, "add", "-A", "--", ".", *GENERATED_EXCLUDES)
        if not git(path, "diff", "--cached", "--name-only"):
            return None
        git(path, *AUTHOR, "commit", "--quiet", "--no-verify", "-m", message)
        return git(path, "rev-parse", "HEAD")

    @staticmethod
    def diff(path: Path, base_sha: str) -> list[FileDiff]:
        names = git(path, "diff", "--name-status", "-M", f"{base_sha}..HEAD")
        files = []
        for line in names.splitlines():
            parts = line.split("\t")
            status, file_path = parts[0][0], parts[-1]
            patch = git(path, "diff", "--unified=0", f"{base_sha}..HEAD", "--", file_path)
            files.append(FileDiff(file_path, status, patch))
        return files

    @staticmethod
    def integrate(path: Path, repo: Path, base_branch: str) -> tuple[str, str]:
        """Merge the fresh base into the job branch. Returns (base_sha, candidate_sha)."""
        base_sha = fetch_base(repo, base_branch)
        git(path, "fetch", "--quiet", str(repo), f"refs/remotes/origin/{base_branch}")
        result = subprocess.run(
            ["git", *AUTHOR, "merge", "--no-ff", "--no-edit", base_sha], cwd=path, capture_output=True, text=True
        )
        if result.returncode != 0:
            subprocess.run(["git", "merge", "--abort"], cwd=path, capture_output=True)
            raise GitError(["merge", base_sha], result.returncode, result.stdout + result.stderr)
        return base_sha, git(path, "rev-parse", "HEAD")

    @staticmethod
    def push_to_base(path: Path, base_branch: str, expected_base_sha: str) -> str:
        """Fast-forward the remote base to the tested candidate, atomically conditional on the base.

        ``--force-with-lease`` with an explicit expected value makes the server reject the
        update if the base moved after the candidate was tested; no force otherwise.
        """
        head = git(path, "rev-parse", "HEAD")
        proc = subprocess.run(
            ["git", "push", "--porcelain", f"--force-with-lease=refs/heads/{base_branch}:{expected_base_sha}",
             "origin", f"{head}:refs/heads/{base_branch}"],
            cwd=path, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise GitError(["push", base_branch], proc.returncode, proc.stdout + proc.stderr)
        return head

    def cleanup(self, repo: Path, job_id: str, *, merged: bool, dry_run: bool = False) -> list[str]:
        """Remove the worktree only if clean; never --force, never branch -D."""
        path = self.path_for(job_id)
        actions: list[str] = []
        if path.exists():
            dirty = git(path, "status", "--porcelain", "--untracked-files=all", "--", ".", *GENERATED_EXCLUDES)
            if dirty:
                raise GitError(["cleanup"], 1, f"worktree has uncommitted work, preserved:\n{dirty[:500]}")
            actions.append(f"git worktree remove {path}")
            if not dry_run:
                git(repo, "worktree", "remove", str(path))
        branch = f"factory/{job_id}"
        if merged and git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
            actions.append(f"git branch -d {branch}")
            if not dry_run:
                git(repo, "branch", "-d", branch, check=False)  # squash merges keep the branch: archived, not -D
        return actions


# --- ports --------------------------------------------------------------------------------


def allocate_port(store: Store, job_id: str, low: int = 20000, high: int = 29999) -> int:
    """Atomic registry allocation plus a real bind test (a formula is not enough)."""
    for port in range(low, high + 1):
        try:
            with store.tx() as conn:
                conn.execute(
                    "INSERT INTO resource_allocations VALUES (?, 'port', ?, ?, 'workspace', ?, NULL)",
                    (new_id("port"), str(port), job_id, now()),
                )
        except sqlite3.IntegrityError:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                with store.tx() as conn:
                    conn.execute(
                        "UPDATE resource_allocations SET released_at = ?, owner = 'bind-failed' WHERE kind = 'port' AND value = ? AND released_at IS NULL",
                        (now(), str(port)),
                    )
                continue
        return port
    raise RuntimeError("no free port")


def release_job_resources(store: Store, job_id: str) -> None:
    with store.tx() as conn:
        conn.execute("UPDATE resource_allocations SET released_at = ? WHERE job_id = ? AND released_at IS NULL", (now(), job_id))
