"""Defects found after merge are recorded per job and reach every later review of the same repository."""

from __future__ import annotations

import json

import pytest

from agent_factory import escapes
from agent_factory.cli import main

from ..conftest import request_inputs


def _report(env, job, **kw):
    fields = {"area": "failure-mode", "location": "app/greet.py:2", "problem": "a hung request blocks saving",
              "missed_by": "code-review", "actor": "operator", **kw}
    return escapes.report(env.store, job, **fields)


def test_a_later_review_of_the_same_repository_gets_the_escapes(make_env):
    env = make_env("trivial")
    shipped = env.submit()
    assert env.factory.run(shipped) == "READY_TO_MERGE"
    assert all("past_escapes" not in request_inputs(c) for c in env.runtime.calls)  # nothing reported yet
    escape_id = _report(env, shipped)
    assert json.loads(env.events(shipped, "defect.escaped")[0]["payload_json"])["missed_by"] == "code-review"
    env.runtime.calls.clear()
    later = env.submit("Make the greeting polite.")
    env.factory.run(later)
    reviews = [request_inputs(c) for c in env.runtime.calls if c.skill == "factory-code-review"]
    assert reviews and all(r["past_escapes"][0]["escape_id"] == escape_id for r in reviews)
    builds = [request_inputs(c) for c in env.runtime.calls if c.skill == "factory-implement"]
    assert builds and all("past_escapes" not in b for b in builds)  # the builder is judged on them, not handed them


def test_another_repository_does_not_see_them(make_env):
    env = make_env("trivial")
    job = env.submit()
    _report(env, job)
    assert escapes.for_repo(env.store, "/somewhere/else") == []


@pytest.mark.parametrize("field, value, problem", [
    ("area", "vibes", "area must be one of"),
    ("missed_by", "nobody", "missed_by must be one of"),
    ("problem", "  ", "required"),
])
def test_a_report_needs_known_categories_and_a_problem(make_env, field, value, problem):
    env = make_env("trivial")
    job = env.submit()
    with pytest.raises(escapes.EscapeRefused, match=problem):
        _report(env, job, **{field: value})
    with pytest.raises(escapes.EscapeRefused, match="unknown job"):
        _report(env, "job-000000000000")


def test_the_cli_reports_and_summarises_per_review(make_env, capsys):
    env = make_env("trivial")
    job = env.submit()
    base = ["--factory-dir", str(env.factory_dir), "--data-dir", str(env.cfg.data_dir()), "--json"]
    for missed_by in ("code-review", "code-review", "ui-review"):
        assert main([*base, "defect", "report", job, "--area", "ux", "--location", "src/App.tsx:10",
                     "--problem", "the board jumps when the round starts", "--missed-by", missed_by]) == 0
    capsys.readouterr()
    assert main([*base, "defect", "list", "--repo", str(env.repo)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["summary"]["escapes"] == 3 and listed["summary"]["by_missed_by"] == {"code-review": 2, "ui-review": 1}
    with pytest.raises(SystemExit) as refused:  # argparse refuses an unknown review
        main([*base, "defect", "report", job, "--area", "ux", "--location", "x", "--problem", "y", "--missed-by", "nobody"])
    assert refused.value.code == 2
