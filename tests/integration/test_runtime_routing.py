"""Each role runs on the runtime models.yaml binds it to; endpoint pools and fallbacks."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent_factory.scheduler import acquire_slot

REVIEWER = {"runtime": "codex", "model": "gpt-x", "tools": ["Read", "Glob", "Grep"]}
FALLBACK = {"runtime": "claude", "model": "claude-sonnet-5-5"}


class _Models(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        body = json.dumps({"data": [{"id": "qwen3-coder:30b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def model_server():
    httpd = HTTPServer(("127.0.0.1", 0), _Models)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()


def _binary(tmp_path):
    path = tmp_path / "agent-cli"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def _reviews(env, skill="factory-code-review"):
    return [c for c in env.runtime.calls if c.skill == skill]


def test_a_role_bound_to_codex_runs_on_codex(make_env, tmp_path):
    env = make_env("trivial", **{"runtime.runtimes.codex": {"binary": _binary(tmp_path)}, "runtime.roles.code_reviewer": REVIEWER})
    assert env.factory.run(env.submit()) == "READY_TO_MERGE"
    calls = _reviews(env)
    assert calls and all(c.runtime == "codex" and c.model == "gpt-x" for c in calls)
    assert all(c.runtime == "claude" for c in env.runtime.calls if c.skill == "factory-implement")


def test_a_missing_runtime_falls_back_and_says_so(make_env, tmp_path):
    env = make_env("trivial", **{"runtime.runtimes.codex": {"binary": str(tmp_path / "not-installed")},
                                 "runtime.roles.code_reviewer": {**REVIEWER, "fallback": FALLBACK}})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert all(c.runtime == "claude" and c.model == "claude-sonnet-5-5" for c in _reviews(env))
    events = [json.loads(e["payload_json"]) for e in env.store.events_for(job) if e["kind"] == "runtime.fallback"]
    assert events and "not installed" in events[0]["reason"]


def test_a_missing_runtime_without_fallback_stops_the_job(make_env, tmp_path):
    env = make_env("trivial", **{"runtime.runtimes.codex": {"binary": str(tmp_path / "not-installed")},
                                 "runtime.roles.code_reviewer": REVIEWER})
    job = env.submit()
    assert env.factory.run(job) == "BLOCKED_ENV"
    assert "code_reviewer" in env.store.job(job)["blocked_reason"] and "not installed" in env.store.job(job)["blocked_reason"]


def test_endpoint_pool_skips_unreachable_and_busy_servers(make_env, tmp_path, model_server):
    endpoints = {"down": {"base_url": "http://127.0.0.1:9/v1", "models": ["qwen3-coder:30b"]},
                 "busy": {"base_url": model_server, "models": ["qwen3-coder:30b"], "max_concurrent": 1},
                 "free": {"base_url": model_server, "models": ["qwen3-coder:30b"], "max_concurrent": 2}}
    role = {"runtime": "opencode", "model": "qwen3-coder:30b", "endpoints": ["down", "busy", "free"], "tools": ["Read", "Glob", "Grep"]}
    env = make_env("trivial", **{"runtime.runtimes.opencode": {"binary": _binary(tmp_path)}, "runtime.endpoints": endpoints,
                                 "runtime.roles.code_reviewer": role})
    acquire_slot(env.store, "endpoint:busy", 1, "other-job", "someone")  # another job holds the only slot
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    calls = _reviews(env)
    assert calls and all(c.runtime == "opencode" and c.runtime_options["endpoint"]["name"] == "free" for c in calls)
    held = env.store.one("SELECT COUNT(*) FROM resource_allocations WHERE kind = 'slot:endpoint:free' AND released_at IS NULL")[0]
    assert held == 0  # released after each call


def test_no_reachable_endpoint_uses_the_fallback(make_env, tmp_path):
    endpoints = {"down": {"base_url": "http://127.0.0.1:9/v1", "models": ["qwen3-coder:30b"]}}
    role = {"runtime": "opencode", "model": "qwen3-coder:30b", "endpoints": ["down"], "tools": ["Read"], "fallback": FALLBACK}
    env = make_env("trivial", **{"runtime.runtimes.opencode": {"binary": _binary(tmp_path)}, "runtime.endpoints": endpoints,
                                 "runtime.roles.code_reviewer": role})
    assert env.factory.run(env.submit()) == "READY_TO_MERGE"
    assert all(c.runtime == "claude" and not c.runtime_options for c in _reviews(env))


def test_endpoint_config_is_validated(tmp_path):
    from agent_factory.config import ConfigError, load_config

    from ..conftest import make_factory_dir

    bad = [
        ({"runtime.roles.code_reviewer": {**REVIEWER, "endpoints": ["x"]}}, "need runtime opencode"),
        ({"runtime.runtimes.opencode": {"binary": "o"}, "runtime.roles.code_reviewer":
          {"runtime": "opencode", "model": "m", "endpoints": ["nope"], "tools": []}}, "does not define"),
        ({"runtime.runtimes.opencode": {"binary": "o"}, "runtime.endpoints": {"e": {"base_url": "http://h/v1", "models": ["a"]}},
          "runtime.roles.code_reviewer": {"runtime": "opencode", "model": "b", "endpoints": ["e"], "tools": []}}, "does not serve"),
        ({"runtime.endpoints": {"e": {"base_url": "ftp://h", "models": ["a"]}}}, "base_url"),
    ]
    for i, (overrides, match) in enumerate(bad):
        d = make_factory_dir(tmp_path / str(i), **overrides)
        with pytest.raises(ConfigError, match=match):
            load_config(d)


def test_claude_model_overrides_never_reach_another_runtime(make_env, tmp_path):
    builder = {"runtime": "codex", "model": "gpt-builder", "tools": ["Read", "Edit", "Write", "Glob", "Grep", "Bash"]}
    env = make_env("trivial", **{"runtime.runtimes.codex": {"binary": _binary(tmp_path)}, "runtime.roles.builder": builder})
    assert env.factory.run(env.submit()) == "READY_TO_MERGE"
    builds = [c for c in env.runtime.calls if c.skill == "factory-implement"]
    assert builds and all(c.runtime == "codex" and c.model == "gpt-builder" for c in builds)  # routing names Claude models
