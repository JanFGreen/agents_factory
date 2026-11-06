"""Guards against the costs of LearnEnglish F4-F5a: a gate fix-up pass instead of a rebuild, no repeat UI review of
a task whose screens are not on show, and medium findings that become follow-ups after N review rounds."""

from __future__ import annotations

import pytest

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import ALL_SCENARIOS_PASS, FAREWELL, FAREWELL_TEST, GREET_BANG, GREET_TEST, plan_reply, request_inputs

BROKEN_TEST = 'from app.greet import greet\n\n\ndef test_greet_broken():\n    assert greet("Ada") == "nope"\n'
VIEW = "<p>hello</p>\n"


def _tasks(*, ui: bool = False) -> list[dict]:
    return [
        {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "depends_on": [],
         "file_scope": ["app/greet.py", *(["app/view.html"] if ui else []), "tests"], "acceptance": "greeting ends with !",
         "estimated_minutes": 20},
        {"task_id": "TASK-02", "requirement_ids": ["REQ-2"], "depends_on": [], "file_scope": ["app/farewell.py", "tests"],
         "acceptance": "farewell exists", "estimated_minutes": 20},
    ]


def _reply(edits: dict) -> FakeReply:
    return FakeReply(envelope("implement", payload={"changed_files": list(edits), "tests_added": [], "handoff": "Done: as tasked\nSelf-review: no changes",
                                                    "claimed_checks": []}), edits=edits)


class Builder:
    """TASK-01's first build is refused by the gate (broken test or a file outside the scope); the fix-up repairs it."""

    def __init__(self, failure: str = "code", ui: bool = False):
        self.failure, self.ui, self.fixups, self.builds = failure, ui, [], 0

    def __call__(self, request) -> FakeReply:
        if "task=TASK-02" in request.prompt:
            return _reply({"app/farewell.py": FAREWELL, "tests/test_farewell.py": FAREWELL_TEST})
        fixup = request_inputs(request).get("gate_fixup")
        if fixup:
            self.fixups.append((fixup, request.model))
            return _reply({"tests/test_greet_bang.py": GREET_TEST, "app/helper.py": None})
        self.builds += 1
        edits = {"app/greet.py": GREET_BANG, "tests/test_greet_bang.py": GREET_TEST, **({"app/view.html": VIEW} if self.ui else {})}
        if self.builds == 1 and self.failure == "code":
            edits["tests/test_greet_bang.py"] = BROKEN_TEST
        if self.builds == 1 and self.failure == "scope":
            edits["app/helper.py"] = "X = 1\n"
        return _reply(edits)


def _tasks_state(env, job) -> dict[str, tuple[str, int]]:
    return {r["task_id"]: (r["state"], r["attempts"]) for r in env.store.all("SELECT task_id, state, attempts FROM tasks WHERE job_id = ?", (job,))}


def _events(env, job, kind: str) -> list:
    return [e for e in env.store.events_for(job) if e["kind"] == kind]


@pytest.mark.parametrize("builders", [1, 2])
@pytest.mark.parametrize("failure", ["code", "scope"])
def test_refused_gate_gets_a_fixup_pass_in_the_same_attempt(make_env, builders, failure):
    builder = Builder(failure)
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks()), "factory-implement": builder},
                   **{"execution.max_builders_per_job": builders, "workflow.implementation.gate_fixups_per_attempt": 1,
                      "workflow.implementation.gate_fixup_model": "claude-haiku-5-5"})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert _tasks_state(env, job)["TASK-01"] == ("ACCEPTED", 1)  # no second attempt
    assert builder.builds == 1 and len(builder.fixups) == 1
    fixup, model = builder.fixups[0]
    assert fixup["verdict"] == ("FAIL_CODE" if failure == "code" else "FAIL_POLICY") and model == "claude-haiku-5-5"
    if failure == "scope":
        assert "app/helper.py" in fixup["reason"]
    assert [e for e in _events(env, job, "gate.fixup")]


def test_a_retried_task_gets_its_gate_fixup_again(make_env):
    """Live finding (LearnEnglish F5c TASK-06): attempt numbers restart after `task retry`, and the fix-up key of the first
    round ("TASK-06#2") blocked the fix-up of a later refusal: each removed-test refusal cost a full Opus rebuild."""
    builder = Builder("code")
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks()), "factory-implement": builder},
                   **{"execution.max_builders_per_job": 1, "workflow.implementation.gate_fixups_per_attempt": 1})
    job = env.submit("Add exclamation greeting and farewell")
    while env.state(job) != "BUILD_TASK":
        env.factory.step(job)
    env.factory.request_pause(job, ["TASK-01", "TASK-02"], "before feature acceptance")
    assert env.factory.run(job) == "PAUSED" and len(builder.fixups) == 1
    builder.builds = 0  # the reopened task's first build is refused by the gate again
    env.factory.retry_task(job, "TASK-01", 3, "fix the greeting again")  # attempts restart at 0: the next build is #1 again
    env.factory.resume(job)
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert builder.builds == 1 and len(builder.fixups) == 2  # a fix-up pass, not a second full build


