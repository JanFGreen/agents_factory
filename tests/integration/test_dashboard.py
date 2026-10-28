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
from agent_factory.dashboard import make_handler
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
