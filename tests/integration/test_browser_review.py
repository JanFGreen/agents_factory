"""UI reviewers look at the served web build in a browser; screenshots become evidence."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import yaml

from agent_factory.adapters.fake import FakeReply, envelope

from ..conftest import UNIT_CHECK, git_commit_all, sh

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image"
BUILD = {"name": "web-build", "argv": ["{python}", "-c", "import pathlib; d = pathlib.Path('dist'); d.mkdir(exist_ok=True); "
                                       "(d / 'index.html').write_text('<h1>Greeter</h1>')"],
         "timeout_seconds": 60, "stage": "task"}


def _fake_cli(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "cli.log"
    cli = tmp_path / "fake-playwright-cli"
    cli.write_text(f"#!/bin/sh\necho \"$@\" >> '{log}'\n")
    cli.chmod(0o755)
    return cli, log


def _env(make_env, tmp_path, repo, responders):
    (repo / ".gitignore").write_text("dist/\n")
    git_commit_all(repo, "ignore build output")
    cli, log = _fake_cli(tmp_path)
    env = make_env("trivial", responders=responders, **{"browser": {"binary": str(cli), "viewports": ["390x844"]}})
    (env.factory_dir / "checks" / "default.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "checks": [UNIT_CHECK, BUILD], "preview": {"static_dir": "dist"}}))
    env.reload()
    return env, log


class Reviewer:
    """Acts like a browser-driving reviewer: reads the served page and saves a screenshot."""

    def __init__(self, kind: str):
        self.kind = kind
        self.seen: list[dict] = []

    def __call__(self, request) -> FakeReply:
        inputs = json.loads(request.prompt.split("<inputs>\n", 1)[1].split("\n</inputs>", 1)[0])
        environment = inputs["environment"]
        page = urllib.request.urlopen(environment["url"], timeout=5).read().decode()
        evidence = Path(environment["browser"]["evidence_dir"])
        (evidence / "home-390.png").write_bytes(PNG)
        self.seen.append({"environment": environment, "page": page, "tools": request.tools,
                          "writable": [str(p) for p in request.sandbox_policy.writable], "env": request.sandbox_policy.env})
        return FakeReply(envelope(self.kind, payload={}))


def test_reviewer_gets_the_served_build_a_browser_and_its_screenshots_are_kept(make_env, tmp_path, repo):
    reviewer = Reviewer("product-verify")
    env, log = _env(make_env, tmp_path, repo, {"factory-product-verify": reviewer})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"

    first = reviewer.seen[0]
    assert first["environment"]["kind"] == "served" and first["page"] == "<h1>Greeter</h1>"
    assert first["environment"]["browser"]["viewports"] == ["390x844"]
    assert "Bash" in first["tools"] and first["environment"]["browser"]["evidence_dir"] in first["writable"]
    assert first["env"]["PLAYWRIGHT_MCP_SANDBOX"] == "false" and first["env"]["PLAYWRIGHT_MCP_OUTPUT_DIR"] == first["environment"]["browser"]["evidence_dir"]
    assert first["env"]["PLAYWRIGHT_MCP_ALLOWED_ORIGINS"] == first["environment"]["url"].rstrip("/")

    results = env.store.all("SELECT payload_artifact_id FROM results WHERE job_id = ? AND kind = 'product-verify'", (job,))
    payloads = [env.factory.evidence.read_json(r["payload_artifact_id"]) for r in results]
    shots = [s for p in payloads for s in p.get("screenshots", [])]
    assert shots and shots[0]["name"] == "home-390.png" and env.factory.evidence.read(shots[0]["artifact_id"]) == PNG
    assert not any(Path(s["environment"]["browser"]["evidence_dir"]).exists() for s in reviewer.seen)  # moved into the store
    served = [c.skill for c in env.runtime.calls if '"kind": "served"' in c.prompt]
    assert set(served) == {"factory-ui-review", "factory-product-verify"}  # only the browser reviews get the browser
    assert log.read_text().count("close") == len(served)  # the controller closes every session

    try:
        urllib.request.urlopen(first["environment"]["url"], timeout=2)
        reachable = True
    except OSError:
        reachable = False
    assert not reachable  # previews stop when the run returns


def test_without_a_web_build_review_stays_local_checkout(make_env, tmp_path, repo):
    reviewer_calls = []

    def product_verify(request):
        reviewer_calls.append(request)
        return FakeReply(envelope("product-verify", payload={}))

    cli, _ = _fake_cli(tmp_path)
    env = make_env("trivial", responders={"factory-product-verify": product_verify}, **{"browser": {"binary": str(cli)}})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert reviewer_calls and all('"kind": "local-checkout"' in c.prompt for c in reviewer_calls)
    assert all("PLAYWRIGHT_MCP_SANDBOX" not in c.sandbox_policy.env for c in reviewer_calls)


def test_a_missing_browser_binary_falls_back_to_local_checkout(make_env, tmp_path, repo):
    seen = []

    def product_verify(request):
        seen.append(request.prompt)
        return FakeReply(envelope("product-verify", payload={}))

    env, _ = _env(make_env, tmp_path, repo, {"factory-product-verify": product_verify})
    os.remove(env.cfg.factory.browser.binary)
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert seen and all('"kind": "local-checkout"' in p for p in seen)
