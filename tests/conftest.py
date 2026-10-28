from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_factory.adapters.fake import FakeReply, FakeRuntime, envelope
from agent_factory.adapters.github import LocalMergeHandler
from agent_factory.config import load_config
from agent_factory.factory import Factory
from agent_factory.features import normalize
from agent_factory.sandbox import get_sandbox
from agent_factory.state import Store

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "repos" / "greeter"


def sh(cwd: Path, *argv: str) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def git_commit_all(repo: Path, message: str) -> str:
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return sh(repo, "git", "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A user checkout of the fixture app with a local bare remote as origin."""
    remote = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "trunk", str(remote))
    work = tmp_path / "user-checkout"
    shutil.copytree(FIXTURE_REPO, work, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sh(work, "git", "init", "-q", "-b", "trunk")
    git_commit_all(work, "initial")
    sh(work, "git", "remote", "add", "origin", str(remote))
    sh(work, "git", "push", "-q", "origin", "trunk")
    sh(work, "git", "remote", "set-head", "origin", "trunk")
    return work


def push_base_change(repo: Path, tmp_path: Path, rel: str, content: str) -> str:
    """Another actor advances the remote base branch."""
    other = tmp_path / f"other-{len(list(tmp_path.iterdir()))}"
    sh(tmp_path, "git", "clone", "-q", sh(repo, "git", "remote", "get-url", "origin"), str(other))
    (other / rel).parent.mkdir(parents=True, exist_ok=True)
    (other / rel).write_text(content)
    sha = git_commit_all(other, f"base change {rel}")
    sh(other, "git", "push", "-q", "origin", "HEAD:trunk")
    return sha


def make_factory_dir(tmp_path: Path, **overrides: Any) -> Path:
    target = tmp_path / "factory"
    shutil.copytree(ROOT / "factory", target, ignore=shutil.ignore_patterns("manifests"))
    cfg = yaml.safe_load((target / "config.yaml").read_text())
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["budget"]["plan_approval"] = "off"  # tests that exercise the approval stop turn it back on
    cfg["decision_model"]["provider"] = "none"  # tests never call an external decision model
    for dotted, value in overrides.items():
        node = cfg
        *path, last = dotted.split(".")
        for key in path:
            node = node[key]
        node[last] = value
    (target / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return target


def write_checks(factory_dir: Path, checks: list[dict]) -> None:
    (factory_dir / "checks" / "default.yaml").write_text(yaml.safe_dump({"schema_version": 1, "checks": checks}))


UNIT_CHECK = {
    "name": "unit", "argv": ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml={report}"],
    "timeout_seconds": 120, "report": "junit", "min_tests": 1, "required": True, "network": "none", "stage": "task",
}


# --- scripted agent replies -----------------------------------------------------------------

DIMS_TRIVIAL = {d: {"score": 0, "evidence": "app/greet.py one-liner"} for d in
                ("scope", "coupling", "state_and_data", "interfaces", "uncertainty", "verification")}


def classify_reply(score: int = 0, risk: str = "low", atomic: bool = True, unknown: str | None = None) -> FakeReply:
    dims = {k: {"score": min(score, 2), "evidence": "fixture"} for k in DIMS_TRIVIAL}
    if score > 2:
        remaining = score
        for k in dims:
            dims[k]["score"] = min(2, remaining)
            remaining -= dims[k]["score"]
    if unknown:
        dims[unknown]["score"] = None
    return FakeReply(envelope("classify-feature", payload={
        "dimensions": dims, "risk": risk, "risk_reasons": [], "single_atomic_task": atomic, "known_pattern": atomic,
        "new_trust_boundary": False, "forced_complex_reasons": [],
    }))


def spec_reply(reqs: int = 1) -> FakeReply:
    requirements = [{"id": f"REQ-{i}", "text": f"requirement {i}", "source": "original_request"} for i in range(1, reqs + 1)]
    return FakeReply(envelope("spec-author", payload={
        "requirements": requirements,
        "intent_map": [{"intent": f"intent {i}", "requirement_ids": [f"REQ-{i}"]} for i in range(1, reqs + 1)],
        "acceptance_criteria": [{"id": f"AC-{i}", "requirement_ids": [f"REQ-{i}"], "observable": f"observable {i}"} for i in range(1, reqs + 1)],
        "files": {"proposal.md": "# Proposal\n\nWhy: fixture.\n"},
    }))


def spec_eval_reply(verdict: str = "PASS", uncovered: list[str] | None = None) -> FakeReply:
    dims = {d: ("PASS" if verdict == "PASS" else "CHANGES") for d in ("intent_coverage", "consistency", "testability", "feasibility", "risk_controls")}
    findings = [] if verdict == "PASS" else [{"id": "SE-1", "severity": "high", "blocking": True, "problem": "requirement from the request is missing", "status": "open"}]
    return FakeReply(envelope("spec-evaluate", verdict, payload={
        "dimensions": dims, "counterexample": "returning the old greeting passes nothing", "uncovered_requirements": uncovered or [],
    }, findings=findings))


def options_reply(ratings: dict[str, dict] | None = None, selected: str | None = "OPT-A") -> FakeReply:
    ratings = ratings or {
        "OPT-A": {"goal_fit": 5, "correctness": 5, "maintainability": 4, "delivery_time": 4, "cost": 4},
        "OPT-B": {"goal_fit": 3, "correctness": 4, "maintainability": 3, "delivery_time": 3, "cost": 3},
    }
    options = [{"option_id": oid, "title": oid, "feasible": True, "ratings": r, "estimated_cost_usd": 1, "estimated_minutes": 30} for oid, r in ratings.items()]
    return FakeReply(envelope("options-review", payload={"options": options, "selected_option_id": selected}))


def select_reply(selected: str = "OPT-A", ratings: dict | None = None) -> FakeReply:
    reply = options_reply(ratings, selected)
    reply.envelope["kind"] = "select-option"
    return reply


def plan_reply(tasks: list[dict] | None = None) -> FakeReply:
    tasks = tasks or [
        {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [], "file_scope": ["app/greet.py", "tests"],
         "acceptance": "greeting ends with !", "estimated_minutes": 20},
        {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": ["TASK-01"], "file_scope": ["app/farewell.py", "tests"],
         "acceptance": "farewell exists", "estimated_minutes": 20},
    ]
    return FakeReply(envelope("plan", payload={"tasks": tasks, "future_needs": [{"description": "export later", "status": "hypothetical"}]}))


def decomposition_reply(verdict: str = "PASS", reqs: int = 2) -> FakeReply:
    mapping = {f"REQ-{i}": [f"TASK-0{i}"] for i in range(1, reqs + 1)}
    return FakeReply(envelope("decomposition-review", verdict, payload={"requirements_map": mapping, "per_task": [], "dag_ok": verdict == "PASS"},
                              findings=[] if verdict == "PASS" else [{"id": "DR-1", "severity": "high", "blocking": True, "problem": "cycle", "status": "open"}]))


GREET_BANG = 'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n'
GREET_TEST = 'from app.greet import greet\n\n\ndef test_greet_exclaims():\n    assert greet("Ada") == "Hello, Ada!"\n'
FAREWELL = 'def farewell(name: str) -> str:\n    return f"Bye, {name}!"\n'
FAREWELL_TEST = 'from app.farewell import farewell\n\n\ndef test_farewell():\n    assert farewell("Ada") == "Bye, Ada!"\n'


def implement_by_task(request) -> FakeReply:
    if "TASK-02" in request.prompt and "task=TASK-02" in request.prompt:
        edits = {"app/farewell.py": FAREWELL, "tests/test_farewell.py": FAREWELL_TEST}
    else:
        edits = {"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": GREET_TEST}
    return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [], "handoff": "done", "claimed_checks": ["all tests pass"]}), edits=edits)


