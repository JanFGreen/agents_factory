"""A repository with a graphify graph or an Obsidian vault: the factory plans with them and keeps them current."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_factory import knowledge

from ..conftest import git_commit_all, request_inputs, sh

GRAPH = {"nodes": [{"id": "greet", "source_file": "app/greet.py"}, {"id": "notes", "source_file": "app/notes.py"},
                   {"id": "test_greet", "source_file": "tests/test_greet.py"}],
         "edges": [{"source": "test_greet", "target": "greet", "relation": "imports"},
                   {"source": "notes", "target": "greet", "relation": "calls"},
                   {"source": "greet", "target": "greet", "relation": "contains"}]}

# Stands in for the pinned graphify: records each call in graphify-out/calls.txt and writes what graphify writes.
FAKE = f"""#!{sys.executable}
import json, sys
from pathlib import Path
args = sys.argv[1:]
graph = {json.dumps(GRAPH)!r}
out = Path("graphify-out")
if args[0] == "extract" and "--out" in args:
    target = Path(args[args.index("--out") + 1]) / "graphify-out"
    target.mkdir(parents=True, exist_ok=True)
    (target / "graph.json").write_text(graph)
    sys.exit(0)
out.mkdir(exist_ok=True)
with open(out / "calls.txt", "a") as log:
    log.write(" ".join(args[:2]) + "\\n")
if args[0] == "extract" and Path("docs/PARTIAL.md").exists():
    sys.exit("[graphify extract] error: Refusing to overwrite a complete graph with a partial one.")
if args[0] == "update":
    (out / "graph.json").write_text(graph)
    (out / "GRAPH_REPORT.md").write_text("# Graph Report - " + Path.cwd().name + "  (2026-11-01)\\n\\n## Summary\\n")
if args[0] == "export":
    target = Path(args[args.index("--dir") + 1])
    target.mkdir(parents=True, exist_ok=True)
    (target / "greet.md").write_text("[[notes]]\\n")
    (target / "notes.md").write_text("[[greet]]\\n")
    if Path("docs/BREAK_EXPORT.md").exists():
        (target / "broken.md").write_text("[[greet_9]]\\n")
