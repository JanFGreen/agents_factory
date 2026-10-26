"""CLI smoke: init, submit, status, inspect, budget, feature commands, exit codes and JSON mode."""

from __future__ import annotations

import json
import subprocess
import sys

from ..conftest import make_factory_dir


def cli(factory_dir, *args):
    proc = subprocess.run([sys.executable, "-m", "agent_factory.cli", "--factory-dir", str(factory_dir), "--json", *args],
                          capture_output=True, text=True)
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip().startswith(("{", "[")) else proc.stdout), proc.stderr


def test_cli_flow(tmp_path, repo):
    d = make_factory_dir(tmp_path)
    assert cli(d, "init", "--repo", str(repo), "--mode", "pr")[0] == 3  # cannot widen policy from the CLI
    code, out, _ = cli(d, "init", "--repo", str(repo), "--mode", "local", "--repo-id", "greeter")
    assert code == 0 and out["repo_id"] == "greeter"
    code, out, _ = cli(d, "submit", "--text", "Make greet exclaim")
    assert code == 0
    job = out["job_id"]
    code, out, _ = cli(d, "status", "--job", job)
    assert out["state"] == "FEATURE_RECEIVED" and out["analysis_rounds"] == "0/3"
    assert cli(d, "budget", "raise", job, "--to", "30", "--reason", "bigger")[0] == 0
    assert cli(d, "status", "--job", job)[1]["budget"]["limit"] == 30
    code, out, _ = cli(d, "inspect", job)
    assert any(e["kind"] == "budget.raised" for e in out["events"])
    code, out, err = cli(d, "approve", job, "--revision", "x" * 64)
    assert code == 3 and "not awaiting" in err
    code, out, _ = cli(d, "submit", "--issue", "5")
    assert code == 3  # issue intake refused in local mode
