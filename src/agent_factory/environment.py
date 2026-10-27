"""Toolchain facts the factory must not take from whatever shell started it.

- The Claude CLI must be new enough for the configured models (live finding: CLI 2.1.74 could
  not call the 5.5 models; the API rejected every request and the job lost 30 minutes).
- A repo's Node version comes from its own ``.nvmrc`` (or ``engines.node``), resolved to an
  installed nvm toolchain, instead of the first ``node`` on the controller's PATH.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text or "")
    return tuple(int(x) for x in match.groups()) if match else None  # type: ignore[return-value]


def claude_cli_version(binary: str) -> tuple[int, int, int] | None:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(out)


def cli_too_old(binary: str, minimum: str) -> str | None:
    """Reason the CLI cannot be used, or None when it is new enough."""
    found, wanted = claude_cli_version(binary), parse_version(minimum)
    if found is None:
        return f"cannot read the version of {binary}"
    if wanted is not None and found < wanted:
        return (f"Claude CLI {'.'.join(map(str, found))} is older than {minimum}, the minimum for the configured "
                "models (runtime.min_cli_version); update it (`claude update` or `npm i -g @anthropic-ai/claude-code`)")
    return None


def wanted_node_major(repo: Path) -> int | None:
    nvmrc = repo / ".nvmrc"
    if nvmrc.exists():
        match = re.search(r"(\d+)", nvmrc.read_text())
        if match:
            return int(match.group(1))
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            engines = json.loads(pkg.read_text()).get("engines", {}).get("node", "")
        except json.JSONDecodeError:
            return None
        match = re.search(r"(\d+)", engines)
        if match:
            return int(match.group(1))
    return None


def node_bin_dir(repo: Path, nvm_dir: Path | None = None) -> Path | None:
    """bin dir of the newest installed nvm Node with the repo's major version; None if the repo pins none.

    Raises FileNotFoundError when the repo pins a version that is not installed.
    """
    major = wanted_node_major(repo)
    if major is None:
        return None
    root = (nvm_dir or Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))) / "versions" / "node"
    candidates = []
    if root.is_dir():
        for child in root.iterdir():
            version = parse_version(child.name)
            if version and version[0] == major and (child / "bin" / "node").exists():
                candidates.append((version, child / "bin"))
    if not candidates:
        raise FileNotFoundError(f"the repo pins Node {major} (.nvmrc/engines) but no nvm Node {major} is installed in {root}")
    return max(candidates)[1]
