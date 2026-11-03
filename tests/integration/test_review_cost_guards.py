"""Review cost guards from LearnEnglish F5a: reviewers read the task's own diff, re-reviews check the fixes against the
delta, code review of easy/medium tasks and fix-check rounds runs on the lighter model, read-only calls cache for 5 minutes."""

from __future__ import annotations

import pytest

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import ALL_SCENARIOS_PASS, FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, plan_reply, request_inputs

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


def _tasks(**first) -> list[dict]:
    return [
        {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [], "file_scope": ["app/greet.py", "tests"],
         "acceptance": "greeting ends with !", "estimated_minutes": 20, **first},
        {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": ["TASK-01"], "file_scope": ["app/farewell.py", "tests"],
         "acceptance": "farewell exists", "estimated_minutes": 20},
    ]


def _reply(edits: dict) -> FakeReply:
    return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [],
                                                    "handoff": "Done: as tasked\nSelf-review: no changes", "claimed_checks": []}), edits=edits)


def builder(request) -> FakeReply:
    if "task=TASK-02" in request.prompt:
        return _reply({"app/farewell.py": FAREWELL, "tests/test_farewell.py": FAREWELL_TEST})
    fixed = bool(request_inputs(request).get("open_findings"))  # the rework changes greet.py again
    return _reply({"app/greet.py": GREET_BANG + ("# guarded\n" if fixed else ""), "tests/test_greet_bang.py": GREET_TEST})


def _modes(request) -> list[dict]:
    return [{"path": s["path"], "operation": ", ".join(s["kinds"]), "scenarios": ALL_SCENARIOS_PASS, "evidence": "fixture"}
            for s in request_inputs(request).get("effect_sites", [])]


def _finding(fid: str, path: str, status: str = "open", severity: str = "medium") -> dict:
    return {"id": fid, "severity": severity, "blocking": True, "requirement_ids": [], "location": {"path": path, "line": 1},
            "problem": f"problem {fid}", "expected_fix": "fix it", "status": status}


class CodeReview:
    """TASK-01: round 1 asks for a change, round 2 confirms it and raises a new medium finding elsewhere."""

    def __init__(self, new_severity: str = "medium"):
        self.seen: list[tuple[str, dict, str]] = []
        self.new_severity = new_severity

    def __call__(self, request) -> FakeReply:
        inputs = request_inputs(request)
        task = "TASK-02" if "task=TASK-02" in request.prompt else "TASK-01"
        self.seen.append((task, inputs, request.model))
        rounds = sum(1 for t, _, _ in self.seen if t == task)
        if task == "TASK-02" or rounds > 2:
            return FakeReply(envelope("code-review", payload={"failure_modes": _modes(request)}))
        if rounds == 1:
            return FakeReply(envelope("code-review", "CHANGES", payload={"failure_modes": _modes(request)},
                                      findings=[_finding("F-1", "app/greet.py")]))
        return FakeReply(envelope("code-review", "CHANGES", payload={"failure_modes": _modes(request)}, findings=[
            _finding("F-1", "app/greet.py", status="fixed"), _finding("F-2", "tests/test_greet_bang.py", severity=self.new_severity)]))


def _env(make_env, review: CodeReview, tasks: list[dict] | None = None, **overrides):
    return make_env("standard", responders={"factory-plan": plan_reply(tasks or _tasks()), "factory-implement": builder,
                                            "factory-code-review": review},
                    **{"execution.max_builders_per_job": 1, "review.cheaper_models_for_risk": [], **overrides})


def _events(env, job, kind: str) -> list:
    return [e for e in env.store.events_for(job) if e["kind"] == kind]


@pytest.mark.parametrize("builders", [1, 2])
def test_reviewers_get_the_task_diff_and_the_rest_as_a_file_list(make_env, builders):
    review = CodeReview()
    env = _env(make_env, review, **{"execution.max_builders_per_job": builders})
    assert env.factory.run(env.submit("Add exclamation greeting and farewell")) == "READY_TO_MERGE"
    first = next(i for t, i, _ in review.seen if t == "TASK-01")
    second = next(i for t, i, _ in review.seen if t == "TASK-02")
    assert "app/greet.py" in first["diff"] and "diff_scope" not in first  # the first task's base is the feature base
    assert "app/farewell.py" in second["diff"] and "app/greet.py" not in second["diff"]
    assert second["diff_scope"].startswith("task") and any(line.endswith("app/greet.py") for line in second["other_feature_files"])


def test_second_round_checks_the_fixes_against_the_delta_on_the_lighter_model(make_env):
    review = CodeReview()
    env = _env(make_env, review, **{"review.lighter_models_for_difficulty": []})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    rounds = [(i, m) for t, i, m in review.seen if t == "TASK-01"]
    assert len(rounds) == 2  # the new finding outside the delta did not cost a third round
    (full, full_model), (check, check_model) = rounds
    assert full_model == OPUS and "diff" in full and "review_mode" not in full
    assert check_model == SONNET and check["review_mode"] == "fix_check" and "diff" not in check
    assert [f["id"] for f in check["previous_review"]["open_findings"]] == ["F-1"]
    assert "# guarded" in check["fix_delta"] and "test_greet_bang.py" not in check["fix_delta"]
    assert any(line.endswith("app/greet.py") for line in check["task_files"])
    row = env.store.one("SELECT blocking, status, problem FROM findings WHERE job_id = ? AND finding_id = 'F-2'", (job,))
    assert row["blocking"] == 0 and row["status"] == "open" and row["problem"].startswith("[follow-up: outside the fix delta]")
    assert env.store.one("SELECT status FROM findings WHERE job_id = ? AND finding_id = 'F-1'", (job,))["status"] == "fixed"
    assert _events(env, job, "findings.outside_delta")


def test_a_high_finding_outside_the_delta_still_blocks(make_env):
    review = CodeReview(new_severity="high")
    env = _env(make_env, review, **{"review.lighter_models_for_difficulty": []})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert len([t for t, _, _ in review.seen if t == "TASK-01"]) == 3 and not _events(env, job, "findings.outside_delta")


def test_code_review_model_follows_task_difficulty(make_env):
    review = CodeReview()
    hard = {"difficulty": "hard", "difficulty_reasons": ["indivisible: one function and its single test"]}
    env = _env(make_env, review, tasks=_tasks(**hard), **{"review.fix_check_reviews": []})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    models = {t: [m for tt, _, m in review.seen if tt == t] for t in ("TASK-01", "TASK-02")}
    assert set(models["TASK-01"]) == {OPUS} and set(models["TASK-02"]) == {SONNET}
    assert [e for e in _events(env, job, "review.routed") if '"TASK-02"' in e["payload_json"]]


def test_read_only_calls_use_the_short_prompt_cache(make_env):
    env = _env(make_env, CodeReview())
    assert env.factory.run(env.submit("Add exclamation greeting and farewell")) == "READY_TO_MERGE"
    flag = {c.skill: (c.sandbox_policy.env or {}).get("FORCE_PROMPT_CACHING_5M") for c in env.runtime.calls}
    assert flag["factory-code-review"] == "1" and flag["factory-plan"] == "1" and flag["factory-implement"] is None