def review_pass(kind: str) -> FakeReply:
    return FakeReply(envelope(kind, payload={}))


def base_responders(path: str = "trivial") -> dict[str, Any]:
    responders: dict[str, Any] = {
        "factory-intake": FakeReply(envelope("intake", payload={"goal": "greeting exclaims", "acceptance_criteria": [], "constraints": [], "open_questions": []})),
        "factory-classify-feature": classify_reply(0 if path == "trivial" else 4, atomic=path == "trivial"),
        "factory-spec-author": spec_reply(1 if path == "trivial" else 2),
        "factory-spec-evaluate": spec_eval_reply(),
        "factory-implement": implement_by_task,
        "factory-root-cause": FakeReply(envelope("root-cause", payload={"hypothesis": "h"})),
        "factory-code-review": review_pass("code-review"),
        "factory-security-review": review_pass("security-review"),
        "factory-ui-review": review_pass("ui-review"),
        "factory-product-verify": review_pass("product-verify"),
        "factory-test-adequacy": review_pass("test-adequacy"),
        "factory-integration-review": review_pass("integration-review"),
    }
    if path != "trivial":
        responders.update({
            "factory-options-review": options_reply(),
            "factory-select-option": select_reply(),
            "factory-plan": plan_reply(),
            "factory-decomposition-review": decomposition_reply(),
        })
    return responders


class Env:
    def __init__(self, tmp_path: Path, repo: Path, responders: dict[str, Any], **overrides: Any):
        self.tmp_path = tmp_path
        self.repo = repo
        self.factory_dir = make_factory_dir(tmp_path, **overrides)
        write_checks(self.factory_dir, [UNIT_CHECK])
        self.reload(responders)

    def reload(self, responders: dict[str, Any] | None = None) -> None:
        self.cfg = load_config(self.factory_dir)
        self.store = Store(self.cfg.data_dir() / "state.db")
        if responders is not None:
            self.runtime = FakeRuntime(responders)
        self.factory = Factory(self.cfg, self.store, self.runtime, get_sandbox(),
                               merge_handler=LocalMergeHandler(self.cfg.factory.policy), poll_interval=0.01)

    def submit(self, text: str = "Make the greeting end with an exclamation mark.", mode: str | None = None) -> str:
        feature = normalize({"original_request": text, "acceptance_criteria": []}, source_kind="cli", repo_id="greeter")
        return self.factory.submit(feature, self.repo, mode=mode)

    def state(self, job_id: str) -> str:
        return self.store.job(job_id)["state"]

    def events(self, job_id: str, kind: str | None = None) -> list:
        return [e for e in self.store.events_for(job_id) if kind is None or e["kind"] == kind]

    def calls(self, skill: str) -> int:
        return sum(1 for c in self.runtime.calls if c.skill == skill)


@pytest.fixture
def make_env(tmp_path, repo):
    def factory(path: str = "trivial", responders: dict | None = None, **overrides):
        merged = base_responders(path)
        merged.update(responders or {})
        return Env(tmp_path, repo, merged, **overrides)

    return factory
