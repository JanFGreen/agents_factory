"""Security review: baseline per task with triggers, one full review per feature, triage of findings."""

from __future__ import annotations

import json

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, implement_by_task, review_pass

FETCHER = 'import urllib.request\n\n\ndef fetch(url):\n    return urllib.request.urlopen(url).read()\n'
AWS = "AKIA" + "Q3VZ7RTXK2MNP4LD"


def with_fetcher(request) -> FakeReply:
    """The usual change plus a module that fetches a URL (a security trigger that does not raise the risk)."""
    reply = implement_by_task(request)
    return FakeReply(reply.envelope, edits={**reply.edits, "app/fetcher.py": FETCHER})


def triaged(env, job) -> list[dict]:
    return [json.loads(e["payload_json"]) for e in env.events(job, "security.findings_triaged")]


def security_calls(env, feature: bool) -> list:
    return [c for c in env.runtime.calls if c.skill == "factory-security-review" and ('"mode": "feature"' in c.prompt) == feature]


def inputs(call) -> dict:
    return json.loads(call.prompt.split("<inputs>\n", 1)[1].split("\n</inputs>", 1)[0])


def finding(fid: str, path: str, problem: str, blocking: bool = True) -> dict:
    return {"id": fid, "severity": "high" if blocking else "low", "blocking": blocking, "location": {"path": path, "line": 1},
            "problem": problem, "status": "open"}


def test_task_reviews_are_baseline_and_a_trigger_buys_one_full_review_of_the_feature(make_env):
    env = make_env("trivial", responders={"factory-implement": with_fetcher})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    task = security_calls(env, feature=False)
    assert task and all(c.model == env.cfg.factory.review.security.baseline_model for c in task)
    first = inputs(task[0])
    assert first["security_depth"] == "baseline" and {"trigger": "outbound-request", "path": "app/fetcher.py"} in first["security_triggers"]
    feature = security_calls(env, feature=True)
    assert len(feature) == 1 and feature[0].model == env.cfg.factory.runtime.roles["security_reviewer"].model
    assert inputs(feature[0])["security_depth"] == "full"
    rows = env.store.all("SELECT verdict FROM results WHERE job_id = ? AND kind = 'security-review' AND task_id IS NULL", (job,))
    assert [r["verdict"] for r in rows] == ["PASS"]


def test_a_feature_without_triggers_skips_the_full_review(make_env):
    env = make_env("trivial")
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert not security_calls(env, feature=True)
    assert env.store.job_context(job)["feature_security_required"] is False


def test_a_blocking_finding_outside_the_diff_does_not_block(make_env):
    def review(request):
        return FakeReply(envelope("security-review", "CHANGES", payload={}, findings=[
            finding("SR-1", "app/legacy.py", "This is existing code, not part of this diff: no rate limit on login")]))

    env = make_env("trivial", responders={"factory-security-review": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    row = env.store.one("SELECT blocking, problem FROM findings WHERE job_id = ? AND finding_id = 'SR-1'", (job,))
    assert row["blocking"] == 0 and row["problem"].startswith("[outside this diff]")
    assert triaged(env, job)[0]["outside_diff"] == ["SR-1"]
    verdicts = env.store.all("SELECT verdict FROM results WHERE job_id = ? AND kind = 'security-review'", (job,))
    assert all(v["verdict"] == "PASS" for v in verdicts)


def test_a_finding_another_task_already_reported_is_not_repeated(make_env):
    problem = "Greeting renders the raw name without a length limit"

    def review(request):
        if "task=TASK-02" in request.prompt:
            known = inputs(request)["known_findings"]
            assert [k["id"] for k in known] == ["SR-1"]  # the reviewer is told what is on record
            return FakeReply(envelope("security-review", payload={}, findings=[finding("SR-9", "app/greet.py", problem, blocking=False)]))
        return FakeReply(envelope("security-review", payload={}, findings=[finding("SR-1", "app/greet.py", problem, blocking=False)]))

    env = make_env("standard", responders={"factory-security-review": review}, **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    ids = [r["finding_id"] for r in env.store.all("SELECT finding_id FROM findings WHERE job_id = ? AND kind = 'security-review'", (job,))]
    assert ids == ["SR-1"]
    assert any(e["duplicates"] == ["SR-9"] for e in triaged(env, job))


def test_a_feature_review_finding_reopens_the_task_that_owns_the_file(make_env):
    feature_calls = {"n": 0}

    def review(request):
        if '"mode": "feature"' not in request.prompt:
            return review_pass("security-review")
        feature_calls["n"] += 1
        if feature_calls["n"] == 1:
            return FakeReply(envelope("security-review", "CHANGES", payload={}, findings=[
                finding("SR-F1", "app/farewell.py", "farewell passes the name to a shell command")]))
        return review_pass("security-review")

    def implement(request):
        if "task=TASK-02" in request.prompt:
            edits = {"app/farewell.py": "import subprocess\n\n\n" + FAREWELL, "tests/test_farewell.py": FAREWELL_TEST}
        else:
            edits = {"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": GREET_TEST}
        return FakeReply(envelope("implement", payload={"changed_files": list(edits), "handoff": "done"}), edits=edits)

    env = make_env("standard", responders={"factory-security-review": review, "factory-implement": implement})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    rebuilt = [c for c in env.runtime.calls if c.skill == "factory-implement" and "task=TASK-02" in c.prompt]
    assert len(rebuilt) == 2 and "SR-F1" in rebuilt[1].prompt
    rows = env.store.all("SELECT verdict FROM results WHERE job_id = ? AND kind = 'security-review' AND task_id IS NULL ORDER BY rowid", (job,))
    assert [r["verdict"] for r in rows] == ["CHANGES", "PASS"]


def test_a_secret_in_the_change_fails_the_gate_without_echoing_it(make_env):
    attempts = {"n": 0}

    def implement(request):
        attempts["n"] += 1
        reply = implement_by_task(request)
        extra = {"app/config.py": f'KEY = "{AWS}"\n'} if attempts["n"] == 1 else {"app/config.py": 'import os\n\nKEY = os.environ["KEY"]\n'}
        return FakeReply(reply.envelope, edits={**reply.edits, **extra})

    env = make_env("trivial", responders={"factory-implement": implement})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    gates = env.store.all("SELECT verdict, payload_artifact_id FROM results WHERE job_id = ? AND kind = 'gate' ORDER BY rowid", (job,))
    first = env.factory.evidence.read_json(gates[0]["payload_artifact_id"])
    assert gates[0]["verdict"] == "FAIL_POLICY" and "possible secret (AWS access key) added in app/config.py:1" in first["violations"][0]
    assert AWS not in json.dumps(first) and gates[-1]["verdict"] == "PASS"
