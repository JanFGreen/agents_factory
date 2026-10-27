"""Toolchain checks: minimum Claude CLI version and the repo's pinned Node."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agent_factory.environment import cli_too_old, node_bin_dir, parse_version, wanted_node_major


def _fake_cli(tmp_path: Path, version: str) -> str:
    cli = tmp_path / "claude"
    cli.write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    return str(cli)


def test_cli_older_than_the_model_minimum_is_refused(tmp_path):
    """Live finding: CLI 2.1.74 could not call the 5.5 models and every agent call failed."""
    assert "older than 2.1.280" in (cli_too_old(_fake_cli(tmp_path, "2.1.74"), "2.1.280") or "")
    assert cli_too_old(_fake_cli(tmp_path, "2.1.311"), "2.1.280") is None
    assert cli_too_old(_fake_cli(tmp_path, "2.10.0"), "2.1.280") is None  # numeric, not string, comparison


def test_unreadable_cli_version_is_refused(tmp_path):
    assert "cannot read" in (cli_too_old(_fake_cli(tmp_path, "unknown"), "2.1.280") or "")
    assert "cannot read" in (cli_too_old(str(tmp_path / "missing"), "2.1.280") or "")


def _nvm(tmp_path: Path, *versions: str) -> Path:
    root = tmp_path / "nvm"
    for version in versions:
        bin_dir = root / "versions" / "node" / version / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "node").write_text("")
    return root


def test_repo_node_comes_from_nvmrc_newest_matching_major(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".nvmrc").write_text("24\n")
    nvm = _nvm(tmp_path, "v20.20.0", "v24.1.0", "v24.22.1")
    assert node_bin_dir(repo, nvm) == nvm / "versions" / "node" / "v24.22.1" / "bin"


def test_engines_field_is_used_without_nvmrc(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}}))
    assert wanted_node_major(repo) == 22


def test_missing_pinned_node_is_an_environment_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".nvmrc").write_text("v26")
    with pytest.raises(FileNotFoundError, match="Node 26"):
        node_bin_dir(repo, _nvm(tmp_path, "v24.22.1"))


def test_repo_without_a_pin_uses_the_controller_path(tmp_path):
    assert node_bin_dir(tmp_path, _nvm(tmp_path, "v24.22.1")) is None


def test_parse_version():
    assert parse_version("2.1.311 (Claude Code)") == (2, 1, 311)
    assert parse_version("nope") is None
