"""Code review must analyse how effectful code fails; probes prove defects; acceptance takes a second look at low findings."""

from __future__ import annotations

import json
from pathlib import Path

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import ALL_SCENARIOS_PASS, GREET_BANG, implement_by_task, request_inputs, review_pass

FETCHER = 'import urllib.request\n\n\ndef fetch(url):\n    return urllib.request.urlopen(url).read()\n'
FAILING_PROBE = "from app.fetcher import fetch\n\n\ndef test_fetch_has_a_deadline():\n    assert False, 'urlopen without timeout hangs forever'\n"
PASSING_PROBE = "def test_nothing_wrong():\n    assert 1 + 1 == 2\n"


def with_fetcher(request) -> FakeReply:
    reply = implement_by_task(request)
    return FakeReply(reply.envelope, edits={**reply.edits, "app/fetcher.py": FETCHER})


def mode(path: str, finding_id: str | None = None, probe: str | None = None, timeout: str = "PASS") -> dict:
    return {"path": path, "operation": "fetch(url)", "scenarios": {**ALL_SCENARIOS_PASS, "timeout": timeout},
            "evidence": "fetcher.py:5", "probe": probe, "finding_id": finding_id}


def finding(fid: str, blocking: bool = False) -> dict:
    return {"id": fid, "severity": "low", "blocking": blocking, "location": {"path": "app/fetcher.py", "line": 5},
            "problem": "urlopen has no timeout; a stalled server hangs the caller", "status": "open"}


def results(env, job, kind="code-review"):
    rows = env.store.all("SELECT verdict, payload_artifact_id FROM results WHERE job_id = ? AND kind = ? ORDER BY rowid", (job, kind))
    return [(r["verdict"], env.factory.evidence.read_json(r["payload_artifact_id"])) for r in rows]


