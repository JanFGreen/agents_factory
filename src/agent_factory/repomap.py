"""The exported surface of a repository, read without a model: what analysis agents would otherwise read file by file.

Live finding (LearnEnglish F5b): the planner read 18 whole files and grepped for `export (function|const|interface|type)`
to learn the shapes it plans against; the decomposition reviewer then read most of the same files again. Each file a
read-only agent reads is written to its prompt cache (Opus: $5/M) and re-read on every later turn. The map gives them
the exported signatures and type bodies up front, so they read a file only for the body of a function they need.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".py")
_TS_EXPORT = re.compile(r"^export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
                        r"(?:function\*?|const|let|class|abstract\s+class|interface|type|enum)\b")
_TS_BODY = re.compile(r"(?:interface\s+\w+[^{]*|type\s+\w+[^=]*=\s*|enum\s+\w+\s*)\{\s*$")
_PY_DEF = re.compile(r"^(?:async\s+)?(?:def|class)\s+[A-Za-z]|^    (?:async\s+)?def\s+[a-zA-Z]")
_TEST = re.compile(r"(^|/)(tests?|__tests__|e2e)/|\.(test|spec)\.[jt]sx?$|(^|/)test_[^/]*\.py$|_test\.py$|\.d\.ts$")
_SKIP_DIRS = ("graphify-out/", "openspec/", "node_modules/", "dist/", "build/")
MAX_LINE = 200
MAX_BODY_LINES = 30
MAX_SIGNATURE_LINES = 8


def _signature(lines: list[str], i: int) -> tuple[list[str], int]:
    """The declaration at ``i``, joined over lines until its parentheses close; returns the lines and the last index."""
    out, depth, j = [], 0, i
    while j < len(lines) and j - i < MAX_SIGNATURE_LINES:
        out.append(lines[j].rstrip()[:MAX_LINE])
        depth += lines[j].count("(") - lines[j].count(")")
        if depth <= 0:
            break
        j += 1
    return out, j


def _ts_surface(lines: list[str]) -> list[str]:
    found, i = [], 0
    while i < len(lines):
        if _TS_EXPORT.match(lines[i]):
            sig, i = _signature(lines, i)
            found += sig
            if _TS_BODY.search(sig[-1]):  # interface / object type / enum: its members are the contract
                body, j = [], i + 1
                while j < len(lines) and not lines[j].startswith("}") and len(body) < MAX_BODY_LINES:
                    body.append(lines[j].rstrip()[:MAX_LINE])
                    j += 1
                found += body + (["  ..."] if j < len(lines) and not lines[j].startswith("}") else []) + ["}"]
                i = j
        i += 1
    return found


def _py_surface(lines: list[str]) -> list[str]:
    found, i = [], 0
    while i < len(lines):
        if _PY_DEF.match(lines[i]):
            sig, i = _signature(lines, i)
            found += sig
        i += 1
    return found


def api_map(root: Path, files: Iterable[str], max_chars: int = 60000) -> dict:
    """``{"files": {path: surface}, "omitted": [...]}`` for the tracked, non-test source files under ``root``."""
    surfaces, omitted, size = {}, [], 0
    for path in sorted(files):
        if not path.endswith(SOURCE_SUFFIXES) or _TEST.search(path) or path.startswith(_SKIP_DIRS):
            continue
        try:
            lines = (root / path).read_text(errors="replace").splitlines()
        except OSError:
            continue
        found = _py_surface(lines) if path.endswith(".py") else _ts_surface(lines)
        if not found:
            continue
        text = "\n".join(found)
        if size + len(path) + len(text) > max_chars:
            omitted.append(path)
            continue
        surfaces[path] = text
        size += len(path) + len(text)
    return {"files": surfaces, "omitted": omitted,
            "note": "Exported signatures and type bodies of each non-test source file, extracted by the controller "
                    "from the job's base. Read a file only for a body or detail this map does not show."}
