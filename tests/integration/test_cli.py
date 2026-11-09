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
    code, out, _ = cli(d, "mirror", job, "--github", "acme/app")  # nothing leaves the machine without external_messages
    assert code == 3 and out["mirrored"] is False and "external_messages" in out["reason"]
    code, out, err = cli(d, "approve", job, "--revision", "x" * 64)
    assert code == 3 and "not awaiting" in err
    code, out, _ = cli(d, "submit", "--issue", "5")
    assert code == 3  # issue intake refused in local mode


def test_the_mirror_text_shows_state_tasks_budget_and_what_is_asked():
    from agent_factory.cli import mirror_text

    row = {"job_id": "job-1", "feature_id": "F1", "state": "PAUSED", "path": "standard", "risk": "low",
           "budget": {"spent": 3.5, "limit": 20}, "analysis_rounds": "1/3", "next_step": "`factory resume <id>`",
           "tasks": [{"task_id": "TASK-01", "state": "ACCEPTED", "attempts": 1}],
           "needs_you": {"asks": ["Read why the job stopped below, then resume or cancel it."]}}
    title, body = mirror_text(row)
    assert title == "[factory] F1 (job-1): PAUSED"
    assert "| TASK-01 | ACCEPTED | 1 |" in body and "$3.50 of $20.00" in body and "Needs you" in body


def test_collect_reports_an_export_and_refuses_an_unreadable_source(tmp_path):
    d = make_factory_dir(tmp_path)
    export = tmp_path / "errors.json"
    export.write_text(json.dumps([{"id": "1", "title": "Crash for ada@example.com", "count": 4, "last_seen": "2026-11-08T10:00:00Z"}]))
    code, out, _ = cli(d, "collect", "--repo-id", "app", "--file", str(export), "--since", "2026-11-05T00:00:00Z")
    assert code == 0 and out["counts"]["new"] == 1
    from pathlib import Path
    assert "ada@" not in Path(out["report"]).read_text()
    code, out, _ = cli(d, "collect", "--repo-id", "app", "--file", str(tmp_path / "missing.json"))
    assert code == 4 and "source unavailable" in out["reason"]
