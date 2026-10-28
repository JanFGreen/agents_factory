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