"""


def _with_knowledge(env, repo: Path, tmp_path: Path, *, vault_note: str = "See [[PRODUCT]] and [[GRAPH_REPORT]].\n") -> None:
    (repo / "graphify-out").mkdir()
    (repo / "graphify-out" / "graph.json").write_text(json.dumps(GRAPH))
    (repo / "docs" / ".obsidian").mkdir(parents=True)
    (repo / "docs" / ".obsidian" / "app.json").write_text("{}\n")
    (repo / "docs" / "PRODUCT.md").write_text("# Product\n")
    (repo / "docs" / "README.md").write_text(vault_note)
    (repo / ".gitignore").write_text("docs/graph/\n")
    git_commit_all(repo, "knowledge graph and vault")
    sh(repo, "git", "push", "-q", "origin", "trunk")
    fake = tmp_path / "graphify"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    env.factory.graphify.binary = fake


def test_detects_a_graph_and_vaults_from_tracked_files():
    found = knowledge.detect(["graphify-out/graph.json", "docs/.obsidian/app.json", "notes/.obsidian/x.json", "src/a.ts"])
    assert found.graph and found.vaults == ("docs", "notes") and found.present
    assert not knowledge.detect(["src/a.ts", "graphify-out/cache/x"]).present


def test_dependents_are_the_files_that_use_a_file(tmp_path):
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(GRAPH))
    assert knowledge.dependents(path, ["app/greet.py", "app/notes.py"]) == {"app/greet.py": ["app/notes.py", "tests/test_greet.py"]}


def test_broken_wikilinks_name_no_note_of_the_vault(tmp_path):
    vault = tmp_path / "docs"
    (vault / "graph").mkdir(parents=True)
    (vault / "graph" / "GRAPH_REPORT.md").write_text("x")
    (vault / "PRODUCT.md").write_text("[[GRAPH_REPORT]] [[graph/GRAPH_REPORT|report]] [[PRODUCT#Modes]] ![[pic.png]]\n"
                                      "```\n[[inside code]]\n```\n[[Missing note]]\n"
                                      "A format example: `[[Words/<name>|<lemma>]]` and ``[[x]]``, not links.\n")
    assert knowledge.broken_wikilinks(tmp_path, "docs") == ["docs/PRODUCT.md: [[Missing note]]"]


def test_planning_gets_dependents_and_integration_keeps_graph_and_vault_current(make_env, repo, tmp_path):
    env = make_env("standard")
    _with_knowledge(env, repo, tmp_path)
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE", env.store.job(job)["blocked_reason"]
    plan = next(request_inputs(c) for c in env.runtime.calls if c.skill == "factory-plan")
    assert plan["dependency_map"] == {"app/greet.py": ["app/notes.py", "tests/test_greet.py"]}
    review = next(request_inputs(c) for c in env.runtime.calls if c.skill == "factory-decomposition-review")
    assert review["knowledge"]["vaults"] == ["docs"] and review["knowledge"]["graph"]
    assert any(deps.get("app/greet.py") == ["app/notes.py", "tests/test_greet.py"] for deps in review["dependents"].values())
    build = next(request_inputs(c) for c in env.runtime.calls if c.skill == "factory-implement")
    assert "knowledge" in build and "dependents" in build
    wt = Path(env.store.job(job)["worktree"])
    calls = (wt / "graphify-out" / "calls.txt").read_text().split("\n")
    # the job exported its spec as markdown under openspec/: docs changed, so the small model re-extracts them
    assert calls[:3] == ["update .", "extract .", "label ."] and "export obsidian" in calls
    assert (wt / "graphify-out" / "GRAPH_REPORT.md").read_text().startswith("# Graph Report - user-checkout  (2026-11-01)")
    assert (wt / "docs" / "graph" / "GRAPH_REPORT.md").is_file()
    assert "graphify-out/GRAPH_REPORT.md" in sh(wt, "git", "show", "--name-only", "--format=", "HEAD")
    assert env.events(job, "knowledge.refreshed")


def test_a_broken_vault_link_sends_the_job_back_to_rework(make_env, repo, tmp_path):
    env = make_env("trivial")
    _with_knowledge(env, repo, tmp_path, vault_note="See [[No such note]].\n")
    job = env.submit()
    env.factory.run(job)
    refreshed = env.events(job, "knowledge.refreshed")
    assert refreshed and "[[No such note]]" in json.loads(refreshed[0]["payload_json"])["broken_links"][0]
    assert any("resolve to no note" in json.loads(e["payload_json"]).get("reason", "") for e in env.events(job, "state.transition"))


def test_missing_graphify_stops_the_job_instead_of_leaving_the_graph_stale(make_env, repo, tmp_path):
    env = make_env("trivial")
    _with_knowledge(env, repo, tmp_path)
    env.factory.graphify.binary = tmp_path / "not-installed"
    job = env.submit()
    assert env.factory.run(job) == "BLOCKED_ENV"
    assert "uv sync --project tools/graphify" in env.store.job(job)["blocked_reason"]


def test_a_partial_docs_pass_keeps_the_code_graph_current_and_is_reported(make_env, repo, tmp_path):
    """Live finding (LearnEnglish F4b): haiku left some docs out, graphify refused the smaller graph, and the job stopped."""
    env = make_env("trivial")
    _with_knowledge(env, repo, tmp_path)
    (repo / "docs" / "PARTIAL.md").write_text("# Partial\n")
    git_commit_all(repo, "a doc the model leaves out")
    sh(repo, "git", "push", "-q", "origin", "trunk")
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE", env.store.job(job)["blocked_reason"]
    calls = (Path(env.store.job(job)["worktree"]) / "graphify-out" / "calls.txt").read_text().split("\n")
    assert calls[:4] == ["update .", "extract .", "extract .", "label ."]  # retried once with smaller chunks
    assert "Refusing to overwrite" in json.loads(env.events(job, "knowledge.refreshed")[0]["payload_json"])["docs_partial"]


def test_the_vault_export_starts_from_an_empty_graph_folder(make_env, repo, tmp_path):
    """Live finding (LearnEnglish F6a): notes of an older export stayed next to the new ones and 24 links broke."""
    env = make_env("trivial")
    _with_knowledge(env, repo, tmp_path)
    (repo / ".gitignore").write_text("")
    (repo / "docs" / "graph").mkdir()
    (repo / "docs" / "graph" / "SignIn()_1.md").write_text("[[gone_7]]\n")  # numbered by an older graph
    git_commit_all(repo, "an older export")
    sh(repo, "git", "push", "-q", "origin", "trunk")
    job = env.submit()
    assert env.factory.run(job) == "READY_TO_MERGE", env.store.job(job)["blocked_reason"]
    wt = Path(env.store.job(job)["worktree"])
    assert not (wt / "docs" / "graph" / "SignIn()_1.md").exists() and (wt / "docs" / "graph" / "greet.md").is_file()


def test_a_broken_link_in_the_exported_notes_stops_the_job_without_a_rebuild(make_env, repo, tmp_path):
    """No builder may edit the generated notes, so their broken links are an environment problem, not a rework reason."""
    env = make_env("trivial")
    _with_knowledge(env, repo, tmp_path)
    (repo / "docs" / "BREAK_EXPORT.md").write_text("marker\n")
    git_commit_all(repo, "export marker")
    sh(repo, "git", "push", "-q", "origin", "trunk")
    job = env.submit()
    assert env.factory.run(job) == "BLOCKED_ENV"
    assert "exported graph notes" in env.store.job(job)["blocked_reason"] and "[[greet_9]]" in env.store.job(job)["blocked_reason"]
    assert env.calls("factory-implement") == 1  # built once, never sent back for the generated notes
