"""Guided spec mode: the operator answers the product decisions intake found before any analysis."""

from __future__ import annotations

import json

import pytest

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.controller import S, TransitionRefused

from .test_cli import cli
from ..conftest import make_factory_dir

DECISIONS = [
    {"id": "Q-1", "question": "Punctuation at the end of the greeting?", "why": "Changes the greeting text and its test",
     "options": ["!", "!!"], "proposed": "!"},
    {"id": "Q-2", "question": "Greet an empty name?", "why": "Adds or omits a fallback path",
     "options": [], "proposed": "Greet 'friend'"},
]


def _intake(decisions=DECISIONS):
    return FakeReply(envelope("intake", payload={"goal": "greeting exclaims", "acceptance_criteria": [], "constraints": [],
                                                 "open_questions": [], "decisions": decisions}))


def _spec_inputs(env) -> list[str]:
    return [c.prompt for c in env.runtime.calls if c.skill == "factory-spec-author"]


def test_guided_job_waits_for_answers_then_runs_with_them(make_env):
    env = make_env("trivial", responders={"factory-intake": _intake()})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    reason = env.store.job(job)["blocked_reason"]
    assert "2 product decision(s)" in reason and "Q-1" in reason and f"factory decide {job}" in reason
    assert env.calls("factory-classify-feature") == 0  # no analysis on open decisions

    assert [d["id"] for d in env.factory.answer_decisions(job, {"Q-1": "!!"}, False, "ada")] == ["Q-2"]
    env.factory.resume(job)
    assert env.factory.run(job) == "NEEDS_INFO"  # still one open decision; intake is not re-run
    assert env.calls("factory-intake") == 1

    assert env.factory.answer_decisions(job, {}, True, "ada") == []
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-intake") == 1
    decided = {d["id"]: (d["answer"], d["source"]) for d in env.store.job_context(job)["decisions"]}
    assert decided == {"Q-1": ("!!", "human"), "Q-2": ("Greet 'friend'", "accepted")}
    specs = _spec_inputs(env)
    assert specs and all('"answer": "!!"' in p and '"source": "human"' in p for p in specs)
    kinds = [e["kind"] for e in env.store.events_for(job)]
    assert kinds.count("decisions.answered") == 2 and "decisions.opened" in kinds


def test_auto_mode_assumes_the_proposals_and_does_not_stop(make_env):
    env = make_env("trivial", responders={"factory-intake": _intake()})
    job = env.submit(mode="auto")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert {d["source"] for d in env.store.job_context(job)["decisions"]} == {"assumed"}
    specs = _spec_inputs(env)
    assert specs and all('"source": "assumed"' in p for p in specs)


def test_guided_without_decisions_does_not_stop(make_env):
    env = make_env("trivial", responders={"factory-intake": _intake([])})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "READY_TO_MERGE"


def test_decisions_beyond_the_limit_are_assumed(make_env):
    env = make_env("trivial", responders={"factory-intake": _intake()}, **{"spec.max_decisions": 1})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    sources = {d["id"]: d["source"] for d in env.store.job_context(job)["decisions"]}
    assert sources == {"Q-1": None, "Q-2": "assumed"}


def test_mode_defaults_to_the_config_and_is_validated(make_env):
    env = make_env("trivial", **{"spec.default_mode": "auto"})
    assert env.store.job_context(env.submit())["spec_mode"] == "auto"
    with pytest.raises(ValueError):
        env.submit("Other request", mode="manual")


def test_answers_are_validated_and_the_guard_holds(make_env):
    env = make_env("trivial", responders={"factory-intake": _intake()})
    job = env.submit(mode="guided")
    with pytest.raises(TransitionRefused):
        env.factory.answer_decisions(job, {"Q-1": "!"}, False, "ada")  # not waiting yet
    env.factory.run(job)
    with pytest.raises(TransitionRefused, match="not an open decision"):
        env.factory.answer_decisions(job, {"Q-9": "x"}, False, "ada")
    with pytest.raises(TransitionRefused, match="empty"):
        env.factory.answer_decisions(job, {"Q-1": "  "}, False, "ada")
    env.store.update_job_context(job, resume_state=str(S.INTAKE))
    env.factory.resume(job)
    with pytest.raises(TransitionRefused, match="unanswered decisions"):
        env.factory.controller.transition(job, S.COMPLEXITY_ASSESSMENT)


def test_cli_decide_answers_and_resumes(tmp_path, repo, monkeypatch):
    d = make_factory_dir(tmp_path)
    cli(d, "init", "--repo", str(repo), "--mode", "local", "--repo-id", "greeter")
    code, out, _ = cli(d, "submit", "--text", "Make greet exclaim", "--mode", "guided")
    job = out["job_id"]
    # Put the job where a guided intake leaves it (the CLI runs no agents in this test).
    from agent_factory.config import load_config
    from agent_factory.state import Store

    store = Store(load_config(d).data_dir() / "state.db")
    decisions = [{**q, "answer": None, "source": None} for q in DECISIONS]
    store.update_job_context(job, intake={"goal": "g"}, decisions=decisions, resume_state="INTAKE")
    with store.tx() as conn:
        row = conn.execute("SELECT version FROM jobs WHERE job_id = ?", (job,)).fetchone()
        store.cas_update_job(conn, job, row["version"], state="NEEDS_INFO")

    code, out, _ = cli(d, "status", "--job", job)
    assert out["spec_mode"] == "guided" and "factory decide" in out["next_step"] and len(out["decisions"]) == 2
    code, out, _ = cli(d, "decide", job)  # non-interactive without answers: lists them
    assert code == 4 and [q["id"] for q in out["open_decisions"]] == ["Q-1", "Q-2"]
    assert cli(d, "decide", job, "--answer", "bad")[0] == 2
    assert cli(d, "decide", job, "--answer", "Q-7=x")[0] == 3
    code, out, _ = cli(d, "decide", job, "--answer", "Q-1=!!")
    assert code == 4 and out["open_decisions"] == ["Q-2"]
    code, out, _ = cli(d, "decide", job, "--accept-proposed")
    assert code == 0 and out["decisions"] == "answered"
    assert store.job(job)["state"] == "INTAKE"
    assert json.loads(store.job(job)["context_json"])["decisions"][1]["source"] == "accepted"
