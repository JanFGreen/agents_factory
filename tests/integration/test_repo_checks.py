"""Per-repo check manifests and operator-installed dependencies linked into worktrees."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_factory.features import normalize
from agent_factory.runner import ChecksManifest

from ..conftest import UNIT_CHECK, git_commit_all, sh


def _write_manifest(env, name: str, checks: list[dict], links: list[str]) -> None:
    (env.factory_dir / "checks" / f"{name}.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "link_from_checkout": links, "checks": checks}))
    env.reload()


def _submit(env, checks: str | None) -> str:
    feature = normalize({"original_request": "Make the greeting end with an exclamation mark.", "acceptance_criteria": []},
                        source_kind="cli", repo_id="greeter")
    return env.factory.submit(feature, env.repo, checks=checks)


def _install_deps(repo: Path, ignore_line: str | None) -> None:
    if ignore_line:
        (repo / ".gitignore").write_text(f"{ignore_line}\n")
        git_commit_all(repo, "ignore deps")
        sh(repo, "git", "push", "-q", "origin", "HEAD:trunk")
    (repo / "deps").mkdir()
    (repo / "deps" / "marker.txt").write_text("installed by the operator\n")


def test_job_uses_the_repo_manifest_and_gets_linked_dependencies(make_env, monkeypatch):
    env = make_env("trivial")
    _install_deps(env.repo, "deps")
    _write_manifest(env, "alt", [{**UNIT_CHECK, "name": "alt-unit"}], ["deps"])
    gates: list[list[str]] = []
    real_gate = env.factory.runner.run_gate

    def run_gate(checks, worktree, **kw):
        gates.append([c.name for c in checks])
        assert (worktree / "deps" / "marker.txt").read_text() == "installed by the operator\n"
        return real_gate(checks, worktree, **kw)

    monkeypatch.setattr(env.factory.runner, "run_gate", run_gate)
    job = _submit(env, "alt")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert gates and all(names == ["alt-unit"] for names in gates)
    worktree = Path(env.store.job(job)["worktree"])
    assert (worktree / "deps").is_symlink()
    assert "deps" not in sh(worktree, "git", "ls-tree", "-r", "--name-only", "HEAD").splitlines()


def test_dependency_link_that_git_would_track_blocks_the_job(make_env):
    env = make_env("trivial")
    _install_deps(env.repo, "deps/")  # a trailing slash does not ignore a symlink
    _write_manifest(env, "alt", [UNIT_CHECK], ["deps"])
    job = _submit(env, "alt")
    assert env.factory.run(job) == "BLOCKED_ENV"
    assert "gitignored" in env.store.job(job)["blocked_reason"]


def test_missing_dependency_dir_in_checkout_blocks_the_job(make_env):
    env = make_env("trivial")
    _write_manifest(env, "alt", [UNIT_CHECK], ["deps"])
    job = _submit(env, "alt")
    assert env.factory.run(job) == "BLOCKED_ENV"
    assert "install dependencies" in env.store.job(job)["blocked_reason"]


def test_unknown_manifest_name_is_refused_at_submit(make_env):
    env = make_env("trivial")
    with pytest.raises(ValueError):
        _submit(env, "../default")
    with pytest.raises(FileNotFoundError):
        _submit(env, "nope")


@pytest.mark.parametrize("link", ["../outside", "/abs", ".git", ""])
def test_manifest_rejects_links_outside_the_worktree(link):
    with pytest.raises(ValueError):
        ChecksManifest.model_validate({"schema_version": 1, "checks": [UNIT_CHECK], "link_from_checkout": [link]})


def test_shipped_node_manifest_is_valid():
    from agent_factory.runner import load_checks

    manifest = load_checks(Path(__file__).resolve().parents[2] / "factory" / "checks" / "node.yaml")
    assert manifest.link_from_checkout == ["node_modules"]
    assert {c.name for c in manifest.for_stage("task")} == {"unit", "typecheck", "lint", "web-build"}
