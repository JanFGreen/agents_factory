"""The agent never holds the operator's key: a one-time token, swapped by the controller's proxy."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_factory.keyproxy import KeyProxy, Route

SECRET = "sk-real-operator-key"


@pytest.fixture
def upstream():
    seen = []

    class Api(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "goog": self.headers.get("x-goog-api-key"),
                         "body": body})
            out = json.dumps({"output": "hi", "usage": {"input_tokens": 1000, "output_tokens": 500}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", seen
    server.shutdown()


def post(url, token, header="Authorization"):
    request = urllib.request.Request(url, data=b'{"input": "x"}', method="POST",
                                     headers={header: f"Bearer {token}" if header == "Authorization" else token})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_the_proxy_swaps_the_token_for_the_key_and_refuses_anything_else(upstream):
    base, seen = upstream
    proxy = KeyProxy({"codex-openai_api_key": Route(base, "OPENAI_API_KEY")}, environ={"OPENAI_API_KEY": SECRET})
    try:
        token = proxy.grant("codex-openai_api_key", 60)
        url = proxy.base_url("codex-openai_api_key") + "/responses"
        assert post(url, token) == (200, {"output": "hi", "usage": {"input_tokens": 1000, "output_tokens": 500}})
        assert seen[0]["path"] == "/v1/responses" and seen[0]["auth"] == f"Bearer {SECRET}" and SECRET not in token
        assert post(url, "kp-guessed")[0] == 401
        proxy.revoke([token])
        assert post(url, token)[0] == 401 and len(seen) == 1
    finally:
        proxy.close()


def test_a_spent_call_budget_stops_further_requests(upstream):
    base, seen = upstream
    proxy = KeyProxy({"r": Route(base, "K", "x-goog-api-key")}, environ={"K": SECRET})
    try:
        token = proxy.grant("r", 60, budget_usd=0.01, price={"input_per_mtok": 5.0, "output_per_mtok": 10.0})  # $0.01 a call
        url = proxy.base_url("r") + "/models/gemini:generateContent"
        assert post(url, token, header="x-goog-api-key")[0] == 200 and seen[0]["goog"] == SECRET
        status, body = post(url, token, header="x-goog-api-key")
        assert status == 402 and "budget" in body["error"]["message"] and len(seen) == 1
    finally:
        proxy.close()


def test_an_agent_gets_a_token_and_the_proxy_address_never_the_key(tmp_path, monkeypatch):
    from agent_factory.adapters.codex import CodexRuntime
    from agent_factory.adapters.protocol import AgentRequest
    from agent_factory.config import ProxyKeyDef
    from agent_factory.sandbox import get_sandbox

    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    key = ProxyKeyDef(source="OPENAI_API_KEY", upstream="https://api.openai.com/v1", base_url_env="OPENAI_BASE_URL")
    proxy = KeyProxy({"codex-openai_api_key": Route(key.upstream, key.source)})
    runtime = CodexRuntime("codex", get_sandbox(), tmp_path, proxy_keys={"OPENAI_API_KEY": key}, key_proxy=proxy)
    request = AgentRequest(job_id="j", attempt_id="a", role="builder", skill="s", prompt="p", system_append="", cwd=tmp_path,
                           model="gpt", tools=[], timeout_seconds=60, session_id="x")
    try:
        env = runtime._env(request)
        assert env["OPENAI_API_KEY"].startswith("kp-") and SECRET not in json.dumps(env)
        assert env["OPENAI_BASE_URL"].startswith("http://127.0.0.1:") and env["OPENAI_API_KEY"] in proxy.grants
    finally:
        proxy.close()


def test_opencode_points_its_cloud_provider_at_the_proxy(tmp_path, monkeypatch):
    from agent_factory.adapters.opencode import OpencodeRuntime
    from agent_factory.adapters.protocol import AgentRequest
    from agent_factory.config import ProxyKeyDef
    from agent_factory.sandbox import get_sandbox

    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    key = ProxyKeyDef(source="GEMINI_API_KEY", upstream="https://generativelanguage.googleapis.com/v1beta",
                      header="x-goog-api-key", provider="google")
    proxy = KeyProxy({"opencode-google_generative_ai_api_key": Route(key.upstream, key.source, key.header)})
    runtime = OpencodeRuntime("opencode", get_sandbox(), tmp_path, proxy_keys={"GOOGLE_GENERATIVE_AI_API_KEY": key}, key_proxy=proxy)
    request = AgentRequest(job_id="j", attempt_id="a", role="builder", skill="s", prompt="p", system_append="", cwd=tmp_path,
                           model="google/gemini-3.8-flash", tools=[], timeout_seconds=60, session_id="x")
    try:
        config = runtime.config(request, tmp_path / "i.md")
        assert config["provider"]["google"]["options"]["baseURL"].startswith("http://127.0.0.1:")
        assert runtime._env(request)["GOOGLE_GENERATIVE_AI_API_KEY"].startswith("kp-")
    finally:
        proxy.close()
