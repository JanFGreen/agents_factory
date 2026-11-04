"""B2: real isolation attempts. Nothing here is proven by a flag."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_factory.evidence import EvidenceStore
from agent_factory.runner import CheckSpec, GateVerdict, TrustedRunner
from agent_factory.sandbox import MacSandbox, SandboxPolicy, SandboxUnavailable, get_sandbox
from agent_factory.state import Store


@pytest.fixture
def setup(tmp_path):
    data = tmp_path / "data"
    store = Store(data / "state.db")
    ev = EvidenceStore(store, data)
    wt = data / "worktrees" / "job-000000000001"
    other = data / "worktrees" / "job-000000000002"
    for d in (wt, other, data / "holdout"):
        d.mkdir(parents=True, exist_ok=True)
    (other / "private.txt").write_text("other job data")
    (data / "holdout" / "case.txt").write_text("hidden case")
    runner = TrustedRunner(get_sandbox(), ev, data / "runs")
    return runner, wt, data, other


def check(name, code, **kw):
    return CheckSpec(name=name, argv=[sys.executable, "-c", code], timeout_seconds=kw.pop("timeout", 30), **kw)


def run(runner, wt, spec):
    return runner.run_check(spec, wt, job_id="job-000000000001", head_sha=None, tree_sha=None)


def test_af18_builder_cannot_read_keys_holdout_or_other_job(setup):
    runner, wt, data, other = setup
    for target in (data / "keys" / "evidence.key", data / "holdout" / "case.txt", other / "private.txt", data / "state.db"):
        ev = run(runner, wt, check("read", f"open({str(target)!r}).read()"))
        assert ev.exit_code != 0, target
        assert "Operation not permitted" in ev.excerpt or "PermissionError" in ev.excerpt


def test_af18_cannot_write_outside_worktree_or_into_controller(setup, tmp_path):
    runner, wt, data, _ = setup
    outside = tmp_path / "outside.txt"
    ev = run(runner, wt, check("write", f"open({str(outside)!r}, 'w').write('x')"))
    assert ev.exit_code != 0 and not outside.exists()
    factory_dir = Path(__file__).resolve().parents[2] / "factory" / "checks"
    ev = run(runner, wt, check("ctl", f"open({str(factory_dir / 'pwn.yaml')!r}, 'w').write('x')"))
    assert ev.exit_code != 0 and not (factory_dir / "pwn.yaml").exists()
    ev = run(runner, wt, check("inside", "open('ok.txt', 'w').write('x')"))
    assert ev.exit_code == 0 and (wt / "ok.txt").exists()


def test_af18_no_outbound_network_and_no_secrets_in_env(setup, monkeypatch):
    runner, wt, _, _ = setup
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GH_TOKEN", "ghp-should-not-leak")
    code = "import socket; s=socket.create_connection(('1.1.1.1', 443), timeout=5)"
    ev = run(runner, wt, check("net", code))
    assert ev.exit_code != 0
    ev = run(runner, wt, check("env", "import os; print(sorted(os.environ))"))
    assert "ANTHROPIC_API_KEY" not in ev.excerpt and "GH_TOKEN" not in ev.excerpt


def test_localhost_bind_allowed_for_dev_servers(setup):
    runner, wt, _, _ = setup
    code = "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); c=socket.create_connection(s.getsockname()); print('ok')"
    ev = run(runner, wt, check("bind", code, network="localhost"))
    assert ev.exit_code == 0


def test_af25_hanging_process_group_is_killed_at_deadline(setup):
    runner, wt, _, _ = setup
    code = "import subprocess, time, sys; subprocess.Popen([sys.executable, '-c', 'import time; open(\"child.pid\",\"w\").write(str(__import__(\"os\").getpid())); time.sleep(600)']); time.sleep(600)"
    start = time.time()
    ev = run(runner, wt, check("hang", code, timeout=3))
    assert ev.timed_out and ev.verdict == "TIMEOUT" and time.time() - start < 20
    time.sleep(0.5)
    child = int((wt / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_orphaned_children_die_when_check_exits(setup):
    runner, wt, _, _ = setup
    code = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time,os; open(\"orphan.pid\",\"w\").write(str(os.getpid())); time.sleep(600)']); import time; time.sleep(1)"
    run(runner, wt, check("orphan", code))
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(int((wt / "orphan.pid").read_text()), 0)


def test_af12_zero_or_skipped_tests_never_pass(setup):
    runner, wt, _, _ = setup
    (wt / "test_skip.py").write_text("import pytest\n\n@pytest.mark.skip\ndef test_a():\n    assert False\n")
    spec = CheckSpec(name="unit", argv=["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml={report}"],
                     timeout_seconds=60, report="junit", min_tests=1)
    assert run(runner, wt, spec).verdict == "FAIL_POLICY"
    (wt / "test_skip.py").unlink()
    ev = run(runner, wt, spec)
    assert ev.verdict == "FAIL_POLICY"  # no tests collected at all
    gate = runner.run_gate([spec], wt, job_id="job-000000000001", head_sha=None, tree_sha=None)
    assert gate.verdict is GateVerdict.FAIL_POLICY


def test_missing_tool_is_env_error_not_code_failure(setup):
    runner, wt, _, _ = setup
    ev = run(runner, wt, CheckSpec(name="lint", argv=["/nonexistent/tool"], timeout_seconds=5))
    assert ev.verdict == "ERROR_ENV"


def test_runner_evidence_is_complete(setup):
    runner, wt, _, _ = setup
    ev = run(runner, wt, check("ok", "print('hi')"))
    assert ev.verdict == "PASS" and ev.stdout_artifact and len(ev.stdout_sha256) == 64
    assert ev.sandbox == "macos-sandbox-exec" and "HOME" in ev.env_keys and ev.started_at <= ev.ended_at


def test_af33_no_sandbox_fails_closed(monkeypatch):
    monkeypatch.setenv("AGENT_FACTORY_DISABLE_SANDBOX", "1")
    with pytest.raises(SandboxUnavailable):
        get_sandbox()


def test_af33_non_enforcing_sandbox_fails_probe():
    from agent_factory.sandbox import Sandbox, probe

    class Pretend(Sandbox):
        name = "pretend"

        def wrap(self, argv, policy):
            return argv  # "sandbox=true" without enforcement

    with pytest.raises(SandboxUnavailable, match="did not enforce"):
        probe(Pretend())


def test_factory_and_base_placeholders_are_filled_and_a_missing_value_is_an_env_error(setup, tmp_path):
    runner, wt, data, _ = setup
    runner.factory_root = tmp_path / "factory-root"
    spec = CheckSpec(name="args", argv=[sys.executable, "-c", "import sys; print(sys.argv[1:])", "{factory}/x", "{base_sha}"], timeout_seconds=30)
    ok = runner.run_check(spec, wt, job_id="job-000000000001", head_sha=None, tree_sha=None, base_sha="abc123")
    assert ok.verdict == "PASS" and f"'{tmp_path / 'factory-root'}/x', 'abc123'" in ok.excerpt
    missing = run(runner, wt, spec)
    assert missing.verdict == "ERROR_ENV" and "no value for {base_sha}" in missing.reason


SEMGREP = Path(__file__).resolve().parents[2] / "tools" / "semgrep" / ".venv" / "bin" / "semgrep"


@pytest.mark.skipif(not SEMGREP.exists(), reason="NOT_RUN: pinned semgrep not installed (uv sync --project tools/semgrep)")
def test_sast_check_reports_only_new_code_offline_in_the_sandbox(setup):
    from agent_factory.runner import load_checks

    runner, wt, data, _ = setup
    root = Path(__file__).resolve().parents[2]
    runner.factory_root = root
    sast = next(c for c in load_checks(root / "factory" / "checks" / "node.yaml").checks if c.name == "sast")
    assert sast.network == "none"

    def commit(msg):
        subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], cwd=wt, check=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True, check=True).stdout.strip()

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=wt, check=True)
    (wt / "old.py").write_text("import os\nos.system(cmd)\n")  # pre-existing: not this change's fault
    base = commit("base")
    (wt / "ok.ts").write_text("export const add = (a: number, b: number) => a + b;\n")
    commit("clean change")
    clean = runner.run_check(sast, wt, job_id="job-000000000001", head_sha=None, tree_sha=None, base_sha=base)
    assert clean.verdict == "PASS", clean.excerpt
    (wt / "view.ts").write_text("el.innerHTML = data;\n")
    commit("xss")
    bad = runner.run_check(sast, wt, job_id="job-000000000001", head_sha=None, tree_sha=None, base_sha=base)
    assert bad.verdict == "FAIL_CODE" and "js-html-sink" in bad.excerpt and "old.py" not in bad.excerpt


@pytest.mark.skipif(not SEMGREP.exists(), reason="NOT_RUN: pinned semgrep not installed (uv sync --project tools/semgrep)")
def test_sast_tells_a_database_exec_from_a_shell_and_allows_a_pragma_from_a_constant(setup):
    from agent_factory.runner import load_checks

    runner, wt, _, _ = setup
    root = Path(__file__).resolve().parents[2]
    runner.factory_root = root
    sast = next(c for c in load_checks(root / "factory" / "checks" / "node.yaml").checks if c.name == "sast")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=wt, check=True)
    (wt / "README.md").write_text("app\n")
    base = _commit(wt, "base")
    (wt / "migrate.ts").write_text("db.exec(`PRAGMA user_version = ${String(version)}`);\ndb.exec(`PRAGMA busy_timeout = ${BUSY_MS}`);\n")
    _commit(wt, "pragmas")
    ok = runner.run_check(sast, wt, job_id="job-000000000001", head_sha=None, tree_sha=None, base_sha=base)
    assert ok.verdict == "PASS", ok.excerpt
    (wt / "query.ts").write_text("db.exec(`PRAGMA user_version = ${String(req.body.v + 1)}`);\n"
                                 "db.exec(`SELECT * FROM t WHERE id = ${id}`);\nexecSync(`rm ${path}`);\n")
    _commit(wt, "injections")
    bad = runner.run_check(sast, wt, job_id="job-000000000001", head_sha=None, tree_sha=None, base_sha=base)
    assert bad.verdict == "FAIL_CODE" and "js-sql-concat" in bad.excerpt and "js-child-process-shell" in bad.excerpt
    assert "migrate.ts" not in bad.excerpt


def _commit(wt, msg):
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], cwd=wt, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True, check=True).stdout.strip()


def _node_check(name):
    from agent_factory.runner import load_checks

    root = Path(__file__).resolve().parents[2]
    return root, next(c for c in load_checks(root / "factory" / "checks" / "vite.yaml").checks if c.name == name)


@pytest.mark.skipif(not SEMGREP.exists(), reason="NOT_RUN: pinned semgrep not installed (uv sync --project tools/semgrep)")
def test_hygiene_check_refuses_debug_leftovers_and_third_party_loads_in_new_code(setup):
    runner, wt, _, _ = setup
    runner.factory_root, hygiene = _node_check("hygiene")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=wt, check=True)
    (wt / "src").mkdir()
    (wt / "src" / "old.ts").write_text("console.log('legacy');\n")  # pre-existing: not this change's fault
    base = _commit(wt, "base")
    (wt / "src" / "api.ts").write_text("export const base = import.meta.env.VITE_API ?? '/api';\nconsole.info('ready');\n")
    (wt / "src" / "api.test.ts").write_text("console.log('fine in a test');\n")
    _commit(wt, "clean change")
    clean = runner.run_check(hygiene, wt, job_id="job-000000000001", head_sha=None, tree_sha=None, base_sha=base)
    assert clean.verdict == "PASS", clean.excerpt
    (wt / "src" / "save.ts").write_text("// TODO: retry\nconsole.log(fetch('http://localhost:3001/api'));\n")
    (wt / "src" / "fonts.css").write_text("@import url('https://fonts.googleapis.com/css2?family=Inter');\n")
    _commit(wt, "leftovers")
    bad = runner.run_check(hygiene, wt, job_id="job-000000000001", head_sha=None, tree_sha=None, base_sha=base)
    assert bad.verdict == "FAIL_CODE"
    for rule in ("debug-console", "unfinished-work", "hard-coded-local-address", "third-party-resource"):
        assert rule in bad.excerpt, rule
    assert "old.ts" not in bad.excerpt and "api.test.ts" not in bad.excerpt


def _lockfile(wt, packages):
    import json

    (wt / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "app"}, **packages}}))


def test_licence_check_refuses_shipped_copyleft_and_unlicensed_packages(setup):
    runner, wt, _, _ = setup
    runner.factory_root, licenses = _node_check("licenses")
    _lockfile(wt, {
        "node_modules/react": {"version": "19.3.0", "license": "MIT"},
        "node_modules/dual": {"version": "1.0.0", "license": "(MIT OR GPL-3.0-only)"},
        "node_modules/lightningcss": {"version": "1.0.0", "license": "MPL-2.0", "dev": True},  # a build tool, not shipped
    })
    ok = runner.run_check(licenses, wt, job_id="job-000000000001", head_sha=None, tree_sha=None)
    assert ok.verdict == "PASS", ok.excerpt
    _lockfile(wt, {
        "node_modules/chart-gpl": {"version": "2.0.0", "license": "GPL-3.0-only"},
        "node_modules/mystery": {"version": "0.1.0"},
        "node_modules/both": {"version": "1.0.0", "license": "MIT AND AGPL-3.0-only"},
        "node_modules/devtool": {"version": "1.0.0", "license": "AGPL-3.0-only", "dev": True},
    })
    bad = runner.run_check(licenses, wt, job_id="job-000000000001", head_sha=None, tree_sha=None)
    assert bad.verdict == "FAIL_CODE"
    for package in ("chart-gpl@2.0.0", "mystery@0.1.0: no licence", "both@1.0.0", "devtool@1.0.0 (build tool)"):
        assert package in bad.excerpt, package
    (wt / ".license-exceptions.json").write_text('{"chart-gpl": "x", "mystery": "x", "both": "x", "devtool": "operator: internal tool only"}')
    assert runner.run_check(licenses, wt, job_id="job-000000000001", head_sha=None, tree_sha=None).verdict == "PASS"


SERVICE = (
    "import http.server, os, pathlib\n"
    "class H(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        if self.path == '/write':\n"
    "            try:\n"
    "                pathlib.Path('escaped.txt').write_text('x'); body = b'wrote'\n"
    "            except OSError:\n"
    "                body = b'denied'\n"
    "        else:\n"
    "            body = (os.environ['ORIGIN'] + ' ' + os.environ['SECRET']).encode()\n"
    "        self.send_response(200); self.end_headers(); self.wfile.write(body)\n"
    "    def log_message(self, *a): pass\n"
    "http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n"
)


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_preview_backend_runs_in_the_sandbox_until_stopped(setup):
    import urllib.request

    from agent_factory.runner import PreviewBackend
    runner, wt, _, _ = setup
    spec = PreviewBackend(argv=["{python}", "-c", SERVICE], env={"PORT": "{port}", "ORIGIN": "{origin}", "SECRET": "{secret}"},
                          build_url="http://127.0.0.1:3000", ready_path="/health", ready_timeout_seconds=20)
    port = _free_port()
    service = runner.start_service(spec, wt, job_id="job-000000000001", port=port, origin="http://127.0.0.1:9")
    try:
        origin, secret = urllib.request.urlopen(service.url + "/env", timeout=5).read().decode().split()
        assert origin == "http://127.0.0.1:9" and len(secret) == 64
        assert urllib.request.urlopen(service.url + "/write", timeout=5).read() == b"denied"  # the worktree is read-only
        assert not (wt / "escaped.txt").exists()
    finally:
        service.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(service.url + "/env", timeout=2)


def test_a_backend_that_exits_or_a_sandbox_without_localhost_is_an_error(setup):
    from agent_factory.runner import PreviewBackend, ServiceError
    runner, wt, _, _ = setup
    crash = PreviewBackend(argv=["{python}", "-c", "import sys; print('no config'); sys.exit(3)"], build_url="http://127.0.0.1:3000")
    with pytest.raises(ServiceError, match="exited with 3: no config"):
        runner.start_service(crash, wt, job_id="job-000000000001", port=_free_port(), origin="http://127.0.0.1:9")
    runner.sandbox = type("NoLocal", (type(runner.sandbox),), {"serves_localhost": False})()
    with pytest.raises(ServiceError, match="localhost-only network"):
        runner.start_service(crash, wt, job_id="job-000000000001", port=_free_port(), origin="http://127.0.0.1:9")


FLAKY = (
    "import pathlib, sys\n"
    "count = pathlib.Path('runs.txt'); n = int(count.read_text()) if count.exists() else 0; count.write_text(str(n + 1))\n"
    "fail = n < int(sys.argv[2])\n"
    "case = '<testcase classname=\"tests/test_onboarding.py\" name=\"t\">' + ('<failure message=\"x\"/>' if fail else '') + '</testcase>'\n"
    "pathlib.Path(sys.argv[1]).write_text(f'<testsuite tests=\"1\" failures=\"{int(fail)}\" errors=\"0\" skipped=\"0\">{case}</testsuite>')\n"
    "sys.exit(1 if fail else 0)\n"
)


@pytest.mark.parametrize("fails, changed, verdict, flaky", [
    (1, set(), "PASS", ["tests/test_onboarding.py"]),               # failed once in a file the job did not touch: a flake
    (1, {"tests/test_onboarding.py"}, "FAIL_CODE", None),           # the job changed that test: its failure is the build's
    (2, set(), "FAIL_CODE", None),                                  # fails again: a real failure
])
def test_a_test_check_that_failed_only_outside_the_change_runs_once_more(setup, fails, changed, verdict, flaky):
    runner, wt, _, _ = setup
    (wt / "runs.txt").unlink(missing_ok=True)
    spec = CheckSpec(name="unit", argv=[sys.executable, "-c", FLAKY, "{report}", str(fails)], timeout_seconds=30, report="junit")
    gate = runner.run_gate([spec], wt, job_id="job-000000000001", head_sha=None, tree_sha=None, changed_files=changed)
    assert str(gate.verdict) == verdict and gate.checks[0].flaky_files == flaky
    assert (wt / "runs.txt").read_text() == ("2" if not changed else "1")
