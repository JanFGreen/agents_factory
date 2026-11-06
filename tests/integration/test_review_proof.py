"""A low or medium review finding blocks only when a run proves it; recurring defect classes reach the builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory import lessons
from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.cli import main
from agent_factory.runner import LessonRule, load_checks

from ..conftest import request_inputs

FAILING_PROBE = "from app.greet import greet\n\n\ndef test_greet_keeps_the_name():\n    assert greet('Ada') == 'Hi Ada'\n"


def finding(fid: str, severity: str = "medium", path: str = "app/greet.py", status: str = "open") -> dict:
    return {"id": fid, "severity": severity, "blocking": True, "location": {"path": path, "line": 2},
            "problem": "the greeting drops the name for a long input", "status": status}


def row(env, job: str, fid: str):
    return env.store.one("SELECT blocking, status, problem FROM findings WHERE job_id = ? AND finding_id = ?", (job, fid))


def test_an_unproven_medium_finding_is_an_open_follow_up(make_env):
    def review(request):
        return FakeReply(envelope("code-review", "CHANGES", payload={}, findings=[finding("CR-1")]))

    env = make_env("trivial", responders={"factory-code-review": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-implement") == 1  # no rework for a claim no run showed
    r = row(env, job, "CR-1")
    assert r["blocking"] == 0 and r["status"] == "open" and r["problem"].startswith("[unproven: no run showed it]")
    assert '"CR-1"' in env.events(job, "findings.unproven")[0]["payload_json"]


def test_a_failing_probe_proves_a_finding_of_a_file_without_effects(make_env):
    seen = []

    def review(request):
        inputs = request_inputs(request)
        seen.append(inputs)
        if len(seen) == 1:
            probe = Path(inputs["probe_workspace"]["dir"])
            (probe / "tests" / "test_probe_greet.py").write_text(FAILING_PROBE)
            return FakeReply(envelope("code-review", "CHANGES", payload={"probes": [
                {"finding_id": "CR-1", "path": "tests/test_probe_greet.py"}]}, findings=[finding("CR-1")]))
        # the next round reports it open again without a new probe: once proven, it stays blocking
        if len(seen) == 2:
            return FakeReply(envelope("code-review", "CHANGES", payload={}, findings=[finding("CR-1")]))
        return FakeReply(envelope("code-review", payload={}, findings=[finding("CR-1", status="fixed")]))

    env = make_env("trivial", responders={"factory-code-review": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert "effect_sites" not in seen[0] and "proves its finding" in seen[0]["probe_workspace"]["how"]
    assert env.calls("factory-implement") == 3 and not env.events(job, "findings.unproven")


@pytest.mark.parametrize("severity,path,blocks", [("high", "app/greet.py", True), ("medium", "tests/test_greet_bang.py", True)])
def test_high_findings_and_findings_on_a_changed_test_block_without_a_run(make_env, severity, path, blocks):
    calls = []

    def review(request):
        calls.append(1)
        status = "open" if len(calls) == 1 else "fixed"
        return FakeReply(envelope("test-adequacy", "CHANGES" if status == "open" else "PASS", payload={},
                                  findings=[finding("TA-1", severity, path, status)]))

    env = make_env("trivial", responders={"factory-test-adequacy": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert (env.calls("factory-implement") == 2) is blocks and not env.events(job, "findings.unproven")


def test_a_mutation_the_tests_let_through_proves_a_missed_fault(make_env):
    seen = []
    survives = {"finding_id": "TA-1", "path": "app/greet.py", "find": "def greet(name: str) -> str:", "replace": "def greet(name):"}
    killed = {"finding_id": "TA-2", "path": "app/greet.py", "find": "!", "replace": "?"}

    def review(request):
        inputs = request_inputs(request)
        seen.append(inputs)
        if len(seen) == 1:
            return FakeReply(envelope("test-adequacy", "CHANGES", payload={"mutations": [survives, killed, {"finding_id": "TA-3"}]},
                                      findings=[finding("TA-1"), finding("TA-2")]))
        return FakeReply(envelope("test-adequacy", payload={}, findings=[finding("TA-1", status="fixed")]))

    env = make_env("trivial", responders={"factory-test-adequacy": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert "payload.mutations" in seen[0]["probe_workspace"]["how"]
    first = env.store.one("SELECT payload_artifact_id FROM results WHERE job_id = ? AND kind = 'test-adequacy' ORDER BY rowid", (job,))
    verdicts = {m["finding_id"]: m["verdict"] for m in env.factory.evidence.read_json(first["payload_artifact_id"])["mutations"]}
    assert verdicts == {"TA-1": "SURVIVED", "TA-2": "KILLED", "TA-3": "INVALID"}
    assert env.calls("factory-implement") == 2  # TA-1 proven: rework
    assert row(env, job, "TA-2")["blocking"] == 0  # the tests caught TA-2's mutation: unproven
    assert not list((env.factory.data_dir / "probes" / job).iterdir())  # every mutation copy is removed


def test_recurring_defect_classes_reach_the_builder(make_env):
    env = make_env("trivial")
    jobs = []
    for i in range(3):
        job = env.submit(f"Make the greeting end with an exclamation mark, variant {i}.")
        jobs.append(job)
        with env.store.tx() as conn:
            conn.execute("INSERT INTO findings (finding_key, finding_id, job_id, task_id, result_id, kind, severity, blocking, status,"
                         " problem, expected_fix, location_json, requirement_ids_json, author, created_at, updated_at)"
                         " VALUES (?, ?, ?, NULL, 'res-x', 'ui-review', 'medium', 1, 'fixed', ?, NULL, NULL, '[]', 'controller', ?, ?)",
                         (f"k{i}", f"CTRL-contrast-{i}", job, f"Option {i} in state hover: indicator contrast 1.2{i}:1 (minimum 3.0:1)",
                          "2026-01-0%d" % (i + 1), "2026-01-0%d" % (i + 1)))
    found = lessons.recurring(env.store, env.store.job(jobs[0])["repo_path"], classes=lessons.CLASSES, enforced={}, min_jobs=3)
    assert [(c["class"], c["jobs"], c["enforced_by"]) for c in found] == [("contrast", 3, None)]
    repeats = lessons.recurring(env.store, env.store.job(jobs[0])["repo_path"], classes=lessons.CLASSES, min_jobs=3,
                                enforced={"contrast": LessonRule(check="unit", since="2026-01-02")})
    assert [(c["enforced_by"], c["found_after_enforcement"]) for c in repeats] == [("unit", 1)]  # 01-03 is a repeat
    request = lessons.proposal("greeter", found[0])
    assert request["feature_id"].startswith("LESSON-contrast-") and "indicator contrast" in request["original_request"]

    job = env.submit("Make the greeting end with an exclamation mark, at last.")
    assert env.factory.run(job) == "READY_TO_MERGE"
    build = next(c for c in env.runtime.calls if c.skill == "factory-implement")
    assert request_inputs(build)["recurring_defects"][0]["class"] == "contrast"


def test_an_enforced_class_is_reported_only_when_it_turns_up_again(tmp_path):
    manifest = tmp_path / "m.yaml"
    manifest.write_text("schema_version: 1\nchecks:\n- {name: unit, argv: [x], timeout_seconds: 5, stage: task}\n"
                        "lessons:\n  contrast: {check: unit, since: 2026-01-02}\n")
    loaded = load_checks(manifest)
    assert loaded.lessons["contrast"].since == "2026-01-02"
    without = manifest.read_text().split("lessons:")[0]
    plain = tmp_path / "p.yaml"
    plain.write_text(without)
    assert load_checks(plain).digest() == loaded.digest()  # the table changes no check: results stay current
    manifest.write_text(manifest.read_text().replace("check: unit", "check: nope"))
    with pytest.raises(ValueError, match="unknown check 'nope'"):
        load_checks(manifest)


def test_lessons_refuses_an_unknown_repo(tmp_path, capsys):
    factory_dir = Path(__file__).resolve().parents[2] / "factory"
    assert main(["--factory-dir", str(factory_dir), "--data-dir", str(tmp_path), "lessons", "--repo-id", "nope"]) != 0
    assert "unknown repo id" in capsys.readouterr().err
