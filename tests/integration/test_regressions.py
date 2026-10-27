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


def test_generated_bytecode_is_never_committed_or_counted(make_env):
    """Live finding: a builder ran Python via Bash; .pyc files were committed, inflating the diff,
    triggering a false reclassification and out-of-scope review/policy failures."""
    from ..conftest import GREET_BANG, GREET_TEST

    edits = {"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": GREET_TEST,
             **{f"app/__pycache__/m{i}.cpython-312.pyc": "bytecode" for i in range(6)}, ".pytest_cache/v/x": "c"}
    env = make_env("trivial", responders={"factory-implement": FakeReply(envelope("implement", payload={}), edits=edits)})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert [a["source"] for a in env.store.all("SELECT source FROM complexity_assessments WHERE job_id = ?", (job,))] == ["intake"]
    import subprocess
    files = subprocess.run(["git", "ls-files"], cwd=env.store.job(job)["worktree"], capture_output=True, text=True).stdout
    assert "__pycache__" not in files and ".pytest_cache" not in files


def test_reviewer_downgrading_a_finding_to_non_blocking_unblocks_acceptance(make_env):
    """Live finding: TA-1 was re-reported as non-blocking in a PASS run, but the stored row kept blocking=1."""
    blocking = FakeReply(envelope("test-adequacy", "UNKNOWN", findings=[{"id": "TA-1", "severity": "medium", "blocking": True, "problem": "test plan missing"}]))
    downgraded = FakeReply(envelope("test-adequacy", "PASS", findings=[{"id": "TA-1", "severity": "low", "blocking": False, "problem": "test plan missing"}]))
    env = make_env("trivial", responders={"factory-test-adequacy": [blocking, downgraded]})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    row = env.store.one("SELECT blocking, severity FROM findings WHERE job_id = ? AND finding_id = 'TA-1'", (job,))
    assert row["blocking"] == 0 and row["severity"] == "low"
    assert env.calls("factory-implement") == 1


def test_decision_passed_downstream_has_a_single_consistent_choice(make_env):
    """Eval finding: planner saw selection=OPT-C next to the options reviewer's OPT-A and refused to plan."""
    from ..conftest import options_reply, select_reply
    ratings = {"OPT-A": {"goal_fit": 4, "correctness": 4, "maintainability": 4, "delivery_time": 4, "cost": 4},
               "OPT-C": {"goal_fit": 5, "correctness": 5, "maintainability": 5, "delivery_time": 5, "cost": 5}}
    env = make_env("standard", responders={"factory-options-review": options_reply(ratings, "OPT-A"),
                                           "factory-select-option": select_reply("OPT-C", ratings)})
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    decision = env.store.job_context(job)["decision"]
    assert decision["selected_option_id"] == decision["options"]["selected_option_id"] == decision["selection"]["selected_option_id"] == "OPT-C"


def test_spec_changes_revise_the_spec_without_redrafting_options(make_env):
    """Eval finding: every spec CHANGES re-ran options draft and selection, burning the 3 analysis rounds before any build."""
    from ..conftest import spec_eval_reply
    advisory = FakeReply(envelope("spec-evaluate", "PASS", payload={
        "dimensions": {d: "PASS" for d in ("intent_coverage", "consistency", "testability", "feasibility", "risk_controls")},
        "counterexample": "remaining gap (legacy duplicate ids) is outside the request", "uncovered_requirements": [],
    }, findings=[{"id": "SE-2", "severity": "low", "blocking": False, "problem": "legacy duplicate ids are not handled"}]))
    gap = spec_eval_reply("CHANGES")
    gap.envelope["payload"]["dimensions"]["feasibility"] = "PASS"
    env = make_env("standard", responders={"factory-spec-evaluate": [gap, advisory],
                                           "factory-spec-author": [spec_reply(2), spec_reply(2)]})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-options-review") == 1 and env.calls("factory-select-option") == 1
    assert env.calls("factory-spec-author") == 2
    transitions = [(e["from_state"], e["to_state"]) for e in env.events(job, "state.transition")]
    assert ("AI_SPEC_EVALUATION", "OPTION_SELECTED") in transitions
    assert env.store.one("SELECT COUNT(*) FROM analysis_rounds WHERE job_id = ?", (job,))[0] == 2
    builds = [c for c in env.runtime.calls if c.skill == "factory-implement"]
    assert all("legacy duplicate ids are not handled" in c.prompt for c in builds)


def test_infeasible_spec_goes_back_to_options(make_env):
    from ..conftest import spec_eval_reply
    infeasible = spec_eval_reply("CHANGES")
    infeasible.envelope["payload"]["dimensions"]["feasibility"] = "CHANGES"
    env = make_env("standard", responders={"factory-spec-evaluate": [infeasible, spec_eval_reply()],
                                           "factory-spec-author": [spec_reply(2), spec_reply(2)]})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-options-review") == 2


def test_runtime_gets_the_exact_payload_schema_for_each_skill(make_env):
    """Eval finding: with only the envelope schema, a third of analysis calls were re-run for extra/mistyped payload fields."""
    from agent_factory.schemas import envelope_json_schema
    schema = envelope_json_schema("spec-author")
    assert schema["properties"]["kind"]["const"] == "spec-author"
    spec_def = schema["$defs"]["SpecPayload"]
    assert spec_def["additionalProperties"] is False and "change_id" not in spec_def["properties"]
    env = make_env("trivial")
    job = env.submit()
    env.factory.run(job)
    for call in env.runtime.calls:
        assert call.json_schema["properties"]["kind"]["const"] == call.skill.removeprefix("factory-")