def test_without_fixups_a_refused_gate_costs_an_attempt(make_env):
    builder = Builder("code")
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks()), "factory-implement": builder},
                   **{"execution.max_builders_per_job": 1})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert _tasks_state(env, job)["TASK-01"] == ("ACCEPTED", 2) and builder.fixups == []


def test_builder_is_told_the_scope_self_check(make_env):
    builder = Builder("none")
    seen = []

    def implement(request):
        seen.append(request_inputs(request).get("file_scope_rule", ""))
        return builder(request)

    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks()), "factory-implement": implement},
                   **{"execution.max_builders_per_job": 1})
    assert env.factory.run(env.submit("Add exclamation greeting and farewell")) == "READY_TO_MERGE"
    assert seen and all("git status --porcelain" in rule and "tests/" in rule for rule in seen)


def _code_review(severity: str, rounds: list):
    """Every round a new blocking finding, as in F5a TASK-14."""

    def reply(request) -> FakeReply:
        rounds.append(1)
        if "task=TASK-02" in request.prompt:
            rounds.pop()
            return FakeReply(envelope("code-review", payload={"failure_modes": _modes(request)}))
        finding = {"id": f"F-{len(rounds)}", "severity": severity, "blocking": True, "requirement_ids": [], "location": None,
                   "problem": f"race number {len(rounds)}", "expected_fix": "guard it", "status": "open"}
        return FakeReply(envelope("code-review", "CHANGES", payload={"failure_modes": _modes(request)}, findings=[finding]))

    return reply


def _modes(request) -> list[dict]:
    sites = request_inputs(request).get("effect_sites", [])
    return [{"path": s["path"], "operation": ", ".join(s["kinds"]), "scenarios": ALL_SCENARIOS_PASS, "evidence": "fixture"} for s in sites]


def test_medium_findings_become_follow_ups_after_the_round_limit(make_env):
    rounds: list = []
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks()), "factory-implement": Builder("none"),
                                          "factory-code-review": _code_review("medium", rounds)},
                   **{"execution.max_builders_per_job": 1, "workflow.implementation.demote_medium_after_review_rounds": 2,
                      "review.proof_required": []})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert _tasks_state(env, job)["TASK-01"] == ("ACCEPTED", 3) and len(rounds) == 3
    demoted = _events(env, job, "findings.demoted")
    assert len(demoted) == 1 and '"F-3"' in demoted[0]["payload_json"]
    row = env.store.one("SELECT blocking, status, problem FROM findings WHERE job_id = ? AND finding_id = 'F-3'", (job,))
    assert row["blocking"] == 0 and row["status"] == "open" and row["problem"].startswith("[follow-up after 2 review rounds]")


def test_high_findings_still_block_after_the_round_limit(make_env):
    rounds: list = []
    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks()), "factory-implement": Builder("none"),
                                          "factory-code-review": _code_review("high", rounds)},
                   **{"execution.max_builders_per_job": 1, "workflow.implementation.demote_medium_after_review_rounds": 1})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "NEEDS_HUMAN"
    assert _tasks_state(env, job)["TASK-01"][0] == "FAILED" and not _events(env, job, "findings.demoted")


def test_ui_review_is_not_repeated_while_no_touched_screen_is_on_show(make_env):
    rounds: list = []
    ui_calls: list = []

    def ui_review(request) -> FakeReply:
        ui_calls.append(1)
        return FakeReply(envelope("ui-review", payload={"touched_screens_rendered": False,
                                                        "not_rendered_reason": "a provider; no route mounts it yet"}))

    def code_review(request) -> FakeReply:
        if "task=TASK-01" in request.prompt and not rounds:
            rounds.append(1)
            finding = {"id": "F-1", "severity": "medium", "blocking": True, "requirement_ids": [], "location": None,
                       "problem": "race", "expected_fix": "guard it", "status": "open"}
            return FakeReply(envelope("code-review", "CHANGES", payload={"failure_modes": _modes(request)}, findings=[finding]))
        return FakeReply(envelope("code-review", payload={"failure_modes": _modes(request)}))

    env = make_env("standard", responders={"factory-plan": plan_reply(_tasks(ui=True)), "factory-implement": Builder("none", ui=True),
                                          "factory-code-review": code_review, "factory-ui-review": ui_review},
                   **{"execution.max_builders_per_job": 1, "review.proof_required": []})
    job = env.submit("Add exclamation greeting and farewell")
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert _tasks_state(env, job)["TASK-01"] == ("ACCEPTED", 2)
    task_ui = [c for c in env.runtime.calls if c.skill == "factory-ui-review" and "task=TASK-01" in c.prompt]
    assert len(task_ui) == 1 and _events(env, job, "ui_review.skipped")

