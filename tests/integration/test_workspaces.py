from __future__ import annotations

import pytest

from agent_factory.state import Store
from agent_factory.workspaces import GitError, WorkspaceManager, allocate_port, default_branch

from ..conftest import push_base_change, sh


@pytest.fixture
def wm(tmp_path):
    store = Store(tmp_path / "data" / "state.db")
    return store, WorkspaceManager(store, tmp_path / "data")


def test_default_branch_is_discovered_not_assumed(repo):
    assert default_branch(repo) == "trunk"


def test_af19_two_jobs_get_separate_worktrees_branches_and_ports(repo, wm):
    store, mgr = wm
    a = mgr.create(repo, "job-00000000000a", "trunk")
    b = mgr.create(repo, "job-00000000000b", "trunk")
    assert a.path != b.path and a.branch != b.branch
    (a.path / "x.txt").write_text("a")
    assert not (b.path / "x.txt").exists()
    ports = {allocate_port(store, "job-00000000000a"), allocate_port(store, "job-00000000000b")}
    assert len(ports) == 2
    assert sh(repo, "git", "status", "--porcelain") == ""  # user checkout untouched


def test_invalid_job_id_rejected(wm):
    _, mgr = wm
    with pytest.raises(ValueError):
        mgr.path_for("../../etc")


def test_af34_cleanup_preserves_uncommitted_work(repo, wm):
    _, mgr = wm
    ws = mgr.create(repo, "job-00000000000c", "trunk")
    (ws.path / "wip.py").write_text("unsaved")
    with pytest.raises(GitError, match="uncommitted work"):
        mgr.cleanup(repo, "job-00000000000c", merged=True)
    assert (ws.path / "wip.py").exists()


def test_cleanup_dry_run_and_never_force(repo, wm):
    _, mgr = wm
    mgr.create(repo, "job-00000000000d", "trunk")
    actions = mgr.cleanup(repo, "job-00000000000d", merged=False, dry_run=True)
    assert actions and all("--force" not in a and "-D" not in a for a in actions)


def test_af31_push_rejected_when_base_moved(repo, wm, tmp_path):
    _, mgr = wm
    ws = mgr.create(repo, "job-00000000000e", "trunk")
    (ws.path / "f.txt").write_text("f")
    WorkspaceManager.commit_all(ws.path, "change")
    push_base_change(repo, tmp_path, "g.txt", "g")
    with pytest.raises(GitError):
        WorkspaceManager.push_to_base(ws.path, "trunk", ws.base_sha)
