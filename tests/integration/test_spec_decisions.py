"""Guided spec mode: the operator answers the product decisions intake found before any analysis."""

from __future__ import annotations

import json

import pytest

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.controller import S, TransitionRefused

from .test_cli import cli
from ..conftest import make_factory_dir, request_inputs

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
    assert env.calls("factory-intake") == 2  # Q-1 differs from its proposal: one follow-up round, which opened nothing
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


CRITICAL = {"id": "Q-3", "question": "Store the names users type?", "why": "Personal data kept or not",
            "options": ["yes", "no"], "proposed": "no", "critical": True}


def test_auto_mode_still_asks_a_critical_decision(make_env):
    env = make_env("trivial", responders={"factory-intake": _intake([*DECISIONS, CRITICAL])})
    job = env.submit(mode="auto")
    assert env.factory.run(job) == "NEEDS_INFO"
    reason = env.store.job(job)["blocked_reason"]
    assert "1 product decision(s)" in reason and "Q-3 (critical)" in reason and "--skip" not in reason
    sources = {d["id"]: d["source"] for d in env.store.job_context(job)["decisions"]}
    assert sources == {"Q-1": "assumed", "Q-2": "assumed", "Q-3": None}
    env.factory.answer_decisions(job, {"Q-3": "yes"}, False, "ada")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-intake") == 1  # auto mode has no interview rounds


def _interviewer(rounds: list[list[dict]]):
    """Intake that asks ``rounds[n]`` in round n+1 and records the interview inputs it was given."""
    seen = []

    def reply(request):
        interview = request_inputs(request).get("interview")
        seen.append(interview)
        return _intake(rounds[min((interview or {}).get("round", 1), len(rounds)) - 1])
    return reply, seen


def test_an_answer_that_differs_opens_a_follow_up_round(make_env):
    follow_up = {"id": "Q-2", "question": "Use '!!' in the farewell too?", "why": "A second message changes",
                 "options": ["yes", "no"], "proposed": "yes"}
    reply, seen = _interviewer([DECISIONS, [DECISIONS[0], follow_up], [CRITICAL]])
    env = make_env("trivial", responders={"factory-intake": reply})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    env.factory.answer_decisions(job, {"Q-1": "!!", "Q-2": "Greet 'friend'"}, False, "ada")
    env.factory.resume(job)
    assert env.factory.run(job) == "NEEDS_INFO"  # round 2 asks what '!!' opened; a repeated question is dropped
    decisions = env.store.job_context(job)["decisions"]
    assert [(d["id"], d["round"], d["answer"]) for d in decisions] == [
        ("Q-1", 1, "!!"), ("Q-2", 1, "Greet 'friend'"), ("Q-3", 2, None)]
    assert seen[1]["round"] == 2 and [d["answer"] for d in seen[1]["settled"]] == ["!!", "Greet 'friend'"]
    env.factory.answer_decisions(job, {"Q-3": "yes"}, False, "ada")  # the proposal: the agent predicted it, no round 3
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-intake") == 2
    assert [json.loads(e["payload_json"])["round"] for e in env.events(job, "decisions.opened")] == [1, 2]


def test_interview_rounds_are_limited(make_env):
    reply, seen = _interviewer([[{**DECISIONS[0], "id": f"Q-{i}", "question": f"Question {i}?"}] for i in range(1, 6)])
    env = make_env("trivial", responders={"factory-intake": reply}, **{"spec.interview_rounds": 2})
    job = env.submit(mode="guided")
    for qid in ("Q-1", "Q-2"):
        assert env.factory.run(job) == "NEEDS_INFO"
        env.factory.answer_decisions(job, {qid: "something else"}, False, "ada")
        env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-intake") == 2


def test_skipping_the_interview_delegates_all_but_critical_decisions(make_env):
    reply, _ = _interviewer([[*DECISIONS, CRITICAL], [{**DECISIONS[0], "id": "Q-1", "question": "Never asked?"}]])
    env = make_env("trivial", responders={"factory-intake": reply})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    still = env.factory.answer_decisions(job, {"Q-1": "!!"}, False, "ada", skip=True)
    assert [d["id"] for d in still] == ["Q-3"]
    with pytest.raises(TransitionRefused, match="unanswered decisions"):
        env.factory.controller.transition(job, S.COMPLEXITY_ASSESSMENT)
    env.factory.answer_decisions(job, {"Q-3": "no"}, False, "ada")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-intake") == 1  # skipped: no follow-up round although Q-1 differs from its proposal
    sources = {d["id"]: d["source"] for d in env.store.job_context(job)["decisions"]}
    assert sources == {"Q-1": "human", "Q-2": "delegated", "Q-3": "human"}


