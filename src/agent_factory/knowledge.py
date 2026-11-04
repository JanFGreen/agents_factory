"""Repository knowledge the factory reads and keeps current: a graphify graph and Obsidian vaults.

A repository opts in by tracking `graphify-out/graph.json` (a graph of code and docs) and/or a
folder with an `.obsidian/` config (a vault). When either is present the factory:

- plans with it: the decomposition review and each builder get, per file in a task's scope, the
  files that import, call or reference it (from a fresh code graph built offline in the sandbox),
  so callers of a changed signature are known instead of rediscovered by a reviewer;
- keeps it current at integration: the code graph is rebuilt (no model), docs a job changed are
  re-extracted by the small model graphify recommends for it, communities are named, the graph
  is exported into each vault, and every wikilink in a vault must resolve. Missing tooling stops
  the job (fail-closed); a stale graph is never merged silently.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .adapters.claude import ClaudeRuntime
from .adapters.cli_base import AGENT_ENV_KEYS
from .sandbox import Sandbox, SandboxPolicy, scrubbed_env

GRAPH = "graphify-out/graph.json"
REPORT = "graphify-out/GRAPH_REPORT.md"
# Edges that make the source file depend on the target file; `contains` and `method` stay inside one file.
DEPENDS = {"imports", "imports_from", "calls", "references", "re_exports", "indirect_call", "inherits", "extends", "dynamic_import"}
DOC_SUFFIXES = (".md", ".mdx", ".txt", ".rst")
DOCS_MODEL = "haiku"  # graphify: haiku is enough for naming communities and short doc extraction
_WIKILINK = re.compile(r"(?<!!)\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
_FENCE = re.compile(r"```.*?```", re.DOTALL)


class KnowledgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Knowledge:
    graph: bool
    vaults: tuple[str, ...]

    @property
    def present(self) -> bool:
        return self.graph or bool(self.vaults)


def detect(tracked: Iterable[str]) -> Knowledge:
    files = list(tracked)
    vaults = sorted({f.split("/.obsidian/", 1)[0] if not f.startswith(".obsidian/") else "." for f in files if "/.obsidian/" in f"/{f}"})
    return Knowledge(graph=GRAPH in files, vaults=tuple(vaults))


def is_doc(path: str) -> bool:
    return path.lower().endswith(DOC_SUFFIXES) and not path.startswith("graphify-out/")


def dependents(graph_path: Path, files: Iterable[str], limit: int = 40) -> dict[str, list[str]]:
    """For each file, the other files whose code imports, calls or references it."""
    graph = json.loads(graph_path.read_text())
    where = {n["id"]: n.get("source_file") for n in graph.get("nodes", [])}
    wanted = set(files)
    found: dict[str, set[str]] = {f: set() for f in wanted}
    for edge in graph.get("edges") or graph.get("links") or []:
        if edge.get("relation") not in DEPENDS:
            continue
        source, target = where.get(edge.get("source")), where.get(edge.get("target"))
        if target in wanted and source and source != target:
            found[target].add(source)
    return {f: sorted(deps)[:limit] for f, deps in found.items() if deps}


def dependency_map(graph_path: Path, max_chars: int = 40000) -> dict[str, list[str]]:
    """Every file that others depend on, with its dependents: what a planner needs to scope callers of a change."""
    graph = json.loads(graph_path.read_text())
    where = {n["id"]: n.get("source_file") for n in graph.get("nodes", [])}
    found: dict[str, set[str]] = {}
    for edge in graph.get("edges") or graph.get("links") or []:
        source, target = where.get(edge.get("source")), where.get(edge.get("target"))
        if edge.get("relation") in DEPENDS and source and target and source != target:
            found.setdefault(target, set()).add(source)
    result, size = {}, 0
    for target in sorted(found, key=lambda f: (-len(found[f]), f)):  # the most depended-on files first
        entry = sorted(found[target])
        size += len(target) + sum(len(e) + 4 for e in entry)
        if size > max_chars:
            break
        result[target] = entry
    return dict(sorted(result.items()))


def broken_wikilinks(root: Path, vault: str) -> list[str]:
    """Wikilinks in the vault's notes that name no file of the vault (Obsidian resolves by name or path)."""
    base = root / vault
    notes = [p for p in base.rglob("*") if p.is_file() and ".obsidian" not in p.relative_to(base).parts]
    names = {p.stem.lower() for p in notes} | {p.name.lower() for p in notes}
    names |= {str(p.relative_to(base).with_suffix("")).lower() for p in notes} | {str(p.relative_to(base)).lower() for p in notes}
    broken = []
    for note in notes:
        if note.suffix != ".md":
            continue
        for target in _WIKILINK.findall(_FENCE.sub("", note.read_text(errors="replace"))):
            if target.strip().lower() not in names:
                broken.append(f"{note.relative_to(root)}: [[{target.strip()}]]")
    return broken


