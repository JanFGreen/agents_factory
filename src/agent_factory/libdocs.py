"""Current library documentation for the builder, fetched by the controller from Context7 (context7.com).

Builders run without network beyond the model API, and their memory of a library's API is often older than the
installed version (EP-12). The controller reads which dependencies the task's files import, fetches each one's docs
once per installed version, and hands them to the builder as untrusted data next to the version it must match.
Off by default (`docs.context7`): it sends the dependency names, never code, to an external service.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

API = "https://context7.com/api/v1"
_JS_IMPORT = re.compile(r"""(?:from\s+|require\(\s*|import\(\s*|import\s+)["']((?:@[\w.-]+/)?[\w.-]+)""")
_PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.M)

Fetch = Callable[[str], str]


def http_fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "agent-factory"})
    if key := os.environ.get("CONTEXT7_API_KEY"):
        request.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 (fixed https host)
        return response.read(400_000).decode("utf-8", "replace")


def installed(repo: Path) -> dict[str, str]:
    """Declared runtime and dev dependencies with their versions (package.json, pyproject.toml)."""
    deps: dict[str, str] = {}
    package = repo / "package.json"
    if package.is_file():
        data = json.loads(package.read_text())
        for section in ("dependencies", "devDependencies"):
            deps.update({name: str(v).lstrip("^~=") for name, v in (data.get(section) or {}).items()})
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        for name, version in re.findall(r'^\s*"([A-Za-z0-9_.-]+)\s*(?:[=<>~!]=?\s*([\w.]+))?', pyproject.read_text(), re.M):
            deps[name.lower().replace("-", "_")] = version
    return deps


def imported(repo: Path, paths: list[str], deps: dict[str, str]) -> list[str]:
    """Dependencies the given files import, in order of first use."""
    seen: dict[str, None] = {}
    for rel in paths:
        path = repo / rel
        if not path.is_file() or path.stat().st_size > 400_000:
            continue
        text = path.read_text(errors="replace")
        names = _PY_IMPORT.findall(text) if path.suffix == ".py" else _JS_IMPORT.findall(text)
        for name in names:
            key = name.lower() if path.suffix == ".py" else name
            if key in deps:
                seen[key] = None
    return list(seen)


def docs_for(repo: Path, paths: list[str], cache: Path, *, max_libraries: int = 3, max_tokens: int = 3000,
             fetch: Fetch = http_fetch) -> list[dict[str, str]]:
    """Docs of the installed libraries the task's files use; a library Context7 does not know, or an error, is skipped."""
    deps = installed(repo)
    out = []
    for name in imported(repo, paths, deps)[:max_libraries]:
        version = deps[name] or "unknown"
        target = cache / f"{re.sub(r'[^\w.@-]', '_', name)}@{version}.txt"
        try:
            if not target.is_file():
                found = json.loads(fetch(f"{API}/search?query={urllib.parse.quote(name)}")).get("results") or []
                if not found:
                    continue
                query = urllib.parse.urlencode({"type": "txt", "tokens": max_tokens})
                text = fetch(f"{API}{found[0]['id']}?{query}")
                cache.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            out.append({"library": name, "installed_version": version, "docs": target.read_text()[: max_tokens * 4]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out
