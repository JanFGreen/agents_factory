"""The decision register and the documents a request names reach intake and the analysis; integration records the
feature's own decisions."""

from __future__ import annotations

import json

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import base_responders, git_commit_all, request_inputs, sh

EARLIER = "# Decisions: F1\n\n## Not doing\n\n- A farewell message\n"
PRODUCT = "# Product\n\nGreetings are short and friendly.\n"


def _seed_docs(repo) -> None:
    (repo / "docs" / "decisions").mkdir(parents=True)
    (repo / "docs" / "PRODUCT.md").write_text(PRODUCT)
    (repo / "docs" / "decisions" / "F1.md").write_text(EARLIER)
    (repo / "docs" / "UNRELATED.md").write_text("not named by the request\n")
    git_commit_all(repo, "docs")
    sh(repo, "git", "push", "-q", "origin", "trunk")


def _recording(reply, seen: dict, skill: str):
    def respond(request):
        seen.setdefault(skill, []).append(request_inputs(request).get("project_notes"))
        return reply(request) if callable(reply) else reply
    return respond


def test_named_docs_and_the_register_reach_intake_and_the_analysis(make_env, repo):
    _seed_docs(repo)
    seen: dict = {}
    base = base_responders("standard")
    responders = {s: _recording(base[s], seen, s) for s in
                  ("factory-intake", "factory-options-review", "factory-spec-author", "factory-plan", "factory-decomposition-review")}
    env = make_env("standard", responders=responders)
    job = env.submit("Make the greeting exclaim. docs/PRODUCT.md applies, and docs/MISSING.md too.", mode="auto")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert set(seen) == {"factory-intake", "factory-options-review", "factory-spec-author", "factory-plan", "factory-decomposition-review"}
    for notes in (n for calls in seen.values() for n in calls):
        assert notes["named_docs"] == {"docs/PRODUCT.md": PRODUCT}  # a named file that does not exist is left out
        assert notes["decision_register"] == {"docs/decisions/F1.md": EARLIER}
        assert "Not doing" in notes["rule"]


def test_notes_are_capped(make_env, repo):
    _seed_docs(repo)
    seen: dict = {}
    env = make_env("trivial", responders={"factory-intake": _recording(base_responders()["factory-intake"], seen, "i")},
                   **{"spec.notes_max_chars": 20})
    env.factory.run(env.submit("Exclaim; see docs/PRODUCT.md.", mode="auto"))
    notes = seen["i"][0]
    assert notes["named_docs"]["docs/PRODUCT.md"].startswith(PRODUCT[:20]) and "truncated: read docs/PRODUCT.md" in notes["named_docs"]["docs/PRODUCT.md"]
    assert notes["decision_register"] == {}


def test_no_notes_without_named_docs_or_register(make_env):
    seen: dict = {}
    env = make_env("trivial", responders={"factory-intake": _recording(base_responders()["factory-intake"], seen, "i")})
    env.factory.run(env.submit(mode="auto"))
    assert seen["i"] == [None]


def test_integration_records_the_decisions_and_what_is_not_done(make_env, repo):
    intake = FakeReply(envelope("intake", payload={
        "goal": "greeting exclaims", "acceptance_criteria": [], "constraints": [], "open_questions": [],
        "decisions": [{"id": "Q-1", "question": "Which mark?", "why": "Changes the text", "options": ["!", "!!"],
                       "proposed": "!"}, {"id": "Q-2", "question": "Keep [[names]]?", "why": "w", "proposed": "yes"}],
        "future_directions": [{"description": "Localised greetings", "status": "committed_next"}],
        "not_doing": ["A farewell message"]}))
    env = make_env("trivial", responders={"factory-intake": intake})
    job = env.submit(mode="guided")
    assert env.factory.run(job) == "NEEDS_INFO"
    env.factory.answer_decisions(job, {"Q-1": "!"}, True, "ada")
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    feature_id = env.store.job(job)["feature_id"]
    note = sh(repo, "git", "show", f"factory/{job}:docs/decisions/{feature_id}.md")
    assert note.startswith(f"# Decisions: {feature_id}") and f"Factory job {job}" in note
    assert "- **Q-1** Which mark? → ! (human)" in note and "- **Q-2** Keep [ [names]]? → yes (accepted)" in note
    assert "## Not doing\n\n- A farewell message\n- Localised greetings (next)" in note
    assert "factory: record the feature's decisions" in sh(repo, "git", "log", "--format=%s", f"factory/{job}")
    assert json.loads(env.store.job(job)["context_json"])["intake"]["not_doing"] == ["A farewell message"]
