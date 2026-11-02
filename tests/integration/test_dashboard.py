"""Interactive dashboard: live JSON views, screenshots, and operator actions behind token and origin checks."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.dashboard import data, make_handler
from agent_factory.dashboard.actions import ActionError, ActionRunner, build_argv
from agent_factory.evidence import EvidenceStore, Producer

from ..conftest import ROOT

TOKEN = "t" * 43
STATIC = ROOT / "src" / "agent_factory" / "dashboard" / "static"


class Dashboard:
    def __init__(self, env, runner: ActionRunner | None):
        self.env = env
        evidence = EvidenceStore(env.store, env.cfg.data_dir())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(env.store, evidence, runner, TOKEN))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def get(self, path: str, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def post(self, path: str, body: dict, *, token: str = TOKEN, origin: str | None = None):
        headers = {"Content-Type": "application/json", "X-Factory-Token": token, "Origin": origin or self.base}
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def close(self):
        self.server.shutdown()


@pytest.fixture
def dashboard():
    started = []

    def start(env, runner=None):
        started.append(Dashboard(env, runner))
        return started[-1]

    yield start
    for d in started:
        d.close()


def _guided_intake():
    return FakeReply(envelope("intake", payload={"goal": "greeting exclaims", "decisions": [
        {"id": "Q-1", "question": "Punctuation?", "why": "Changes the text", "options": ["!", "!!"], "proposed": "!"}]}))


def test_job_view_has_tasks_costs_reviews_and_events(make_env, dashboard):
    env = make_env("trivial")
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    d = dashboard(env)
    status, _, body = d.get("/api/jobs")
    jobs = json.loads(body)
    assert status == 200 and jobs[0]["job_id"] == job and jobs[0]["tasks"]["accepted"] == jobs[0]["tasks"]["total"] == 1
    status, headers, body = d.get(f"/api/job/{job}")
    view = json.loads(body)
    assert status == 200 and "script-src 'self'" in headers["Content-Security-Policy"]
    assert view["tasks"][0]["state"] == "ACCEPTED" and view["reviews"][view["tasks"][0]["task_id"]]["code-review"] == "PASS"
    assert {r["role"] for r in view["costs"]["by_role"]} >= {"builder", "code_reviewer"}
    assert view["events"] and view["next_step"] and view["run"] is None
    assert d.get("/api/job/job-000000000000")[0] == 404


def test_only_screenshots_are_served_as_artifacts(make_env, dashboard):
    env = make_env("trivial")
    job = env.submit()
    evidence = EvidenceStore(env.store, env.cfg.data_dir())
    png = b"\x89PNG\r\n\x1a\nimage"
    shot = evidence.put(png, "screenshot", Producer("agent-adapter", "ui_reviewer"), job)
    log = evidence.put(b"secret-ish log", "log", Producer("trusted-runner", "gate"), job)
    d = dashboard(env)
    status, headers, body = d.get(f"/api/artifact/{shot}")
    assert status == 200 and body == png and headers["Content-Type"] == "image/png"
    assert d.get(f"/api/artifact/{log}")[0] == 404
    assert json.loads(d.get(f"/api/job/{job}")[2])["screenshots"][0]["artifact_id"] == shot


def test_actions_need_the_token_a_local_origin_and_a_known_action(make_env, dashboard, tmp_path):
    env = make_env("trivial")
    job = env.submit()
    d = dashboard(env, ActionRunner(env.cfg.data_dir(), env.factory_dir, cwd=ROOT))
    assert d.post(f"/api/job/{job}/cancel", {}, token="wrong")[0] == 403
    assert d.post(f"/api/job/{job}/cancel", {}, origin="http://evil.example")[0] == 403
    assert d.get("/api/jobs", headers={"Host": "evil.example:80"})[0] == 403  # DNS rebinding
    status, out = d.post(f"/api/job/{job}/drop-database", {})
    assert status == 400 and "unknown action" in out["error"]
    assert env.store.job(job)["state"] == "FEATURE_RECEIVED"  # nothing above changed the job


def test_page_carries_the_token_and_the_script_never_renders_html(make_env, dashboard):
    env = make_env("trivial")
    d = dashboard(env)
    status, headers, body = d.get("/")
    assert status == 200 and f'content="{TOKEN}"' in body.decode() and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    script = (STATIC / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in script, f"app.js must insert data as text, found {sink}"
    assert d.get("/static/../__init__.py")[0] == 404


def test_answering_decisions_from_the_dashboard_runs_the_cli(make_env, dashboard):
    env = make_env("trivial", responders={"factory-intake": _guided_intake()})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    d = dashboard(env, ActionRunner(env.cfg.data_dir(), env.factory_dir, cwd=ROOT))
    view = json.loads(d.get(f"/api/job/{job}")[2])
    assert view["waiting"] and view["decisions"][0]["options"] == ["!", "!!"]
    status, out = d.post(f"/api/job/{job}/decide", {"answers": {"Q-1": "!!"}})
    assert status == 200 and out["ok"], out
    decided = env.store.job_context(job)["decisions"][0]
    assert (decided["answer"], decided["source"]) == ("!!", "human")
    assert env.store.job(job)["state"] == "INTAKE"  # resumed; the operator starts the run next


def test_a_refused_cli_action_is_reported_not_hidden(make_env, dashboard):
    env = make_env("trivial")
    job = env.submit()
    d = dashboard(env, ActionRunner(env.cfg.data_dir(), env.factory_dir, cwd=ROOT))
    status, out = d.post(f"/api/job/{job}/approve-plan", {"budget": 10})
    assert status == 409 and out["ok"] is False and out["exit_code"] == 3  # no plan yet: the controller refuses


def test_one_background_run_per_job(tmp_path):
    fake = tmp_path / "fake-python"
    fake.write_text(f"#!{sys.executable}\nimport sys, time\nprint('args', sys.argv[1:])\ntime.sleep(1.5)\n")
    fake.chmod(0o755)
    runner = ActionRunner(tmp_path / "data", tmp_path / "factory", cwd=tmp_path, python=str(fake))
    job = "job-0123456789ab"
    first = runner.execute("resume", job, {})
    assert first.background and first.ok
    with pytest.raises(ActionError, match="in progress"):
        runner.execute("run", job, {})
    run = runner.background(job)
    assert run["alive"] and run["argv"] == ["factory", "resume", job]
    deadline = time.time() + 10
    while runner.background(job)["alive"] and time.time() < deadline:
        time.sleep(0.1)
    assert "args" in runner.background(job)["log_tail"]


@pytest.mark.parametrize("action, body, problem", [
    ("decide", {"answers": {"Q-1; rm -rf /": "x"}}, "unknown decision id"),
    ("decide", {}, "answer at least one"),
    ("decide", {"answers": {"Q-1": "x" * 3000}}, "text of 1..2000"),
    ("approve-plan", {"budget": -5}, "amount in USD"),
    ("approve-plan", {"budget": True}, "amount in USD"),
    ("raise-budget", {"to": 30}, "reason"),
    ("merge-approve", {}, "revision"),
])
def test_action_arguments_are_validated(action, body, problem):
    with pytest.raises(ActionError, match=problem):
        build_argv(action, "job-0123456789ab", body)


def test_answers_become_single_argv_entries():
    argv, background = build_argv("decide", "job-0123456789ab", {"answers": {"Q-1": "--budget 999 && echo"}, "accept_proposed": True})
    assert argv == ["decide", "job-0123456789ab", "--answer", "Q-1=--budget 999 && echo", "--accept-proposed"] and not background
    assert build_argv("resume", "job-0123456789ab", {}) == (["resume", "job-0123456789ab"], True)
    with pytest.raises(ActionError):
        build_argv("resume", "job-../../etc", {})


@pytest.mark.parametrize("action, body, problem", [
    ("task-retry", {"task_id": "TASK-14; rm", "attempts": 2, "reason": "x"}, "TASK-NN"),
    ("task-retry", {"task_id": "TASK-14", "attempts": 4, "reason": "x"}, "between 1 and 3"),
    ("task-retry", {"task_id": "TASK-14", "attempts": True, "reason": "x"}, "between 1 and 3"),
    ("task-retry", {"task_id": "TASK-14", "attempts": 2}, "reason"),
    ("attempts-raise", {"to": 501, "reason": "x"}, "between 1 and 500"),
    ("attempts-raise", {"to": 60, "reason": "  "}, "reason"),
    ("reopen", {}, "reason"),
])
def test_decision_action_arguments_are_validated(action, body, problem):
    with pytest.raises(ActionError, match=problem):
        build_argv(action, "job-0123456789ab", body)


def test_decision_actions_map_to_the_cli_commands():
    job = "job-0123456789ab"
    note = "Fix F-1 --attempts 3 && echo"
    assert build_argv("task-retry", job, {"task_id": "TASK-14", "attempts": 2, "reason": note}) == (
        ["task", "retry", job, "TASK-14", "--attempts", "2", "--reason", note], False)
    assert build_argv("attempts-raise", job, {"to": 70, "reason": "more"}) == (["attempts", "raise", job, "--to", "70", "--reason", "more"], False)
    assert build_argv("reopen", job, {"reason": "old cap"}) == (["reopen", job, "--reason", "old cap"], False)


def test_decision_actions_reach_the_controller(make_env, dashboard):
    """The argv is what the CLI parses: the controller refuses (exit 3), not argparse (exit 2)."""
    env = make_env("trivial")
    job = env.submit()
    d = dashboard(env, ActionRunner(env.cfg.data_dir(), env.factory_dir, cwd=ROOT))
    for action, body in (("task-retry", {"task_id": "TASK-01", "attempts": 2, "reason": "fix it"}),
                         ("attempts-raise", {"to": 80, "reason": "more"}), ("reopen", {"reason": "old cap"})):
        status, out = d.post(f"/api/job/{job}/{action}", body)
        assert status == 409 and out["exit_code"] == 3, (action, out)
    assert env.store.job(job)["state"] == "FEATURE_RECEIVED"


def _task(task_id, state, depends_on=()):
    return {"task_id": task_id, "state": state, "attempts": 3, "depends_on": list(depends_on), "file_scope": [], "acceptance": "",
            "requirement_ids": [], "difficulty": "hard"}


def _finding(fid, task_id, blocking, fix, status="open"):
    return {"finding_id": fid, "task_id": task_id, "kind": "code-review", "severity": "medium", "blocking": int(blocking),
            "status": status, "problem": f"problem {fid}", "expected_fix": fix}


def test_a_failed_task_asks_for_a_retry_with_the_blocking_fixes_as_the_note():
    tasks = [_task("TASK-01", "ACCEPTED"), _task("TASK-02", "FAILED", ["TASK-01"]), _task("TASK-03", "PENDING", ["TASK-02"])]
    findings = [_finding("F-1", "TASK-02", True, "guard the double sign-in"), _finding("F-2", "TASK-02", False, "nice to have"),
                _finding("F-3", "TASK-02", True, "stale fix", status="fixed")]
    ask = data.human("NEEDS_HUMAN", "tasks ['TASK-02'] used all their attempts", {}, tasks, findings,
                     {"limit": 100.0, "committed": 10.0, "spent": 10.0}, {"TASK-02": 6.0}, waiting=True)
    assert ask["kind"] == "task_failed" and ask["failed_tasks"] == ["TASK-02"] and ask["blocked_by"] == {"TASK-02": ["TASK-03"]}
    assert "TASK-02 used all its attempts ($6.00 so far) with 1 blocking finding(s) open; it holds up TASK-03." in ask["asks"]
    retry = ask["proposals"][0]
    assert (retry["action"], retry["body"], retry["then_resume"]) == ("task-retry", {"task_id": "TASK-02", "attempts": 2}, True)
    assert "F-1 (code-review): guard the double sign-in" in retry["reason"]
    assert "nice to have" not in retry["reason"] and "stale fix" not in retry["reason"]
    assert [p["action"] for p in ask["proposals"]] == ["task-retry", "resume", "cancel"]  # the budget still fits


def test_a_budget_that_will_not_last_is_proposed_with_the_retry():
    tasks = ([_task(f"TASK-{i:02d}", "ACCEPTED") for i in range(1, 15)] + [_task("TASK-15", "FAILED")]
             + [_task(f"TASK-{i:02d}", "PENDING") for i in range(16, 21)])
    ask = data.human("NEEDS_HUMAN", "", {}, tasks, [], {"limit": 150.0, "committed": 104.48, "spent": 104.48}, {}, waiting=True)
    budget = next(p for p in ask["proposals"] if p["action"] == "raise-budget")
    assert budget["body"] == {"to": 200} and budget["then_resume"] is False  # $7.46 per task, 6 left: (104.48 + 44.78) / 0.75
    assert any("do not fit in the budget" in a for a in ask["asks"])


@pytest.mark.parametrize("state, reason, ctx, kind, first", [
    ("NEEDS_HUMAN", "feature used all 40 build attempts; to go on: ...", {}, "attempt_cap", "attempts-raise"),
    ("FAILED", "feature used all 30 build attempts", {}, "reopen", "reopen"),
    ("NEEDS_INFO", "", {"decisions": [{"id": "Q-1", "answer": None}]}, "decisions", "cancel"),
    ("BUDGET_EXHAUSTED", "reserving $3.00 would exceed", {}, "budget", "raise-budget"),
    ("BLOCKED_ENV", "node missing", {}, "environment", "resume"),
])
def test_each_halt_says_what_is_asked(state, reason, ctx, kind, first):
    tasks = [_task("TASK-01", "ACCEPTED"), _task("TASK-02", "PENDING")]
    ask = data.human(state, reason, ctx, tasks, [], {"limit": 20.0, "committed": 10.0, "spent": 10.0}, {},
                     waiting=state in data.WAITING)
    assert ask["kind"] == kind and ask["asks"] and ask["proposals"][0]["action"] == first
    assert ask["proposals"][-1]["action"] == "cancel"
    if kind == "attempt_cap":
        assert ask["proposals"][0]["body"] == {"to": 50}


def test_a_running_or_finished_job_asks_nothing_and_a_halted_one_is_flagged(make_env, dashboard):
    nothing = {"limit": 1.0, "committed": 0.0, "spent": 0.0}
    assert data.human("BUILD_TASK", None, {}, [], [], nothing, {}, waiting=False) is None
    assert data.human("FAILED", "checks failed", {}, [], [], nothing, {}, waiting=False) is None
    env = make_env("trivial", responders={"factory-intake": _guided_intake()})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    d = dashboard(env)
    view = json.loads(d.get(f"/api/job/{job}")[2])
    assert view["human"]["kind"] == "decisions" and view["human"]["history"] == []
    listed = next(j for j in json.loads(d.get("/api/jobs")[2]) if j["job_id"] == job)
    assert listed["needs_human"] and listed["failed_tasks"] == 0
