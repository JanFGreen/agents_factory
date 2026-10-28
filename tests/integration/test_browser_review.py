"""UI reviewers look at the served web build in a browser; screenshots become evidence."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import yaml

from agent_factory.adapters.fake import FakeReply, envelope
from agent_factory.schemas import HEURISTICS, VISUAL_ASPECTS

from ..conftest import UNIT_CHECK, git_commit_all, implement_by_task, sh

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


def ui_payload(shot: str = "home-390.png", width: int = 390, score: int = 4, design: str = "NOT_APPLICABLE") -> dict:
    return {
        "viewports": [{"name": "mobile", "width": width, "evidence_ids": [shot]}],
        "heuristics": [{"id": h, "status": "PASS", "evidence_ids": [shot]} for h in HEURISTICS],
        "visual": [{"aspect": a, "score": score, "evidence_ids": [shot], "note": f"{a} ok"} for a in VISUAL_ASPECTS],
        "design_direction": {"status": design},
    }


def walkthrough(shift: float = 0, first_ok: bool = True, contrast: float = 7.0, shot: str = "home-390.png",
                design: str = "NOT_APPLICABLE") -> dict:
    """A complete feature walkthrough (mode: feature) on top of the screen review."""
    return {**ui_payload(shot, design=design),
            "flows": [{"name": "first run on a phone", "steps": ["open", "pick mode", "start"], "actions": 2, "status": "PASS",
                       "evidence_ids": [shot]},
                      {"name": "next round", "steps": ["New round"], "actions": 1, "status": "PASS", "evidence_ids": [shot]}],
            "first_screen": {"primary_action_visible": True, "required_choices_visible": first_ok, "evidence_ids": [shot]},
            "layout_shifts": [{"transition": "Start round", "element": "the board", "shift_px": shift, "evidence_ids": [shot]}],
            "interaction_states": [{"control": "option", "state": s, "text_contrast": contrast, "indicator_contrast": 3.5,
                                    "evidence_ids": [shot]} for s in ("hover", "focus", "checked", "checked+hover", "checked+focus")]}


class UiReviewer(Reviewer):
    """A browser UI reviewer; ``replies`` gives (save_screenshot, payload) per task review, the last one repeats.

    Feature walkthroughs (mode: feature) answer from ``feature_replies`` (default: one complete walkthrough).
    """

    def __init__(self, replies: list[tuple[bool, dict]], feature_replies: list[tuple[bool, dict]] | None = None):
        super().__init__("ui-review")
        self.replies = replies
        self.feature_replies = feature_replies or [(True, walkthrough())]
        self.inputs: list[dict] = []
        self.feature_inputs: list[dict] = []

    def __call__(self, request) -> FakeReply:
        inputs = json.loads(request.prompt.split("<inputs>\n", 1)[1].split("\n</inputs>", 1)[0])
        if inputs.get("mode") == "feature":
            self.feature_inputs.append(inputs)
            save, payload = self.feature_replies[min(len(self.feature_inputs), len(self.feature_replies)) - 1]
        else:
            self.inputs.append(inputs)
            save, payload = self.replies[min(len(self.inputs), len(self.replies)) - 1]
        if save and inputs["environment"]["kind"] == "served":
            (Path(inputs["environment"]["browser"]["evidence_dir"]) / "home-390.png").write_bytes(PNG)
        return FakeReply(envelope("ui-review", payload=payload))


def implement_ui(request) -> FakeReply:
    """The usual greeting change plus a page, so the job diff has a UI surface."""
    reply = implement_by_task(request)
    return FakeReply(reply.envelope, edits={**reply.edits, "app/page.html": "<p>hi</p>\n"})


def _push(repo: Path, message: str) -> None:
    git_commit_all(repo, message)
    sh(repo, "git", "push", "-q", "origin", "trunk")


def _ui_results(env, job):
    rows = env.store.all("SELECT verdict, payload_artifact_id FROM results WHERE job_id = ? AND kind = 'ui-review' ORDER BY rowid", (job,))
    return [(r["verdict"], env.factory.evidence.read_json(r["payload_artifact_id"])) for r in rows]


def test_reviewer_gets_the_served_build_a_browser_and_its_screenshots_are_kept(make_env, tmp_path, repo):
    reviewer = Reviewer("product-verify")
    env, log = _env(make_env, tmp_path, repo, {"factory-product-verify": reviewer, "factory-ui-review": UiReviewer([(True, ui_payload())])})
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


def test_ui_review_gets_the_design_direction_and_its_reference_images(make_env, tmp_path, repo):
    (repo / "docs" / "design").mkdir(parents=True)
    (repo / "docs" / "DESIGN.md").write_text("# Design\n\nCalm and focused.\n")
    (repo / "docs" / "design" / "home.png").write_bytes(PNG)
    _push(repo, "design direction")
    ui = UiReviewer([(True, ui_payload(design="PASS"))], [(True, walkthrough(design="PASS"))])
    seen: dict[str, dict] = {}

    def spy(name, reply):
        def respond(request):
            seen[name] = json.loads(request.prompt.split("<inputs>\n", 1)[1].split("\n</inputs>", 1)[0])
            return reply(request) if callable(reply) else reply
        return respond

    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    for skill in ("factory-intake", "factory-spec-author", "factory-implement"):
        env.runtime.responders[skill] = spy(skill, env.runtime.responders[skill])
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    design = ui.inputs[0]["design"]
    assert design["exists"] and design["references"] == ["docs/design/home.png"] and "Calm and focused" in design["text"]
    assert seen["factory-intake"]["design"]["exists"] and "text" not in seen["factory-intake"]["design"]
    assert "Calm and focused" in seen["factory-spec-author"]["design"]["text"]
    assert seen["factory-implement"]["design"]["doc"] == "docs/DESIGN.md"


def test_a_ui_review_without_saved_screenshots_is_rerun_with_a_controller_note(make_env, tmp_path, repo):
    ui = UiReviewer([(False, ui_payload()), (True, ui_payload())])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    results = _ui_results(env, job)
    assert results[0][0] == "UNKNOWN" and "no saved screenshot cited for viewport width(s) [390]" in results[0][1]["controller"]["problem"]
    assert "controller_note" not in ui.inputs[0] and "[390]" in ui.inputs[1]["controller_note"]
    assert results[-1][0] == "PASS"


def test_a_ui_review_that_skips_heuristics_or_cites_unknown_files_is_inconclusive(make_env, tmp_path, repo):
    partial = ui_payload()
    partial["heuristics"] = partial["heuristics"][:3]
    partial["visual"][0]["evidence_ids"] = ["made-up.png"]
    ui = UiReviewer([(True, partial)])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "NEEDS_HUMAN"  # inconclusive on every retry
    problem = _ui_results(env, job)[0][1]["controller"]["problem"]
    assert "heuristics not assessed: ['H4'" in problem and "evidence_ids that are not saved screenshots: ['made-up.png']" in problem


def test_a_ui_pass_with_a_visual_score_below_the_floor_becomes_changes(make_env, tmp_path, repo):
    ui = UiReviewer([(True, ui_payload(score=2)), (True, ui_payload())])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    verdicts = [v for v, _ in _ui_results(env, job)]
    assert verdicts[0] == "CHANGES" and verdicts[-1] == "PASS"
    findings = env.store.all("SELECT finding_id, blocking, status FROM findings WHERE job_id = ? AND kind = 'ui-review'", (job,))
    assert {f["finding_id"] for f in findings} == {f"CTRL-visual-{a}" for a in VISUAL_ASPECTS}
    assert all(f["blocking"] == 1 and f["status"] == "fixed" for f in findings)  # closed by the next review, not by the builder


def test_an_existing_design_doc_must_be_checked_even_without_a_browser(make_env, tmp_path, repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "DESIGN.md").write_text("# Design\n")
    _push(repo, "design doc")
    ui = UiReviewer([(False, {"design_direction": {"status": "NOT_APPLICABLE"}}), (False, {"design_direction": {"status": "PASS"}})])
    env = make_env("trivial", responders={"factory-ui-review": ui, "factory-implement": implement_ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    results = _ui_results(env, job)
    assert results[0][0] == "UNKNOWN" and "docs/DESIGN.md exists" in results[0][1]["controller"]["problem"]
    assert results[-1][0] == "PASS" and ui.inputs[0]["environment"]["kind"] == "local-checkout"


def test_screens_a_later_task_mounts_need_screenshots_but_no_visual_scores(make_env, tmp_path, repo):
    unmounted = {"viewports": [{"name": "mobile", "width": 390, "evidence_ids": ["home-390.png"]}],
                 "touched_screens_rendered": False, "not_rendered_reason": "App does not mount SetupPanel yet (TASK-08)"}
    ui = UiReviewer([(True, unmounted)])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert [v for v, _ in _ui_results(env, job)] == ["PASS", "PASS"]  # the task review, then the feature walkthrough
    assert env.events(job, "ui_review.not_rendered")


def test_not_rendered_still_needs_the_screenshots_of_what_the_app_shows(make_env, tmp_path, repo):
    unmounted = {"viewports": [{"name": "mobile", "width": 390, "evidence_ids": []}],
                 "touched_screens_rendered": False, "not_rendered_reason": "not mounted yet"}
    ui = UiReviewer([(False, unmounted), (True, ui_payload())])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert [v for v, _ in _ui_results(env, job)][0] == "UNKNOWN"



def _feature_results(env, job):
    rows = env.store.all("SELECT verdict, payload_artifact_id FROM results WHERE job_id = ? AND kind = 'ui-review' AND task_id IS NULL"
                         " ORDER BY rowid", (job,))
    return [(r["verdict"], env.factory.evidence.read_json(r["payload_artifact_id"])) for r in rows]


def test_the_feature_walkthrough_runs_once_at_acceptance_and_guards_integration(make_env, tmp_path, repo):
    ui = UiReviewer([(True, ui_payload())])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    assert len(ui.feature_inputs) == 1 and ui.feature_inputs[0]["limits"]["max_layout_shift_px"] == 8
    assert [v for v, _ in _feature_results(env, job)] == ["PASS"]
    assert env.store.job_context(job)["feature_ui_required"] is True


def test_a_walkthrough_without_flows_or_measurements_is_rerun_with_what_is_missing(make_env, tmp_path, repo):
    partial = {**walkthrough(), "flows": [], "layout_shifts": []}
    ui = UiReviewer([(True, ui_payload())], [(True, partial), (True, walkthrough())])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    note = ui.feature_inputs[1]["controller_note"]
    assert "flows:" in note and "layout_shifts:" in note
    assert [v for v, _ in _feature_results(env, job)] == ["PASS"]  # the inconclusive try is not recorded as a verdict


def test_measurements_over_the_limits_block_and_reopen_a_task(make_env, tmp_path, repo):
    bad = walkthrough(shift=68, first_ok=False, contrast=1.6)
    ui = UiReviewer([(True, ui_payload())], [(True, bad), (True, walkthrough())])
    env, _ = _env(make_env, tmp_path, repo, {"factory-ui-review": ui})
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE"
    verdicts = [v for v, _ in _feature_results(env, job)]
    assert verdicts == ["CHANGES", "PASS"]
    ids = {r["finding_id"] for r in env.store.all("SELECT finding_id FROM findings WHERE job_id = ? AND task_id IS NULL"
                                                   " AND kind = 'ui-review'", (job,))}
    assert {"CTRL-first-screen", "CTRL-shift-1"} <= ids and any(i.startswith("CTRL-contrast-") for i in ids)
    assert env.calls("factory-implement") == 2  # the owner task was rebuilt with the findings
