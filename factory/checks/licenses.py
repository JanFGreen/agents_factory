"""Trusted licence check for npm lockfiles (`licenses` check in node.yaml / vite.yaml).

Every package the product ships (not `dev`) must carry a licence from the allow list, read
from `package-lock.json` (or the installed package.json when the lockfile omits it). Build
tools (`dev`) are not distributed, so only licences that bind the user of the tool are
refused for them. Exceptions are operator decisions recorded in the repository's protected
`.license-exceptions.json`: {"<package name>": "<reason>"}. Runs offline; exit 1 lists
every refused package.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

POLICY = Path(__file__).with_name("licenses.json")
EXCEPTIONS = ".license-exceptions.json"
_TOKEN = re.compile(r"\(|\)|[^\s()]+")


def _allowed(expression: str, ok: set[str]) -> bool:
    """SPDX expression: OR needs one allowed side, AND needs both; `X WITH exception` is judged by X."""
    tokens = _TOKEN.findall(expression)
    pos = 0

    def term() -> bool:
        nonlocal pos
        token = tokens[pos]
        pos += 1
        if token == "(":
            value = either()
            pos += 1  # ")"
        else:
            value = token.rstrip("+") in ok
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 2
        return value

    def both() -> bool:
        nonlocal pos
        value = term()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            value = term() and value
        return value

    def either() -> bool:
        nonlocal pos
        value = both()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            value = both() or value
        return value

    try:
        return bool(tokens) and either() and pos == len(tokens)
    except IndexError:
        return False


def _licence(path: str, entry: dict, root: Path) -> str | None:
    found = entry.get("license")
    if found is None and (root / path / "package.json").is_file():
        found = json.loads((root / path / "package.json").read_text()).get("license")
    if isinstance(found, dict):  # legacy {"type": "MIT"}
        found = found.get("type")
    return found if isinstance(found, str) and found.strip() else None


def refused(root: Path, policy: dict) -> list[str]:
    lock = json.loads((root / "package-lock.json").read_text())
    exceptions_file = root / EXCEPTIONS
    exceptions = json.loads(exceptions_file.read_text()) if exceptions_file.is_file() else {}
    shipped, refused_for_tools = set(policy["shipped"]), set(policy["refused_for_tools"])
    problems = []
    for path, entry in sorted(lock.get("packages", {}).items()):
        if not path or entry.get("link"):
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[-1]
        if name in exceptions:
            continue
        licence = _licence(path, entry, root)
        if entry.get("dev"):
            if licence and any(_allowed(licence, {bad}) for bad in refused_for_tools) and not _allowed(licence, shipped):
                problems.append(f"{name}@{entry.get('version', '?')} (build tool): {licence} binds its users")
        elif licence is None:
            problems.append(f"{name}@{entry.get('version', '?')}: no licence; the product may not use it")
        elif not _allowed(licence, shipped):
            problems.append(f"{name}@{entry.get('version', '?')}: {licence} is not on the allow list")
    return problems


def main() -> int:
    root = Path.cwd()
    if not (root / "package-lock.json").is_file():
        print("licenses: no package-lock.json; nothing to check")
        return 0
    problems = refused(root, json.loads(POLICY.read_text()))
    for problem in problems:
        print(f"licenses: {problem}")
    if problems:
        print(f"licenses: {len(problems)} refused. Replace the package, or record the operator's decision in {EXCEPTIONS}.")
        return 1
    print("licenses: every shipped package has an allowed licence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