class Graphify:
    """The pinned graphify CLI, always run in the sandbox: offline for the code graph, model access only for docs."""

    def __init__(self, binary: Path, sandbox: Sandbox, scratch: Path, timeout_seconds: int = 900, docs_timeout_seconds: int = 3600):
        self.binary, self.sandbox, self.scratch, self.timeout = binary, sandbox, scratch, timeout_seconds
        self.docs_timeout = docs_timeout_seconds  # a cold extraction cache reads every doc once
        self.docs_partial: str | None = None

    def available(self) -> bool:
        return self.binary.is_file()

    def code_graph(self, worktree: Path, out: Path) -> Path:
        """A fresh graph of the code (tree-sitter, no model) written outside the worktree."""
        out.mkdir(parents=True, exist_ok=True)
        self._run([str(self.binary), "extract", ".", "--code-only", "--no-cluster", "--out", str(out)], worktree, writable=(out,))
        return out / GRAPH

    def refresh(self, worktree: Path, *, docs_changed: bool, cache: Path, seed: Path | None = None) -> None:
        """Rebuild the tracked graph in the worktree; re-extract docs with the small model when they changed.

        graphify keys its extraction cache by file content; keeping it per repository (`cache`) across jobs
        means the model reads only the docs a job added or changed, not the whole corpus again.
        """
        self.docs_partial = None
        local = worktree / "graphify-out" / "cache"
        if not cache.is_dir() and seed is not None and seed.is_dir():  # first job: start from the maintainer's own build
            shutil.copytree(seed, cache)
        if cache.is_dir():
            shutil.copytree(cache, local, dirs_exist_ok=True)
        self._run([str(self.binary), "update", ".", "--force"], worktree, writable=(worktree,))
        if docs_changed:
            self.docs_partial = self._extract_docs(worktree)
        # update re-clusters and saves each new community under a hub file's name, which label then keeps:
        # drop those names so the small model names every community again
        (worktree / "graphify-out" / ".graphify_labels.json").unlink(missing_ok=True)
        self._run([str(self.binary), "label", ".", "--backend=claude-cli"], worktree, writable=(worktree,), model=True)
        if local.is_dir():
            shutil.copytree(local, cache, dirs_exist_ok=True)

    def _extract_docs(self, worktree: Path) -> str | None:
        """Re-extract docs; None when complete, else what the small model left out (the code graph stays current).

        graphify refuses to replace a graph with a smaller one when the model omitted files; smaller chunks
        usually make the small model cover every file, so that is tried once before reporting a partial pass.
        """
        for budget in ("12000", "5000"):
            try:
                self._run([str(self.binary), "extract", ".", "--backend", "claude-cli", "--token-budget", budget], worktree,
                          writable=(worktree,), model=True, timeout=self.docs_timeout)
                return None
            except KnowledgeError as exc:
                if "Refusing to overwrite" not in str(exc):
                    raise
                partial = str(exc)
        return partial[-400:]

    def export_vault(self, worktree: Path, vault: str) -> None:
        """Export the graph into the vault's generated ``graph/`` folder, from empty.

        Live finding (LearnEnglish F6a): exporting over the previous export left notes numbered by an older graph
        (``SignIn()_1.md`` next to names differing only in case, which a case-insensitive disk keeps apart by number);
        24 links of the new notes pointed at files that were gone. The folder is the controller's own output.
        """
        target = worktree / vault / "graph"
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        self._run([str(self.binary), "export", "obsidian", "--graph", GRAPH, "--dir", str(target)], worktree, writable=(worktree,))
        report = worktree / REPORT
        if report.is_file():  # the vault's notes link [[GRAPH_REPORT]]
            (target / "GRAPH_REPORT.md").write_text(report.read_text())

    def _run(self, argv: list[str], cwd: Path, *, writable: tuple[Path, ...], model: bool = False, timeout: int | None = None) -> None:
        run_dir = self.scratch / f"graphify-{os.getpid()}-{len(list(self.scratch.glob('graphify-*')))}"
        home, tmp = run_dir / "home", run_dir / "tmp"
        for path in (home, tmp):
            path.mkdir(parents=True, exist_ok=True)
        env_extra = {"GRAPHIFY_CLAUDE_CLI_MODEL": DOCS_MODEL, "GRAPHIFY_QUERY_LOG_DISABLE": "1", "PATH": os.environ.get("PATH", "")}
        if model:
            # The claude CLI authenticates (and refreshes its OAuth session) from the user's config and the system
            # temp dirs, exactly as the factory's agents do (adapters/cli_base.py, adapters/claude.py).
            real_home = Path.home()
            claude_state = tuple(real_home / p for p in ClaudeRuntime.home_writable)
            system_tmp = (Path(os.environ.get("TMPDIR", "/tmp")), Path("/private/tmp"), Path("/private/var/folders"))
            policy = SandboxPolicy(writable=(*writable, run_dir, *claude_state, *system_tmp), network="full", env=env_extra)
            env = {**{k: os.environ[k] for k in AGENT_ENV_KEYS if k in os.environ}, "HOME": str(real_home),
                   "PYTHONDONTWRITEBYTECODE": "1", **env_extra}
        else:
            policy = SandboxPolicy(writable=(*writable, run_dir), network="none", env=env_extra)
            env = scrubbed_env(policy, home, tmp)
        try:
            proc = subprocess.run(self.sandbox.wrap(argv, policy), cwd=cwd, env=env, capture_output=True, text=True,
                                  timeout=timeout or self.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            raise KnowledgeError(f"graphify {' '.join(argv[1:3])} timed out after {exc.timeout:.0f}s") from exc
        if proc.returncode != 0:
            raise KnowledgeError(f"graphify {argv[1]} failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-600:]}")
        if model and "failed:" in proc.stderr:  # graphify degrades a failed model call to placeholders and exits 0
            raise KnowledgeError(f"graphify {argv[1]}: the model call failed: {proc.stderr.strip()[-600:]}")
