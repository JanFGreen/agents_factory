"""Regression tests for defects found during review of the implementation."""

from __future__ import annotations

from pathlib import Path

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.identities import ResultKind

from ..conftest import spec_reply


def test_trivial_review_spec_defect_returns_to_micro_spec_not_crash(make_env):
    spec_defect = FakeReply(envelope("product-verify", "CHANGES", payload={"error_class": "spec"},
                                     findings=[{"id": "PV-1", "severity": "high", "blocking": True, "problem": "spec misses a case"}]))
    env = make_env("trivial", responders={"factory-product-verify": [spec_defect, FakeReply(envelope("product-verify"))],
                                          "factory-spec-author": [spec_reply(1), spec_reply(1)]})
    job = env.submit()
    final = env.factory.run(job)
    transitions = [(e["from_state"], e["to_state"]) for e in env.events(job, "state.transition")]
    assert ("REVIEW", "MICRO_SPEC") in transitions
    assert final == "READY_TO_MERGE"


def test_check_manifest_change_invalidates_gate_and_halts_visibly(make_env):
    env = make_env("trivial")
    job = env.submit()
    for _ in range(80):
        if env.state(job) == "REVIEW":
            break
        env.factory.step(job)
    ok, _ = env.factory.evidence.current_pass(job, ResultKind.GATE, env.factory.controller.identity(job, "TASK-01"), "TASK-01")
    assert ok
    manifest = env.factory_dir / "checks" / "default.yaml"
    manifest.write_text(manifest.read_text().replace("timeout_seconds: 120", "timeout_seconds: 121"))
    ok, why = env.factory.evidence.current_pass(job, ResultKind.GATE, env.factory.controller.identity(job, "TASK-01"), "TASK-01")
    assert not ok and "checks_digest" in why
    assert env.factory.run(job) in ("NEEDS_HUMAN", "READY_TO_MERGE")  # never an uncaught crash
    assert env.state(job) != "MERGED"


def test_reviewer_modifying_worktree_is_blocked(make_env):
    def dirty_review(request):
        (request.cwd / "app" / "greet.py").write_text("tampered = True\n")
        return FakeReply(envelope("code-review"))

    env = make_env("trivial", responders={"factory-code-review": dirty_review})
    job = env.submit()
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert "read-only" in env.store.job(job)["blocked_reason"]


def test_resume_cannot_jump_to_illegal_state(make_env):
    import pytest

    from agent_factory.controller import TransitionRefused

    env = make_env("trivial")
    job = env.submit()
    with env.store.tx() as conn:
        conn.execute("UPDATE jobs SET state = 'NEEDS_INFO', context_json = json_set(context_json, '$.resume_state', 'MERGED') WHERE job_id = ?", (job,))
    with pytest.raises(TransitionRefused):
        env.factory.resume(job)


def test_payload_tampering_breaks_signature(make_env):
    env = make_env("trivial")
    job = env.submit()
    env.factory.run(job)
    row = env.store.one("SELECT payload_artifact_id FROM results WHERE job_id = ? AND kind = 'gate' AND verdict = 'PASS'", (job,))
    env.store.conn.execute("UPDATE artifacts SET sha256 = ? WHERE artifact_id = ?", ("0" * 64, row["payload_artifact_id"]))
    ok, why = env.factory.evidence.current_pass(job, ResultKind.GATE, env.factory.controller.identity(job, "TASK-01"), "TASK-01")
    assert not ok and "signature" in why


def test_same_finding_id_in_two_tasks_does_not_collide(make_env):
    env = make_env("standard")
    job = "job-000000000099"
    from agent_factory.evidence import Producer
    from agent_factory.identities import InputIdentity
    finding = {"id": "F-1", "severity": "high", "blocking": True, "problem": "p", "status": "open"}
    for task in ("TASK-01", "TASK-02"):
        env.factory.evidence.record_result(job_id=job, kind=ResultKind.CODE_REVIEW, verdict="CHANGES", identity=InputIdentity(),
                                           producer=Producer("agent-adapter", "r"), payload={}, task_id=task, findings=[finding])
    rows = env.store.all("SELECT task_id FROM findings WHERE job_id = ? ORDER BY task_id", (job,))
    assert [r["task_id"] for r in rows] == ["TASK-01", "TASK-02"]


def test_resume_from_blocked_env_returns_to_recorded_state(make_env, tmp_path):
    from ..conftest import push_base_change
    env = make_env("trivial")
    push_base_change(env.repo, tmp_path, "tests/test_broken.py", "def test_broken():\n    assert False\n")
    job = env.submit()
    assert env.factory.run(job) == "BLOCKED_ENV"
    push_base_change(env.repo, tmp_path, "tests/test_broken.py", "def test_broken():\n    assert True\n")
    # operator fixed the base; the worktree base is stale, so the job is cancelled rather than forced
    env.factory.resume(job)
    assert env.state(job) == "SINGLE_TASK_READY"


def test_stale_spec_after_policy_change_resumes_into_analysis(make_env):
    env = make_env("trivial")
    job = env.submit()
    for _ in range(80):
        if env.state(job) == "CHECKS":
            break
        env.factory.step(job)
    import yaml
    cfg_path = env.factory_dir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["policy"]["protected_paths"].append("docs/*")
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    env.reload()
    assert env.factory.run(job) == "NEEDS_HUMAN"
    env.factory.resume(job)
    assert env.state(job) == "MICRO_SPEC"
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_agents_cannot_read_factory_golden_tests():
    from pathlib import Path

    from agent_factory.sandbox import default_denied_reads
    evals = Path(__file__).resolve().parents[1] / "evals"
    assert evals in [p.resolve() for p in default_denied_reads(Path("/nonexistent"))]
