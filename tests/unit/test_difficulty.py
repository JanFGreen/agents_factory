"""Task difficulty: planner label, structural floor and an optional decision model; it only goes up."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent_factory.adapters.typesafe import JevDecider, make_decider
from agent_factory.config import DecisionModel
from agent_factory.difficulty import Choice, assess, builder_level, indivisible, structural_floor
from agent_factory.schemas import TaskSpec


def task(**kw) -> TaskSpec:
    base = {"task_id": "TASK-01", "requirement_ids": ["REQ-1"], "acceptance": "works", "file_scope": ["src/a.ts", "src/a.test.ts"]}
    return TaskSpec.model_validate({**base, **kw})


class FixedDecider:
    name = "fixed"

    def __init__(self, choice: Choice | None):
        self.result = choice
        self.calls = []

    def choose(self, state, instructions, criteria):
        self.calls.append((state, criteria))
        return self.result


@pytest.mark.parametrize("kw,level", [
    ({}, "easy"),
    ({"file_scope": ["src/a.ts", "src/b.ts", "src/c.ts", "src/d.ts"]}, "medium"),
    ({"file_scope": [f"src/{n}.ts" for n in "abcdefg"]}, "hard"),
    ({"file_scope": ["src/ui"]}, "medium"),
    ({"file_scope": ["src/**/*.ts"]}, "medium"),
    ({"risk": "high"}, "medium"),
    ({"requirement_ids": ["R1", "R2", "R3", "R4"]}, "medium"),
    ({"outputs": ["a", "b", "c"]}, "medium"),
    ({"file_scope": ["src/a.ts", "src/a.test.ts", "tests/test_b.py", "src/c.spec.tsx", "tests", "src/__tests__/"]}, "easy"),  # tests do not count
])
def test_structural_floor(kw, level):
    assert structural_floor(task(**kw))[0] == level


def test_planner_label_counts_and_missing_label_is_medium():
    assert assess(task(difficulty="easy"), None, 0.6)["difficulty"] == "easy"
    assert assess(task(), None, 0.6)["difficulty"] == "medium"
    raised = assess(task(difficulty="easy", risk="medium"), None, 0.6)
    assert raised["difficulty"] == "medium" and "risk medium" in raised["reasons"]


def test_a_confident_decider_raises_but_never_lowers():
    hard = FixedDecider(Choice("hard", 0.9, 300))
    result = assess(task(difficulty="easy"), hard, 0.6)
    assert result["difficulty"] == "hard" and result["decider"]["used"] and result["decider"]["input_tokens"] == 300
    state, criteria = hard.calls[0]
    assert state["task_id"] == "TASK-01" and set(criteria) == {"easy", "medium", "hard"}
    assert assess(task(difficulty="hard"), FixedDecider(Choice("easy", 0.99)), 0.6)["difficulty"] == "hard"


def test_an_unsure_or_unavailable_decider_is_ignored():
    unsure = assess(task(difficulty="easy"), FixedDecider(Choice("hard", 0.4)), 0.6)
    assert unsure["difficulty"] == "easy" and unsure["decider"]["used"] is False
    assert assess(task(difficulty="easy"), FixedDecider(None), 0.6) == {**assess(task(difficulty="easy"), None, 0.6)}
    assert assess(task(difficulty="easy"), FixedDecider(Choice("trivial", 0.99)), 0.6)["difficulty"] == "easy"


@pytest.mark.parametrize("level,attempt,escalate,expected", [
    ("easy", 1, True, "easy"), ("easy", 2, True, "medium"), ("easy", 3, True, "hard"),
    ("medium", 2, True, "hard"), ("hard", 3, True, "hard"), ("easy", 3, False, "easy"), ("easy", 0, True, "easy"),
])
def test_builder_level_escalates_per_attempt(level, attempt, escalate, expected):
    assert builder_level(level, attempt, escalate) == expected


def test_indivisible_needs_the_prefix():
    assert indivisible(task(difficulty_reasons=["Indivisible: one migration with its rollback"]))
    assert not indivisible(task(difficulty_reasons=["big but could be split"]))


# --- Jev adapter against a local stand-in for api.typesafe.ai ---------------------------------


class _Handler(BaseHTTPRequestHandler):
    reply: tuple[int, dict] = (200, {})
    seen: list = []

    def do_POST(self):  # noqa: N802 (http.server API)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, self.headers.get("Authorization"), body))
        status, payload = type(self).reply
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1/systemone"
    httpd.shutdown()


def test_jev_sends_a_choice_question_and_reads_the_answer(server):
    _Handler.reply = (200, {"model": "jev-1.14.0", "answers": {"decision": {
        "type": "choice", "choice": "medium", "confidence": 0.82, "probabilities": {"easy": 0.1, "medium": 0.85, "hard": 0.05}}},
        "usage": {"input_tokens": 412, "output_tokens": 3}})
    decider = JevDecider("k-test", "jev-latest", server, 5)
    choice = decider.choose({"task_id": "TASK-01"}, "How hard?", {"easy": "e", "medium": "m", "hard": "h"})
    assert choice == Choice("medium", 0.82, 412)
    path, auth, body = _Handler.seen[0]
    assert path == "/v1/systemone" and auth == "Bearer k-test"
    assert body["model"] == "jev-latest" and body["state"] == {"task_id": "TASK-01"}
    assert body["questions"]["decision"] == {"type": "choice", "instructions": "How hard?", "criteria": {"easy": "e", "medium": "m", "hard": "h"}}


@pytest.mark.parametrize("reply", [(422, {"detail": [{"msg": "bad"}]}), (200, {"answers": {}}), (200, {"answers": {"decision": {"choice": "x"}}})])
def test_jev_failures_fall_back_to_none(server, reply):
    _Handler.reply = reply
    decider = JevDecider("k", "jev-latest", server, 5)
    assert decider.choose({}, "q", {"a": "a"}) is None and decider.last_error


def test_unreachable_jev_falls_back_to_none():
    decider = JevDecider("k", "jev-latest", "http://127.0.0.1:9/v1/systemone", 1)
    assert decider.choose({}, "q", {"a": "a"}) is None and decider.last_error


def test_jev_is_used_only_when_configured_and_its_key_is_set(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert make_decider(DecisionModel(provider="jev")) is None
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert make_decider(DecisionModel(provider="none")) is None
    assert isinstance(make_decider(DecisionModel(provider="jev")), JevDecider)