def test_cli_decide_skip(tmp_path, repo):
    d = make_factory_dir(tmp_path)
    cli(d, "init", "--repo", str(repo), "--mode", "local", "--repo-id", "greeter")
    job = cli(d, "submit", "--text", "Make greet exclaim", "--mode", "guided")[1]["job_id"]
    from agent_factory.config import load_config
    from agent_factory.state import Store

    store = Store(load_config(d).data_dir() / "state.db")
    decisions = [{**q, "answer": None, "source": None} for q in [*DECISIONS, CRITICAL]]
    store.update_job_context(job, intake={"goal": "g"}, decisions=decisions, resume_state="INTAKE")
    with store.tx() as conn:
        row = conn.execute("SELECT version FROM jobs WHERE job_id = ?", (job,)).fetchone()
        store.cas_update_job(conn, job, row["version"], state="NEEDS_INFO")
    code, out, _ = cli(d, "decide", job, "--skip")
    assert code == 4 and out["open_decisions"] == ["Q-3"]
    ctx = json.loads(store.job(job)["context_json"])
    assert ctx["interview"]["closed"] and [x["source"] for x in ctx["decisions"]] == ["delegated", "delegated", None]


SPLIT = {"reason": "Exercises and check mode are tested and shipped separately",
         "parts": [{"title": "Exercises", "request": "Add gap exercises."}, {"title": "Check mode", "request": "Undo in check."}]}


def _split_intake(decisions=DECISIONS):
    return FakeReply(envelope("intake", payload={"goal": "g", "decisions": decisions, "scope_split": SPLIT}))


def test_an_accepted_split_writes_the_parts_without_the_other_answers(make_env):
    env = make_env("trivial", responders={"factory-intake": _split_intake()})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    split = env.store.job_context(job)["decisions"][0]
    assert split["id"] == "Q-3" and split["critical"] and split["split"] and split["answer"] is None
    assert split["options"] == ["Split into 2: Exercises; Check mode", "Keep it as one feature"]
    env.factory.answer_decisions(job, {"Q-3": split["proposed"]}, False, "ada")  # the split alone, Q-1/Q-2 left open
    env.factory.resume(job)
    assert env.factory.run(job) == "CANCELLED"
    assert env.calls("factory-classify-feature") == 0  # no analysis was spent on the bundle
    files = env.store.job_context(job)["split_files"]
    feature_id = env.store.job(job)["feature_id"]
    import yaml
    parts = [yaml.safe_load(open(f)) for f in files]
    assert [p["feature_id"] for p in parts] == [f"{feature_id}-A", f"{feature_id}-B"]
    assert parts[0]["original_request"].startswith("Add gap exercises.") and "Part 1 of 2" in parts[0]["original_request"]
    assert "builds on the earlier parts" in parts[1]["original_request"] and parts[1]["repo_id"] == "greeter"
    assert env.events(job, "scope.split")


def test_keeping_one_feature_goes_on_without_a_follow_up_round(make_env):
    env = make_env("trivial", responders={"factory-intake": _split_intake([])})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    env.factory.answer_decisions(job, {"Q-1": "Keep it as one feature"}, False, "ada")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert env.calls("factory-intake") == 1  # a differing split answer opens no interview round
    specs = _spec_inputs(env)
    assert specs and all("Keep it as one feature" not in p for p in specs)  # not a product decision for the spec


def test_a_split_proposal_is_asked_in_auto_mode(make_env):
    env = make_env("trivial", responders={"factory-intake": _split_intake()})
    job = env.submit(mode="auto")
    assert env.factory.run(job) == "NEEDS_INFO"
    assert [d["id"] for d in env.store.job_context(job)["decisions"] if d["answer"] is None] == ["Q-3"]