def test_an_effectful_file_without_its_failure_mode_table_is_rerun_with_a_note(make_env):
    seen = []

    def review(request):
        seen.append(request_inputs(request))
        modes = [] if len(seen) == 1 else [mode("app/fetcher.py")]
        return FakeReply(envelope("code-review", payload={"failure_modes": modes}))

    env = make_env("trivial", responders={"factory-implement": with_fetcher, "factory-code-review": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert seen[0]["effect_sites"] == [{"path": "app/fetcher.py", "kinds": ["network-call"]}]
    verdicts = [v for v, _ in results(env, job)]
    assert verdicts[0] == "UNKNOWN" and verdicts[-1] == "PASS"
    assert "app/fetcher.py" in seen[1]["controller_note"]


def test_a_probe_that_fails_in_the_trusted_runner_makes_the_finding_blocking(make_env):
    seen = []

    def review(request):
        inputs = request_inputs(request)
        seen.append({"inputs": inputs, "tools": request.tools, "writable": [str(p) for p in request.sandbox_policy.writable]})
        if len(seen) == 1:  # the reviewer proves the missing timeout with a probe and rates it low
            probe = Path(inputs["probe_workspace"]["dir"])
            (probe / "tests" / "test_probe_fetch.py").write_text(FAILING_PROBE)
            return FakeReply(envelope("code-review", payload={"failure_modes": [
                mode("app/fetcher.py", "F-1", "tests/test_probe_fetch.py", timeout="FAIL")]}, findings=[finding("F-1")]))
        return FakeReply(envelope("code-review", payload={"failure_modes": [mode("app/fetcher.py")]}))

    env = make_env("trivial", responders={"factory-implement": with_fetcher, "factory-code-review": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    first = seen[0]
    probe_dir = first["inputs"]["probe_workspace"]["dir"]
    assert "Bash" in first["tools"] and probe_dir in first["writable"]
    assert not Path(probe_dir).exists()  # the probe copy is removed after the review
    verdict, payload = results(env, job)[0]
    assert verdict == "CHANGES" and payload["probes"][0]["verdict"] == "FAIL_CODE"
    row = env.store.one("SELECT blocking, severity, status FROM findings WHERE job_id = ? AND finding_id = 'F-1'", (job,))
    assert (row["blocking"], row["severity"]) == (1, "medium") and row["status"] == "fixed"  # closed by the next review
    assert env.calls("factory-implement") == 2
    assert env.factory.evidence.read(payload["probes"][0]["artifact_id"]).decode() == FAILING_PROBE


def test_a_probe_that_passes_does_not_block(make_env):
    def review(request):
        probe = Path(request_inputs(request)["probe_workspace"]["dir"])
        (probe / "tests" / "test_probe_ok.py").write_text(PASSING_PROBE)
        return FakeReply(envelope("code-review", payload={"failure_modes": [
            mode("app/fetcher.py", "F-1", "tests/test_probe_ok.py", timeout="FAIL")]}, findings=[finding("F-1")]))

    env = make_env("trivial", responders={"factory-implement": with_fetcher, "factory-code-review": review})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert results(env, job)[0][1]["probes"][0]["verdict"] == "PASS"
    assert env.store.one("SELECT blocking FROM findings WHERE job_id = ? AND finding_id = 'F-1'", (job,))["blocking"] == 0


def test_acceptance_escalates_a_low_finding_that_is_a_real_defect(make_env):
    accept_calls = []

    def code_review(request):
        fixed = "keeps the name" in Path(request.cwd, "app", "greet.py").read_text()
        return FakeReply(envelope("code-review", payload={}, findings=[  # rates it low again until the fix lands
            {"id": "CR-7", "severity": "low", "blocking": False, "location": {"path": "app/greet.py", "line": 2},
             "problem": "the typed name is lost when the screen changes", "status": "fixed" if fixed else "open"}]))

    def implement(request):
        reply = implement_by_task(request)
        if "CR-7" in request.prompt:  # the rework fixes the escalated finding
            return FakeReply(reply.envelope, edits={**reply.edits, "app/greet.py": GREET_BANG + "# keeps the name\n"})
        return reply

    def product_verify(request):
        inputs = request_inputs(request)
        if inputs.get("mode") != "feature-acceptance":
            return review_pass("product-verify")
        accept_calls.append(inputs)
        escalate = [{"finding_id": "CR-7", "task_id": "TASK-01", "reason": "confirmed: name lost after navigation"},
                    {"finding_id": "NOPE-1", "task_id": "TASK-01", "reason": "invented"}] if len(accept_calls) == 1 else []
        return FakeReply(envelope("product-verify", payload={"escalate": escalate}))

    env = make_env("trivial", responders={"factory-code-review": code_review, "factory-product-verify": product_verify,
                                          "factory-implement": implement})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert [f["finding_id"] for f in accept_calls[0]["open_findings"]] == ["CR-7"]
    escalated = json.loads(env.events(job, "findings.escalated")[0]["payload_json"])["findings"]
    assert [f["finding_id"] for f in escalated] == ["CR-7"]  # an unknown id cannot be escalated
    acceptance = [v for v, _ in results(env, job, "feature-acceptance")]
    assert acceptance[0] == "CHANGES"
    rebuild = [c for c in env.runtime.calls if c.skill == "factory-implement"]
    assert len(rebuild) == 2 and "CR-7" in rebuild[1].prompt  # the owner task got the escalated finding
    assert acceptance[-1] == "PASS" and env.store.one(
        "SELECT status FROM findings WHERE job_id = ? AND finding_id = 'CR-7'", (job,))["status"] == "fixed"


def test_a_reviewer_cannot_demote_an_escalated_finding(make_env):
    builds = []

    def code_review(request):
        fixed = "keeps the name" in Path(request.cwd, "app", "greet.py").read_text()
        return FakeReply(envelope("code-review", payload={}, findings=[
            {"id": "CR-7", "severity": "low", "blocking": False, "location": {"path": "app/greet.py", "line": 2},
             "problem": "the typed name is lost when the screen changes", "status": "fixed" if fixed else "open"}]))

    def implement(request):
        builds.append(request)
        reply = implement_by_task(request)
        greet = GREET_BANG + ("# keeps the name\n" if len(builds) >= 3 else f"# attempt {len(builds)}\n")
        return FakeReply(reply.envelope, edits={**reply.edits, "app/greet.py": greet})

    def product_verify(request):
        inputs = request_inputs(request)
        first = inputs.get("mode") == "feature-acceptance" and not env.events(job, "findings.escalated")
        return FakeReply(envelope("product-verify", payload={"escalate": [
            {"finding_id": "CR-7", "task_id": "TASK-01", "reason": "confirmed"}] if first else []}))

    env = make_env("trivial", responders={"factory-code-review": code_review, "factory-product-verify": product_verify,
                                          "factory-implement": implement})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    reviews = [v for v, _ in results(env, job)]
    # build 2 did not fix it: the reviewer said "low" again, the controller kept it blocking; build 3 fixed it
    assert len(builds) == 3 and reviews[-1] == "PASS"
    reworks = [json.loads(e["payload_json"])["reason"] for e in env.events(job, "task.rework")] + \
              [e["payload_json"] for e in env.events(job) if "open blocking findings" in (e["payload_json"] or "")]
    assert any("CR-7" in r for r in reworks)
