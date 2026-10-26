"""GitHub adapter (issue intake, PR, checks, merge) and the local-remote merge handler.

All writes go through the outbox (``EffectHandler``). PRs carry an idempotency marker
so a retry after a lost response finds the existing PR instead of opening a second
one. Merges pass the tested head ``sha`` (server rejects if the head moved), never use
admin bypass, and require every required check to be complete and successful.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import Policy
from ..outbox import OperationFailed, OperationTimeout
from ..policy import authorize_side_effect
from ..workspaces import GitError, WorkspaceManager, git

MARKER = "<!-- agent-factory:idem={key} -->"


class Transport(Protocol):
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]: ...


class GhCliTransport:
    """Uses the operator's ``gh`` auth in the controller process (never inside the sandbox)."""

    def __init__(self, binary: str = "gh", timeout: int = 60):
        self.binary = binary
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        argv = [self.binary, "api", "--method", method, "-H", "Accept: application/vnd.github+json", "--include", path]
        if body is not None:
            argv += ["--input", "-"]
        try:
            proc = subprocess.run(
                argv, input=json.dumps(body) if body is not None else None, capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise OperationTimeout(f"{method} {path} timed out") from exc
        head, _, payload = proc.stdout.partition("\r\n\r\n")
        status = int(head.split()[1]) if head.startswith("HTTP/") else (200 if proc.returncode == 0 else 500)
        try:
            return status, json.loads(payload) if payload.strip() else None
        except json.JSONDecodeError:
            return status, payload


@dataclass
class GitHubRepo:
    owner: str
    name: str

    @property
    def base(self) -> str:
        return f"/repos/{self.owner}/{self.name}"


class GitHubAdapter:
    def __init__(self, transport: Transport, repo: GitHubRepo, policy: Policy):
        self.t = transport
        self.repo = repo
        self.policy = policy

    # --- reads ----------------------------------------------------------------------

    def issue(self, number: int) -> dict[str, Any]:
        status, body = self.t.request("GET", f"{self.repo.base}/issues/{int(number)}")
        if status != 200:
            raise OperationFailed(f"issue {number}: HTTP {status}")
        # Issue text is untrusted data: only these fields are taken, nothing is executed.
        return {"number": body["number"], "title": body["title"], "body": body.get("body") or "", "labels": [l["name"] for l in body.get("labels", [])]}

    def find_pr(self, head_branch: str, key: str) -> dict[str, Any] | None:
        status, body = self.t.request("GET", f"{self.repo.base}/pulls?state=all&head={self.repo.owner}:{head_branch}")
        if status != 200:
            raise OperationTimeout(f"cannot list PRs: HTTP {status}")
        marker = MARKER.format(key=key)
        for pr in body or []:
            if marker in (pr.get("body") or ""):
                return {"number": pr["number"], "url": pr.get("html_url"), "head_sha": pr["head"]["sha"]}
        return None

    def required_checks_state(self, sha: str) -> tuple[str, list[str]]:
        """'success' only if every check run is completed+successful; else 'pending'/'failure'."""
        status, body = self.t.request("GET", f"{self.repo.base}/commits/{sha}/check-runs")
        if status != 200:
            return "unknown", [f"HTTP {status}"]
        runs = body.get("check_runs", [])
        if not runs:
            return "pending", ["no check runs reported"]
        problems = [f"{r['name']}: {r['status']}/{r.get('conclusion')}" for r in runs if r["status"] != "completed" or r.get("conclusion") != "success"]
        if any(r["status"] != "completed" for r in runs):
            return "pending", problems
        return ("failure", problems) if problems else ("success", [])

    # --- effects (outbox) --------------------------------------------------------------

    def perform(self, kind: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        authorize_side_effect(self.policy, kind)
        if kind == "github.create_pr":
            body = f"{payload['body']}\n\n{MARKER.format(key=key)}"
            status, resp = self.t.request("POST", f"{self.repo.base}/pulls", {
                "title": payload["title"], "head": payload["head"], "base": payload["base"], "body": body,
            })
            if status in (200, 201):
                return {"number": resp["number"], "url": resp.get("html_url"), "head_sha": resp["head"]["sha"]}
            if status >= 500 or status == 429:
                raise OperationTimeout(f"create PR: HTTP {status}")
            raise OperationFailed(f"create PR: HTTP {status} {resp}")
        if kind == "github.comment":
            status, resp = self.t.request("POST", f"{self.repo.base}/issues/{int(payload['number'])}/comments",
                                          {"body": f"{payload['body']}\n\n{MARKER.format(key=key)}"})
            if status in (200, 201):
                return {"id": resp["id"]}
            raise OperationTimeout(f"comment: HTTP {status}")
        if kind == "github.merge":
            state, problems = self.required_checks_state(payload["sha"])
            if state != "success":
                raise OperationFailed(f"required checks not green ({state}): {problems}")
            status, resp = self.t.request("PUT", f"{self.repo.base}/pulls/{int(payload['number'])}/merge", {
                "sha": payload["sha"], "merge_method": payload.get("merge_method", "merge"),
            })
            if status == 200 and resp.get("merged"):
                return {"merged": True, "sha": resp["sha"]}
            if status in (405, 409):
                raise OperationFailed(f"merge refused by server (head moved or protection): {resp}")
            if status == 403:
                raise OperationFailed("EXTERNAL_POLICY_REQUIRES_REVIEW")
            raise OperationTimeout(f"merge: HTTP {status}")
        raise OperationFailed(f"unsupported operation {kind}")

    def lookup(self, kind: str, payload: dict[str, Any], key: str) -> dict[str, Any] | None:
        if kind == "github.create_pr":
            return self.find_pr(payload["head"], key)
        if kind == "github.merge":
            status, resp = self.t.request("GET", f"{self.repo.base}/pulls/{int(payload['number'])}")
            if status == 200 and resp.get("merged"):
                return {"merged": True, "sha": resp.get("merge_commit_sha")}
            return None
        if kind == "github.comment":
            status, resp = self.t.request("GET", f"{self.repo.base}/issues/{int(payload['number'])}/comments")
            marker = MARKER.format(key=key)
            for comment in resp or []:
                if marker in comment.get("body", ""):
                    return {"id": comment["id"]}
            return None
        return None


# --- in-memory GitHub API stub for tests (no network) -----------------------------------


@dataclass
class StubGitHub:
    owner: str = "acme"
    name: str = "app"
    prs: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    checks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    branch_heads: dict[str, str] = field(default_factory=dict)
    fail_after_apply: set[str] = field(default_factory=set)  # e.g. {"POST /pulls"}: apply, then time out
    calls: list[tuple[str, str]] = field(default_factory=list)

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        base = f"/repos/{self.owner}/{self.name}"
        route = path.removeprefix(base).split("?")[0]
        status, resp = self._handle(method, route, path, body)
        if f"{method} {route}" in self.fail_after_apply:
            self.fail_after_apply.discard(f"{method} {route}")
            raise OperationTimeout(f"stub: response lost for {method} {route}")
        return status, resp

    def _handle(self, method: str, route: str, full: str, body: dict | None) -> tuple[int, Any]:
        parts = route.strip("/").split("/")
        if method == "GET" and parts[0] == "issues" and len(parts) == 2:
            issue = self.issues.get(int(parts[1]))
            return (200, issue) if issue else (404, None)
        if method == "POST" and route == "/pulls":
            number = len(self.prs) + 1
            pr = {"number": number, "html_url": f"https://github.test/pr/{number}", "body": body["body"], "head": {"ref": body["head"], "sha": self.branch_heads.get(body["head"], "a" * 40)}, "base": body["base"], "merged": False, "title": body["title"]}
            self.prs[number] = pr
            return 201, pr
        if method == "GET" and route == "/pulls":
            head = full.split("head=")[-1].split(":")[-1] if "head=" in full else None
            return 200, [pr for pr in self.prs.values() if head is None or pr["head"]["ref"] == head]
        if parts[0] == "pulls" and len(parts) == 2 and method == "GET":
            pr = self.prs.get(int(parts[1]))
            return (200, pr) if pr else (404, None)
        if parts[0] == "pulls" and len(parts) == 3 and parts[2] == "merge" and method == "PUT":
            pr = self.prs[int(parts[1])]
            if body["sha"] != pr["head"]["sha"]:
                return 409, {"message": "Head branch was modified"}
            pr["merged"] = True
            pr["merge_commit_sha"] = "f" * 40
            return 200, {"merged": True, "sha": pr["merge_commit_sha"]}
        if parts[0] == "commits" and parts[-1] == "check-runs":
            return 200, {"check_runs": self.checks.get(parts[1], [])}
        if parts[0] == "issues" and parts[-1] == "comments":
            number = int(parts[1])
            if method == "POST":
                comment = {"id": len(self.comments.get(number, [])) + 1, "body": body["body"]}
                self.comments.setdefault(number, []).append(comment)
                return 201, comment
            return 200, self.comments.get(number, [])
        return 404, None


# --- local bare-remote merge (mode: local) ------------------------------------------------


class LocalMergeHandler:
    """Merges by pushing the tested candidate to the local remote's base branch.

    The push is conditional on the base sha that the candidate was tested against
    (``--force-with-lease`` with an explicit value), so a moved base is rejected.
    """

    def __init__(self, policy: Policy):
        self.policy = policy

    def perform(self, kind: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        authorize_side_effect(self.policy, kind)
        if kind != "local.merge":
            raise OperationFailed(f"unsupported operation {kind}")
        worktree = Path(payload["worktree"])
        if WorkspaceManager.head(worktree) != payload["candidate_sha"]:
            raise OperationFailed("worktree head differs from the tested candidate")
        try:
            WorkspaceManager.push_to_base(worktree, payload["base_branch"], payload["base_sha"])
        except GitError as exc:
            raise OperationFailed(f"BASE_MOVED: {exc}") from exc
        return {"merged": True, "sha": payload["candidate_sha"]}

    def lookup(self, kind: str, payload: dict[str, Any], key: str) -> dict[str, Any] | None:
        worktree = Path(payload["worktree"])
        remote_head = git(worktree, "ls-remote", "origin", f"refs/heads/{payload['base_branch']}").split("\t")[0]
        if remote_head == payload["candidate_sha"]:
            return {"merged": True, "sha": remote_head}
        return None