def test_decomposition_changes_replan_without_redoing_spec_or_options(make_env):
    """Eval finding: a plan defect (tests scheduled in a later task) restarted options, selection and spec."""
    from ..conftest import decomposition_reply
    env = make_env("standard", responders={"factory-decomposition-review": [decomposition_reply("CHANGES"), decomposition_reply()]})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-plan") == 2
    assert env.calls("factory-options-review") == env.calls("factory-spec-author") == env.calls("factory-spec-evaluate") == 1
    transitions = [(e["from_state"], e["to_state"]) for e in env.events(job, "state.transition")]
    assert ("AI_DECOMPOSITION_REVIEW", "DECOMPOSE") in transitions
    replan = [c for c in env.runtime.calls if c.skill == "factory-plan"][1]
    assert "previous_plan" in replan.prompt and "cycle" in replan.prompt


def test_stalled_agent_stream_is_stopped_and_retried(make_env, monkeypatch):
    """Eval finding: CLI streams stalled with no result event and were only stopped by the 900 s timeout."""
    from agent_factory.adapters.protocol import CancelResult, RunSnapshot
    env = make_env("trivial")
    env.cfg.factory.execution.__dict__["agent_stall_seconds"] = 0
    real_poll = env.runtime.poll
    stalled: set[str] = set()

    async def poll(handle):
        if handle.attempt_id in stalled:
            return RunSnapshot("failed", events_seen=1, error="killed")  # stopped, nothing salvageable
        if not stalled:
            return RunSnapshot("running", events_seen=1)  # first run: stream never advances
        return await real_poll(handle)

    async def cancel(handle):
        stalled.add(handle.attempt_id)
        return CancelResult(True, "stopped")

    monkeypatch.setattr(env.runtime, "poll", poll)
    monkeypatch.setattr(env.runtime, "cancel", cancel)
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert len(stalled) == 1
    assert env.calls("factory-intake") == 2


def test_planner_receives_the_current_spec_evaluation(make_env):
    """Eval finding: the planner returned NEEDS_INFO because the spec-evaluation PASS was not in its inputs."""
    env = make_env("standard")
    job = env.submit("Add exclamation greeting and farewell")
    env.factory.run(job)
    plan_call = next(c for c in env.runtime.calls if c.skill == "factory-plan")
    spec_digest = env.store.job_context(job)["spec_digest"]
    assert '"spec_evaluation"' in plan_call.prompt and spec_digest in plan_call.prompt.split("<inputs>")[1]


def test_plan_task_without_files_is_sent_back_and_builder_knows_tests_are_in_scope(make_env):
    """Eval finding: a planned end-to-end task with an empty file_scope produced 'no changes' builds and repeated reviews;
    a source-only file_scope made the builder skip the tests that test-adequacy then demanded."""
    from ..conftest import decomposition_reply, plan_reply
    padded = plan_reply([
        {"task_id": "TASK-01", "requirement_ids": ["REQ-1", "REQ-2"], "file_scope": ["app/greet.py"], "acceptance": "greeting ends with !"},
        {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": ["TASK-01"], "file_scope": [], "acceptance": "end-to-end"},
    ])
    env = make_env("standard", responders={"factory-plan": [padded, plan_reply()],
                                           "factory-decomposition-review": [decomposition_reply(), decomposition_reply()]})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-plan") == 2
    replan = [e for e in env.events(job, "state.transition") if e["to_state"] == "DECOMPOSE" and "retry from" in e["payload_json"]]
    assert replan and "TASK-02 has an empty file_scope" in replan[0]["payload_json"]
    build = next(c for c in env.runtime.calls if c.skill == "factory-implement")
    assert "are always in scope" in build.prompt and "*.test.ts(x)" in build.prompt


def test_inconclusive_integration_review_is_retried_not_rebuilt(make_env):
    """Eval finding: integration BLOCKED for 'inputs not supplied' sent a correct task back to the builder until attempts ran out."""
    blocked = FakeReply(envelope("integration-review", "BLOCKED", payload={}))
    env = make_env("trivial", responders={"factory-integration-review": [blocked, FakeReply(envelope("integration-review", payload={}))]})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-implement") == 1 and env.calls("factory-integration-review") == 2
    retry = [c for c in env.runtime.calls if c.skill == "factory-integration-review"][1]
    assert "previous integration evaluation was BLOCKED" in retry.prompt
    assert '"candidate_tree_sha"' in retry.prompt and '"required_checks"' in retry.prompt and "local-checkout" in retry.prompt


def test_twice_inconclusive_acceptance_stops_for_a_human_without_rebuilding(make_env):
    blocked = FakeReply(envelope("product-verify", "BLOCKED", payload={}))
    passing = FakeReply(envelope("product-verify", payload={}))

    def product(request):
        return blocked if "feature-acceptance" in request.prompt else passing

    env = make_env("trivial", responders={"factory-product-verify": product})
    job = env.submit()
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert env.calls("factory-implement") == 1
    assert "acceptance evaluation BLOCKED twice" in env.store.job(job)["blocked_reason"]
