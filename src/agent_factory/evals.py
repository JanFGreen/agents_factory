"""Factory evaluation (blueprint §14.3): full factory vs a single strong agent.

Each task runs on a fresh copy of a fixture repo with a local bare remote. Success is
decided by golden tests that the builder never sees: they are added to a scratch
clone of the final head and executed by the trusted runner in the sandbox.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import yaml

from .adapters.claude import ClaudeRuntime
from .adapters.github import LocalMergeHandler
from .config import LoadedConfig
from .features import normalize
from .runner import CheckSpec, TrustedRunner
from .sandbox import get_sandbox
from .state import Store

GOLDEN_CHECK = CheckSpec(
    name="golden", argv=["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml={report}", "golden_tests"],
    timeout_seconds=300, report="junit", min_tests=1,
)


def _sh(cwd: Path, *argv: str) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _fixture_repo(fixture: Path, root: Path) -> Path:
    remote = root / "remote.git"
    _sh(root, "git", "init", "-q", "--bare", "-b", "trunk", str(remote))
    work = root / "checkout"
    shutil.copytree(fixture, work, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    _sh(work, "git", "init", "-q", "-b", "trunk")
    _sh(work, "git", "add", "-A")
    _sh(work, "git", "-c", "user.name=eval", "-c", "user.email=eval@localhost", "commit", "-q", "-m", "fixture")
    _sh(work, "git", "remote", "add", "origin", str(remote))
    _sh(work, "git", "push", "-q", "origin", "trunk")
    _sh(work, "git", "remote", "set-head", "origin", "trunk")
    return work


def _golden(runner: TrustedRunner, head_tree: Path, task: dict, job_id: str) -> dict:
    runner.work_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(dir=runner.work_root))
    shutil.copytree(head_tree, scratch / "tree", ignore=shutil.ignore_patterns(".git"))
    golden = scratch / "tree" / "golden_tests"
    golden.mkdir()
    (golden / "test_golden.py").write_text(task["golden"])
    ev = runner.run_check(GOLDEN_CHECK, scratch / "tree", job_id=job_id, head_sha=None, tree_sha=None)
    return {"verdict": ev.verdict, "reason": ev.reason, "tests": ev.tests}


def run_suite(
    cfg: LoadedConfig,
    suite_path: Path,
    profile: str,
    *,
    runtime_factory: Callable[[Path], Any] | None = None,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    from .factory import Factory

    suite = yaml.safe_load(suite_path.read_text())
    fixture = (suite_path.parent / suite["fixture"]).resolve()
    sandbox = get_sandbox()
    results = []
    root = cfg.data_dir() / "evals" / f"{suite['name']}-{profile}-{uuid.uuid4().hex[:6]}"
    root.mkdir(parents=True)
    for task in suite["tasks"]:
        if task_ids and task["id"] not in task_ids:
            continue
        task_root = root / task["id"]
        task_root.mkdir()
        repo = _fixture_repo(fixture, task_root)
        store = Store(task_root / "state.db")
        task_cfg = cfg.model_copy(update={"factory": cfg.factory.model_copy(update={"data_dir": str(task_root / "data")})})
        runtime = runtime_factory(task_root) if runtime_factory else ClaudeRuntime(cfg.factory.runtime.claude_binary, sandbox, task_root / "logs")
        factory = Factory(task_cfg, store, runtime, sandbox, merge_handler=LocalMergeHandler(task_cfg.factory.policy))
        feature = normalize({"original_request": task["request"]}, source_kind="file", repo_id="eval")
        started = time.time()
        job_id = factory.submit(feature, repo)
        if profile == "full":
            final = factory.run(job_id)
        else:
            final = _single_agent(factory, job_id, task)
        job = store.job(job_id)
        spend = store.one("SELECT COALESCE(SUM(settled_usd), 0) FROM budget_reservations WHERE job_id = ?", (job_id,))[0]
        golden = _golden(factory.runner, Path(job["worktree"]), task, job_id) if job["worktree"] else {"verdict": "NOT_RUN"}
        results.append({
            "task_id": task["id"], "category": task["category"], "final_state": final, "golden": golden,
            "accepted_by_factory": final in ("READY_TO_MERGE", "MERGED"), "success": golden["verdict"] == "PASS",
            "cost_usd": round(spend, 4), "seconds": round(time.time() - started, 1), "build_attempts": job["build_attempts"],
            "analysis_rounds": store.one("SELECT COUNT(*) FROM analysis_rounds WHERE job_id = ?", (job_id,))[0],
        })
    n = len(results)
    summary = {
        "suite": suite["name"], "profile": profile, "n": n,
        "success": sum(r["success"] for r in results),
        "false_accepts": sum(r["accepted_by_factory"] and not r["success"] for r in results),
        "false_rejects": sum(not r["accepted_by_factory"] and r["success"] for r in results),
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "median_seconds": sorted(r["seconds"] for r in results)[n // 2] if n else None,
        "note": "pilot: small n, not a statistical proof" if n < 30 else "",
        "results": results,
    }
    (root / "report.json").write_text(json.dumps(summary, indent=2))
    summary["report_path"] = str(root / "report.json")
    return summary


def _single_agent(factory, job_id: str, task: dict) -> str:
    """Baseline: one builder call with the same tools and sandbox, no analysis/review loop."""
    factory._ensure_worktree(job_id)
    factory.call_agent(job_id, "factory-implement", {"task": {"task_id": "TASK-01", "acceptance": task["request"]},
                                                     "feature_goal": task["request"]}, task_id="TASK-01", writable=True, purpose="build")
    from .workspaces import WorkspaceManager

    WorkspaceManager.commit_all(Path(factory.store.job(job_id)["worktree"]), "single-agent baseline")
    return "SINGLE_AGENT_DONE"
